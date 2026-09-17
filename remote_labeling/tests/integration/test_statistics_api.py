"""Statistics API coverage with actual saved drafts, permissions and SQLite."""

import secrets
import json
from pathlib import Path

from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from remote_labeling.backend.application.service import AnnotationService
from remote_labeling.backend.config import Settings
from unittest.mock import patch

import pytest

from remote_labeling.backend.application import statistics, writeback
from remote_labeling.backend.application.statistics import StatisticsCache
from remote_labeling.tests.integration.test_api import BASE, fence
from remote_labeling.tests.integration.test_writeback import (
    draft,
    publish,
    request,
)

URL = "/api/v1/datasets/text/statistics"


def counts(client: TestClient) -> dict:
    """Fetch authoritative counts while checking the response contract."""
    response = client.get(URL)
    assert response.status_code == 200, response.text
    return {k: v for k, v in response.json().items() if k != "generated_at"}


def test_saved_lifecycle_and_idempotency(
    client: TestClient, region: dict
) -> None:
    """Draft, formal, edit, undo and deletion share the left navigation status."""
    initial = counts(client)
    assert initial["sample_groups"] == 2
    assert initial["image_files"] == 8
    assert initial["instances"] == initial["pending_samples"] == 0
    write, body = draft(client, region, complete=True)
    stats = counts(client)
    assert stats["instances"] == stats["completed_instances"] == 1
    assert (
        stats["recoverability_assigned"] == stats["recoverability_total"] == 4
    )
    assert stats["complete_samples"] == 0
    assert stats["pending_samples"] == 1
    formal = publish(client, body).json()
    assert counts(client)["complete_samples"] == 1
    assert counts(client)["pending_samples"] == 0
    assert publish(client, body).json() == formal
    previous = formal
    for hr, done in (
        ([region | {"description": "edited"}], 0),
        ([region], 1),
        ([], 0),
    ):
        response = client.put(
            BASE + "/draft",
            json=write
            | {
                "base_revision": previous["revision"],
                "operation_id": secrets.token_hex(16),
                "hr": hr,
                "recoverability": {},
            },
        )
        assert response.status_code == 200, response.text
        previous = response.json()
        current = counts(client)
        assert current["instances"] == len(hr)
        assert current["complete_samples"] == done
        assert current["pending_samples"] == 1 - done
        listing = next(
            d
            for d in client.get("/api/v1/datasets").json()
            if d["id"] == "text"
        )
        assert listing["complete"] == current["complete_samples"]
    response = publish(
        client, request(previous, write) | {"confirm_empty": True}
    )
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["complete"]
    assert counts(client)["complete_samples"] == 1
    assert counts(client)["recoverability_total"] == 0


def test_failure_preserves_statistics_and_retry_updates(
    client: TestClient, region: dict, monkeypatch: MonkeyPatch
) -> None:
    """A failed file publication cannot advance formal progress."""
    _, body = draft(client, region, complete=True)
    before = counts(client)
    original = writeback.replace_file
    calls = 0

    def fail_once(path: Path, data: bytes | None) -> None:
        """Inject one failure after replacing a source file."""
        nonlocal calls
        calls += 1
        original(path, data)
        if calls == 2:
            raise OSError("test publication failure")

    monkeypatch.setattr(writeback, "replace_file", fail_once)
    assert publish(client, body).status_code == 503
    assert counts(client) == before
    monkeypatch.setattr(writeback, "replace_file", original)
    assert publish(client, body).status_code == 200
    assert counts(client)["complete_samples"] == 1
    assert counts(client)["pending_samples"] == 0


@pytest.mark.parametrize("role", ["view", "edit"])
@pytest.mark.parametrize("scoped", [False, True])
def test_authorization_scope_and_revocation(
    client: TestClient,
    service: AnnotationService,
    region: dict,
    role: str,
    scoped: bool,
) -> None:
    """Cached owner counts cannot leak into a restricted share response."""
    draft(client, region)
    assert counts(client)["instances"] == 1
    share = client.post(
        "/api/v1/shares",
        json={
            "role": role,
            "dataset": "text",
            "sample": "000001.png" if scoped else None,
        },
    ).json()
    assert (
        client.post(
            "/api/v1/session",
            json={
                "token": share["url"].split("#token=")[1],
            },
        ).status_code
        == 200
    )
    result = counts(client)
    assert result["scope"] == ("sample" if scoped else "dataset")
    assert result["sample_groups"] == (1 if scoped else 2)
    assert result["instances"] == (0 if scoped else 1)
    assert (
        client.get(
            URL,
            params={"sample": "000000.png", "search": "missing", "offset": 50},
        ).json()["sample_groups"]
        == result["sample_groups"]
    )
    assert client.get("/api/v1/datasets/face/statistics").status_code == 403
    with service.store.transaction() as db:
        db.execute("UPDATE shares SET revoked=1 WHERE role!= 'owner'")
    assert client.get(URL).status_code == 401


