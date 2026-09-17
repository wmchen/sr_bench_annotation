"""Crash, rollback, rescan and inference-result fencing regressions."""

import json
import secrets
import sqlite3

import pytest

from remote_labeling.backend.application.inference import InferenceService
from remote_labeling.backend.application.service import digest
from remote_labeling.backend.config import ModelConfig
from remote_labeling.backend.domain.rules import DomainError
from remote_labeling.backend.infrastructure.inference.devices import (
    select_device,
)
from remote_labeling.backend.infrastructure.persistence.sqlite import (
    SQLiteStore,
)


def owner(service, settings) -> str:
    """Create an independently authorized owner access session."""
    return digest(
        service.exchange(
            (settings.state_dir / "owner.token").read_text().strip(),
            "test",
            "127.0.0.1",
        )[0]
    )


def write_fence(service, sid: str) -> dict:
    """Acquire a fresh lease and construct its write fence."""
    tab = secrets.token_hex(16)
    lease = service.lease(sid, "text", "000000.png", tab, "acquire")
    return {
        "tab_id": tab,
        "lease_id": lease["id"],
        "lease_generation": lease["generation"],
        "base_revision": 0,
        "operation_id": secrets.token_hex(16),
    }


def test_transaction_rollback_after_update(
    service, settings, region, monkeypatch
) -> None:
    """A failed transaction publishes neither a partial group nor an operation."""
    sid = owner(service, settings)
    request = write_fence(service, sid) | {
        "hr": [region],
        "recoverability": {},
    }

    def fail(*args) -> None:
        raise OSError("simulated disk full")

    monkeypatch.setattr(
        "remote_labeling.backend.application.service.event", fail
    )
    with pytest.raises(OSError):
        service.save(sid, "text", "000000.png", request)
    row = service.get_sample(sid, "text", "000000.png")
    assert row["revision"] == 0
    assert row["draft"]["HR"] == []
    with service.store.read() as db:
        assert db.execute("SELECT COUNT(*) FROM operations").fetchone()[0] == 0


def test_busy_has_bounded_failure(service, settings, region) -> None:
    """A held writer transaction produces an actionable save failure."""
    sid = owner(service, settings)
    request = write_fence(service, sid) | {
        "hr": [region],
        "recoverability": {},
    }
    held = service.store.connect()
    held.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(DomainError) as error:
            service.save(sid, "text", "000000.png", request)
        assert error.value.status == 503
    finally:
        held.rollback()
        held.close()
    assert service.get_sample(sid, "text", "000000.png")["revision"] == 0


def test_rescan_preserves_online_changes(service, settings, region) -> None:
    """Explicit source rescan never overwrites an online-modified sample."""
    sid = owner(service, settings)
    request = write_fence(service, sid)
    service.save(
        sid,
        "text",
        "000000.png",
        request | {"hr": [region], "recoverability": {}},
    )
    service.lease(
        sid,
        "text",
        "000000.png",
        request["tab_id"],
        "release",
        request["lease_id"],
    )
    service.scan("text")
    assert (
        service.get_sample(sid, "text", "000000.png")["draft"]["HR"][0][
            "description"
        ]
        == "Hello"
    )


@pytest.mark.parametrize("invalidate", ["revision", "lease", "share", "model"])
def test_inference_never_applies_stale_results(
    service, settings, region, tmp_path, invalidate: str
) -> None:
    """Exercise the application result transaction without needing model weights."""
    sid = owner(service, settings)
    share = service.create_share(sid, "edit", "text", None, None)
    sid = digest(
        service.exchange(share["url"].split("#token=")[1], "editor")[0]
    )
    path = tmp_path / "fake.onnx"
    path.write_bytes(b"test registry; never loaded")
    settings.models["ocr"] = ModelConfig(
        kind="ppocr_v6",
        attribute="text",
        files={k: path for k in ("det", "rec", "cls", "dictionary")},
    )
    manager = InferenceService(service)
    manager.state = "READY"
    manager.model_id = "ocr"
    manager.model_version = manager.models[0]["version"]
    fence = write_fence(service, sid)
    sample = service.get_sample(sid, "text", "000000.png")
    request = fence | {
        "dataset": "text",
        "sample": "000000.png",
        "model_id": "ocr",
        "model_version": manager.model_version,
        "slot_generation": 0,
        "image_version": sample["image_version"],
        "region_ids": [],
    }
    job_id = manager.submit(sid, request)["id"]
    with service.store.read() as db:
        job = dict(
            db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        )
    if invalidate == "revision":
        service.save(
            sid,
            "text",
            "000000.png",
            fence
            | {
                "operation_id": secrets.token_hex(16),
                "hr": [region],
                "recoverability": {},
            },
        )
    elif invalidate == "lease":
        service.lease(
            sid,
            "text",
            "000000.png",
            fence["tab_id"],
            "release",
            fence["lease_id"],
        )
    elif invalidate == "share":
        service.revoke(owner(service, settings), share["id"])
    else:
        manager.generation += 1
    with pytest.raises(DomainError):
        manager.apply_result(job, [region])
    with service.store.read() as db:
        current = db.execute(
            "SELECT revision FROM samples WHERE dataset='text' AND id='000000.png'"
        ).fetchone()
        assert current["revision"] == (1 if invalidate == "revision" else 0)


