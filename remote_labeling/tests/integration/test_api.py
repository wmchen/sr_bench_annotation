"""End-to-end service contracts using actual SQLite and temporary sources."""

import copy
import json
import secrets
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from remote_labeling.backend.application.service import digest
from remote_labeling.backend.domain.rules import DomainError
from remote_labeling.backend.infrastructure.persistence.sqlite import (
    SQLiteStore,
)

BASE = "/api/v1/datasets/text/samples/000000.png"


def fence(client, base: str = BASE) -> dict:
    """Acquire one fresh tab's lease."""
    tab = secrets.token_hex(16)
    response = client.post(base + "/lease", json={"tab_id": tab})
    assert response.status_code == 200, response.text
    lease = response.json()
    return {
        "tab_id": tab,
        "lease_id": lease["id"],
        "lease_generation": lease["generation"],
        "base_revision": 0,
        "operation_id": secrets.token_hex(16),
    }


def test_complete_roundtrip_and_export(client, region) -> None:
    """Save, confirm, preserve formal versions and publish compatible JSON."""
    write = fence(client)
    payload = write | {
        "hr": [region],
        "recoverability": {
            v: {region["region_id"]: 1} for v in ("LR2", "LR3", "LR4")
        },
    }
    response = client.put(BASE + "/draft", json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["revision"] == 1
    assert response.json()["formal"] is None
    commit = write | {
        "base_revision": 1,
        "operation_id": secrets.token_hex(16),
    }
    committed = client.post(BASE + "/commit", json=commit)
    assert committed.status_code == 200, committed.text
    assert committed.json()["complete"]
    assert (
        client.post(BASE + "/commit", json=commit).json() == committed.json()
    )
    payload.update(base_revision=2, operation_id=secrets.token_hex(16))
    payload["hr"][0]["description"] = "changed"
    response = client.put(BASE + "/draft", json=payload)
    assert response.json()["formal"]["HR"][0]["description"] == "Hello"
    assert not response.json()["complete"]
    exported = client.post("/api/v1/exports", json={"dataset": "text"})
    assert exported.status_code == 202
    for _ in range(100):
        info = client.get("/api/v1/exports").json()[0]
        if info["state"] == "ready":
            break
        time.sleep(0.02)
    assert info["state"] == "ready", info
    assert (
        client.get(f"/api/v1/exports/{info['id']}/download").status_code == 200
    )


def test_idempotency_and_conflicts(client, region) -> None:
    """Lost responses can be retried; reused operation IDs cannot mutate twice."""
    write = fence(client)
    payload = write | {"hr": [region], "recoverability": {}}
    first = client.put(BASE + "/draft", json=payload)
    assert first.status_code == 200, first.text
    assert client.put(BASE + "/draft", json=payload).json() == first.json()
    changed = copy.deepcopy(payload)
    changed["hr"][0]["description"] = "wrong"
    assert client.put(BASE + "/draft", json=changed).status_code == 409
    changed["operation_id"] = secrets.token_hex(16)
    assert client.put(BASE + "/draft", json=changed).status_code == 409


def test_tab_competition_and_expired_generation(
    client, service, region
) -> None:
    """Two tabs cannot share a lease, and old generations remain fenced."""
    write = fence(client)
    denied = client.post(
        BASE + "/lease", json={"tab_id": secrets.token_hex(16)}
    )
    assert denied.status_code == 423
    with service.store.transaction() as db:
        db.execute("UPDATE leases SET expires=0")
    new = fence(client)
    assert new["lease_generation"] != write["lease_generation"]
    result = client.put(
        BASE + "/draft", json=write | {"hr": [region], "recoverability": {}}
    )
    assert result.status_code == 409


def test_share_scope_revocation_and_cached_image(client, settings) -> None:
    """Revoke already exchanged sessions and authorize before returning 304."""
    share = client.post(
        "/api/v1/shares",
        json={"role": "view", "dataset": "text", "sample": "000000.png"},
    ).json()
    owner_cookie = client.cookies.get("realisr_session")
    token = share["url"].split("#token=")[1]
    client.post("/api/v1/session", json={"token": token})
    viewer_cookie = client.cookies.get("realisr_session")
    assert client.get("/api/v1/datasets/text/samples").json()["total"] == 1
    assert (
        client.get("/api/v1/datasets/text/samples/000001.png").status_code
        == 403
    )
    assert (
        client.get("/api/v1/datasets/face/samples/000000.png").status_code
        == 403
    )
    assert (
        client.post(
            BASE + "/lease", json={"tab_id": secrets.token_hex(16)}
        ).status_code
        == 403
    )
    image = client.get(BASE + "/images/HR")
    assert image.status_code == 200
    tag = image.headers["etag"]
    client.cookies.clear()
    client.cookies.set("realisr_session", owner_cookie)
    assert client.delete("/api/v1/shares/" + share["id"]).status_code == 200
    client.cookies.clear()
    client.cookies.set("realisr_session", viewer_cookie)
    assert (
        client.get(
            BASE + "/images/HR", headers={"If-None-Match": tag}
        ).status_code
        == 401
    )


def test_commit_warnings_and_face_missing(client, region) -> None:
    """Empty and non-monotonic confirmations are explicit; missing data blocks."""
    write = fence(client)
    assert (
        client.post(BASE + "/commit", json=write).json()["error"]["code"]
        == "confirm_empty"
    )
    assert (
        client.post(
            BASE + "/commit", json=write | {"confirm_empty": True}
        ).status_code
        == 200
    )
    base = "/api/v1/datasets/face/samples/000000.png"
    write = fence(client, base)
    face = region | {"label": "face", "description": "", "recoverable": None}
    saved = client.put(
        base + "/draft", json=write | {"hr": [face], "recoverability": {}}
    )
    assert saved.status_code == 200, saved.text
    assert (
        client.post(
            base + "/commit",
            json=write
            | {"base_revision": 1, "operation_id": secrets.token_hex(16)},
        ).json()["error"]["code"]
        == "missing_recoverability"
    )


def test_origin_protection(client) -> None:
    """A cross-origin site cannot write using the session cookie."""
    assert (
        client.post(
            BASE + "/lease",
            json={"tab_id": secrets.token_hex(16)},
            headers={"Origin": "http://evil.example"},
        ).status_code
        == 403
    )


def test_concurrent_sessions_single_winner(service, settings) -> None:
    """SQLite's transactional acquire has exactly one winner under contention."""
    token = (settings.state_dir / "owner.token").read_text().strip()
    sessions = [
        digest(service.exchange(token, str(i), "127.0.0.1")[0])
        for i in range(10)
    ]

    def acquire(sid: str) -> int:
        try:
            service.lease(
                sid, "text", "000000.png", secrets.token_hex(16), "acquire"
            )
            return 200
        except DomainError as exc:
            return exc.status

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(acquire, sessions))
    assert results.count(200) == 1
    assert set(results) <= {200, 423}


def test_backup_and_instance_lock(service, settings, tmp_path) -> None:
    """Backup reads WAL data and a second supervisor cannot own the same DB."""
    service.store.acquire_instance()
    other = SQLiteStore(settings.state_dir)
    with pytest.raises(DomainError):
        other.acquire_instance()
    service.store.backup(tmp_path / "backup.db")
    service.store.release_instance()
    import sqlite3

    with sqlite3.connect(tmp_path / "backup.db") as db:
        assert db.execute("SELECT COUNT(*) FROM samples").fetchone()[0] == 4


def test_model_missing_leaves_manual_work_available(client) -> None:
    """No registered weights never disables manual annotation."""
    assert client.get("/api/v1/model-slot").json()["state"] == "UNLOADED"
    assert (
        client.put(
            "/api/v1/model-slot",
            json={"generation": 0, "model_id": "missing", "device": "cpu"},
        ).status_code
        == 409
    )
    assert client.get(BASE).status_code == 200
