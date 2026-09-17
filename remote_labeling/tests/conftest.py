"""Temporary datasets and authenticated HTTP clients."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from remote_labeling.backend.application.service import AnnotationService
from remote_labeling.backend.config import DatasetConfig, Settings
from remote_labeling.backend.infrastructure.persistence.sqlite import (
    SQLiteStore,
)
from remote_labeling.backend.main import create_app


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Build real PNG fixtures without touching registered source data."""
    datasets = {}
    for task in ("text", "face"):
        root = tmp_path / task
        for variant, size in {
            "HR": (120, 90),
            "LR2": (60, 45),
            "LR3": (40, 30),
            "LR4": (30, 23),
        }.items():
            (root / variant).mkdir(parents=True)
            for number in range(2):
                Image.new("RGB", size, "#dddddd").save(
                    root / variant / f"{number:06d}.png"
                )
        datasets[task] = DatasetConfig(root=root, attribute=task)
    return Settings(
        state_dir=tmp_path / "state",
        cache_dir=tmp_path / "cache",
        export_dir=tmp_path / "exports",
        development=True,
        public_origin="http://testserver",
        datasets=datasets,
        models={},
    )


@pytest.fixture
def service(settings: Settings) -> AnnotationService:
    """Initialize the real SQLite schema and read-only importer."""
    store = SQLiteStore(settings.state_dir, settings.sqlite_journal_mode)
    store.initialize()
    service = AnnotationService(settings, store)
    service.initialize_owner()
    for dataset in settings.datasets:
        assert service.scan(dataset)["errors"] == []
    return service


@pytest.fixture
def client(settings: Settings, service: AnnotationService):
    """Use the actual lifespan, permission boundary and owner session."""
    with TestClient(
        create_app(settings),
        headers={"Origin": settings.public_origin},
        client=("127.0.0.1", 50000),
    ) as client:
        token = (settings.state_dir / "owner.token").read_text().strip()
        response = client.post(
            "/api/v1/session", json={"token": token, "nickname": "测试所有者"}
        )
        assert response.status_code == 200, response.text
        yield client


@pytest.fixture
def region() -> dict:
    """A finite, in-bounds HR rectangle."""
    return {
        "region_id": "stable-region-0001",
        "shape_type": "rectangle",
        "points": [[10, 10], [80, 70]],
        "label": "text",
        "description": "Hello",
        "recoverable": 0,
    }
