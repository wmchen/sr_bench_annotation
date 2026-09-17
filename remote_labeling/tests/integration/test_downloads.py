"""Real HTTP transfers against a local server; never fetch public weights."""

import base64
import dataclasses
import os
import hashlib
import secrets
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from remote_labeling.backend.config import ModelConfig, Settings
from remote_labeling.backend.infrastructure.inference.downloads import (
    DownloadCancelled,
    DownloadError,
    ModelDownloader,
)
from remote_labeling.backend.infrastructure.inference.presets import (
    Asset,
    PRESETS,
    resolve_model,
)

DATA = b"controlled-model-fixture" * 1000


def ready_worker(connection, config, device, threads, cache, parent) -> None:
    """A CPU-only lifecycle fixture that does not need ONNX dependencies."""
    connection.send({"state": "ready", "providers": []})
    try:
        while connection.recv()["action"] != "stop":
            pass
    except EOFError:
        pass


@pytest.fixture
def download_server(settings, monkeypatch):
    """Expose complete, interrupted and corrupt response modes deterministically."""
    state = {"requests": 0, "mode": "valid"}
    started, proceed = threading.Event(), threading.Event()
    proceed.set()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            state["requests"] += 1
            state["path"] = self.path
            state["proxy_authorization"] = self.headers.get(
                "Proxy-Authorization"
            )
            mode = state["mode"]
            data = b"corrupt" if mode == "corrupt" else DATA
            self.send_response(200)
            if mode != "unknown_length":
                self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data[:256])
            self.wfile.flush()
            started.set()
            proceed.wait(timeout=10)
            if mode != "truncated":
                self.wfile.write(data[256:])

        def do_CONNECT(self) -> None:
            state["connect"] = self.path
            state["proxy_authorization"] = self.headers.get(
                "Proxy-Authorization"
            )
            self.send_error(502, "Controlled proxy failure")

        def log_message(self, *args) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    asset = Asset(
        "model.onnx",
        hashlib.sha256(DATA).hexdigest(),
        f"http://127.0.0.1:{server.server_port}/model",
    )
    monkeypatch.setitem(
        PRESETS,
        "scrfd",
        dataclasses.replace(PRESETS["scrfd"], assets={"model": asset}),
    )
    settings.download_proxies = {}
    settings.models["scrfd"] = ModelConfig(
        kind="scrfd", attribute="face", preset="scrfd"
    )
    settings.download_retries = 1
    settings.model_hub = "github"
    monkeypatch.setattr(
        "remote_labeling.backend.application.inference.worker_main",
        ready_worker,
    )
    yield state, started, proceed
    proceed.set()
    server.shutdown()
    server.server_close()
    thread.join()


def wait_slot(client, state: str) -> dict:
    """Wait for the public state, with a finite test deadline."""
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        result = client.get("/api/v1/model-slot").json()
        if result["state"] == state:
            return result
        time.sleep(0.02)
    raise AssertionError(result)


