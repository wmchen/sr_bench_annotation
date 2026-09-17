"""Bounded transactions, exclusive service ownership and online backups."""

from __future__ import annotations

import fcntl
import sqlite3
import time
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Iterator, Literal

from ...domain.rules import DomainError


class SQLiteStore:
    """Open independent connections; SQLite arbitrates short transactions."""

    def __init__(
        self, state_dir: Path, journal_mode: Literal["WAL", "DELETE"] = "WAL"
    ) -> None:
        if journal_mode not in ("WAL", "DELETE"):
            raise DomainError("journal_mode", "仅支持 WAL 或 DELETE 日志模式")
        self.journal_mode = journal_mode
        self.state_dir = state_dir
        self.path = state_dir / "state.db"
        self._lock_file = None

    def initialize(self) -> None:
        """Install the schema and migrate older supported versions."""
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        with closing(self.connect()) as db:
            actual_mode = db.execute(
                f"PRAGMA journal_mode={self.journal_mode}"
            ).fetchone()[0]
            if actual_mode.lower() != self.journal_mode.lower():
                raise DomainError(
                    "journal_mode",
                    "无法设置数据库日志模式，请停止其他服务实例后重试",
                    503,
                )
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, 2):
                raise DomainError("schema", "不支持的数据库版本")
            if not version:
                db.executescript(
                    Path(__file__).with_name("001.sql").read_text()
                )
            if version < 2:
                db.executescript(
                    Path(__file__).with_name("002.sql").read_text()
                )

    def connect(self) -> sqlite3.Connection:
        """Create a durable connection with a bounded busy timeout."""
        db = sqlite3.connect(self.path, timeout=0.15, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=FULL")
        return db

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Keep authorization, lease and revision checks in one transaction."""
        db = self.connect()
        try:
            for attempt in range(3):
                try:
                    db.execute("BEGIN IMMEDIATE")
                    break
                except sqlite3.OperationalError as exc:
                    if (
                        getattr(exc, "sqlite_errorcode", None)
                        not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)
                        or attempt == 2
                    ):
                        raise
                    time.sleep(0.01 * (attempt + 1))
            yield db
            db.commit()
        except sqlite3.DatabaseError as exc:
            db.rollback()
            raise DomainError(
                "storage_unavailable",
                "保存失败，存储暂不可用，请保留内容并重试",
                503,
            ) from exc
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        """Read from a consistent snapshot without acquiring the writer lock."""
        db = self.connect()
        try:
            db.execute("BEGIN")
            yield db
        finally:
            db.rollback()
            db.close()

    def acquire_instance(self) -> None:
        """Prevent two supervisors from owning the same state directory."""
        self._lock_file = (self.state_dir / "service.lock").open("a")
        try:
            fcntl.flock(self._lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self._lock_file.close()
            self._lock_file = None
            raise DomainError(
                "instance", "已有服务使用该状态目录", 409
            ) from exc

    def release_instance(self) -> None:
        """Release the advisory lock after children have exited."""
        if self._lock_file:
            self._lock_file.close()
            self._lock_file = None

    def backup(self, destination: Path) -> None:
        """Write a consistent backup including committed WAL pages."""
        if not self.path.is_file():
            raise DomainError("backup", "状态库不存在", 404)
        if destination.exists():
            raise DomainError("backup", "备份目标已存在，请选择新文件", 409)
        destination.parent.mkdir(parents=True, exist_ok=True)
        source = self.connect()
        target = sqlite3.connect(destination)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
