"""Publication tests use real SQLite, files, API fences and injected failures."""

import json
import os
import secrets
from contextlib import contextmanager
from typing import Any
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from remote_labeling.backend.application import writeback
from remote_labeling.backend.domain.rules import DomainError, metadata
from remote_labeling.backend.infrastructure.datasets.source import scan_dataset
from remote_labeling.backend.main import create_app
from remote_labeling.tests.integration.test_api import BASE, fence


def draft(
    client: TestClient, region: dict, *, complete: bool = False
) -> tuple[dict, dict]:
    """Create an online draft and a fenced publication request."""
    write = fence(client)
    response = client.put(
        BASE + "/draft",
        json=write
        | {
            "hr": [region],
            "recoverability": (
                {v: {region["region_id"]: 1} for v in ("LR2", "LR3", "LR4")}
                if complete
                else {}
            ),
        },
    )
    assert response.status_code == 200, response.text
    sample = response.json()
    return write, request(sample, write)


def request(sample: dict, write: dict) -> dict:
    """Pin both source and draft versions observed by the caller."""
    return write | {
        "base_revision": sample["revision"],
        "operation_id": secrets.token_hex(16),
        "source_token": sample["source_token"],
        "image_version": sample["image_version"],
    }


def publish(client: TestClient, body: dict) -> Any:
    """Invoke the production source save endpoint."""
    return client.post(BASE + "/save-annotations", json=body)


def snapshots(root: Path) -> dict:
    """Capture annotation bytes to verify lossless rollback."""
    return {
        str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*.json")
    }


def test_partial_save_writes_all_variants_and_formal(
    client, settings, region
) -> None:
    """Incomplete annotations publish immediately without claiming completion."""
    _, body = draft(client, region)
    response = publish(client, body)
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["formal"] == saved["draft"]
    assert not saved["complete"]
    assert not saved["source_dirty"]
    assert saved["source_ready"]
    root = settings.datasets["text"].root
    for variant in ("HR", "LR2", "LR3", "LR4"):
        doc = json.loads(
            (root / "annotations" / variant / "000000.json").read_text()
        )
        assert doc["shapes"] == saved["draft"][variant]
        assert not doc["checked"]
    imported = scan_dataset(root, "text")
    assert not imported["errors"]
    assert imported["samples"][0]["formal"] == saved["draft"]
    assert not imported["samples"][0]["complete"]
    if os.environ.get("REALISR_DESKTOP_TESTS"):
        from anylabeling.views.labeling.realisr_dataset import RealISRDataset

        desktop = RealISRDataset(root, "text")
        assert (
            desktop.records_for("000000.png", "HR")[0]["description"]
            == "Hello"
        )
        assert (
            desktop.records_for("000000.png", "LR2")[0]["recoverable"] is None
        )
        assert not desktop.is_complete("000000.png")
    # Retrying the exact request must return the original committed response.
    assert publish(client, body).json() == saved
    assert client.get(BASE).json()["revision"] == saved["revision"]


def test_format_only_changes_remain_synced(client, settings, region) -> None:
    """Whitespace and key ordering do not enable a redundant save."""
    _, body = draft(client, region, complete=True)
    assert publish(client, body).status_code == 200
    root = settings.datasets["text"].root
    path = root / "annotations/HR/000000.json"
    value = json.loads(path.read_text())
    path.write_text(json.dumps(value, indent=4, sort_keys=False))
    assert not client.get(BASE).json()["source_dirty"]


def test_external_conflict_requires_current_token(
    client, settings, region
) -> None:
    """Each approved overwrite is bound to the exact observed source bytes."""
    write, body = draft(client, region, complete=True)
    saved = publish(client, body).json()
    body = request(saved, write)
    path = settings.datasets["text"].root / "annotations/HR/000000.json"
    original = json.loads(path.read_text())
    original["shapes"][0]["description"] = "external"
    path.write_text(json.dumps(original))
    before = path.read_bytes()
    conflict = publish(client, body)
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "annotation_source_changed"
    assert path.read_bytes() == before
    body["source_token"] = conflict.json()["error"]["details"]["source_token"]
    path.write_bytes(before + b"\n")
    conflict = publish(client, body)
    assert conflict.status_code == 409
    body["source_token"] = conflict.json()["error"]["details"]["source_token"]
    assert publish(client, body).status_code == 200
    assert json.loads(path.read_text())["shapes"][0]["description"] == "Hello"


