"""Permission, lease and revision aware annotation application service."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any

from ..config import Settings
from ..domain.opening import has_pending_draft, opening_selection
from ..domain.rules import DomainError, completion, edit_group
from ..infrastructure.datasets.source import contained, scan_dataset
from ..ports import Store


def encode(value: Any) -> str:
    """Encode canonical JSON for hashes and persisted snapshots."""
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )


def digest(value: str) -> str:
    """Hash capability material; never store raw access tokens in SQLite."""
    return hashlib.sha256(value.encode()).hexdigest()


def normalize_ip(value: str) -> str:
    """Canonicalize a direct peer IP, including IPv4-mapped IPv6."""
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise DomainError("unauthorized", "无法识别访问来源 IP", 401) from exc
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        return str(address.ipv4_mapped)
    return str(address)


def event(
    db: Any, kind: str, dataset: str | None, sample: str | None, payload: dict
) -> None:
    """Append a transactional notification; publish only committed events."""
    db.execute(
        "INSERT INTO events(dataset,sample,kind,payload,created) VALUES(?,?,?,?,?)",
        (dataset, sample, kind, encode(payload), time.time()),
    )
    db.execute(
        "DELETE FROM events WHERE id < (SELECT MAX(id)-10000 FROM events)"
    )


class AnnotationService:
    """Coordinate business rules with transactions and controlled source IO."""

    def __init__(self, settings: Settings, store: Store) -> None:
        self.settings = settings
        self.store = store

    def initialize_owner(self) -> str | None:
        """Create a separate owner credential exactly once."""
        with self.store.transaction() as db:
            if db.execute(
                "SELECT 1 FROM shares WHERE role='owner'"
            ).fetchone():
                return None
            path = self.settings.state_dir / "owner.token"
            if path.exists():
                # Recover a credential written before an interrupted DB commit.
                token = path.read_text(encoding="utf-8").strip()
                if not 32 <= len(token) <= 256:
                    raise DomainError(
                        "owner_credential", "所有者凭据文件无效，不能初始化"
                    )
            else:
                token = secrets.token_urlsafe(32)
                descriptor = os.open(
                    path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
                )
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    stream.write(token + "\n")
                    stream.flush()
                    os.fsync(stream.fileno())
            db.execute(
                "INSERT INTO shares(id,digest,role) VALUES(?,?,?)",
                (secrets.token_hex(16), digest(token), "owner"),
            )
        return token

    def sync_owner_credential(self) -> None:
        """Apply an edited credential at startup while holding the instance lock."""
        try:
            token = (
                (self.settings.state_dir / "owner.token")
                .read_text(encoding="utf-8")
                .strip()
            )
        except (OSError, UnicodeError) as exc:
            raise DomainError(
                "owner_credential", "无法读取 owner.token，服务未启动"
            ) from exc
        if not 32 <= len(token) <= 256:
            raise DomainError(
                "owner_credential", "owner.token 必须包含 32 至 256 个字符"
            )
        fingerprint = digest(token)
        with self.store.transaction() as db:
            owner = db.execute(
                "SELECT * FROM shares WHERE role='owner'"
            ).fetchone()
            if not owner:
                raise DomainError(
                    "owner_credential", "缺少所有者凭据，请先初始化"
                )
            if owner["digest"] == fingerprint:
                return
            db.execute("DELETE FROM owner_ip_bindings")
            db.execute(
                "DELETE FROM leases WHERE session IN "
                "(SELECT id FROM sessions WHERE share=?)",
                (owner["id"],),
            )
            db.execute("DELETE FROM sessions WHERE share=?", (owner["id"],))
            db.execute(
                "UPDATE shares SET digest=?,revoked=0,expires=NULL WHERE id=?",
                (fingerprint, owner["id"]),
            )

    def _new_session(
        self, db: Any, share: str, nickname: str, owner_ip: str | None
    ) -> tuple[str, dict]:
        """Issue an independent session inside the caller's transaction."""
        cookie = secrets.token_urlsafe(32)
        db.execute(
            "INSERT INTO sessions(id,share,nickname,expires,owner_ip) "
            "VALUES(?,?,?,?,?)",
            (
                digest(cookie),
                share,
                nickname,
                time.time() + self.settings.session_seconds,
                owner_ip,
            ),
        )
        return cookie, self.auth(db, digest(cookie))

    def exchange(
        self, token: str, nickname: str, client_ip: str | None = None
    ) -> tuple[str, dict]:
        """Exchange a capability; owner credentials also bind the direct peer IP."""
        now = time.time()
        nickname = nickname.strip() or "访客"
        with self.store.transaction() as db:
            share = db.execute(
                "SELECT * FROM shares WHERE digest=?", (digest(token),)
            ).fetchone()
            if (
                not share
                or share["revoked"]
                or (share["expires"] is not None and share["expires"] <= now)
            ):
                raise DomainError(
                    "unauthorized", "链接无效、已撤销或已到期", 401
                )
            owner_ip = None
            if share["role"] == "owner":
                owner_ip = normalize_ip(client_ip or "")
                db.execute(
                    "INSERT INTO owner_ip_bindings VALUES(?,?,?,?) "
                    "ON CONFLICT(ip) DO UPDATE SET "
                    "owner_digest=excluded.owner_digest,"
                    "nickname=excluded.nickname,bound_at=excluded.bound_at",
                    (owner_ip, share["digest"], nickname, now),
                )
            return self._new_session(db, share["id"], nickname, owner_ip)

    def restore_session(
        self, session: str, client_ip: str
    ) -> tuple[str | None, dict]:
        """Restore a session or issue an owner session from a remembered IP."""
        client_ip = normalize_ip(client_ip)
        with self.store.transaction() as db:
            existing = db.execute(
                "SELECT h.role FROM sessions s JOIN shares h ON h.id=s.share "
                "WHERE s.id=?",
                (session,),
            ).fetchone()
            # Even expired/revoked share sessions must never become owners.
            if existing and existing["role"] != "owner":
                return None, self.auth(db, session)
            if existing:
                try:
                    user = self.auth(db, session)
                except DomainError as exc:
                    if exc.code != "unauthorized":
                        raise
                else:
                    if user["owner_ip"] == client_ip:
                        return None, user
            binding = db.execute(
                "SELECT b.*,h.id share FROM owner_ip_bindings b "
                "JOIN shares h ON h.digest=b.owner_digest "
                "WHERE b.ip=? AND h.role='owner' AND h.revoked=0 "
                "AND (h.expires IS NULL OR h.expires>?)",
                (client_ip, time.time()),
            ).fetchone()
            if not binding:
                raise DomainError(
                    "unauthorized", "此 IP 尚未验证，请输入 owner token", 401
                )
            return self._new_session(
                db, binding["share"], binding["nickname"], client_ip
            )

    def check_session_ip(self, session: str, client_ip: str) -> None:
        """Reject owner cookies presented from a different direct peer IP."""
        with self.store.read() as db:
            user = self.auth(db, session)
            if user["role"] == "owner" and user["owner_ip"] != normalize_ip(
                client_ip
            ):
                raise DomainError(
                    "unauthorized", "访问 IP 已改变，请重新验证", 401
                )

    def logout(self, session: str) -> None:
        """Forget an owner's IP and all its sessions, or end one share session."""
        with self.store.transaction() as db:
            user = self.auth(db, session)
            if user["role"] == "owner":
                db.execute(
                    "DELETE FROM owner_ip_bindings WHERE ip=?",
                    (user["owner_ip"],),
                )
                db.execute(
                    "DELETE FROM leases WHERE session IN "
                    "(SELECT id FROM sessions WHERE owner_ip=?)",
                    (user["owner_ip"],),
                )
                db.execute(
                    "DELETE FROM sessions WHERE owner_ip=?",
                    (user["owner_ip"],),
                )
            else:
                db.execute("DELETE FROM leases WHERE session=?", (session,))
                db.execute("DELETE FROM sessions WHERE id=?", (session,))

    def auth(
        self,
        db: Any,
        session: str | None,
        dataset: str | None = None,
        sample: str | None = None,
        *,
        edit: bool = False,
        owner: bool = False,
    ) -> dict:
        """Recheck current share validity inside every use-case transaction."""
        row = db.execute(
            "SELECT s.id session_id,s.nickname,s.expires session_expires,s.owner_ip,h.* "
            "FROM sessions s JOIN shares h ON h.id=s.share WHERE s.id=?",
            (session or "",),
        ).fetchone()
        now = time.time()
        if (
            not row
            or row["revoked"]
            or row["session_expires"] <= now
            or (row["expires"] is not None and row["expires"] <= now)
        ):
            raise DomainError("unauthorized", "访问权限已失效", 401)
        if (
            row["role"] == "owner"
            and not db.execute(
                "SELECT 1 FROM owner_ip_bindings WHERE ip=? AND owner_digest=?",
                (row["owner_ip"], row["digest"]),
            ).fetchone()
        ):
            raise DomainError("unauthorized", "IP 授权已失效，请重新验证", 401)
        value = dict(row)
        if owner and value["role"] != "owner":
            raise DomainError("forbidden", "仅所有者可执行此操作", 403)
        if edit and value["role"] not in ("owner", "edit"):
            raise DomainError("forbidden", "此链接只有查看权限", 403)
        if dataset is not None and value["role"] != "owner":
            if value["dataset"] != dataset or (
                sample is not None and value["sample"] not in (None, sample)
            ):
                raise DomainError("forbidden", "请求超出分享范围", 403)
        return value

    def whoami(self, session: str) -> dict:
        """Expose only safe permission and timing fields."""
        with self.store.read() as db:
            user = self.auth(db, session)
            return {
                "session_id": user["session_id"],
                "role": user["role"],
                "dataset": user["dataset"],
                "sample": user["sample"],
                "nickname": user["nickname"],
                "lease_seconds": self.settings.lease_seconds,
                "heartbeat_seconds": self.settings.heartbeat_seconds,
            }

    def reserve_scan(self, session: str, dataset: str) -> None:
        """Reserve a visible background import state before returning 202."""
        config = self.settings.datasets.get(dataset)
        if config is None:
            raise DomainError("dataset", "未登记的数据集", 404)
        with self.store.transaction() as db:
            self.auth(db, session, owner=True)
            current = db.execute(
                "SELECT status FROM datasets WHERE id=?", (dataset,)
            ).fetchone()
            if current and current["status"] == "scanning":
                raise DomainError("scan_busy", "数据集正在扫描", 409)
            if db.execute(
                "SELECT 1 FROM leases WHERE dataset=? AND expires>?",
                (dataset, time.time()),
            ).fetchone():
                raise DomainError("leased", "数据集仍有编辑租约", 423)
            db.execute(
                "INSERT INTO datasets(id,attribute,root,status,errors) VALUES(?,?,?,'scanning','[]') ON CONFLICT(id) DO UPDATE SET status='scanning'",
                (dataset, config.attribute, str(config.root.resolve())),
            )
            event(db, "dataset", dataset, None, {"status": "scanning"})

    def finish_scan(self, dataset: str) -> None:
        """Record background errors instead of losing them after the HTTP reply."""
        try:
            self.scan(dataset)
        except Exception as exc:
            with self.store.transaction() as db:
                db.execute(
                    "UPDATE datasets SET status='invalid',errors=? WHERE id=?",
                    (encode([{"sample": None, "message": str(exc)}]), dataset),
                )
                event(db, "dataset", dataset, None, {"status": "invalid"})

    def scan(self, dataset: str) -> dict:
        """Scan outside transactions; reject edits racing with rescan."""
        config = self.settings.datasets.get(dataset)
        if config is None:
            raise DomainError("dataset", "未登记的数据集", 404)
        report = scan_dataset(config.root, config.attribute)
        with self.store.transaction() as db:
            existing = db.execute(
                "SELECT * FROM datasets WHERE id=?", (dataset,)
            ).fetchone()
            if existing and (
                existing["attribute"] != config.attribute
                or existing["root"] != str(config.root.resolve())
            ):
                raise DomainError(
                    "dataset_binding",
                    "已导入的数据集不能重新绑定其他路径或属性",
                    409,
                )
            if (
                existing
                and db.execute(
                    "SELECT 1 FROM leases WHERE dataset=? AND expires>?",
                    (dataset, time.time()),
                ).fetchone()
            ):
                raise DomainError(
                    "leased", "数据集仍有编辑租约，稍后重新扫描", 423
                )
            db.execute(
                "INSERT INTO datasets(id,attribute,root,status,errors) VALUES(?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET status=excluded.status,errors=excluded.errors",
                (
                    dataset,
                    config.attribute,
                    str(config.root.resolve()),
                    "invalid" if report["errors"] else "ready",
                    encode(report["errors"]),
                ),
            )
            if report["errors"]:
                return {
                    "dataset": dataset,
                    "samples": len(report["samples"]),
                    "errors": report["errors"],
                    "repairs": report["repairs"],
                }
            protected = {
                r["id"]: r
                for r in db.execute(
                    "SELECT id,image_version FROM samples WHERE dataset=? AND modified=1",
                    (dataset,),
                )
            }
            incoming = {s["id"]: s for s in report["samples"]}
            for sid, old in protected.items():
                if (
                    sid not in incoming
                    or incoming[sid]["image_version"] != old["image_version"]
                ):
                    raise DomainError(
                        "source_changed",
                        f"{sid}: 在线修改对应原图已变化，需显式处理新数据版本",
                        409,
                    )
            db.execute("DELETE FROM leases WHERE dataset=?", (dataset,))
            db.execute(
                "DELETE FROM samples WHERE dataset=? AND modified=0",
                (dataset,),
            )
            for position, sample in enumerate(report["samples"]):
                if sample["id"] in protected:
                    continue
                db.execute(
                    "INSERT INTO samples(dataset,id,position,images,dimensions,image_version,draft,formal,committed_revision,complete,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        dataset,
                        sample["id"],
                        position,
                        encode(sample["images"]),
                        encode(sample["dimensions"]),
                        sample["image_version"],
                        encode(sample["group"]),
                        (
                            encode(sample["formal"])
                            if sample["formal"] is not None
                            else None
                        ),
                        0 if sample["formal"] is not None else None,
                        int(sample["complete"]),
                        time.time(),
                    ),
                )
            db.execute(
                "UPDATE datasets SET import_version=import_version+1 WHERE id=?",
                (dataset,),
            )
            event(db, "dataset", dataset, None, {})
        return {
            "dataset": dataset,
            "samples": len(report["samples"]),
            "errors": [],
            "repairs": [
                item
                for item in report["repairs"]
                if item["sample"] not in protected
            ],
        }

    def datasets(self, session: str) -> list[dict]:
        """List only authorized datasets and authorized sample progress."""
        with self.store.read() as db:
            user = self.auth(db, session)
            output = []
            for row in db.execute("SELECT * FROM datasets ORDER BY id"):
                if user["role"] != "owner" and user["dataset"] != row["id"]:
                    continue
                query, args = (
                    "SELECT COUNT(*),COALESCE(SUM(complete),0) FROM samples WHERE dataset=?",
                    [row["id"]],
                )
                if user["role"] != "owner" and user["sample"]:
                    query += " AND id=?"
                    args.append(user["sample"])
                total, done = db.execute(query, args).fetchone()
                output.append(
                    {
                        "id": row["id"],
                        "attribute": row["attribute"],
                        "status": row["status"],
                        "import_version": row["import_version"],
                        "total": total,
                        "complete": done,
                        "errors": (
                            json.loads(row["errors"])
                            if user["role"] == "owner"
                            else []
                        ),
                    }
                )
            return output

    def opening_selection(
        self, session: str, dataset: str, sample: str | None = None
    ) -> dict:
        """Resolve an opening sample inside one authorized read snapshot."""
        with self.store.read() as db:
            user = self.auth(db, session, dataset, sample)
            if not db.execute(
                "SELECT 1 FROM datasets WHERE id=?", (dataset,)
            ).fetchone():
                raise DomainError("dataset", "数据集不存在", 404)
            where, args = "dataset=?", [dataset]
            if user["role"] != "owner" and user["sample"]:
                where += " AND id=?"
                args.append(user["sample"])
            if sample is not None:
                row = self.sample_row(db, dataset, sample)
                # Rescans can retain online rows with the same position as a
                # newly imported row. Enumerate the actual accessible order.
                index = next(
                    index
                    for index, item in enumerate(
                        db.execute(
                            "SELECT id FROM samples WHERE "
                            + where
                            + " ORDER BY position",
                            args,
                        )
                    )
                    if item["id"] == sample
                )
                return {
                    "sample": sample,
                    "index": index,
                    "pending_draft": has_pending_draft(
                        json.loads(row["draft"]),
                        (
                            json.loads(row["formal"])
                            if row["formal"] is not None
                            else None
                        ),
                        row["revision"],
                    ),
                }
            rows = db.execute(
                "SELECT id,draft,formal,revision,complete FROM samples WHERE "
                + where
                + " ORDER BY position",
                args,
            )
            return opening_selection(
                {
                    **dict(row),
                    "draft": json.loads(row["draft"]),
                    "formal": (
                        json.loads(row["formal"])
                        if row["formal"] is not None
                        else None
                    ),
                }
                for row in rows
            )

    def list_samples(
        self, session: str, dataset: str, search: str, offset: int, limit: int
    ) -> dict:
        """Paginate the prebuilt index without rescanning source directories."""
        with self.store.read() as db:
            user = self.auth(db, session, dataset)
            where, args = "dataset=? AND instr(id,?)>0", [dataset, search]
            if user["role"] != "owner" and user["sample"]:
                where += " AND id=?"
                args.append(user["sample"])
            total = db.execute(
                "SELECT COUNT(*) FROM samples WHERE " + where, args
            ).fetchone()[0]
            rows = db.execute(
                "SELECT id,revision,committed_revision,complete FROM samples WHERE "
                + where
                + " ORDER BY position LIMIT ? OFFSET ?",
                [*args, limit, offset],
            ).fetchall()
            return {"items": [dict(r) for r in rows], "total": total}

    def sample_row(
        self, db: Any, dataset: str, sample: str, *, editable: bool = False
    ) -> Any:
        """Resolve an indexed sample and optionally enforce import readiness."""
        row = db.execute(
            "SELECT s.*,d.attribute,d.status,d.root FROM samples s JOIN datasets d ON d.id=s.dataset WHERE s.dataset=? AND s.id=?",
            (dataset, sample),
        ).fetchone()
        if row is None:
            raise DomainError("sample", "样本不存在", 404)
        if editable and row["status"] != "ready":
            raise DomainError(
                "dataset_invalid", "数据集校验失败，不能编辑", 409
            )
        return row

    def view(self, row: Any) -> dict:
        """Return annotation data without private filesystem paths."""
        draft = json.loads(row["draft"])
        return {
            "dataset": row["dataset"],
            "id": row["id"],
            "attribute": row["attribute"],
            "revision": row["revision"],
            "committed_revision": row["committed_revision"],
            "draft": draft,
            "formal": (
                json.loads(row["formal"])
                if row["formal"] is not None
                else None
            ),
            "complete": bool(row["complete"]),
            "dimensions": json.loads(row["dimensions"]),
            "image_version": row["image_version"],
            "validation": completion(draft),
        }

    def get_sample(self, session: str, dataset: str, sample: str) -> dict:
        """Read current saved versions and an advisory occupancy summary."""
        with self.store.read() as db:
            self.auth(db, session, dataset, sample)
            value = self.view(self.sample_row(db, dataset, sample))
            lease = db.execute(
                "SELECT l.expires,s.nickname FROM leases l JOIN sessions s ON s.id=l.session WHERE l.dataset=? AND l.sample=? AND l.expires>?",
                (dataset, sample, time.time()),
            ).fetchone()
            value["occupancy"] = dict(lease) if lease else None
            return value

    def lease(
        self,
        session: str,
        dataset: str,
        sample: str,
        tab: str,
        action: str,
        lease_id: str | None = None,
    ) -> dict:
        """Acquire, renew or release a tab-bound exclusive editing lease."""
        now = time.time()
        with self.store.transaction() as db:
            self.auth(db, session, dataset, sample, edit=True)
            self.sample_row(db, dataset, sample, editable=True)
            old = db.execute(
                "SELECT * FROM leases WHERE dataset=? AND sample=?",
                (dataset, sample),
            ).fetchone()
            if old:
                try:
                    self.auth(db, old["session"], dataset, sample, edit=True)
                except DomainError:
                    db.execute(
                        "DELETE FROM leases WHERE dataset=? AND sample=?",
                        (dataset, sample),
                    )
                    old = None
            mine = (
                old
                and old["session"] == session
                and old["tab"] == tab
                and (
                    old["id"] == lease_id
                    or (action == "acquire" and lease_id is None)
                )
                and old["expires"] > now
            )
            if action == "acquire":
                if old and old["expires"] > now:
                    if not mine:
                        raise DomainError(
                            "leased", "样本正由其他编辑会话占用", 423
                        )
                    db.execute(
                        "UPDATE leases SET expires=? WHERE dataset=? AND sample=?",
                        (now + self.settings.lease_seconds, dataset, sample),
                    )
                    return {
                        **dict(old),
                        "expires": now + self.settings.lease_seconds,
                    }
                value = {
                    "dataset": dataset,
                    "sample": sample,
                    "id": secrets.token_hex(16),
                    "generation": secrets.token_hex(16),
                    "session": session,
                    "tab": tab,
                    "expires": now + self.settings.lease_seconds,
                }
                db.execute(
                    "INSERT OR REPLACE INTO leases VALUES(?,?,?,?,?,?,?)",
                    tuple(value.values()),
                )
            else:
                if not mine:
                    raise DomainError(
                        "lease_lost", "编辑租约已失效，请重新申请", 409
                    )
                if action == "release":
                    db.execute(
                        "DELETE FROM leases WHERE dataset=? AND sample=?",
                        (dataset, sample),
                    )
                    value = {"released": True}
                else:
                    db.execute(
                        "UPDATE leases SET expires=? WHERE dataset=? AND sample=?",
                        (now + self.settings.lease_seconds, dataset, sample),
                    )
                    value = {
                        **dict(old),
                        "expires": now + self.settings.lease_seconds,
                    }
            event(db, "lease", dataset, sample, {})
            return value

    def check_write(
        self, db: Any, session: str, dataset: str, sample: str, request: dict
    ) -> Any:
        """Fence old leases and stale revisions before any new write."""
        row = self.sample_row(db, dataset, sample, editable=True)
        lease = db.execute(
            "SELECT * FROM leases WHERE dataset=? AND sample=?",
            (dataset, sample),
        ).fetchone()
        if not lease or any(
            (
                lease["session"] != session,
                lease["tab"] != request["tab_id"],
                lease["id"] != request["lease_id"],
                lease["generation"] != request["lease_generation"],
                lease["expires"] <= time.time(),
            )
        ):
            raise DomainError(
                "lease_lost", "编辑权已失效，本地内容尚未提交", 409
            )
        if row["revision"] != request["base_revision"]:
            raise DomainError(
                "revision_conflict",
                "服务器版本已变化，请保留并核对本地内容",
                409,
                {"revision": row["revision"]},
            )
        return row

    def save(
        self,
        session: str,
        dataset: str,
        sample: str,
        request: dict,
        *,
        commit: bool = False,
    ) -> dict:
        """Apply an idempotent whole-group save or formal confirmation."""
        signature = digest(encode([dataset, sample, commit, request]))
        with self.store.transaction() as db:
            self.auth(db, session, dataset, sample, edit=True)
            old = db.execute(
                "SELECT * FROM operations WHERE session=? AND id=?",
                (session, request["operation_id"]),
            ).fetchone()
            if old:
                if old["digest"] != signature:
                    raise DomainError(
                        "operation_reused", "同一操作 ID 不能提交不同内容", 409
                    )
                return json.loads(old["result"])
            row = self.check_write(db, session, dataset, sample, request)
            group = (
                json.loads(row["draft"])
                if commit
                else edit_group(
                    request["hr"],
                    json.loads(row["draft"]),
                    json.loads(row["dimensions"]),
                    row["attribute"],
                    request["recoverability"],
                )
            )
            check = completion(group)
            if commit:
                if check["missing"]:
                    raise DomainError(
                        "missing_recoverability",
                        "仍有未设置的可恢复度",
                        details=check,
                    )
                if check["empty"] and not request.get("confirm_empty"):
                    raise DomainError(
                        "confirm_empty",
                        "请明确确认这是空标注组",
                        details=check,
                    )
                if check["violations"] and not request.get(
                    "confirm_monotonic"
                ):
                    raise DomainError(
                        "confirm_monotonic",
                        "可恢复度不满足单调性，请确认是否继续",
                        details=check,
                    )
            serialized = encode(group)
            revision = row["revision"] + 1
            formal = serialized if commit else row["formal"]
            complete = (
                formal is not None
                and json.loads(formal) == group
                and not check["missing"]
            )
            db.execute(
                "UPDATE samples SET draft=?,formal=?,revision=?,committed_revision=?,modified=1,complete=?,updated=? WHERE dataset=? AND id=?",
                (
                    serialized,
                    formal,
                    revision,
                    revision if commit else row["committed_revision"],
                    int(complete),
                    time.time(),
                    dataset,
                    sample,
                ),
            )
            result = self.view(self.sample_row(db, dataset, sample))
            db.execute(
                "INSERT INTO operations VALUES(?,?,?,?)",
                (session, request["operation_id"], signature, encode(result)),
            )
            event(db, "sample", dataset, sample, {"revision": revision})
            return result

    def shares(self, session: str) -> list[dict]:
        """List owner-managed shares without token digests."""
        with self.store.read() as db:
            self.auth(db, session, owner=True)
            return [
                dict(r)
                for r in db.execute(
                    "SELECT id,role,dataset,sample,expires,revoked FROM shares WHERE role!='owner'"
                )
            ]

    def create_share(
        self,
        session: str,
        role: str,
        dataset: str,
        sample: str | None,
        expires: float | None,
    ) -> dict:
        """Issue a high-entropy dataset or single-sample capability."""
        with self.store.transaction() as db:
            self.auth(db, session, owner=True)
            if not db.execute(
                "SELECT 1 FROM datasets WHERE id=?", (dataset,)
            ).fetchone():
                raise DomainError("dataset", "数据集不存在", 404)
            if sample:
                self.sample_row(db, dataset, sample)
            if expires is not None and expires <= time.time():
                raise DomainError("expires", "到期时间必须晚于当前时间")
            token, sid = secrets.token_urlsafe(32), secrets.token_hex(16)
            db.execute(
                "INSERT INTO shares VALUES(?,?,?,?,?,?,0)",
                (sid, digest(token), role, dataset, sample, expires),
            )
            return {
                "id": sid,
                "url": self.settings.public_origin + "/#token=" + token,
            }

    def revoke(self, session: str, share: str) -> dict:
        """Revoke existing sessions and release all associated editing leases."""
        with self.store.transaction() as db:
            self.auth(db, session, owner=True)
            row = db.execute(
                "SELECT * FROM shares WHERE id=? AND role!='owner'", (share,)
            ).fetchone()
            if not row:
                raise DomainError("share", "分享不存在", 404)
            db.execute("UPDATE shares SET revoked=1 WHERE id=?", (share,))
            db.execute(
                "DELETE FROM leases WHERE session IN (SELECT id FROM sessions WHERE share=?)",
                (share,),
            )
            event(db, "share", row["dataset"], row["sample"], {})
            return {"revoked": True}

    def events(self, session: str, after: int) -> list[dict]:
        """Read authorized event hints; callers requery authoritative state."""
        with self.store.read() as db:
            user = self.auth(db, session)
            output = []
            where, arguments = "id>?", [after]
            if user["role"] == "view":
                where += " AND kind!='model'"
            if user["role"] != "owner":
                where += " AND (dataset IS NULL OR dataset=?)"
                arguments.append(user["dataset"])
                if user["sample"]:
                    where += " AND (sample IS NULL OR sample=?)"
                    arguments.append(user["sample"])
            for row in db.execute(
                "SELECT * FROM events WHERE "
                + where
                + " ORDER BY id LIMIT 1000",
                arguments,
            ):
                output.append(
                    {
                        "id": row["id"],
                        "kind": row["kind"],
                        "dataset": row["dataset"],
                        "sample": row["sample"],
                        "payload": json.loads(row["payload"]),
                    }
                )
            return output

    def image(
        self, session: str, dataset: str, sample: str, variant: str
    ) -> tuple[Path, str]:
        """Authorize original bytes and detect changed source metadata."""
        with self.store.read() as db:
            self.auth(db, session, dataset, sample)
            row = self.sample_row(db, dataset, sample)
            if variant not in ("HR", "LR2", "LR3", "LR4"):
                raise DomainError("variant", "倍率不存在", 404)
            info = json.loads(row["images"])[variant]
            path = contained(Path(row["root"]), Path(info["path"]))
            stat = path.stat()
            if (
                stat.st_size != info["bytes"]
                or stat.st_mtime_ns != info["mtime_ns"]
            ):
                raise DomainError(
                    "source_changed", "原图已改变，请重新校验数据集", 409
                )
            return path, info["sha256"]
