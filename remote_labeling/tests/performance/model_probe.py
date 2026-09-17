"""Explicit real-weight CPU/GPU worker probe; no annotation database writes."""

from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import shutil
import tempfile
import time
from pathlib import Path

from PIL import Image, ImageDraw

from remote_labeling.backend.infrastructure.inference.presets import (
    local_model,
)
from remote_labeling.backend.config import Settings
from remote_labeling.backend.infrastructure.datasets.source import fingerprint
from remote_labeling.backend.infrastructure.inference.devices import (
    catalog,
    devices,
    select_device,
)
from remote_labeling.backend.infrastructure.inference.worker import worker_main


def main() -> None:
    """Load each registered model, infer twice, and verify worker teardown."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--weights", type=Path, required=True)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--text-image", type=Path)
    parser.add_argument("--face-image", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    settings = Settings.load(args.config)
    settings.models = {
        key: local_model(config, args.weights)
        for key, config in settings.models.items()
    }
    report = {
        "device_request": args.device,
        "devices": devices(settings),
        "models": [],
    }
    context = multiprocessing.get_context("spawn")
    for info in catalog(settings):
        config = settings.models[info["id"]]
        result = {
            "id": info["id"],
            "version": info["version"],
            "available": info["available"],
        }
        if not info["available"]:
            result["error"] = info["errors"]
            report["models"].append(result)
            continue
        with tempfile.TemporaryDirectory(
            prefix="realisr-model-probe-"
        ) as folder:
            folder = Path(folder)
            image_path = folder / "probe.png"
            image = Image.new("RGB", (800, 600), "white")
            draw = ImageDraw.Draw(image)
            draw.text((60, 100), "REAL ISR 2026", fill="black", font_size=60)
            image.save(image_path)
            supplied = (
                args.text_image
                if config.attribute == "text"
                else args.face_image
            )
            if supplied:
                shutil.copyfile(supplied, image_path)
            result["image_sha256"] = fingerprint(image_path)
            result["input_kind"] = (
                "provided-image-copy" if supplied else "synthetic"
            )
            device = select_device(
                settings, args.device, config.required_memory_mb
            )
            result["selected_device"] = device
            parent, child = context.Pipe()
            process = context.Process(
                target=worker_main,
                args=(
                    child,
                    config.model_dump(mode="json"),
                    device,
                    settings.cpu_threads,
                    str(folder),
                    os.getpid(),
                ),
            )
            started = time.perf_counter()
            process.start()
            child.close()
            try:
                if not parent.poll(settings.load_timeout_seconds):
                    raise TimeoutError("load timeout")
                ready = parent.recv()
                result["load_warmup_seconds"] = time.perf_counter() - started
                if ready["state"] != "ready":
                    raise RuntimeError(ready.get("error", str(ready)))
                result["providers"] = ready["providers"]
                result["inference_seconds"] = []
                for attempt in range(2):
                    started = time.perf_counter()
                    parent.send(
                        {
                            "action": "predict",
                            "image": str(image_path),
                            "image_sha256": fingerprint(image_path),
                            "regions": None,
                        }
                    )
                    if not parent.poll(config.timeout_seconds):
                        raise TimeoutError("inference timeout")
                    predicted = parent.recv()
                    if predicted["state"] != "result":
                        raise RuntimeError(
                            predicted.get("error", str(predicted))
                        )
                    result["inference_seconds"].append(
                        time.perf_counter() - started
                    )
                    result["regions"] = len(predicted["result"])
                if config.attribute == "text" and predicted["result"]:
                    regions = [
                        dict(r, region_id=str(i))
                        for i, r in enumerate(predicted["result"])
                    ]
                    parent.send(
                        {
                            "action": "predict",
                            "image": str(image_path),
                            "image_sha256": fingerprint(image_path),
                            "regions": regions,
                        }
                    )
                    if not parent.poll(config.timeout_seconds):
                        raise TimeoutError("recognition timeout")
                    recognized = parent.recv()
                    if recognized["state"] != "result" or {
                        r["region_id"] for r in recognized["result"]
                    } != {r["region_id"] for r in regions}:
                        raise RuntimeError(
                            "recognition did not preserve region IDs"
                        )
                    result["recognition_only"] = "passed"
                result["status"] = "passed"
            except Exception as exc:
                result["status"] = "failed"
                result["error"] = str(exc)
            finally:
                started = time.perf_counter()
                if process.is_alive():
                    try:
                        parent.send({"action": "stop"})
                    except (OSError, EOFError):
                        pass
                    process.join(5)
                if process.is_alive():
                    process.terminate()
                    process.join(5)
                if process.is_alive():
                    process.kill()
                    process.join()
                result["worker_exitcode"] = process.exitcode
                result["unload_seconds"] = time.perf_counter() - started
                process.close()
                parent.close()
            report["models"].append(result)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report["models"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