def test_first_load_progress_and_cached_reload(
    download_server, client, settings
) -> None:
    """Download in the supervisor; browsing stays responsive and reload is offline."""
    state, started, proceed = download_server
    proceed.clear()
    model = client.get("/api/v1/models").json()[0]
    assert not model["available"] and model["downloadable"]
    loaded = client.put(
        "/api/v1/model-slot",
        json={"generation": 0, "model_id": "scrfd", "device": "cpu"},
    )
    assert loaded.status_code == 202, loaded.text
    assert loaded.json()["state"] == "DOWNLOADING"
    assert started.wait(timeout=5)
    slot = wait_slot(client, "DOWNLOADING")
    assert slot["download"]["filename"] == "model.onnx"
    assert (
        client.get("/api/v1/datasets/text/samples/000000.png").status_code
        == 200
    )
    manual_path = "/api/v1/datasets/text/samples/000000.png"
    tab = secrets.token_hex(16)
    lease = client.post(manual_path + "/lease", json={"tab_id": tab}).json()
    saved = client.put(
        manual_path + "/draft",
        json={
            "tab_id": tab,
            "lease_id": lease["id"],
            "lease_generation": lease["generation"],
            "base_revision": 0,
            "operation_id": secrets.token_hex(16),
            "hr": [],
            "recoverability": {},
        },
    )
    assert saved.status_code == 200 and saved.json()["revision"] == 1
    assert (
        client.put(
            "/api/v1/model-slot",
            json={"generation": 0, "model_id": "scrfd", "device": "cpu"},
        ).status_code
        == 409
    )
    proceed.set()
    slot = wait_slot(client, "READY")
    assert state["requests"] == 1
    destination = resolve_model(
        settings.models["scrfd"], settings.cache_dir
    ).files["model"]
    assert destination.read_bytes() == DATA
    assert not list(destination.parent.glob("*.part"))
    assert client.get("/api/v1/models").json()[0]["available"]
    client.put(
        "/api/v1/model-slot",
        json={"generation": slot["generation"], "model_id": None},
    )
    slot = wait_slot(client, "UNLOADED")
    client.put(
        "/api/v1/model-slot",
        json={
            "generation": slot["generation"],
            "model_id": "scrfd",
            "device": "cpu",
        },
    )
    wait_slot(client, "READY")
    assert state["requests"] == 1


@pytest.mark.parametrize("mode", ["truncated", "corrupt"])
def test_failed_download_does_not_publish_and_can_retry(
    download_server, client, settings, mode: str
) -> None:
    """Neither an HTTP short read nor a wrong checksum becomes a loadable model."""
    state, _, _ = download_server
    state["mode"] = mode
    client.put(
        "/api/v1/model-slot",
        json={"generation": 0, "model_id": "scrfd", "device": "cpu"},
    )
    failed = wait_slot(client, "UNLOADED")
    assert "下载失败" in failed["error"]
    destination = resolve_model(
        settings.models["scrfd"], settings.cache_dir
    ).files["model"]
    assert not destination.exists()
    assert not list(destination.parent.glob("*.part"))
    assert not client.get("/api/v1/models").json()[0]["available"]
    state["mode"] = "valid"
    client.put(
        "/api/v1/model-slot",
        json={
            "generation": failed["generation"],
            "model_id": "scrfd",
            "device": "cpu",
        },
    )
    wait_slot(client, "READY")
    assert destination.read_bytes() == DATA


def test_corrupt_cached_file_is_repaired(
    download_server, client, settings
) -> None:
    """Known builtin checksums protect subsequent startup and cache reuse."""
    destination = resolve_model(
        settings.models["scrfd"], settings.cache_dir
    ).files["model"]
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"damaged cached model")
    response = client.put(
        "/api/v1/model-slot",
        json={"generation": 0, "model_id": "scrfd", "device": "cpu"},
    )
    assert response.status_code == 202
    wait_slot(client, "READY")
    assert destination.read_bytes() == DATA


def test_viewer_cannot_start_download(download_server, client) -> None:
    """Read-only capabilities cannot consume the shared download/model slot."""
    state, _, _ = download_server
    share = client.post(
        "/api/v1/shares", json={"role": "view", "dataset": "text"}
    ).json()
    client.post(
        "/api/v1/session", json={"token": share["url"].split("#token=")[1]}
    )
    assert (
        client.put(
            "/api/v1/model-slot",
            json={"generation": 0, "model_id": "scrfd", "device": "cpu"},
        ).status_code
        == 403
    )
    assert client.get("/api/v1/models").status_code == 403
    assert state["requests"] == 0


def test_unknown_length_and_automatic_retry(download_server, settings) -> None:
    """Streaming without Content-Length and automatic retries both remain safe."""
    state, _, _ = download_server
    state["mode"] = "corrupt"
    settings.download_retries = 2
    updates = []

    def progress(value: dict) -> None:
        updates.append(value)
        if value["stage"] == "retrying":
            state["mode"] = "unknown_length"

    result = ModelDownloader(settings).ensure(
        "scrfd", settings.models["scrfd"], progress, lambda: None
    )
    assert result.files["model"].read_bytes() == DATA
    assert state["requests"] == 2
    assert any(
        item["total_bytes"] is None and item["stage"] == "downloading"
        for item in updates
    )


