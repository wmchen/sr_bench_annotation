"""Spawned model process; contains no annotation database access."""

from __future__ import annotations

import os
import hashlib
from io import BytesIO
import threading
import time
from pathlib import Path
from typing import Any


def worker_main(
    connection: Any,
    config_data: dict,
    device: str,
    threads: int,
    cache: str,
    parent: int,
) -> None:
    """Own a single model until pipe closure or parent death."""

    def watch_parent() -> None:
        """Exit even when a native inference call outlives the API process."""
        while True:
            time.sleep(1)
            if os.getppid() != parent:
                os._exit(1)

    threading.Thread(target=watch_parent, daemon=True).start()
    os.environ["CUDA_VISIBLE_DEVICES"] = "" if device == "cpu" else device
    os.environ["OMP_NUM_THREADS"] = str(threads)
    try:
        import numpy as np
        from PIL import Image
        from ...config import ModelConfig
        from .adapters import OCRPredictor, SCRFDPredictor, verify_warmup
        from .devices import model_version

        config = ModelConfig.model_validate(config_data)
        expected = config_data.get("_expected_version")
        if expected is not None and model_version(config) != expected:
            raise RuntimeError("模型文件已改变，请重新确认加载")
        model = (
            OCRPredictor if config.kind == "ppocr_v6" else SCRFDPredictor
        )(
            config,
            device,
            threads,
            Path(cache),
        )
        if expected is not None and model_version(config) != expected:
            raise RuntimeError("模型文件在加载期间改变，请重新确认加载")
        providers = verify_warmup(model.sessions, device)
        connection.send({"state": "ready", "providers": providers})
        while True:
            command = connection.recv()
            if command["action"] == "stop":
                return
            started = time.perf_counter()
            path = Path(command["image"])
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != command["image_sha256"]:
                raise RuntimeError("推理前发现原图版本变化")
            with Image.open(BytesIO(raw)) as image:
                pixels = np.asarray(image.convert("RGB"))
            decoded = time.perf_counter()
            result = model.predict(pixels, command["regions"])
            finished = time.perf_counter()
            connection.send(
                {
                    "state": "result",
                    "result": result,
                    "timings": {
                        "read_decode_ms": (decoded - started) * 1000,
                        "inference_ms": (finished - decoded) * 1000,
                    },
                }
            )
    except EOFError:
        pass
    except BaseException as exc:
        try:
            connection.send(
                {"state": "error", "error": f"{type(exc).__name__}: {exc}"}
            )
        except (BrokenPipeError, EOFError, OSError):
            pass
    finally:
        connection.close()
