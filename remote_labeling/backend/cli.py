"""Manual init, scan, serve, backup and restore entrypoints."""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sqlite3
from pathlib import Path

from .application.service import AnnotationService
from .config import Settings
from .domain.rules import DomainError
from .infrastructure.datasets.source import scan_dataset
from .infrastructure.persistence.sqlite import SQLiteStore


def main() -> None:
    """Run an explicit, reproducible single-process service command."""
    parser = argparse.ArgumentParser(description="Real-ISR 远程标注")
    parser.add_argument(
        "command",
        choices=[
            "init",
            "scan",
            "check-data",
            "serve",
            "backup",
            "restore",
            "openapi",
        ],
    )
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--dataset")
    parser.add_argument("--file", type=Path)
    args = parser.parse_args()
    settings = Settings.load(args.config)
    store = SQLiteStore(settings.state_dir, settings.sqlite_journal_mode)
    service = AnnotationService(settings, store)
    logging.basicConfig(level=logging.INFO)
    try:
        if args.command == "check-data":
            reports = {}
            for key, value in settings.datasets.items():
                if args.dataset and key != args.dataset:
                    continue
                result = scan_dataset(value.root, value.attribute)
                reports[key] = {
                    "samples": len(result["samples"]),
                    "errors": result["errors"],
                    "repairs": result["repairs"],
                    "repaired_samples": len(
                        {item["sample"] for item in result["repairs"]}
                    ),
                    "max_group_bytes": max(
                        (
                            sum(i["bytes"] for i in s["images"].values())
                            for s in result["samples"]
                        ),
                        default=0,
                    ),
                    "max_regions": max(
                        (len(s["group"]["HR"]) for s in result["samples"]),
                        default=0,
                    ),
                }
            print(json.dumps(reports, ensure_ascii=False, indent=2))
            if any(r["errors"] for r in reports.values()):
                raise SystemExit(1)
            return
        if args.command == "openapi":
            from .main import create_app

            output = json.dumps(
                create_app(settings).openapi(), ensure_ascii=False, indent=2
            )
            if args.file:
                args.file.write_text(output, encoding="utf-8")
            else:
                print(output)
            return
        settings.preflight()
        if args.command == "serve":
            import uvicorn
            from .main import create_app

            uvicorn.run(
                create_app(settings),
                host=settings.host,
                port=settings.port,
                workers=1,
                access_log=False,
                proxy_headers=False,
            )
            return
        if args.command == "backup":
            if not args.file:
                parser.error("backup 需要 --file")
            store.backup(args.file)
            print(f"一致性备份已写入 {args.file}")
            return
        if args.command == "restore":
            if not args.file or not args.file.is_file():
                parser.error("restore 需要 --file 指定备份")
            if settings.state_dir.exists() and any(
                settings.state_dir.iterdir()
            ):
                raise DomainError("restore", "只允许恢复到新的空状态目录", 409)
            with sqlite3.connect(
                args.file.resolve().as_uri() + "?mode=ro", uri=True
            ) as source:
                if (
                    source.execute("PRAGMA integrity_check").fetchone()[0]
                    != "ok"
                ):
                    raise DomainError("backup", "备份完整性检查失败")
            settings.state_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(args.file, store.path)
        settings.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        store.acquire_instance()
        try:
            store.initialize()
            if args.command == "restore":
                with store.transaction() as db:
                    db.execute("DELETE FROM leases")
                    db.execute("DELETE FROM sessions")
                    db.execute("DELETE FROM owner_ip_bindings")
                    db.execute("DELETE FROM operations")
                    db.execute("DELETE FROM shares WHERE role='owner'")
                    db.execute("UPDATE shares SET revoked=1")
                    db.execute(
                        "UPDATE jobs SET state='interrupted',error='备份恢复' WHERE state IN ('queued','running')"
                    )
                    db.execute(
                        "UPDATE exports SET state='interrupted',path=NULL,error='需重新导出'"
                    )
            if args.command in ("init", "restore"):
                service.initialize_owner()
                print(f"所有者凭据文件: {settings.state_dir / 'owner.token'}")
                print("通过首页输入凭据；不要将其写入访问日志或普通分享。")
            if args.command in ("init", "scan"):
                for key in settings.datasets:
                    if args.dataset is None or args.dataset == key:
                        print(
                            json.dumps(service.scan(key), ensure_ascii=False)
                        )
        finally:
            store.release_instance()
    except DomainError as exc:
        parser.exit(1, f"{exc.code}: {exc}\n")


if __name__ == "__main__":
    main()
