"""Streaming, retryable model downloads with atomic publication and SHA-256."""

from __future__ import annotations

import hashlib
import http.client
import os
import tempfile
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Callable

from ...config import ModelConfig, Settings
from ..datasets.source import contained, fingerprint
from .presets import PRESETS, Asset, asset_url, managed_assets, resolve_model


class DownloadError(RuntimeError):
    """A failed transfer or integrity check; the model is not loadable."""


class DownloadCancelled(RuntimeError):
    """Stop a download when the service stops or its initiating access expires."""


class ModelDownloader:
    """Download only server-owned preset assets, never browser-provided URLs."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        proxies = (
            None
            if settings.download_proxies is None
            else {
                scheme: proxy.get_secret_value()
                for scheme, proxy in settings.download_proxies.items()
            }
        )
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler(proxies)
        )

    def ensure(
        self,
        model_id: str,
        config: ModelConfig,
        progress: Callable[[dict], None],
        check: Callable[[], None],
    ) -> ModelConfig:
        """Fetch missing/corrupt managed files and return a resolved model."""
        resolved = resolve_model(config, self.settings.cache_dir)
        assets = managed_assets(config)
        for index, (component, asset) in enumerate(assets.items()):
            check()
            path = contained(
                self.settings.cache_dir, resolved.files[component]
            )
            info = {
                "model_id": model_id,
                "component": component,
                "filename": asset.filename,
                "file_index": index + 1,
                "files_total": len(assets),
                "bytes_received": 0,
                "total_bytes": None,
                "attempt": 1,
                "stage": "verifying",
            }
            progress(info)
            if path.is_file() and fingerprint(path) == asset.sha256:
                progress({**info, "stage": "cached"})
                continue
            self._fetch(path, asset, config.preset, info, progress, check)
        check()
        return resolved

    def _fetch(
        self,
        destination: Path,
        asset: Asset,
        preset_id: str,
        info: dict,
        progress: Callable[[dict], None],
        check: Callable[[], None],
    ) -> None:
        """Retry failed components while retaining other verified components."""
        destination.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(1, self.settings.download_retries + 1):
            check()
            temporary = None
            info = {**info, "attempt": attempt, "bytes_received": 0}
            try:
                if asset.resource:
                    resource = (
                        Path(__file__).with_name("assets") / asset.resource
                    )
                    response = resource.open("rb")
                    total = resource.stat().st_size
                else:
                    request = urllib.request.Request(
                        asset_url(
                            PRESETS[preset_id], asset, self.settings.model_hub
                        ),
                        headers={
                            "User-Agent": "RealISR-Remote/0.1",
                            "Accept-Encoding": "identity",
                        },
                    )
                    response = self.opener.open(
                        request,
                        timeout=self.settings.download_timeout_seconds,
                    )
                    header = response.headers.get("Content-Length")
                    total = (
                        int(header) if header and header.isdigit() else None
                    )
                digest = hashlib.sha256()
                received = 0
                last_notice = 0.0
                with (
                    response,
                    tempfile.NamedTemporaryFile(
                        dir=destination.parent,
                        prefix="." + asset.filename + "-",
                        suffix=".part",
                        delete=False,
                    ) as stream,
                ):
                    temporary = Path(stream.name)
                    while True:
                        check()
                        read = getattr(response, "read1", response.read)
                        chunk = read(256 * 1024)
                        if not chunk:
                            break
                        stream.write(chunk)
                        digest.update(chunk)
                        received += len(chunk)
                        if time.monotonic() - last_notice >= 0.1:
                            progress(
                                {
                                    **info,
                                    "stage": "downloading",
                                    "bytes_received": received,
                                    "total_bytes": total,
                                }
                            )
                            last_notice = time.monotonic()
                    stream.flush()
                    os.fsync(stream.fileno())
                check()
                progress(
                    {
                        **info,
                        "stage": "verifying",
                        "bytes_received": received,
                        "total_bytes": total,
                    }
                )
                if total is not None and received != total:
                    raise DownloadError("下载文件不完整")
                if digest.hexdigest() != asset.sha256:
                    raise DownloadError("文件 SHA-256 校验失败")
                # Resolve again before publication; never follow a cache escape.
                contained(self.settings.cache_dir, destination)
                os.replace(temporary, destination)
                progress(
                    {
                        **info,
                        "stage": "ready",
                        "bytes_received": received,
                        "total_bytes": total,
                    }
                )
                return
            except (OSError, http.client.HTTPException, DownloadError) as exc:
                if attempt == self.settings.download_retries:
                    reason = (
                        str(exc)
                        if isinstance(exc, DownloadError)
                        else (
                            f"HTTP {exc.code}"
                            if isinstance(exc, urllib.error.HTTPError)
                            else "网络连接或缓存写入失败"
                        )
                    )
                    raise DownloadError(
                        f"{asset.filename} 下载失败（{attempt} 次尝试）："
                        f"{reason}。请检查服务器网络后重试。"
                    ) from exc
                progress({**info, "stage": "retrying"})
                deadline = time.monotonic() + attempt
                while time.monotonic() < deadline:
                    check()
                    time.sleep(0.1)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
