"""Operator-selected persistent storage and journal-mode regression coverage."""

from pathlib import Path
import sqlite3

import pytest
from pydantic import ValidationError

from remote_labeling.backend.config import DatasetConfig, Settings
from remote_labeling.backend.domain.rules import DomainError
from remote_labeling.backend.infrastructure.persistence.sqlite import (
    SQLiteStore,
)


@pytest.mark.parametrize("development", [False, True])
def test_operator_selected_network_directory_skips_location_checks(
    monkeypatch, development: bool
) -> None:
    """Do not read mount tables or require the old persistence confirmation."""

    def unexpected_read(*args, **kwargs):
        raise AssertionError("preflight must not inspect the mount table")

    monkeypatch.setattr(Path, "read_text", unexpected_read)
    settings = Settings(
        state_dir=Path("/network/persistent/state"),
        export_dir=Path("/network/persistent/exports"),
        cache_dir=Path("/network/persistent/cache"),
        local_persistence_confirmed=False,
        development=development,
        models={},
        sqlite_journal_mode="DELETE",
    )
    settings.preflight()


def test_source_write_protection_is_retained(tmp_path: Path) -> None:
    """Skipping placement checks must not permit writing into import sources."""
    source = tmp_path / "source"
    settings = Settings(
        state_dir=source / "state",
        export_dir=tmp_path / "exports",
        cache_dir=tmp_path / "cache",
        datasets={"text": DatasetConfig(root=source, attribute="text")},
        models={},
    )
    with pytest.raises(DomainError) as error:
        settings.preflight()
    assert error.value.code == "output_path"


@pytest.mark.parametrize("mode", ["WAL", "DELETE"])
def test_selected_journal_mode_preserves_transactions_and_backup(
    tmp_path: Path, mode: str
) -> None:
    """Both supported modes keep committed changes, roll back failures and back up."""
    store = SQLiteStore(tmp_path / mode, mode)
    store.initialize()
    with store.read() as db:
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == mode.lower()
        assert db.execute("PRAGMA synchronous").fetchone()[0] == 2
    with store.transaction() as db:
        db.execute("CREATE TABLE probe(value TEXT)")
        db.execute("INSERT INTO probe VALUES('saved')")
    with pytest.raises(RuntimeError):
        with store.transaction() as db:
            db.execute("INSERT INTO probe VALUES('not saved')")
            raise RuntimeError("simulate an interrupted use case")
    store.initialize()
    with store.read() as db:
        assert [row[0] for row in db.execute("SELECT value FROM probe")] == [
            "saved"
        ]
    backup = tmp_path / (mode + "-backup.db")
    store.backup(backup)
    with sqlite3.connect(backup) as db:
        assert db.execute("SELECT value FROM probe").fetchall() == [("saved",)]
    if mode == "DELETE":
        assert not Path(str(store.path) + "-shm").exists()


def test_offline_wal_to_delete_transition_preserves_data(
    tmp_path: Path,
) -> None:
    """Changing an existing configuration does not require deleting its database."""
    original = SQLiteStore(tmp_path)
    original.initialize()
    with original.transaction() as db:
        db.execute("CREATE TABLE probe(value TEXT)")
        db.execute("INSERT INTO probe VALUES('existing')")
    changed = SQLiteStore(tmp_path, "DELETE")
    changed.initialize()
    with changed.read() as db:
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert (
            db.execute("SELECT value FROM probe").fetchone()[0] == "existing"
        )


def test_configured_delete_mode_is_used_by_api(settings, service) -> None:
    """The application factory must pass the selected mode to SQLiteStore."""
    from fastapi.testclient import TestClient
    from remote_labeling.backend.main import create_app

    settings.sqlite_journal_mode = "DELETE"
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/v1/health").status_code == 200
        with client.app.state.service.store.read() as db:
            assert db.execute("PRAGMA journal_mode").fetchone()[0] == "delete"


def test_unsupported_journal_mode_is_rejected(tmp_path: Path) -> None:
    """Do not accept unvalidated PRAGMA text in configuration or store calls."""
    with pytest.raises(ValidationError):
        Settings(
            state_dir=tmp_path,
            cache_dir=tmp_path,
            export_dir=tmp_path,
            sqlite_journal_mode="OFF",
        )
    with pytest.raises(DomainError):
        SQLiteStore(tmp_path, "OFF")
