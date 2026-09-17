"""Authorized opening selection against the real online state store."""

import json

import pytest
from fastapi.testclient import TestClient

from remote_labeling.backend.application.service import AnnotationService
from remote_labeling.backend.config import Settings

from remote_labeling.tests.integration.test_api import fence

URL = "/api/v1/datasets/text/opening-selection"


def test_cross_page_draft_and_read_only(
    client: TestClient, service: AnnotationService
) -> None:
    """Later drafts win over untouched samples without taking editing leases."""
    with service.store.transaction() as db:
        original = dict(
            db.execute(
                "SELECT * FROM samples WHERE dataset='text' LIMIT 1"
            ).fetchone()
        )
        columns = ",".join(original)
        for index in range(2, 62):
            row = original | {"id": f"{index:06d}.png", "position": index}
            db.execute(
                f"INSERT INTO samples({columns}) VALUES("
                + ",".join("?" for _ in row)
                + ")",
                list(row.values()),
            )
        db.execute(
            "UPDATE samples SET revision=1 WHERE dataset='text' "
            "AND id='000060.png'"
        )
    base = "/api/v1/datasets/text/samples/000060.png"
    fence(client, base)  # Occupancy must not exclude the preview target.
    with service.store.read() as db:
        before = list(db.iterdump())
    assert client.get(URL).json() == {
        "sample": "000060.png",
        "index": 60,
        "pending_draft": True,
    }
    assert client.get(URL, params={"sample": "000061.png"}).json() == {
        "sample": "000061.png",
        "index": 61,
        "pending_draft": False,
    }
    assert client.get(URL, params={"sample": "missing.png"}).status_code == 404
    with service.store.read() as db:
        assert list(db.iterdump()) == before


def test_redundant_and_empty_fallback(
    client: TestClient, service: AnnotationService
) -> None:
    """Parsed content equality ignores JSON key order and newer revisions."""
    empty = {v: [] for v in ("HR", "LR2", "LR3", "LR4")}
    with service.store.transaction() as db:
        db.execute(
            "UPDATE samples SET draft=?,formal=?,revision=9,complete=1 "
            "WHERE dataset='text' AND id='000000.png'",
            (
                json.dumps(empty),
                json.dumps(dict(reversed(list(empty.items())))),
            ),
        )
    assert client.get(URL).json()["sample"] == "000001.png"
    with service.store.transaction() as db:
        db.execute(
            "UPDATE samples SET formal=draft,complete=1 WHERE dataset='text'"
        )
    assert client.get(URL).json() == {
        "sample": "000000.png",
        "index": 0,
        "pending_draft": False,
    }
    with service.store.transaction() as db:
        db.execute("DELETE FROM samples WHERE dataset='text'")
    assert client.get(URL).json() == {
        "sample": None,
        "index": None,
        "pending_draft": False,
    }
    assert (
        client.get("/api/v1/datasets/missing/opening-selection").status_code
        == 404
    )


@pytest.mark.parametrize("role", ["view", "edit"])
@pytest.mark.parametrize("scoped", [False, True])
def test_share_scope(
    client: TestClient, service: AnnotationService, role: str, scoped: bool
) -> None:
    """The accessible index and automatic target respect sample restrictions."""
    with service.store.transaction() as db:
        db.execute(
            "UPDATE samples SET revision=1 WHERE dataset='text' "
            "AND id='000001.png'"
        )
    share = client.post(
        "/api/v1/shares",
        json={
            "role": role,
            "dataset": "text",
            "sample": "000001.png" if scoped else None,
        },
    ).json()
    response = client.post(
        "/api/v1/session",
        json={
            "token": share["url"].split("#token=")[1],
        },
    )
    assert response.status_code == 200
    assert client.get(URL).json() == {
        "sample": "000001.png",
        "index": 0 if scoped else 1,
        "pending_draft": True,
    }
    assert client.get(URL, params={"sample": "000001.png"}).json()[
        "index"
    ] == (0 if scoped else 1)
    assert (
        client.get("/api/v1/datasets/face/opening-selection").status_code
        == 403
    )
    if scoped:
        assert (
            client.get(URL, params={"sample": "000000.png"}).status_code == 403
        )
    client.cookies.clear()
    assert client.get(URL).status_code == 401


def test_natural_order_is_import_order(
    client: TestClient, service: AnnotationService, settings: Settings
) -> None:
    """Names sort numerically even when lexical order would choose 10 first."""
    root = settings.datasets["text"].root
    for variant in ("HR", "LR2", "LR3", "LR4"):
        (root / variant / "000000.png").rename(root / variant / "10.png")
        (root / variant / "000001.png").rename(root / variant / "2.png")
    assert service.scan("text")["errors"] == []
    assert client.get(URL).json()["sample"] == "2.png"
    assert client.get(URL, params={"sample": "10.png"}).json()["index"] == 1


def test_explicit_index_matches_list_with_retained_positions(
    client: TestClient, service: AnnotationService
) -> None:
    """Rescan-retained positions need not be unique or contiguous."""
    with service.store.transaction() as db:
        db.execute("UPDATE samples SET position=9 WHERE dataset='text'")
    items = client.get("/api/v1/datasets/text/samples").json()["items"]
    for index, item in enumerate(items):
        result = client.get(URL, params={"sample": item["id"]}).json()
        assert result["index"] == index
