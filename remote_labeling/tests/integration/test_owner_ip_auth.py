"""Remembered owner IPs preserve authentication and sharing boundaries."""

import secrets
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from remote_labeling.backend.application.service import (
    AnnotationService,
    digest,
)
from remote_labeling.backend.config import Settings
from remote_labeling.backend.domain.rules import DomainError
from remote_labeling.backend.infrastructure.persistence.sqlite import (
    SQLiteStore,
)
from remote_labeling.backend.main import create_app

IP = "192.0.2.10"
OTHER_IP = "192.0.2.11"


def peer(settings: Settings, ip: str = IP) -> TestClient:
    """Create an HTTP peer; tests explicitly enter lifespan when needed."""
    return TestClient(
        create_app(settings),
        client=(ip, 50000),
        headers={"Origin": settings.public_origin},
    )


def login(
    client: TestClient, settings: Settings, nickname: str = "所有者"
) -> dict:
    """Validate an owner token through the actual HTTP exchange route."""
    response = client.post(
        "/api/v1/session",
        json={
            "token": (settings.state_dir / "owner.token").read_text().strip(),
            "nickname": nickname,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def binding_count(service: AnnotationService) -> int:
    """Count remembered peers without exposing credentials."""
    with service.store.read() as db:
        return db.execute("SELECT COUNT(*) FROM owner_ip_bindings").fetchone()[
            0
        ]


def test_first_visit_wrong_token_and_independent_restore(
    service: AnnotationService, settings: Settings
) -> None:
    """Only verified IPs restore; separate browsers get independent sessions."""
    first = peer(settings)
    assert first.post("/api/v1/session/restore").status_code == 401
    assert (
        first.post("/api/v1/session", json={"token": "x" * 40}).status_code
        == 401
    )
    assert binding_count(service) == 0
    user = login(first, settings, "  Alice  ")
    response = peer(settings).post("/api/v1/session/restore")
    assert response.status_code == 200
    assert response.json()["role"] == "owner"
    assert response.json()["nickname"] == "Alice"
    assert response.json()["session_id"] != user["session_id"]
    assert "httponly" in response.headers["set-cookie"].lower()
    assert "samesite=strict" in response.headers["set-cookie"].lower()
    assert "max-age=" in response.headers["set-cookie"].lower()
    assert response.headers["cache-control"] == "no-store"
    assert binding_count(service) == 1
    # A current cookie is reused, so refresh does not abandon a live lease.
    restored = first.post("/api/v1/session/restore")
    assert restored.json()["session_id"] == user["session_id"]
    assert "set-cookie" not in restored.headers
    first.cookies.clear()
    assert first.post("/api/v1/session/restore").status_code == 200


def test_expired_owner_session_restores_latest_nickname(
    service: AnnotationService, settings: Settings
) -> None:
    """Session expiry does not expire the persisted IP authorization."""
    first = peer(settings)
    user = login(first, settings, "旧昵称")
    login(peer(settings), settings, "新昵称")
    with service.store.transaction() as db:
        db.execute(
            "UPDATE sessions SET expires=0 WHERE id=?", (user["session_id"],)
        )
    response = first.post("/api/v1/session/restore")
    assert response.status_code == 200
    assert response.json()["nickname"] == "新昵称"
    assert response.json()["session_id"] != user["session_id"]


def test_other_ip_cannot_use_cookie_or_forwarded_headers(
    service: AnnotationService, settings: Settings
) -> None:
    """Client headers and a stolen cookie cannot substitute for the peer IP."""
    first = peer(settings)
    login(first, settings)
    other = peer(settings, OTHER_IP)
    other.cookies.update(first.cookies)
    other.headers.update(
        {"X-Forwarded-For": IP, "X-Real-IP": IP, "Forwarded": f"for={IP}"}
    )
    assert other.get("/api/v1/session").status_code == 401
    assert other.get("/api/v1/datasets").status_code == 401
    assert other.post("/api/v1/session/restore").status_code == 401
    assert other.delete("/api/v1/session").status_code == 401
    assert binding_count(service) == 1
    login(other, settings)
    assert other.get("/api/v1/datasets").status_code == 200
    assert binding_count(service) == 2


@pytest.mark.parametrize(
    "first_ip,next_ip,canonical",
    [
        ("::ffff:192.0.2.10", IP, IP),
        (
            "2001:0db8:0000:0000:0000:0000:0000:0001",
            "2001:db8::1",
            "2001:db8::1",
        ),
    ],
)
def test_ip_normalization(
    service: AnnotationService,
    settings: Settings,
    first_ip: str,
    next_ip: str,
    canonical: str,
) -> None:
    """Equivalent IPv6 and mapped IPv4 addresses share exactly one binding."""
    login(peer(settings, first_ip), settings)
    assert (
        peer(settings, next_ip).post("/api/v1/session/restore").status_code
        == 200
    )
    with service.store.read() as db:
        assert (
            db.execute("SELECT ip FROM owner_ip_bindings").fetchone()[0]
            == canonical
        )


def test_invalid_peer_is_not_bound(
    service: AnnotationService, settings: Settings
) -> None:
    """An unavailable transport IP never becomes a reusable owner identity."""
    client = peer(settings, "testclient")
    token = (settings.state_dir / "owner.token").read_text().strip()
    assert (
        client.post("/api/v1/session", json={"token": token}).status_code
        == 401
    )
    assert binding_count(service) == 0


@pytest.mark.parametrize("role", ["view", "edit"])
@pytest.mark.parametrize(
    "invalid", ["expired_session", "revoked_share", "expired_share"]
)
def test_share_never_restores_as_owner(
    service: AnnotationService, settings: Settings, role: str, invalid: str
) -> None:
    """A share on a remembered IP retains its role, including after expiry."""
    owner = login(peer(settings), settings)
    share = service.create_share(owner["session_id"], role, "text", None, None)
    visitor = peer(settings)
    response = visitor.post(
        "/api/v1/session", json={"token": share["url"].split("#token=")[1]}
    )
    assert response.json()["role"] == role
    sid = response.json()["session_id"]
    assert visitor.post("/api/v1/session/restore").json()["role"] == role
    assert visitor.get("/api/v1/datasets/face/samples").status_code == 403
    assert binding_count(service) == 1
    with service.store.transaction() as db:
        if invalid == "expired_session":
            db.execute("UPDATE sessions SET expires=0 WHERE id=?", (sid,))
        elif invalid == "revoked_share":
            db.execute(
                "UPDATE shares SET revoked=1 WHERE id=?", (share["id"],)
            )
        else:
            db.execute(
                "UPDATE shares SET expires=0 WHERE id=?", (share["id"],)
            )
    assert visitor.post("/api/v1/session/restore").status_code == 401


def test_share_login_and_logout_do_not_change_bindings(
    service: AnnotationService, settings: Settings
) -> None:
    """A share cannot remember a new IP or forget an owner's existing IP."""
    owner = login(peer(settings), settings)
    share = service.create_share(
        owner["session_id"], "view", "text", None, None
    )
    for ip in (IP, OTHER_IP):
        visitor = peer(settings, ip)
        assert (
            visitor.post(
                "/api/v1/session",
                json={"token": share["url"].split("#token=")[1]},
            ).status_code
            == 200
        )
        assert visitor.delete("/api/v1/session").status_code == 200
        assert binding_count(service) == 1
    assert (
        peer(settings, OTHER_IP).post("/api/v1/session/restore").status_code
        == 401
    )
    assert peer(settings).post("/api/v1/session/restore").status_code == 200


def test_logout_forgets_only_this_ip_and_releases_its_leases(
    service: AnnotationService, settings: Settings
) -> None:
    """Logging out invalidates all owner browsers on this IP, not other IPs."""
    first, second, other = (
        peer(settings),
        peer(settings),
        peer(settings, OTHER_IP),
    )
    login(first, settings)
    second_user = second.post("/api/v1/session/restore").json()
    login(other, settings)
    service.lease(
        second_user["session_id"],
        "text",
        "000000.png",
        secrets.token_hex(16),
        "acquire",
    )
    assert first.delete("/api/v1/session").status_code == 200
    assert first.cookies.get("realisr_session") is None
    assert first.post("/api/v1/session/restore").status_code == 401
    assert second.get("/api/v1/session").status_code == 401
    assert second.post("/api/v1/session/restore").status_code == 401
    assert other.get("/api/v1/session").status_code == 200
    with service.store.read() as db:
        assert db.execute("SELECT COUNT(*) FROM leases").fetchone()[0] == 0
    assert binding_count(service) == 1
    login(first, settings)
    assert second.get("/api/v1/session").status_code == 401


def test_same_token_restart_and_changed_token_restart(
    service: AnnotationService, settings: Settings
) -> None:
    """Only a restart with a changed file invalidates remembered owners."""
    with peer(settings) as first:
        user = login(first, settings)
        cookie = first.cookies.get("realisr_session")
    with peer(settings) as restarted:
        response = restarted.post("/api/v1/session/restore")
        assert response.status_code == 200
        assert response.json()["nickname"] == user["nickname"]
        share = service.create_share(
            response.json()["session_id"], "view", "text", None, None
        )
        visitor = peer(settings)
        visitor.post(
            "/api/v1/session", json={"token": share["url"].split("#token=")[1]}
        )
        path = settings.state_dir / "owner.token"
        old_token = path.read_text().strip()
        new_token = secrets.token_urlsafe(32)
        path.write_text(new_token + "\n")
        # Editing the file alone does not change running-service authority.
        assert restarted.get("/api/v1/session").status_code == 200
    with peer(settings) as rotated:
        rotated.cookies.set("realisr_session", cookie)
        assert rotated.get("/api/v1/session").status_code == 401
        assert rotated.post("/api/v1/session/restore").status_code == 401
        assert binding_count(service) == 0
        assert (
            rotated.post(
                "/api/v1/session", json={"token": old_token}
            ).status_code
            == 401
        )
        # Ordinary shares survive an owner credential change.
        assert visitor.get("/api/v1/session").status_code == 200
        login(rotated, settings)
        assert (
            peer(settings).post("/api/v1/session/restore").status_code == 200
        )


@pytest.mark.parametrize("content", [None, "", "short", "x" * 257, b"\xff"])
def test_bad_owner_file_fails_startup_without_losing_authority(
    service: AnnotationService, settings: Settings, content: str | bytes | None
) -> None:
    """Unreadable or invalid credentials fail closed and release the instance lock."""
    login(peer(settings), settings)
    path = settings.state_dir / "owner.token"
    original = path.read_bytes()
    if content is None:
        path.unlink()
    elif isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content)
    with pytest.raises(DomainError, match="owner.token"):
        with peer(settings):
            pass
    assert binding_count(service) == 1
    path.write_bytes(original)
    with peer(settings) as restarted:
        assert restarted.post("/api/v1/session/restore").status_code == 200


def test_restore_requires_same_origin(
    service: AnnotationService, settings: Settings
) -> None:
    """Remembered IP login is a write action protected by the origin boundary."""
    login(peer(settings), settings)
    visitor = peer(settings)
    assert (
        visitor.post(
            "/api/v1/session/restore",
            headers={"Origin": "http://evil.example"},
        ).status_code
        == 403
    )
    visitor.headers.pop("Origin")
    assert visitor.post("/api/v1/session/restore").status_code == 403


def test_v1_migration_preserves_shares_and_invalidates_owners(
    tmp_path: Path,
) -> None:
    """Upgrade once without granting legacy owner cookies an implicit IP."""
    store = SQLiteStore(tmp_path)
    script = Path(
        "remote_labeling/backend/infrastructure/persistence/001.sql"
    ).read_text()
    with sqlite3.connect(store.path) as db:
        db.executescript(script)
        for role in ("owner", "view"):
            db.execute(
                "INSERT INTO shares(id,digest,role) VALUES(?,?,?)",
                (role, digest(role), role),
            )
            db.execute(
                "INSERT INTO sessions VALUES(?,?,?,?)",
                (role, role, role, 9999999999),
            )
            db.execute(
                "INSERT INTO leases VALUES(?,?,?,?,?,?,?)",
                ("text", role, role, role, role, role, 9999999999),
            )
    store.initialize()
    store.initialize()
    with store.read() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
        assert [row["id"] for row in db.execute("SELECT * FROM sessions")] == [
            "view"
        ]
        assert [
            row["session"] for row in db.execute("SELECT * FROM leases")
        ] == ["view"]
        assert db.execute("SELECT COUNT(*) FROM shares").fetchone()[0] == 2
        assert (
            db.execute("SELECT COUNT(*) FROM owner_ip_bindings").fetchone()[0]
            == 0
        )


def test_cli_serve_disables_proxy_header_trust(
    service: AnnotationService,
    settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The production entrypoint never lets forwarded headers rewrite the peer."""
    import yaml
    from remote_labeling.backend.cli import main

    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump(settings.model_dump(mode="json")))
    monkeypatch.setattr(
        "sys.argv", ["realisr-remote", "serve", "--config", str(config)]
    )
    with patch("uvicorn.run") as run:
        main()
    assert run.call_args.kwargs["proxy_headers"] is False