def test_gpu_probe_failure_requires_explicit_cpu(
    settings, monkeypatch
) -> None:
    """No probe result must never silently become CPU execution."""
    monkeypatch.setattr(
        "remote_labeling.backend.infrastructure.inference.devices.devices",
        lambda _: {"gpus": [], "error": "probe failed"},
    )
    with pytest.raises(DomainError):
        select_device(settings, "auto", 4096)
    assert select_device(settings, "cpu", 4096) == "cpu"


def test_backup_preserves_draft_and_formal(
    service, settings, region, tmp_path
) -> None:
    """The backup contains the last committed WAL snapshot and newer draft."""
    sid = owner(service, settings)
    request = write_fence(service, sid)
    service.save(
        sid,
        "text",
        "000000.png",
        request
        | {
            "hr": [region],
            "recoverability": {
                v: {region["region_id"]: 1} for v in ("LR2", "LR3", "LR4")
            },
        },
    )
    service.save(
        sid,
        "text",
        "000000.png",
        request | {"base_revision": 1, "operation_id": secrets.token_hex(16)},
        commit=True,
    )
    service.save(
        sid,
        "text",
        "000000.png",
        request
        | {
            "base_revision": 2,
            "operation_id": secrets.token_hex(16),
            "hr": [region | {"description": "new draft"}],
            "recoverability": {},
        },
    )
    destination = tmp_path / "snapshot.db"
    service.store.backup(destination)
    with sqlite3.connect(destination) as db:
        draft, formal = db.execute(
            "SELECT draft,formal FROM samples WHERE dataset='text' AND id='000000.png'"
        ).fetchone()
        assert json.loads(draft)["HR"][0]["description"] == "new draft"
        assert json.loads(formal)["HR"][0]["description"] == "Hello"


def test_scoped_events_do_not_stall_behind_unrelated_traffic(
    service, settings
) -> None:
    """Filter scope before pagination so a quiet dataset still receives updates."""
    sid = owner(service, settings)
    share = service.create_share(sid, "view", "face", "000000.png", None)
    viewer = digest(
        service.exchange(share["url"].split("#token=")[1], "viewer")[0]
    )
    with service.store.transaction() as db:
        cursor = db.execute("SELECT MAX(id) FROM events").fetchone()[0]
        db.executemany(
            "INSERT INTO events(dataset,sample,kind,payload,created) VALUES('text','000000.png','sample','{}',0)",
            [() for _ in range(1100)],
        )
        db.execute(
            "INSERT INTO events(dataset,sample,kind,payload,created) VALUES('face','000000.png','sample','{}',0)"
        )
    events = service.events(viewer, cursor)
    assert len(events) == 1
    assert events[0]["dataset"] == "face"


def test_export_freezes_snapshot_and_desktop_roundtrip(
    service, settings, region, tmp_path, monkeypatch
) -> None:
    """Later commits cannot alter an export's pinned formal snapshot."""
    import shutil
    import os
    from remote_labeling.backend.application.exports import ExportService

    sid = owner(service, settings)
    request = write_fence(service, sid)
    service.save(
        sid,
        "text",
        "000000.png",
        request
        | {
            "hr": [region],
            "recoverability": {
                v: {region["region_id"]: 1} for v in ("LR2", "LR3", "LR4")
            },
        },
    )
    service.save(
        sid,
        "text",
        "000000.png",
        request | {"base_revision": 1, "operation_id": secrets.token_hex(16)},
        commit=True,
    )
    exports = ExportService(service)
    monkeypatch.setattr(exports.pool, "submit", lambda *args: None)
    eid = exports.create(sid, "text")["id"]
    service.save(
        sid,
        "text",
        "000000.png",
        request
        | {
            "base_revision": 2,
            "operation_id": secrets.token_hex(16),
            "hr": [region | {"description": "later"}],
            "recoverability": {},
        },
    )
    service.save(
        sid,
        "text",
        "000000.png",
        request | {"base_revision": 3, "operation_id": secrets.token_hex(16)},
        commit=True,
    )
    exports.run(eid)
    output = settings.export_dir / eid
    doc = json.loads((output / "annotations/HR/000000.json").read_text())
    assert doc["shapes"][0]["description"] == "Hello"
    assert doc["imageData"] is None
    assert (
        json.loads((output / "manifest.json").read_text())["samples"][0][
            "revision"
        ]
        == 2
    )
    if os.environ.get("REALISR_DESKTOP_TESTS"):
        from anylabeling.views.labeling.realisr_dataset import RealISRDataset

        root = tmp_path / "desktop-copy"
        shutil.copytree(settings.datasets["text"].root, root)
        shutil.copytree(output / "annotations", root / "annotations")
        desktop = RealISRDataset(root, "text")
        assert desktop.is_committed("000000.png")
        assert (
            desktop.records_for("000000.png", "HR")[0]["description"]
            == "Hello"
        )
    exports.close()


