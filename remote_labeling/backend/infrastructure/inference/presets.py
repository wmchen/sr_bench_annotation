"""Pinned downloads from the same model releases used by the desktop app."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ...config import ModelConfig

RELEASE = "https://github.com/CVHub520/X-AnyLabeling/releases/download/v3.0.0/"


@dataclass(frozen=True)
class Asset:
    """One immutable model component, published only after checksum success."""

    filename: str
    sha256: str
    url: str | None = None
    resource: str | None = None


@dataclass(frozen=True)
class Preset:
    """A verified OCR or face model with desktop-compatible parameters."""

    kind: str
    attribute: str
    mirror_repository: str
    assets: dict[str, Asset]
    parameters: dict
    memory_mb: int


PRESETS = {
    "ppocr-v6-medium": Preset(
        kind="ppocr_v6",
        attribute="text",
        mirror_repository="ch_chinese_cht_en_japan_ppocr_v6_medium",
        assets={
            "det": Asset(
                "ppocrv6_medium_det_infer.onnx",
                "eb13b44b25bb36f89528b68720af8a61d9cf381176107f465db1757b65d086e1",
                RELEASE + "ppocrv6_medium_det_infer.onnx",
            ),
            "rec": Asset(
                "ppocrv6_medium_rec_infer.onnx",
                "9c09abf0957f7968c7586464b7397b84ad2387a0497a351af40e9acc71b673ba",
                RELEASE + "ppocrv6_medium_rec_infer.onnx",
            ),
            "cls": Asset(
                "ch_ppocr_mobile_v2.0_cls_infer.onnx",
                "cf443393df5e23f068c113f2ee5ee286918da7c386535a4ccd8c9c815d808445",
                RELEASE + "ch_ppocr_mobile_v2.0_cls_infer.onnx",
            ),
            # Ship the matching desktop dictionary with the standalone package.
            "dictionary": Asset(
                "ppocrv6_dict.txt",
                "b5f2bfe2bdd9448429e3e82b51c789775d9b42f2403d082b00662eb77e401c5d",
                resource="ppocrv6_dict.txt",
            ),
        },
        parameters={
            "det_db_thresh": 0.2,
            "det_db_box_thresh": 0.45,
            "det_db_unclip_ratio": 1.4,
            "det_db_max_candidates": 3000,
            "drop_score": 0.5,
            "use_angle_cls": True,
        },
        memory_mb=4096,
    ),
    "scrfd": Preset(
        kind="scrfd",
        attribute="face",
        mirror_repository="scrfd_10g_bnkps",
        assets={
            "model": Asset(
                "scrfd_10g_bnkps.onnx",
                "6289de5b582242f5f187afe9d868ef25f627ae5acec740506a913d62a504079c",
                RELEASE + "scrfd_10g_bnkps.onnx",
            ),
        },
        parameters={
            "input_width": 640,
            "input_height": 640,
            "conf_threshold": 0.5,
            "iou_threshold": 0.4,
        },
        memory_mb=2048,
    ),
}


def default_models() -> dict[str, ModelConfig]:
    """Expose both built-in models without requiring any YAML file paths."""
    return {
        key: ModelConfig(
            kind=preset.kind,
            attribute=preset.attribute,
            preset=key,
            required_memory_mb=preset.memory_mb,
        )
        for key, preset in PRESETS.items()
    }


def managed_assets(config: ModelConfig) -> dict[str, Asset]:
    """Only unspecified preset components belong to the download cache."""
    if config.preset is None:
        return {}
    return {
        key: value
        for key, value in PRESETS[config.preset].assets.items()
        if key not in config.files
    }


def resolve_model(config: ModelConfig, cache_dir: Path) -> ModelConfig:
    """Resolve automatic components; explicit local overrides remain untouched."""
    if config.preset is None:
        return config
    preset = PRESETS[config.preset]
    files = {
        key: cache_dir
        / "models"
        / config.preset
        / (asset.sha256[:12] + "-" + asset.filename)
        for key, asset in managed_assets(config).items()
    }
    return config.model_copy(
        update={
            "files": {**files, **config.files},
            "parameters": {**preset.parameters, **config.parameters},
        }
    )


def asset_url(preset: Preset, asset: Asset, hub: str) -> str:
    """Use the same optional ModelScope mirror convention as the desktop app."""
    if hub == "modelscope":
        return (
            "https://www.modelscope.cn/models/CVHub520/"
            + preset.mirror_repository
            + "/resolve/master/"
            + asset.filename
        )
    if asset.url is None:
        raise ValueError("This component is a bundled resource")
    return asset.url


def local_model(config: ModelConfig, directory: Path) -> ModelConfig:
    """Resolve explicit benchmark/offline weights without triggering downloads."""
    files = {key: directory / path.name for key, path in config.files.items()}
    for key, asset in managed_assets(config).items():
        files[key] = directory / asset.filename
    return resolve_model(config.model_copy(update={"files": files}), directory)
