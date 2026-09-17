"""Paired direct-model and API timing on one loaded model and device.

Uses a synthetic temporary PNG group and the actual API/model subprocess.
In-worker predict() is the paired direct-call baseline; HTTP-to-applied time
minus measured queue and predict time is an upper bound on service overhead
(it also includes TestClient polling overhead).
"""

from __future__ import annotations

import argparse
import json
import secrets
import statistics
import tempfile
import time
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from remote_labeling.backend.application.service import AnnotationService
from remote_labeling.backend.infrastructure.inference.presets import (
    local_model,
)
from remote_labeling.backend.config import DatasetConfig, Settings
from remote_labeling.backend.infrastructure.persistence.sqlite import (
    SQLiteStore,
)
from remote_labeling.backend.main import create_app


def percentiles(values: list[float]) -> dict:
    """Summarize at least one thousand measured operations when requested."""
    ordered = sorted(values)
    return {
        "count": len(values),
        "p50_ms": statistics.median(values),
        "p95_ms": ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))],
        "max_ms": max(values),
    }


def main() -> None:
    """Benchmark both selected model adapters using fixed loaded sessions."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--weights", type=Path, required=True)
    parser.add_argument("--device", default="auto")
    parser.add_argument("--iterations", type=int, default=1000)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    results = []
    with tempfile.TemporaryDirectory(
        prefix="realisr-inference-bench-"
    ) as folder:
        root = Path(folder)
        settings = Settings.load(args.config)
        settings.development = True
        settings.state_dir, settings.cache_dir, settings.export_dir = (
            root / "state",
            root / "cache",
            root / "exports",
        )
        settings.public_origin = "http://testserver"
        settings.datasets = {}
        for task in ("text", "face"):
            source = root / task
            image = Image.new("RGB", (800, 600), "white")
            ImageDraw.Draw(image).text(
                (60, 100), "REAL ISR 2026", fill="black", font_size=60
            )
            for variant, factor in (
                ("HR", 1),
                ("LR2", 2),
                ("LR3", 3),
                ("LR4", 4),
            ):
                (source / variant).mkdir(parents=True)
                image.resize((800 // factor, 600 // factor)).save(
                    source / variant / "probe.png"
                )
            settings.datasets[task] = DatasetConfig(
                root=source, attribute=task
            )
        settings.models = {
            key: local_model(config, args.weights)
            for key, config in settings.models.items()
        }
        store = SQLiteStore(settings.state_dir, settings.sqlite_journal_mode)
        store.initialize()
        service = AnnotationService(settings, store)
        token = service.initialize_owner()
        for task in settings.datasets:
            service.scan(task)
        with TestClient(
            create_app(settings), headers={"Origin": settings.public_origin}
        ) as client:
            assert (
                client.post(
                    "/api/v1/session", json={"token": token}
                ).status_code
                == 200
            )
            for model_id, config in settings.models.items():
                current = client.get("/api/v1/model-slot").json()
                loaded = client.put(
                    "/api/v1/model-slot",
                    json={
                        "generation": current["generation"],
                        "model_id": model_id,
                        "device": args.device,
                    },
                )
                assert loaded.status_code == 202, loaded.text
                deadline = time.monotonic() + 180
                while time.monotonic() < deadline:
                    slot = client.get("/api/v1/model-slot").json()
                    if slot["state"] == "READY":
                        break
                    if slot["error"]:
                        raise RuntimeError(slot["error"])
                    time.sleep(0.02)
                assert slot["state"] == "READY", slot
                path = f"/api/v1/datasets/{config.attribute}/samples/probe.png"
                sample = client.get(path).json()
                tab = secrets.token_hex(16)
                lease = client.post(
                    path + "/lease", json={"tab_id": tab}
                ).json()
                common = {
                    "tab_id": tab,
                    "lease_id": lease["id"],
                    "lease_generation": lease["generation"],
                }
                revision = sample["revision"]
                last_renew = time.monotonic()
                measured = {
                    "accept_ms": [],
                    "direct_predict_ms": [],
                    "queue_ms": [],
                    "service_overhead_ms": [],
                }
                for index in range(args.iterations + 10):
                    if time.monotonic() - last_renew > 20:
                        assert (
                            client.put(
                                path + "/lease",
                                json={"tab_id": tab, "lease_id": lease["id"]},
                            ).status_code
                            == 200
                        )
                        last_renew = time.monotonic()
                    cleared = client.put(
                        path + "/draft",
                        json={
                            **common,
                            "base_revision": revision,
                            "operation_id": secrets.token_hex(16),
                            "hr": [],
                            "recoverability": {},
                        },
                    )
                    assert cleared.status_code == 200, cleared.text
                    revision = cleared.json()["revision"]
                    started = time.perf_counter()
                    accepted = client.post(
                        "/api/v1/inference-jobs",
                        json={
                            **common,
                            "base_revision": revision,
                            "operation_id": secrets.token_hex(16),
                            "dataset": config.attribute,
                            "sample": "probe.png",
                            "image_version": sample["image_version"],
                            "model_id": model_id,
                            "model_version": slot["model_version"],
                            "slot_generation": slot["generation"],
                            "region_ids": [],
                        },
                    )
                    assert accepted.status_code == 202, accepted.text
                    accept_ms = (time.perf_counter() - started) * 1000
                    job_id = accepted.json()["id"]
                    deadline = time.monotonic() + config.timeout_seconds + 10
                    while time.monotonic() < deadline:
                        job = client.get(
                            "/api/v1/inference-jobs/" + job_id
                        ).json()
                        if job["state"] not in ("queued", "running"):
                            break
                        time.sleep(0.005)
                    assert job["state"] == "applied", job
                    elapsed = (time.perf_counter() - started) * 1000
                    timings = job["timings"]
                    if index >= 10:
                        measured["accept_ms"].append(accept_ms)
                        measured["direct_predict_ms"].append(
                            timings["inference_ms"]
                        )
                        measured["queue_ms"].append(timings["queue_ms"])
                        measured["service_overhead_ms"].append(
                            max(
                                0,
                                elapsed
                                - timings["queue_ms"]
                                - timings["inference_ms"],
                            )
                        )
                    revision += 1
                client.request(
                    "DELETE",
                    path + "/lease",
                    json={"tab_id": tab, "lease_id": lease["id"]},
                )
                results.append(
                    {
                        "model": model_id,
                        "version": slot["model_version"],
                        "device": slot["device"],
                        "metrics": {
                            k: percentiles(v) for k, v in measured.items()
                        },
                    }
                )
                current = client.get("/api/v1/model-slot").json()
                client.put(
                    "/api/v1/model-slot",
                    json={
                        "generation": current["generation"],
                        "model_id": None,
                    },
                )
                while (
                    client.get("/api/v1/model-slot").json()["state"]
                    != "UNLOADED"
                ):
                    time.sleep(0.02)
        args.output.write_text(
            json.dumps(
                {"kind": "synthetic-paired-api-model", "models": results},
                ensure_ascii=False,
                indent=2,
            )
        )
        print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