def test_cleanup_only_current_desktop_draft(client, settings, region) -> None:
    """Unrelated desktop drafts survive publication and round-trip import."""
    _, body = draft(client, region)
    root = settings.datasets["text"].root / "annotations"
    root.mkdir()
    group = client.get(BASE).json()["draft"]
    path = root / ".realisr_draft.json"
    payload = metadata("text") | {
        "samples": {"000000.png": group, "000001.png": group}
    }
    path.write_text(json.dumps(payload))
    body["source_token"] = client.get(BASE).json()["source_token"]
    assert publish(client, body).status_code == 200
    assert json.loads(path.read_text())["samples"] == {"000001.png": group}


@pytest.mark.parametrize("existing", [False, True])
def test_partial_file_failure_rolls_back(
    client, settings, region, monkeypatch, existing
) -> None:
    """Fail after an actual replacement, covering new and existing files."""
    write, body = draft(client, region)
    if existing:
        first = publish(client, body).json()
        changed = client.put(
            BASE + "/draft",
            json=write
            | {
                "base_revision": first["revision"],
                "operation_id": secrets.token_hex(16),
                "hr": [region | {"description": "new"}],
                "recoverability": {},
            },
        ).json()
        body = request(changed, write)
    root = settings.datasets["text"].root
    before = snapshots(root)
    previous = client.get(BASE).json()
    original = writeback.replace_file
    calls = 0

    def fail_after_replace(path: Path, data: bytes | None) -> None:
        nonlocal calls
        calls += 1
        original(path, data)
        if calls == 2:
            raise OSError("simulated disk failure")

    monkeypatch.setattr(writeback, "replace_file", fail_after_replace)
    response = publish(client, body)
    assert response.status_code == 503, response.text
    assert snapshots(root) == before
    current = client.get(BASE).json()
    assert current["formal"] == previous["formal"]
    assert current["draft"] == previous["draft"]
    assert current["revision"] == previous["revision"]
    monkeypatch.setattr(writeback, "replace_file", original)
    assert publish(client, body).status_code == 200


def test_database_failure_rolls_back_files(client, settings, region) -> None:
    """A failure committing the formal snapshot restores every source file."""
    _, body = draft(client, region)
    service = client.app.state.service
    with service.store.transaction() as db:
        db.execute(
            "CREATE TRIGGER fail_formal BEFORE UPDATE OF formal ON samples BEGIN SELECT RAISE(ABORT, 'simulated commit failure'); END"
        )
    before = snapshots(settings.datasets["text"].root)
    response = publish(client, body)
    assert response.status_code == 503
    assert snapshots(settings.datasets["text"].root) == before
    assert client.get(BASE).json()["formal"] is None


def test_restart_recovers_interrupted_publication(
    client, settings, region, monkeypatch
) -> None:
    """Simulate abrupt process death after one file reached the source root."""
    _, body = draft(client, region)
    service = client.app.state.service
    original = writeback.replace_file

    def interrupted(path: Path, data: bytes | None) -> None:
        original(path, data)
        raise KeyboardInterrupt("simulated process death")

    with monkeypatch.context() as patch:
        patch.setattr(writeback, "replace_file", interrupted)
        # Direct invocation avoids letting BaseException stop TestClient's portal.
        with service.store.read() as db:
            session = db.execute("SELECT id FROM sessions").fetchone()[0]
        with pytest.raises(KeyboardInterrupt):
            service.writeback.save(session, "text", "000000.png", body)
    with service.store.read() as db:
        assert (
            db.execute("SELECT state FROM writebacks").fetchone()[0]
            == "prepared"
        )
    assert client.get(BASE).status_code == 409
    assert client.post("/api/v1/datasets/text/scan").status_code == 409
    service.store.release_instance()
    with TestClient(create_app(settings)):
        pass
    assert snapshots(settings.datasets["text"].root) == {}
    with service.store.read() as db:
        assert (
            db.execute("SELECT state FROM writebacks").fetchone()[0]
            == "rolled_back"
        )
        assert (
            db.execute(
                "SELECT formal FROM samples WHERE dataset='text' AND id='000000.png'"
            ).fetchone()[0]
            is None
        )