def test_export_failure_never_publishes_partial_output(
    service, settings, tmp_path, monkeypatch
) -> None:
    """A filesystem failure retains earlier published exports and fails clearly."""
    from remote_labeling.backend.application.exports import ExportService
    from pathlib import Path

    exports = ExportService(service)
    monkeypatch.setattr(exports.pool, "submit", lambda *args: None)
    sid = owner(service, settings)
    first = exports.create(sid, "text")["id"]
    exports.run(first)
    original = exports.download(sid, first).read_bytes()
    failed = exports.create(sid, "text")["id"]
    write = Path.write_text

    def broken(self, *args, **kwargs):
        if ".partial-" + failed in str(self):
            raise OSError("simulated full export disk")
        return write(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", broken)
    exports.run(failed)
    with pytest.raises(DomainError):
        exports.download(sid, failed)
    assert exports.download(sid, first).read_bytes() == original
    assert not (settings.export_dir / failed).exists()
    exports.close()


def test_cli_restore_invalidates_authority_and_preserves_saved_data(
    service, settings, region, tmp_path
) -> None:
    """Offline restoration keeps annotations but never revives old capabilities."""
    import subprocess
    import sys
    import yaml

    sid = owner(service, settings)
    old_token = (settings.state_dir / "owner.token").read_text()
    share = service.create_share(sid, "edit", "text", None, None)
    request = write_fence(service, sid)
    service.save(
        sid,
        "text",
        "000000.png",
        request | {"hr": [region], "recoverability": {}},
    )
    backup = tmp_path / "restore-source.db"
    service.store.backup(backup)
    restored = settings.model_copy(update={"state_dir": tmp_path / "restored"})
    config = tmp_path / "restore.yaml"
    config.write_text(yaml.safe_dump(restored.model_dump(mode="json")))
    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "remote_labeling.backend.cli",
            "restore",
            "--config",
            str(config),
            "--file",
            str(backup),
        ],
        capture_output=True,
        text=True,
    )
    assert process.returncode == 0, process.stderr
    assert (restored.state_dir / "owner.token").read_text() != old_token
    store = SQLiteStore(restored.state_dir)
    with store.read() as db:
        assert db.execute("SELECT COUNT(*) FROM leases").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
        assert (
            db.execute("SELECT COUNT(*) FROM owner_ip_bindings").fetchone()[0]
            == 0
        )
        assert (
            db.execute(
                "SELECT revoked FROM shares WHERE id=?", (share["id"],)
            ).fetchone()[0]
            == 1
        )
        assert (
            json.loads(
                db.execute(
                    "SELECT draft FROM samples WHERE dataset='text' AND id='000000.png'"
                ).fetchone()[0]
            )["HR"][0]["description"]
            == "Hello"
        )


def test_service_restart_fences_previous_lease(
    service, settings, region
) -> None:
    """Startup invalidates old lease generations while keeping saved drafts."""
    from fastapi.testclient import TestClient
    from remote_labeling.backend.main import create_app

    sid = owner(service, settings)
    request = write_fence(service, sid)
    service.save(
        sid,
        "text",
        "000000.png",
        request | {"hr": [region], "recoverability": {}},
    )
    with TestClient(create_app(settings)):
        with pytest.raises(DomainError) as error:
            service.save(
                sid,
                "text",
                "000000.png",
                request
                | {
                    "base_revision": 1,
                    "operation_id": secrets.token_hex(16),
                    "hr": [region],
                    "recoverability": {},
                },
            )
        assert error.value.code == "lease_lost"
        assert (
            service.get_sample(sid, "text", "000000.png")["draft"]["HR"][0][
                "description"
            ]
            == "Hello"
        )
