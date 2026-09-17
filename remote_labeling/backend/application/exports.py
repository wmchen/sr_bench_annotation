"""Freeze formal snapshots and publish complete versioned JSON exports."""

from __future__ import annotations

import json
import os
import secrets
import shutil
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .service import AnnotationService, encode, event
from ..domain.rules import DomainError, VARIANTS, metadata
from ..infrastructure.datasets.source import contained
from ..infrastructure.datasets.documents import document, json_text


class ExportService:
    """Background filesystem export separated from annotation transactions."""

    def __init__(self, annotations: AnnotationService) -> None:
        self.annotations = annotations
        self.pool = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="export"
        )

    def create(self, session: str, dataset: str) -> dict:
        """Freeze exact snapshots so later commits belong to the next export."""
        service = self.annotations
        with service.store.transaction() as db:
            service.auth(db, session, owner=True)
            data = db.execute(
                "SELECT * FROM datasets WHERE id=?", (dataset,)
            ).fetchone()
            if not data:
                raise DomainError("dataset", "数据集不存在", 404)
            snapshots = [
                {
                    "sample": r["id"],
                    "revision": r["committed_revision"],
                    "group": json.loads(r["formal"]),
                    "dimensions": json.loads(r["dimensions"]),
                    "image_version": r["image_version"],
                }
                for r in db.execute(
                    "SELECT * FROM samples WHERE dataset=? AND formal IS NOT NULL ORDER BY position",
                    (dataset,),
                )
            ]
            manifest = {
                "dataset": dataset,
                "attribute": data["attribute"],
                "samples": snapshots,
            }
            eid = secrets.token_hex(16)
            db.execute(
                "INSERT INTO exports VALUES(?,?,?, ?,NULL,NULL,?)",
                (eid, dataset, "queued", encode(manifest), time.time()),
            )
        self.pool.submit(self.run, eid)
        return {"id": eid, "state": "queued"}

    def run(self, eid: str) -> None:
        """Publish only after JSON, manifest and downloadable archive finish."""
        service = self.annotations
        staging = service.settings.export_dir / (".partial-" + eid)
        final = service.settings.export_dir / eid
        try:
            with service.store.transaction() as db:
                row = db.execute(
                    "SELECT * FROM exports WHERE id=?", (eid,)
                ).fetchone()
                manifest = json.loads(row["manifest"])
                db.execute(
                    "UPDATE exports SET state='running' WHERE id=?", (eid,)
                )
            staging.mkdir(parents=True, exist_ok=False)
            output = staging / "annotations"
            for variant in VARIANTS:
                (output / variant).mkdir(parents=True)
            (output / "RealISRMeta.json").write_text(
                json_text(metadata(manifest["attribute"])), encoding="utf-8"
            )
            listing = []
            for sample in manifest["samples"]:
                listing.append(
                    {
                        k: sample[k]
                        for k in ("sample", "revision", "image_version")
                    }
                )
                for variant in VARIANTS:
                    payload = document(
                        sample["sample"],
                        variant,
                        sample["group"],
                        sample["dimensions"],
                        manifest["attribute"],
                    )
                    (
                        output
                        / variant
                        / (Path(sample["sample"]).stem + ".json")
                    ).write_text(json_text(payload), encoding="utf-8")
            (staging / "manifest.json").write_text(
                json_text(
                    {"dataset": manifest["dataset"], "samples": listing}
                ),
                encoding="utf-8",
            )
            with zipfile.ZipFile(
                staging / "annotations.zip", "w", zipfile.ZIP_DEFLATED
            ) as archive:
                for path in sorted(output.rglob("*.json")):
                    archive.write(path, path.relative_to(staging))
                archive.write(staging / "manifest.json", "manifest.json")
            for path in staging.rglob("*"):
                if path.is_file():
                    with path.open("rb") as stream:
                        os.fsync(stream.fileno())
            staging.rename(final)
            with service.store.transaction() as db:
                db.execute(
                    "UPDATE exports SET state='ready',path=? WHERE id=?",
                    (str(final), eid),
                )
                event(
                    db,
                    "export",
                    manifest["dataset"],
                    None,
                    {"id": eid, "state": "ready"},
                )
        except Exception as exc:
            shutil.rmtree(staging, ignore_errors=True)
            with service.store.transaction() as db:
                db.execute(
                    "UPDATE exports SET state='failed',error=? WHERE id=?",
                    (str(exc), eid),
                )

    def list(self, session: str) -> list[dict]:
        """Return owner-visible export progress without private paths."""
        with self.annotations.store.read() as db:
            self.annotations.auth(db, session, owner=True)
            return [
                dict(r)
                for r in db.execute(
                    "SELECT id,dataset,state,error,created FROM exports ORDER BY created DESC"
                )
            ]

    def download(self, session: str, eid: str) -> Path:
        """Resolve only a completely published export."""
        with self.annotations.store.read() as db:
            self.annotations.auth(db, session, owner=True)
            row = db.execute(
                "SELECT * FROM exports WHERE id=?", (eid,)
            ).fetchone()
            if not row or row["state"] != "ready":
                raise DomainError("export_not_ready", "导出尚未完成", 409)
            return contained(
                self.annotations.settings.export_dir,
                Path(row["path"]) / "annotations.zip",
            )

    def close(self) -> None:
        """Finish filesystem jobs before service shutdown."""
        self.pool.shutdown(wait=True)