def test_cancel_removes_partial_file(download_server, settings) -> None:
    """Shutdown/revocation can stop streaming without publishing a partial model."""
    cancel = False

    def progress(value: dict) -> None:
        nonlocal cancel
        if value["bytes_received"]:
            cancel = True

    def check() -> None:
        if cancel:
            raise DownloadCancelled("cancelled")

    with pytest.raises(DownloadCancelled):
        ModelDownloader(settings).ensure(
            "scrfd", settings.models["scrfd"], progress, check
        )
    destination = resolve_model(
        settings.models["scrfd"], settings.cache_dir
    ).files["model"]
    assert not destination.exists()
    assert not list(destination.parent.glob("*.part"))


def test_default_models_need_no_file_configuration(tmp_path: Path) -> None:
    """Omitting models enables both presets; an explicit empty map opts out."""
    paths = {
        "state_dir": tmp_path / "state",
        "cache_dir": tmp_path / "cache",
        "export_dir": tmp_path / "exports",
    }
    settings = Settings(**paths)
    assert set(settings.models) == {"ppocr-v6-medium", "scrfd"}
    assert all(
        model.preset and not model.files for model in settings.models.values()
    )
    assert Settings(**paths, models={}).models == {}


def test_manual_paths_never_trigger_download(
    settings, tmp_path, monkeypatch
) -> None:
    """Explicit local-model configurations retain the original offline behavior."""
    path = tmp_path / "custom.onnx"
    path.write_bytes(b"local model")
    model = ModelConfig(kind="scrfd", attribute="face", files={"model": path})

    def unexpected(*args, **kwargs):
        raise AssertionError("unexpected network access")

    monkeypatch.setattr(urllib.request.OpenerDirector, "open", unexpected)
    result = ModelDownloader(settings).ensure(
        "custom", model, lambda _: None, lambda: None
    )
    assert result.files["model"] == path


def test_packaged_ocr_dictionary_matches_desktop() -> None:
    """The offline dictionary is the exact vocabulary shipped with the desktop."""
    from remote_labeling.backend.infrastructure.inference import downloads

    path = Path(downloads.__file__).with_name("assets") / "ppocrv6_dict.txt"
    assert (
        hashlib.sha256(path.read_bytes()).hexdigest()
        == PRESETS["ppocr-v6-medium"].assets["dictionary"].sha256
    )


def test_revocation_interrupts_active_download(
    download_server, client, settings
) -> None:
    """An already exchanged edit link loses download authority when revoked."""
    state, started, proceed = download_server
    owner_cookie = client.cookies.get("realisr_session")
    share = client.post(
        "/api/v1/shares", json={"role": "edit", "dataset": "text"}
    ).json()
    client.post(
        "/api/v1/session", json={"token": share["url"].split("#token=")[1]}
    )
    proceed.clear()
    client.put(
        "/api/v1/model-slot",
        json={"generation": 0, "model_id": "scrfd", "device": "cpu"},
    )
    assert started.wait(timeout=5)
    client.cookies.clear()
    client.cookies.set("realisr_session", owner_cookie)
    assert client.delete("/api/v1/shares/" + share["id"]).status_code == 200
    time.sleep(1.05)
    proceed.set()
    slot = wait_slot(client, "UNLOADED")
    assert "访问权限已失效" in slot["error"]
    destination = resolve_model(
        settings.models["scrfd"], settings.cache_dir
    ).files["model"]
    assert not destination.exists()


