"""Validated server-owned configuration and storage preflight."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, Field, ConfigDict, SecretStr, field_validator

from .domain.rules import DomainError


class DatasetConfig(BaseModel):
    """Bind a public dataset identifier to a private source root."""

    root: Path
    attribute: Literal["text", "face"]


class ModelConfig(BaseModel):
    """Register one trusted model configuration; no browser paths."""

    kind: Literal["ppocr_v6", "scrfd"]
    attribute: Literal["text", "face"]
    files: dict[str, Path] = Field(default_factory=dict)
    preset: Literal["ppocr-v6-medium", "scrfd"] | None = None
    parameters: dict = Field(default_factory=dict)
    required_memory_mb: int = Field(default=4096, ge=0)
    timeout_seconds: float = Field(default=120, gt=0)


def built_in_models() -> dict[str, ModelConfig]:
    """Lazily build default presets without coupling configuration imports."""
    from .infrastructure.inference.presets import default_models

    return default_models()


class Settings(BaseModel):
    """Configuration used by CLI, API and worker supervision."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    host: str = "127.0.0.1"
    port: int = Field(default=8765, ge=1, le=65535)
    shutdown_countdown_seconds: int = Field(default=5, ge=0)
    public_origin: str = "http://127.0.0.1:8765"
    state_dir: Path
    export_dir: Path
    cache_dir: Path
    # Accepted for compatibility; directory location is now operator-controlled.
    local_persistence_confirmed: bool = False
    development: bool = False
    sqlite_journal_mode: Literal["WAL", "DELETE"] = "WAL"
    datasets: dict[str, DatasetConfig] = Field(default_factory=dict)
    models: dict[str, ModelConfig] = Field(default_factory=built_in_models)
    model_hub: Literal["github", "modelscope"] = Field(
        default_factory=lambda: os.getenv("XANYLABELING_MODEL_HUB")
        or "github",
        validate_default=True,
    )
    download_timeout_seconds: float = Field(default=30, ge=1, le=120)
    download_retries: int = Field(default=3, ge=1, le=5)
    download_proxies: dict[Literal["http", "https"], SecretStr] | None = None
    allowed_gpus: list[str] = Field(default_factory=list)
    cpu_threads: int = Field(default=4, ge=1)
    gpu_margin_mb: int = Field(default=1024, ge=0)
    queue_limit: int = Field(default=10, ge=1, le=100)
    lease_seconds: int = Field(default=90, ge=30)
    heartbeat_seconds: int = Field(default=20, ge=5)
    session_seconds: int = Field(default=43200, ge=90)
    load_timeout_seconds: int = Field(default=180, ge=1)
    frontend_dir: Path = Path(__file__).parents[1] / "frontend" / "dist"

    @field_validator("download_proxies")
    @classmethod
    def validate_download_proxies(
        cls, value: dict[str, SecretStr] | None
    ) -> dict[str, SecretStr] | None:
        """Reject invalid proxy URLs without including credentials in errors."""
        for proxy in (value or {}).values():
            raw = proxy.get_secret_value()
            try:
                parsed = urlsplit(raw)
                valid = (
                    parsed.scheme in {"http", "https"}
                    and parsed.hostname
                    and (parsed.port is None or parsed.port > 0)
                    and parsed.path in {"", "/"}
                    and not parsed.query
                    and not parsed.fragment
                    and not any(char.isspace() for char in raw)
                )
            except ValueError:
                valid = False
            if not valid:
                raise ValueError(
                    "下载代理必须是有效的 HTTP(S) URL，"
                    "例如 http://用户名:密码@主机:端口"
                )
        return value

    @classmethod
    def load(cls, path: Path) -> Settings:
        """Resolve relative configured paths against the configuration file."""
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
        settings = cls.model_validate(value)
        base = path.resolve().parent
        for field in ("state_dir", "export_dir", "cache_dir", "frontend_dir"):
            target = getattr(settings, field)
            setattr(settings, field, (base / target).resolve())
        for dataset in settings.datasets.values():
            dataset.root = (base / dataset.root).resolve()
        for model in settings.models.values():
            model.files = {
                k: (base / v).resolve() for k, v in model.files.items()
            }
        return settings

    def preflight(self) -> None:
        """Validate application invariants without enforcing disk placement."""
        if self.heartbeat_seconds * 2 >= self.lease_seconds:
            raise DomainError("lease_config", "续约间隔必须小于租约时长的一半")
        for target in (self.state_dir, self.export_dir, self.cache_dir):
            for dataset in self.datasets.values():
                if target.resolve().is_relative_to(dataset.root.resolve()):
                    raise DomainError("output_path", "运行数据不能写入导入源")
        for key in [*self.datasets, *self.models]:
            if not key or any(
                c
                not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
                for c in key
            ):
                raise DomainError(
                    "identifier", "配置 ID 只能使用字母、数字、下划线、连字符"
                )