def test_revision_and_image_fences(client, settings, region) -> None:
    """Reject stale drafts and changed image bytes before touching annotation files."""
    _, body = draft(client, region)
    assert publish(client, body | {"base_revision": 0}).status_code == 409
    image = settings.datasets["text"].root / "HR/000000.png"
    image.write_bytes(image.read_bytes() + b"changed")
    response = publish(client, body)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "source_changed"
    assert not snapshots(settings.datasets["text"].root)


def test_retired_commit_requires_refresh(client) -> None:
    """Old clients cannot silently publish only to the database."""
    assert client.post(BASE + "/commit", json=fence(client)).status_code == 410


def test_symlink_escape_never_written(
    client, settings, region, tmp_path
) -> None:
    """A destination symlink cannot redirect writes outside a dataset root."""
    _, body = draft(client, region)
    outside = tmp_path / "outside"
    outside.mkdir()
    (settings.datasets["text"].root / "annotations").symlink_to(
        outside, target_is_directory=True
    )
    response = publish(client, body)
    assert response.status_code == 403
    assert list(outside.iterdir()) == []


def test_readonly_share_cannot_publish(client, settings, region) -> None:
    """The visible view-mode button does not grant write permission to viewers."""
    _, body = draft(client, region)
    service = client.app.state.service
    with service.store.read() as db:
        owner = db.execute("SELECT id FROM sessions").fetchone()[0]
    from remote_labeling.backend.application.service import digest

    link = service.create_share(owner, "view", "text", "000000.png", None)
    viewer = digest(
        service.exchange(link["url"].split("#token=")[1], "viewer")[0]
    )
    with pytest.raises(DomainError) as error:
        service.writeback.save(viewer, "text", "000000.png", body)
    assert error.value.status == 403
    assert snapshots(settings.datasets["text"].root) == {}


def test_pending_writeback_blocks_draft_scan_and_backup(
    client, settings, region, monkeypatch, tmp_path
) -> None:
    """No online operation may publish against an unresolved file transaction."""
    write, body = draft(client, region)
    service = client.app.state.service
    original = writeback.replace_file

    def interrupted(path: Path, data: bytes | None) -> None:
        original(path, data)
        raise KeyboardInterrupt()

    with service.store.read() as db:
        session = db.execute("SELECT id FROM sessions").fetchone()[0]
    with monkeypatch.context() as patch:
        patch.setattr(writeback, "replace_file", interrupted)
        with pytest.raises(KeyboardInterrupt):
            service.writeback.save(session, "text", "000000.png", body)
    changed = write | {
        "base_revision": 1,
        "operation_id": secrets.token_hex(16),
        "hr": [region],
        "recoverability": {},
    }
    assert client.put(BASE + "/draft", json=changed).status_code == 409
    assert client.post("/api/v1/datasets/text/scan").status_code == 409
    with pytest.raises(DomainError):
        service.store.backup(tmp_path / "unsafe.db")
    assert not (tmp_path / "unsafe.db").exists()
    # A third-party modification must not be destroyed during crash recovery.
    path = settings.datasets["text"].root / "annotations/HR/000000.json"
    saved = path.read_bytes()
    path.write_text('{"external":true}')
    with pytest.raises(DomainError, match="外部修改"):
        service.writeback.recover()
    assert path.read_text() == '{"external":true}'
    path.write_bytes(saved)
    service.writeback.recover()
    assert snapshots(settings.datasets["text"].root) == {}