@pytest.mark.parametrize("preset_id", ["ppocr-v6-medium", "scrfd"])
def test_real_builtin_download_and_cpu_load(
    client, settings, preset_id: str
) -> None:
    """Opt in to actual upstream downloads into a fresh temporary model cache."""
    import os
    from remote_labeling.backend.infrastructure.inference.presets import (
        default_models,
    )

    if os.environ.get("REALISR_TEST_DOWNLOADS") != "1":
        pytest.skip(
            "set REALISR_TEST_DOWNLOADS=1 for real upstream download tests"
        )
    settings.models[preset_id] = default_models()[preset_id]
    response = client.put(
        "/api/v1/model-slot",
        json={"generation": 0, "model_id": preset_id, "device": "cpu"},
    )
    assert response.status_code == 202, response.text
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        slot = client.get("/api/v1/model-slot").json()
        if slot["state"] == "READY":
            break
        assert not slot["error"], slot
        time.sleep(0.1)
    assert slot["state"] == "READY", slot
    resolved = resolve_model(settings.models[preset_id], settings.cache_dir)
    for component, asset in PRESETS[preset_id].assets.items():
        assert (
            hashlib.sha256(resolved.files[component].read_bytes()).hexdigest()
            == asset.sha256
        )
    # All components exist; subsequent preparation is entirely offline.
    from unittest.mock import patch

    with patch(
        "urllib.request.OpenerDirector.open",
        side_effect=AssertionError("cache reload accessed network"),
    ):
        client.put(
            "/api/v1/model-slot",
            json={
                "generation": slot["generation"],
                "model_id": preset_id,
                "device": "cpu",
            },
        )
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            slot = client.get("/api/v1/model-slot").json()
            if slot["state"] == "READY":
                break
            assert not slot["error"], slot
            time.sleep(0.05)
        assert slot["state"] == "READY", slot


def test_retry_keeps_completed_components(
    download_server, settings, monkeypatch
) -> None:
    """An OCR-style multi-file retry reuses earlier verified components."""
    state, _, _ = download_server
    original = PRESETS["scrfd"]
    asset = original.assets["model"]
    monkeypatch.setitem(
        PRESETS,
        "scrfd",
        dataclasses.replace(
            original,
            assets={
                "model": asset,
                "aux": dataclasses.replace(asset, filename="aux.onnx"),
            },
        ),
    )

    def fail_second(value: dict) -> None:
        if value["component"] == "model" and value["stage"] == "ready":
            state["mode"] = "corrupt"

    downloader = ModelDownloader(settings)
    with pytest.raises(DownloadError):
        downloader.ensure(
            "scrfd", settings.models["scrfd"], fail_second, lambda: None
        )
    assert state["requests"] == 2
    state["mode"] = "valid"
    result = downloader.ensure(
        "scrfd", settings.models["scrfd"], lambda _: None, lambda: None
    )
    assert state["requests"] == 3
    assert result.files["aux"].read_bytes() == DATA


def test_sources_follow_desktop_model_configuration() -> None:
    """Keep upstream releases and mirror paths aligned with the desktop presets."""
    import yaml
    from remote_labeling.backend.infrastructure.inference.presets import (
        asset_url,
    )

    root = Path(__file__).parents[3] / "anylabeling/configs/auto_labeling"
    if not root.is_dir():
        pytest.skip("desktop reference requires repository checkout")
    for preset_id, filename, keys in [
        (
            "ppocr-v6-medium",
            "ch_chinese_cht_en_japan_ppocr_v6_medium.yaml",
            {
                "det": "det_model_path",
                "rec": "rec_model_path",
                "cls": "cls_model_path",
            },
        ),
        ("scrfd", "scrfd_10g_bnkps.yaml", {"model": "model_path"}),
    ]:
        reference = yaml.safe_load((root / filename).read_text())
        preset = PRESETS[preset_id]
        for component, field in keys.items():
            asset = preset.assets[component]
            assert asset_url(preset, asset, "github") == reference[field]
            expected = (
                "https://www.modelscope.cn/models/CVHub520/"
                + reference["name"].split("-")[0]
                + "/resolve/master/"
                + asset.filename
            )
            assert asset_url(preset, asset, "modelscope") == expected


