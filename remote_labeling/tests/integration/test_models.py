"""Optional real-model API lifecycle tests and deterministic worker faults."""

import os
import secrets
import time
from pathlib import Path

import psutil
import pytest

from remote_labeling.backend.config import ModelConfig, Settings


def await_slot(client, state: str, timeout: float = 30) -> dict:
    """Poll an externally visible lifecycle transition with a finite deadline."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = client.get("/api/v1/model-slot").json()
        if value["state"] == state:
            return value
        time.sleep(0.05)
    raise AssertionError(value)


def fake_worker(connection, config, device, threads, cache, parent) -> None:
    """Simulate a native model that hangs without importing any GPU library."""
    connection.send({"state": "ready", "providers": []})
    try:
        while True:
            command = connection.recv()
            if command["action"] == "stop":
                return
            time.sleep(30)
    except EOFError:
        pass


def register_fake(settings: Settings, tmp_path: Path) -> None:
    """Register a never-decoded file for supervisor-only fault injection."""
    path = tmp_path / "fake-model.onnx"
    path.write_bytes(b"fake")
    settings.models["fault"] = ModelConfig(
        kind="scrfd",
        attribute="face",
        files={"model": path},
        timeout_seconds=0.2,
    )


def enqueue(client, dataset: str, slot: dict) -> str:
    """Submit one empty-HR job using the public version and lease contract."""
    path = f"/api/v1/datasets/{dataset}/samples/000000.png"
    sample = client.get(path).json()
    tab = secrets.token_hex(16)
    lease = client.post(path + "/lease", json={"tab_id": tab}).json()
    result = client.post(
        "/api/v1/inference-jobs",
        json={
            "dataset": dataset,
            "sample": "000000.png",
            "tab_id": tab,
            "lease_id": lease["id"],
            "lease_generation": lease["generation"],
            "base_revision": sample["revision"],
            "operation_id": secrets.token_hex(16),
            "image_version": sample["image_version"],
            "model_id": slot["model_id"],
            "model_version": slot["model_version"],
            "slot_generation": slot["generation"],
            "region_ids": [],
        },
    )
    assert result.status_code == 202, result.text
    return result.json()["id"]


def test_worker_timeout_keeps_api_usable(
    client, settings, tmp_path, monkeypatch
) -> None:
    """A hung native call fails the job and reaps the worker, not the API."""
    register_fake(settings, tmp_path)
    monkeypatch.setattr(
        "remote_labeling.backend.application.inference.worker_main",
        fake_worker,
    )
    assert (
        client.put(
            "/api/v1/model-slot",
            json={"generation": 0, "model_id": "fault", "device": "cpu"},
        ).status_code
        == 202
    )
    slot = await_slot(client, "READY")
    process = client.app.state.inference.process
    pid = process.pid
    enqueue(client, "face", slot)
    assert client.get("/api/v1/datasets").status_code == 200
    unloaded = await_slot(client, "UNLOADED")
    assert "超时" in unloaded["error"]
    jobs = client.get("/api/v1/inference-jobs").json()
    assert jobs[0]["state"] == "failed"
    assert not psutil.pid_exists(pid)


@pytest.mark.parametrize(
    "device",
    [
        "cpu",
        pytest.param("auto", marks=pytest.mark.gpu),
    ],
)
def test_real_models_load_infer_switch_unload(
    client, settings, device: str
) -> None:
    """Use explicitly provided real weights through the full public job API."""
    weights = os.environ.get("REALISR_TEST_WEIGHTS")
    if not weights:
        pytest.skip("set REALISR_TEST_WEIGHTS to prepared OCR/SCRFD weights")
    root = Path(weights)
    settings.models["ocr"] = ModelConfig(
        kind="ppocr_v6",
        attribute="text",
        files={
            "det": root / "ppocrv6_medium_det_infer.onnx",
            "rec": root / "ppocrv6_medium_rec_infer.onnx",
            "cls": root / "ch_ppocr_mobile_v2.0_cls_infer.onnx",
            "dictionary": root / "ppocrv6_dict.txt",
        },
        parameters={
            "det_db_thresh": 0.2,
            "det_db_box_thresh": 0.45,
            "det_db_unclip_ratio": 1.4,
            "det_db_max_candidates": 3000,
        },
    )
    settings.models["face-model"] = ModelConfig(
        kind="scrfd",
        attribute="face",
        files={"model": root / "scrfd_10g_bnkps.onnx"},
    )
    generation = 0
    for model_id, task in (("ocr", "text"), ("face-model", "face")):
        response = client.put(
            "/api/v1/model-slot",
            json={
                "generation": generation,
                "model_id": model_id,
                "device": device,
            },
        )
        assert response.status_code == 202, response.text
        assert (
            client.put(
                "/api/v1/model-slot",
                json={
                    "generation": generation,
                    "model_id": model_id,
                    "device": device,
                },
            ).status_code
            == 409
        )
        slot = await_slot(client, "READY", timeout=180)
        generation = slot["generation"]
        pid = client.app.state.inference.process.pid
        enqueue(client, task, slot)
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            jobs = client.get("/api/v1/inference-jobs").json()
            if jobs[0]["state"] not in ("queued", "running"):
                break
            time.sleep(0.05)
        assert jobs[0]["state"] == "applied", jobs
        assert (
            client.get(f"/api/v1/datasets/{task}/samples/000000.png").json()[
                "revision"
            ]
            == 1
        )
        assert (
            client.put(
                "/api/v1/model-slot",
                json={"generation": generation, "model_id": None},
            ).status_code
            == 202
        )
        slot = await_slot(client, "UNLOADED")
        generation = slot["generation"]
        assert not psutil.pid_exists(pid)