def test_saved_draft_rescan_and_export_remain_consistent(
    client, settings, region
) -> None:
    """An explicit scan and export preserve published partial results and newer drafts."""
    from remote_labeling.backend.application.exports import ExportService

    write, body = draft(client, region)
    saved = publish(client, body).json()
    service = client.app.state.service
    with service.store.read() as db:
        session = db.execute("SELECT id FROM sessions").fetchone()[0]
    exports = ExportService(service)
    try:
        result = exports.create(session, "text")
    finally:
        exports.close()
    path = settings.export_dir / result["id"] / "annotations/LR2/000000.json"
    assert json.loads(path.read_text())["shapes"] == saved["formal"]["LR2"]
    changed = client.put(
        BASE + "/draft",
        json=write
        | {
            "base_revision": saved["revision"],
            "operation_id": secrets.token_hex(16),
            "hr": [region | {"description": "unsaved online edit"}],
            "recoverability": {},
        },
    ).json()
    assert (
        client.request(
            "DELETE",
            BASE + "/lease",
            json={"tab_id": write["tab_id"], "lease_id": write["lease_id"]},
        ).status_code
        == 200
    )
    assert service.scan("text")["errors"] == []
    current = client.get(BASE).json()
    assert current["draft"] == changed["draft"]
    assert current["formal"] == saved["formal"]
    assert current["source_dirty"]


def test_schema_three_unset_hr_roundtrip(client, settings, region) -> None:
    """An explicit unset HR value is not silently restored to a legacy default."""
    _, body = draft(client, region | {"recoverable": None})
    saved = publish(client, body)
    assert saved.status_code == 200, saved.text
    assert not saved.json()["source_dirty"]
    imported = scan_dataset(settings.datasets["text"].root, "text")
    assert imported["samples"][0]["formal"]["HR"][0]["recoverable"] is None


def test_final_cleanup_failure_restores_desktop_draft(
    client, settings, region, monkeypatch
) -> None:
    """Restore shared metadata and exact desktop draft bytes after late failure."""
    _, body = draft(client, region)
    root = settings.datasets["text"].root
    annotations = root / "annotations"
    annotations.mkdir()
    desktop = annotations / ".realisr_draft.json"
    desktop.write_text(
        json.dumps(
            metadata("text")
            | {
                "samples": {"000000.png": client.get(BASE).json()["draft"]},
            },
            indent=2,
        )
    )
    before = snapshots(root)
    body["source_token"] = client.get(BASE).json()["source_token"]
    original = writeback.replace_file

    def fail_cleanup(path: Path, data: bytes | None) -> None:
        original(path, data)
        if path == desktop and data is None:
            raise OSError("simulated late failure")

    monkeypatch.setattr(writeback, "replace_file", fail_cleanup)
    assert publish(client, body).status_code == 503
    assert snapshots(root) == before
    assert client.get(BASE).json()["formal"] is None


def test_error_after_database_commit_returns_committed_result(
    client, region, monkeypatch
) -> None:
    """An ambiguous post-commit error must never undo a committed publication."""
    _, body = draft(client, region)
    store = client.app.state.service.store
    transaction = store.transaction
    calls = 0

    @contextmanager
    def ambiguous_transaction():
        nonlocal calls
        calls += 1
        current = calls
        with transaction() as db:
            yield db
        if current == 3:
            raise OSError("simulated failure after committed transaction")

    monkeypatch.setattr(store, "transaction", ambiguous_transaction)
    saved = publish(client, body)
    assert saved.status_code == 200, saved.text
    assert saved.json()["formal"] == saved.json()["draft"]
    assert not saved.json()["source_dirty"]
    assert publish(client, body).json() == saved.json()


def test_missing_files_and_region_extensions_survive(
    client, settings, region
) -> None:
    """Repair a missing variant while retaining online and document metadata."""
    region = region | {"custom": {"reviewer": "A"}}
    write, body = draft(client, region)
    saved = publish(client, body).json()
    root = settings.datasets["text"].root / "annotations"
    path = root / "HR/000000.json"
    doc = json.loads(path.read_text())
    doc.update(custom_document="keep", flags={"reviewed": True})
    path.write_text(json.dumps(doc))
    (root / "LR3/000000.json").unlink()
    current = client.get(BASE).json()
    assert current["source_dirty"]
    body = request(current, write)
    response = publish(client, body)
    assert response.status_code == 200, response.text
    assert not response.json()["source_dirty"]
    doc = json.loads(path.read_text())
    assert doc["custom_document"] == "keep"
    assert doc["flags"] == {"reviewed": True}
    assert doc["shapes"][0]["custom"] == {"reviewer": "A"}
    imported = scan_dataset(settings.datasets["text"].root, "text")
    assert not imported["errors"]
    assert imported["samples"][0]["formal"] == saved["draft"]