@pytest.mark.parametrize("source", ["config", "environment"])
def test_authenticated_download_proxy(
    download_server,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    source: str,
) -> None:
    """Transfer and verify a model through the selected authenticated proxy."""
    state, _, _ = download_server
    preset = PRESETS["scrfd"]
    asset = preset.assets["model"]
    proxy = asset.url.replace("http://", "http://user:pa%40ss@")
    proxy = proxy.rsplit("/", 1)[0]
    monkeypatch.setenv("no_proxy", "")
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.delenv("REQUEST_METHOD", raising=False)
    monkeypatch.setenv(
        "http_proxy",
        proxy if source == "environment" else "http://127.0.0.1:1",
    )
    settings = Settings.model_validate(
        {
            **settings.model_dump(),
            "download_proxies": (
                {"http": proxy} if source == "config" else None
            ),
        }
    )
    target = "http://model.invalid/model.onnx"
    monkeypatch.setitem(
        PRESETS,
        "scrfd",
        dataclasses.replace(
            preset, assets={"model": dataclasses.replace(asset, url=target)}
        ),
    )
    environment = dict(os.environ)
    global_opener = urllib.request._opener
    result = ModelDownloader(settings).ensure(
        "scrfd", settings.models["scrfd"], lambda _: None, lambda: None
    )
    assert result.files["model"].read_bytes() == DATA
    assert state["path"] == target
    assert (
        state["proxy_authorization"]
        == "Basic " + base64.b64encode(b"user:pa@ss").decode()
    )
    assert dict(os.environ) == environment
    assert urllib.request._opener is global_opener


def test_https_proxy_uses_authenticated_connect(
    download_server,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTPS weights use CONNECT with proxy auth and sanitized failure text."""
    state, _, _ = download_server
    preset = PRESETS["scrfd"]
    asset = preset.assets["model"]
    proxy = asset.url.rsplit("/", 1)[0].replace(
        "http://", "http://user:proxy-secret@"
    )
    settings = Settings.model_validate(
        {**settings.model_dump(), "download_proxies": {"https": proxy}}
    )
    monkeypatch.setenv("no_proxy", "")
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:1")
    monkeypatch.setitem(
        PRESETS,
        "scrfd",
        dataclasses.replace(
            preset,
            assets={
                "model": dataclasses.replace(
                    asset, url="https://model.invalid/model.onnx"
                )
            },
        ),
    )
    with pytest.raises(DownloadError) as error:
        ModelDownloader(settings).ensure(
            "scrfd", settings.models["scrfd"], lambda _: None, lambda: None
        )
    assert state["connect"] == "model.invalid:443"
    assert (
        state["proxy_authorization"]
        == "Basic " + base64.b64encode(b"user:proxy-secret").decode()
    )
    assert "proxy-secret" not in str(error.value)
    assert "requests" in state and state["requests"] == 0


@pytest.mark.parametrize("mode", ["disabled", "bypass", "unconfigured_scheme"])
def test_download_proxy_direct_connections(
    download_server,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    """Explicit direct mode, omitted schemes and no_proxy bypass bad proxies."""
    state, _, _ = download_server
    bad_proxy = "http://127.0.0.1:1"
    monkeypatch.setenv("http_proxy", bad_proxy)
    monkeypatch.setenv("HTTP_PROXY", bad_proxy)
    monkeypatch.setenv("no_proxy", "127.0.0.1" if mode == "bypass" else "")
    monkeypatch.setenv("NO_PROXY", "")
    proxies = {
        "disabled": {},
        "bypass": {"http": bad_proxy},
        "unconfigured_scheme": {"https": bad_proxy},
    }[mode]
    settings = Settings.model_validate(
        {**settings.model_dump(), "download_proxies": proxies}
    )
    result = ModelDownloader(settings).ensure(
        "scrfd", settings.models["scrfd"], lambda _: None, lambda: None
    )
    assert result.files["model"].read_bytes() == DATA
    assert state["path"] == "/model"
    assert state["proxy_authorization"] is None