def test_snapshot_cache_revisions_rescan_and_no_source_io(
    client: TestClient,
    service: AnnotationService,
    settings: Settings,
    region: dict,
) -> None:
    """Warm requests read counters only; rescan and revisions fence the cache."""
    app_service = client.app.state.service
    session = client.get("/api/v1/session").json()["session_id"]
    original = statistics.sample_statistics
    with patch.object(
        statistics, "sample_statistics", wraps=original
    ) as summarize:
        before = counts(client)
        assert summarize.call_count == 3  # Empty total plus two cold samples.
        summarize.reset_mock()
        with patch("PIL.Image.open", side_effect=AssertionError("no images")):
            assert counts(client) == before
        assert (
            summarize.call_count == 1
        )  # Only the empty total, no sample JSON.
        with (
            patch.object(
                statistics.json,
                "loads",
                side_effect=AssertionError("warm request decoded JSON"),
            ),
            patch.object(
                Path, "read_bytes", side_effect=AssertionError("source read")
            ),
        ):
            warm = app_service.statistics(session, "text")
            assert warm["sample_groups"] == before["sample_groups"]
        draft(client, region)
        summarize.reset_mock()
        after = counts(client)
        assert summarize.call_count == 2
        app_service.statistics_cache = StatisticsCache(capacity=1)
        assert counts(client) == after
        assert len(app_service.statistics_cache._values) == 1
    # Read snapshots and requests themselves must never mutate annotations/DB.
    with service.store.read() as db:
        old_db = list(db.iterdump())
    assert counts(client) == after
    with service.store.read() as db:
        assert list(db.iterdump()) == old_db
    with service.store.transaction() as db:
        db.execute("DELETE FROM leases")
    assert service.scan("text")["errors"] == []
    rescanned = counts(client)
    assert rescanned["import_version"] == after["import_version"] + 1
    assert rescanned["instances"] == after["instances"]
    (settings.datasets["text"].root / "LR4/000001.png").unlink()
    assert service.scan("text")["errors"]
    invalid = counts(client)
    assert invalid["status"] == "invalid"
    assert invalid["sample_groups"] == 2
    assert invalid["import_version"] == rescanned["import_version"]
    with service.store.read() as db:
        last = db.execute(
            "SELECT payload FROM events WHERE kind='dataset' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert "invalid" in last["payload"]


def test_empty_scanning_and_unknown_dataset(
    client: TestClient, service: AnnotationService
) -> None:
    """Unavailable and empty datasets are distinct from request errors."""
    with service.store.transaction() as db:
        db.execute("DELETE FROM samples WHERE dataset='text'")
        db.execute("UPDATE datasets SET status='scanning' WHERE id='text'")
    result = counts(client)
    assert result["sample_groups"] == result["image_files"] == 0
    assert result["status"] == "scanning"
    assert result["recoverability_total"] == 0
    assert client.get("/api/v1/datasets/missing/statistics").status_code == 404


def test_inference_application_invalidates_counts(
    client: TestClient, region: dict
) -> None:
    """Applied inference changes draft counts without requiring model weights."""
    assert counts(client)["instances"] == 0
    write = fence(client)
    sample = client.get(BASE).json()
    session = client.get("/api/v1/session").json()["session_id"]
    inference = client.app.state.inference
    body = write | {
        "image_version": sample["image_version"],
        "slot_generation": inference.generation,
        "region_ids": [],
    }
    job = {
        "id": "statistics-job",
        "session": session,
        "dataset": "text",
        "sample": "000000.png",
        "request": json.dumps(body),
    }
    with client.app.state.service.store.transaction() as db:
        db.execute(
            "INSERT INTO jobs(id,session,tab,dataset,sample,state,request,created,updated) "
            "VALUES(?,?,?,?,?,'running',?,0,0)",
            (
                job["id"],
                session,
                write["tab_id"],
                "text",
                "000000.png",
                job["request"],
            ),
        )
    inference.apply_result(job, [region])
    result = counts(client)
    assert result["instances"] == result["pending_samples"] == 1
    assert result["complete_samples"] == 0
    assert result["recoverability_assigned"] == 1
    assert result["recoverability_total"] == 4
