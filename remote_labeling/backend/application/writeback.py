"""Recoverable, version-fenced publication of online drafts to dataset roots."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, TYPE_CHECKING

from ..domain.rules import DomainError, VARIANTS, completion, metadata
from ..infrastructure.datasets.documents import document, json_text
from ..infrastructure.datasets.source import (
    contained,
    fingerprint,
    import_group,
    schema,
)

if TYPE_CHECKING:
    from .service import AnnotationService


def public_error(error: Exception) -> str:
    """Explain IO failures without disclosing server-owned source paths."""
    if isinstance(error, OSError):
        return error.strerror or "文件系统操作失败"
    return str(error)


def encoded(value: Any) -> str:
    """Serialize a durable journal value without nonfinite numbers."""
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, allow_nan=False
    )


def read_bytes(path: Path) -> bytes | None:
    """Distinguish absent targets from empty files and IO failures."""
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def pack(data: bytes | None) -> str | None:
    """Keep exact original bytes, including malformed JSON, for rollback."""
    return base64.b64encode(data).decode() if data is not None else None


def unpack(data: str | None) -> bytes | None:
    """Decode one journal file image."""
    return base64.b64decode(data) if data is not None else None


def sync_directory(path: Path) -> None:
    """Persist a directory entry update, including newly created directories."""
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def replace_file(path: Path, data: bytes | None) -> None:
    """Durably replace one file; the journal coordinates multiple files."""
    missing = []
    parent = path.parent
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    for directory in reversed(missing):
        directory.mkdir(exist_ok=True)
        sync_directory(directory.parent)
    temporary = None
    try:
        if data is None:
            path.unlink(missing_ok=True)
        else:
            descriptor, temporary = tempfile.mkstemp(
                prefix=".realisr-write-", dir=path.parent
            )
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)


class WritebackService:
    """Journal filesystem changes separately from short SQLite transactions."""

    def __init__(self, service: AnnotationService) -> None:
        self.service = service
        self.locks = {
            key: threading.RLock() for key in service.settings.datasets
        }

    def lock(self, dataset: str) -> Any:
        """Serialize publications and consistent reads for one dataset."""
        if dataset not in self.locks:
            raise DomainError("dataset", "未登记的数据集", 404)
        return self.locks[dataset]

    @staticmethod
    def guard(db: Any, dataset: str) -> None:
        """Fence all writes and scans while a journal awaits resolution."""
        if db.execute(
            "SELECT 1 FROM writebacks WHERE dataset=? AND state='prepared'",
            (dataset,),
        ).fetchone():
            raise DomainError(
                "writeback_pending", "标注写回或恢复尚未完成，请稍后重试", 409
            )

    def files(self, row: Any) -> dict[str, bytes | None]:
        """Snapshot only this group's JSON and its shared annotation metadata."""
        root = Path(row["root"])
        names = [
            f"annotations/{v}/{Path(row['id']).stem}.json" for v in VARIANTS
        ]
        names += [
            "annotations/RealISRMeta.json",
            "annotations/.realisr_draft.json",
        ]
        return {
            name: read_bytes(contained(root, root / name)) for name in names
        }

    @staticmethod
    def token(files: dict[str, bytes | None]) -> str:
        """Fingerprint exact source bytes so overwrite approval cannot go stale."""
        return hashlib.sha256(
            encoded(
                {
                    name: (
                        hashlib.sha256(data).hexdigest()
                        if data is not None
                        else None
                    )
                    for name, data in files.items()
                }
            ).encode()
        ).hexdigest()

    def state(self, row: Any, files: dict | None = None) -> dict:
        """Return a semantic disk baseline without exposing private paths."""
        result = {
            "source_group": None,
            "source_token": "",
            "source_ready": False,
            "source_error": None,
            "source_dirty": True,
        }
        try:
            files = self.files(row) if files is None else files
            result["source_token"] = self.token(files)
            meta = files["annotations/RealISRMeta.json"]
            if meta is not None:
                schema(json.loads(meta), row["attribute"])
            raw, versions = {}, {}
            compatible = meta is not None
            dimensions = json.loads(row["dimensions"])
            for variant in VARIANTS:
                data = files[
                    f"annotations/{variant}/{Path(row['id']).stem}.json"
                ]
                if data is None:
                    return result
                doc = json.loads(data)
                versions[variant] = schema(
                    doc, row["attribute"], document=True
                )
                raw[variant] = doc["shapes"]
                compatible &= (
                    Path(doc.get("imagePath", row["id"])).name == row["id"]
                    and doc.get("imageWidth", dimensions[variant][0])
                    == dimensions[variant][0]
                    and doc.get("imageHeight", dimensions[variant][1])
                    == dimensions[variant][1]
                )
            repairs: list[dict] = []
            group = import_group(
                raw,
                dimensions,
                row["attribute"],
                row["id"],
                versions,
                strict=True,
                repairs=repairs,
            )
            # Normalization must not hide a genuine LR geometry or field repair.
            compatible &= not repairs and all(
                raw[v] == group[v] for v in VARIANTS
            )
            draft = files["annotations/.realisr_draft.json"]
            if draft is not None:
                payload = json.loads(draft)
                schema(payload, row["attribute"])
                compatible &= row["id"] not in payload.get("samples", {})
            result.update(
                source_group=group,
                source_ready=bool(compatible),
                source_dirty=not compatible
                or group != json.loads(row["draft"]),
            )
        except (
            OSError,
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            DomainError,
        ) as exc:
            result["source_error"] = f"无法读取磁盘标注：{public_error(exc)}"
        return result

    def targets(self, row: Any, originals: dict, group: dict) -> dict:
        """Build four documents and remove only this sample's desktop draft."""
        targets = {}
        for variant in VARIANTS:
            name = f"annotations/{variant}/{Path(row['id']).stem}.json"
            # Preserve unrelated top-level extension fields on existing documents.
            previous = {}
            if originals[name] is not None:
                try:
                    value = json.loads(originals[name])
                    if isinstance(value, dict):
                        previous = value
                except (ValueError, UnicodeError):
                    pass
            flags = previous.get("flags", {})
            previous.update(
                document(
                    row["id"],
                    variant,
                    group,
                    json.loads(row["dimensions"]),
                    row["attribute"],
                )
            )
            previous["flags"] = flags
            targets[name] = json_text(previous).encode("utf-8")
        meta = "annotations/RealISRMeta.json"
        if originals[meta] is not None:
            schema(json.loads(originals[meta]), row["attribute"])
        else:
            targets[meta] = json_text(metadata(row["attribute"])).encode(
                "utf-8"
            )
        draft_name = "annotations/.realisr_draft.json"
        if originals[draft_name] is not None:
            draft = json.loads(originals[draft_name])
            schema(draft, row["attribute"])
            if not isinstance(draft.get("samples"), dict):
                raise DomainError("draft", "桌面草稿格式无效，不能安全清理")
            if row["id"] in draft["samples"]:
                del draft["samples"][row["id"]]
                targets[draft_name] = (
                    json_text(draft).encode("utf-8")
                    if draft["samples"]
                    else None
                )
        return targets

    def rollback(self, entry: Any) -> None:
        """Restore only recognized old/new bytes; never overwrite a third version."""
        root = self.service.settings.datasets[entry["dataset"]].root
        with self.service.store.read() as db:
            binding = db.execute(
                "SELECT root FROM datasets WHERE id=?", (entry["dataset"],)
            ).fetchone()
        if (
            binding is None
            or Path(binding["root"]).resolve() != root.resolve()
        ):
            raise DomainError(
                "dataset_binding", "恢复目录与登记数据集不一致", 409
            )
        files = json.loads(entry["files"])
        for name, value in files.items():
            path = contained(root, root / name)
            if read_bytes(path) not in (
                unpack(value["old"]),
                unpack(value["new"]),
            ):
                raise DomainError(
                    "recovery_conflict",
                    "写回恢复发现外部修改，需处理后重启服务",
                    409,
                )
        for name, value in reversed(list(files.items())):
            path = contained(root, root / name)
            old = unpack(value["old"])
            if read_bytes(path) != old:
                replace_file(path, old)
        with self.service.store.transaction() as db:
            db.execute(
                "UPDATE writebacks SET state='rolled_back',files='{}' WHERE session=? AND operation=?",
                (entry["session"], entry["operation"]),
            )

    def recover(self) -> None:
        """Rollback unfinished publications before serving or scanning sources."""
        with self.service.store.read() as db:
            entries = db.execute(
                "SELECT * FROM writebacks WHERE state='prepared'"
            ).fetchall()
        for entry in entries:
            with self.lock(entry["dataset"]):
                self.rollback(entry)

    def save(
        self, session: str, dataset: str, sample: str, request: dict
    ) -> dict:
        """Publish one frozen draft with authorization, fencing and retry safety."""
        try:
            return self._save(session, dataset, sample, request)
        except DomainError:
            raise
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise DomainError(
                "writeback_failed",
                f"不能写回标注，草稿已保留：{public_error(exc)}",
                503,
            ) from exc

    def _save(
        self, session: str, dataset: str, sample: str, request: dict
    ) -> dict:
        """Run the publication under the dataset lock."""
        from .service import digest, encode, event

        service = self.service
        signature = digest(
            encode([dataset, sample, "save-annotations", request])
        )
        with self.lock(dataset):
            with service.store.transaction() as db:
                service.auth(db, session, dataset, sample, edit=True)
                old = db.execute(
                    "SELECT * FROM operations WHERE session=? AND id=?",
                    (session, request["operation_id"]),
                ).fetchone()
                if old:
                    if old["digest"] != signature:
                        raise DomainError(
                            "operation_reused",
                            "同一操作 ID 不能提交不同内容",
                            409,
                        )
                    return json.loads(old["result"])
                prior = db.execute(
                    "SELECT signature FROM writebacks WHERE session=? AND operation=?",
                    (session, request["operation_id"]),
                ).fetchone()
                if prior and prior["signature"] != signature:
                    raise DomainError(
                        "operation_reused", "同一操作 ID 不能提交不同内容", 409
                    )
                row = dict(
                    service.check_write(db, session, dataset, sample, request)
                )
            if request["image_version"] != row["image_version"]:
                raise DomainError(
                    "source_changed", "原图版本已变化，请重新打开样本", 409
                )
            for image in json.loads(row["images"]).values():
                path = contained(Path(row["root"]), Path(image["path"]))
                if fingerprint(path) != image["sha256"]:
                    raise DomainError(
                        "source_changed", "原图内容已变化，不能写回标注", 409
                    )
            originals = self.files(row)
            current_token = self.token(originals)
            if request["source_token"] != current_token:
                raise DomainError(
                    "annotation_source_changed",
                    "磁盘标注已变化，请重新读取或确认覆盖",
                    409,
                    {"source_token": current_token},
                )
            group = json.loads(row["draft"])
            check = completion(group)
            if check["empty"] and not request.get("confirm_empty"):
                raise DomainError(
                    "confirm_empty", "请明确确认这是空标注组", details=check
                )
            if check["violations"] and not request.get("confirm_monotonic"):
                raise DomainError(
                    "confirm_monotonic",
                    "可恢复度不满足单调性，请确认是否继续",
                    details=check,
                )
            targets = self.targets(row, originals, group)
            files = {
                name: {"old": pack(originals[name]), "new": pack(data)}
                for name, data in targets.items()
                if data != originals[name]
            }
            entry = {
                "session": session,
                "operation": request["operation_id"],
                "dataset": dataset,
                "files": encoded(files),
            }
            # Commit recovery information BEFORE any source mutation. Recheck the
            # revision because preparing documents happens outside a DB transaction.
            with service.store.transaction() as db:
                service.auth(db, session, dataset, sample, edit=True)
                service.check_write(db, session, dataset, sample, request)
                db.execute(
                    "INSERT INTO writebacks VALUES(?,?,?,?,?,?,'prepared',NULL,?) "
                    "ON CONFLICT(session,operation) DO UPDATE SET state='prepared',files=excluded.files,error=NULL",
                    (
                        session,
                        request["operation_id"],
                        dataset,
                        sample,
                        signature,
                        entry["files"],
                        time.time(),
                    ),
                )
            attempted = {}
            try:
                if self.token(self.files(row)) != current_token:
                    raise DomainError(
                        "annotation_source_changed",
                        "写回前磁盘标注再次变化，请重新读取",
                        409,
                        {"source_token": self.token(self.files(row))},
                    )
                for name, value in files.items():
                    root = Path(row["root"])
                    path = contained(root, root / name)
                    if read_bytes(path) != unpack(value["old"]):
                        raise DomainError(
                            "annotation_source_changed",
                            "写回期间磁盘标注发生变化",
                            409,
                        )
                    attempted[name] = value
                    replace_file(path, unpack(value["new"]))
                with service.store.transaction() as db:
                    revision = row["revision"] + 1
                    db.execute(
                        "UPDATE samples SET formal=draft,revision=?,committed_revision=?,modified=1,complete=?,updated=? WHERE dataset=? AND id=?",
                        (
                            revision,
                            revision,
                            int(not check["missing"]),
                            time.time(),
                            dataset,
                            sample,
                        ),
                    )
                    result = service.view(
                        service.sample_row(db, dataset, sample)
                    )
                    db.execute(
                        "INSERT INTO operations VALUES(?,?,?,?)",
                        (
                            session,
                            request["operation_id"],
                            signature,
                            encode(result),
                        ),
                    )
                    db.execute(
                        "UPDATE writebacks SET state='done',files='{}' WHERE session=? AND operation=?",
                        (session, request["operation_id"]),
                    )
                    event(
                        db, "sample", dataset, sample, {"revision": revision}
                    )
                return result
            except Exception as exc:
                # An ambiguous COMMIT error must never roll back a committed save.
                with service.store.read() as db:
                    state = db.execute(
                        "SELECT state FROM writebacks WHERE session=? AND operation=?",
                        (session, request["operation_id"]),
                    ).fetchone()
                if state["state"] == "done":
                    with service.store.read() as db:
                        saved = db.execute(
                            "SELECT result FROM operations WHERE session=? AND id=?",
                            (session, request["operation_id"]),
                        ).fetchone()
                    return json.loads(saved["result"])
                try:
                    self.rollback({**entry, "files": encoded(attempted)})
                except Exception as recovery_error:
                    with service.store.transaction() as db:
                        db.execute(
                            "UPDATE writebacks SET error=? WHERE session=? AND operation=?",
                            (
                                str(recovery_error),
                                session,
                                request["operation_id"],
                            ),
                        )
                    raise DomainError(
                        "writeback_recovery",
                        "写回未完成且无法安全恢复，请处理磁盘问题后重启服务；草稿已保留",
                        503,
                    ) from recovery_error
                if isinstance(exc, DomainError):
                    raise
                raise DomainError(
                    "writeback_failed",
                    f"写回失败，已恢复原文件，草稿已保留：{public_error(exc)}",
                    503,
                ) from exc
