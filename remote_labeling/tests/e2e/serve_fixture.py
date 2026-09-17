"""Disposable browser-test server; writes only beneath a temporary directory."""

import json
import os
import tempfile
from pathlib import Path

import uvicorn
from PIL import Image, ImageDraw

from remote_labeling.backend.application.service import AnnotationService
from remote_labeling.backend.config import DatasetConfig, Settings
from remote_labeling.backend.infrastructure.persistence.sqlite import (
    SQLiteStore,
)
from remote_labeling.backend.main import create_app


def main() -> None:
    """Create isolated text/face samples and run the production-built UI."""
    with tempfile.TemporaryDirectory(prefix="realisr-browser-") as directory:
        root = Path(directory)
        datasets = {}
        for task in ("text", "face"):
            source = root / task
            for variant, factor in (
                ("HR", 1),
                ("LR2", 2),
                ("LR3", 3),
                ("LR4", 4),
            ):
                (source / variant).mkdir(parents=True)
                for sample in range(12):
                    image = Image.new(
                        "RGB", (800 // factor, 600 // factor), "#c7d0d4"
                    )
                    draw = ImageDraw.Draw(image)
                    draw.rectangle(
                        (
                            80 // factor,
                            100 // factor,
                            500 // factor,
                            250 // factor,
                        ),
                        fill="#26374a",
                    )
                    draw.text(
                        (100 // factor, 140 // factor),
                        "REAL ISR TEST",
                        fill="white",
                    )
                    image.save(source / variant / f"{sample:06d}.png")
            if task == "text":
                from remote_labeling.backend.domain.rules import (
                    edit_group,
                    metadata,
                )

                dimensions = {
                    v: [2500 // f, 2400 // f]
                    for v, f in (("HR", 1), ("LR2", 2), ("LR3", 3), ("LR4", 4))
                }
                records = []
                for index in range(357):
                    x, y = 30 + (index % 21) * 115, 30 + (index // 21) * 135
                    records.append(
                        {
                            "region_id": f"dense-{index}",
                            "label": "text",
                            "description": str(index),
                            "shape_type": "rectangle",
                            "points": [[x, y], [x + 85, y + 55]],
                            "recoverable": 0,
                        }
                    )
                group = edit_group(
                    records,
                    {},
                    dimensions,
                    "text",
                    {
                        v: {r["region_id"]: 1 for r in records}
                        for v in ("LR2", "LR3", "LR4")
                    },
                )
                annotations = source / "annotations"
                annotations.mkdir()
                (annotations / "RealISRMeta.json").write_text(
                    json.dumps(metadata("text"))
                )
                for variant, size in dimensions.items():
                    image = Image.new("RGB", size, "#b9c5cd")
                    image.save(source / variant / "000010.png")
                    (annotations / variant).mkdir()
                    (annotations / variant / "000010.json").write_text(
                        json.dumps(
                            {
                                "shapes": group[variant],
                                "imagePath": "000010.png",
                                "realisr": {
                                    "schema_version": 3,
                                    "attribute": "text",
                                },
                            }
                        )
                    )
            datasets[task] = DatasetConfig(root=source, attribute=task)
        settings = Settings(
            state_dir=root / "state",
            export_dir=root / "exports",
            cache_dir=root / "cache",
            datasets=datasets,
            development=True,
            host="127.0.0.1",
            port=int(os.environ.get("REALISR_E2E_PORT", "8877")),
            public_origin="http://127.0.0.1:"
            + os.environ.get("REALISR_E2E_PORT", "8877"),
        )
        store = SQLiteStore(settings.state_dir, settings.sqlite_journal_mode)
        store.initialize()
        service = AnnotationService(settings, store)
        token = service.initialize_owner()
        for key in datasets:
            service.scan(key)
        credential = Path(
            os.environ.get("REALISR_E2E_TOKEN", "/tmp/realisr-e2e-token")
        )
        credential.write_text(json.dumps({"token": token}), encoding="utf-8")
        credential.chmod(0o600)
        uvicorn.run(
            create_app(settings),
            host=settings.host,
            port=settings.port,
            access_log=False,
        )
        credential.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
