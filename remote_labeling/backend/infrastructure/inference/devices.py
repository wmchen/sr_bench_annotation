"""Visible GPU discovery, stable UUID selection and model registry."""

from __future__ import annotations

import hashlib
import json
import os
import time

from ...config import ModelConfig, Settings
from ...domain.rules import DomainError
from ..datasets.source import fingerprint
from .presets import PRESETS, managed_assets, resolve_model


def devices(settings: Settings) -> dict:
    """Collect NVML state without claiming that memory is reserved."""
    result = {
        "gpus": [],
        "cpu": True,
        "sampled_at": time.time(),
        "error": None,
    }
    try:
        import pynvml as nvml

        nvml.nvmlInit()
        try:
            visible = os.environ.get("CUDA_VISIBLE_DEVICES")
            allowed_visible = None if visible is None else visible.split(",")
            for index in range(nvml.nvmlDeviceGetCount()):
                handle = nvml.nvmlDeviceGetHandleByIndex(index)
                uuid = nvml.nvmlDeviceGetUUID(handle)
                if isinstance(uuid, bytes):
                    uuid = uuid.decode()
                if allowed_visible is not None and not any(
                    value == str(index)
                    or (value.startswith("GPU-") and uuid.startswith(value))
                    for value in allowed_visible
                ):
                    continue
                if (
                    settings.allowed_gpus
                    and uuid not in settings.allowed_gpus
                    and str(index) not in settings.allowed_gpus
                ):
                    continue
                memory = nvml.nvmlDeviceGetMemoryInfo(handle)
                utilization = nvml.nvmlDeviceGetUtilizationRates(handle)
                name = nvml.nvmlDeviceGetName(handle)
                result["gpus"].append(
                    {
                        "index": index,
                        "uuid": uuid,
                        "name": (
                            name.decode() if isinstance(name, bytes) else name
                        ),
                        "free_mb": memory.free // 1048576,
                        "total_mb": memory.total // 1048576,
                        "utilization": utilization.gpu,
                    }
                )
        finally:
            nvml.nvmlShutdown()
    except Exception as exc:
        result["error"] = str(exc)
    return result


def select_device(settings: Settings, requested: str, required_mb: int) -> str:
    """Choose a permitted UUID at confirmation time; never fall back to CPU."""
    if requested == "cpu":
        return "cpu"
    snapshot = devices(settings)
    candidates = [
        gpu
        for gpu in snapshot["gpus"]
        if gpu["free_mb"] >= required_mb + settings.gpu_margin_mb
        and (
            requested == "auto"
            or requested in (gpu["uuid"], str(gpu["index"]))
        )
    ]
    if not candidates:
        raise DomainError(
            "device_unavailable",
            "没有符合条件的 GPU；请刷新、指定设备或选择 CPU",
            409,
            snapshot,
        )
    return sorted(
        candidates, key=lambda gpu: (-gpu["free_mb"], gpu["utilization"])
    )[0]["uuid"]


def model_version(config: ModelConfig, hashes: dict | None = None) -> str:
    """Bind the recorded version to every declared weight and parameter."""
    if hashes is None:
        hashes = {key: fingerprint(path) for key, path in config.files.items()}
    payload = json.dumps(
        [config.kind, hashes, config.parameters],
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def catalog(settings: Settings) -> list[dict]:
    """Report cached availability separately from automatic-download capability."""
    output = []
    for model_id, original in settings.models.items():
        config = resolve_model(original, settings.cache_dir)
        assets = managed_assets(original)
        required = (
            ("det", "rec", "cls", "dictionary")
            if config.kind == "ppocr_v6"
            else ("model",)
        )
        errors, bad_keys, hashes = [], set(), {}
        compatible = (config.kind == "ppocr_v6") == (
            config.attribute == "text"
        )
        if original.preset:
            preset = PRESETS[original.preset]
            compatible = compatible and (config.kind, config.attribute) == (
                preset.kind,
                preset.attribute,
            )
        if not compatible:
            errors.append("模型类型和任务属性不一致")
        for key in set(required) | set(config.files):
            path = config.files.get(key)
            if path is None or not path.is_file():
                errors.append(f"缺少 {key} 文件")
                bad_keys.add(key)
                continue
            try:
                hashes[key] = fingerprint(path)
                if key in assets and hashes[key] != assets[key].sha256:
                    errors.append(f"{key} 缓存校验失败，需要重新下载")
                    bad_keys.add(key)
            except OSError as exc:
                errors.append(
                    f"{key}: 无法读取模型文件 ({type(exc).__name__})"
                )
                bad_keys.add(key)
        output.append(
            {
                "id": model_id,
                "kind": config.kind,
                "attribute": config.attribute,
                "version": model_version(config, hashes),
                "available": not errors,
                "downloadable": bool(
                    compatible
                    and original.preset
                    and bad_keys <= assets.keys()
                ),
                "errors": errors,
                "required_memory_mb": config.required_memory_mb,
            }
        )
    return output
