"""Automatic import repairs preserve precision, metadata and four-view pairing."""

import copy
import json
import secrets

import pytest

from remote_labeling.backend.application.service import digest
from remote_labeling.backend.domain.rules import (
    DomainError,
    bound_points,
    normalize_record,
    scale_points,
)
from remote_labeling.backend.infrastructure.datasets.source import (
    import_group,
    scan_dataset,
)

DIMS = {"HR": [120, 90], "LR2": [60, 45], "LR3": [40, 30], "LR4": [30, 23]}


@pytest.mark.parametrize(
    "attribute,kind,points,expected",
    [
        (
            "text",
            "rectangle",
            [[-1.25, 10.25], [120.75, 90.5]],
            [[0, 10.25], [120, 90]],
        ),
        (
            "text",
            "quadrilateral",
            [[-2, 5.125], [80.875, -0.25], [122, 60.25], [50.375, 91]],
            [[0, 5.125], [80.875, 0], [120, 60.25], [50.375, 90]],
        ),
        (
            "face",
            "rectangle",
            [[-2, -3], [122, -3], [122, 92], [-2, 92]],
            [[0, 0], [120, 0], [120, 90], [0, 90]],
        ),
    ],
)
def test_import_snaps_vertices_without_changing_metadata(
    attribute, kind, points, expected
) -> None:
    """Snap to width/height edges without rounding interior coordinates."""
    source = {
        "region_id": "stable",
        "label": attribute,
        "shape_type": kind,
        "points": points,
        "description": "文字" if attribute == "text" else "",
        "recoverable": 2,
        "flags": None,
        "custom": {"keep": True},
    }
    before = copy.deepcopy(source)
    result = normalize_record(
        source, attribute, DIMS["HR"], legacy=True, clip_to_bounds=True
    )
    assert result["points"] == expected
    assert source == before
    for key in (
        "region_id",
        "label",
        "description",
        "recoverable",
        "flags",
        "custom",
    ):
        assert result[key] == source[key]


@pytest.mark.parametrize(
    "value", [float("nan"), float("inf"), -float("inf"), True, "2", None]
)
def test_invalid_numbers_are_not_repaired(value) -> None:
    """Non-finite and non-numeric coordinates cannot be interpreted as a border."""
    with pytest.raises(DomainError) as error:
        bound_points([[value, 0]], DIMS["HR"], clip=True)
    assert error.value.code == "coordinate"


@pytest.mark.parametrize(
    "kind,points",
    [
        ("rectangle", [[-10, 10], [-2, 50]]),
        ("rectangle", [[130, 10], [140, 80]]),
        ("quadrilateral", [[-5, -3], [-1, -2], [121, 91], [130, 100]]),
    ],
)
def test_degenerate_clipped_regions_are_reported(kind, points) -> None:
    """Do not invent area or drop a region that collapses onto an edge."""
    with pytest.raises(DomainError) as error:
        normalize_record(
            {"shape_type": kind, "points": points},
            "text",
            DIMS["HR"],
            legacy=True,
            clip_to_bounds=True,
        )
    assert error.value.code == "geometry"


def test_live_requests_still_require_in_bounds_coordinates() -> None:
    """Automatic migration does not relax public HTTP write validation."""
    with pytest.raises(DomainError):
        normalize_record(
            {"shape_type": "rectangle", "points": [[-1, 10], [20, 30]]},
            "text",
            DIMS["HR"],
        )


def group_fixture() -> dict:
    """Build one out-of-bounds formal group with LR-specific metadata."""
    hr = {
        "region_id": "stable",
        "shape_type": "rectangle",
        "label": "text",
        "description": "Original",
        "points": [[-1.25, 10.25], [120.75, 90.5]],
        "recoverable": 0,
        "custom_hr": "preserve",
    }
    group = {"HR": [hr]}
    for variant, value in zip(("LR2", "LR3", "LR4"), (1, 1, 2)):
        group[variant] = [
            dict(
                copy.deepcopy(hr),
                points=scale_points(hr["points"], DIMS["HR"], DIMS[variant]),
                recoverable=value,
                difficult=True,
                custom_lr=variant,
            )
        ]
    group["LR2"][0]["points"][0][0] = -3
    return group


def test_clipped_hr_resynchronizes_all_lr_and_reports_actual_output() -> None:
    """LR follows repaired HR, preserving recoverability and extension fields."""
    source = group_fixture()
    before = copy.deepcopy(source)
    repairs = []
    group = import_group(
        source, DIMS, "text", "000000.png", 3, strict=True, repairs=repairs
    )
    assert source == before
    assert group["HR"][0]["points"] == [[0, 10.25], [120, 90]]
    assert {entry["variant"] for entry in repairs} == {"HR", "LR2"}
    for variant in ("LR2", "LR3", "LR4"):
        record = group[variant][0]
        assert record["points"] == scale_points(
            group["HR"][0]["points"], DIMS["HR"], DIMS[variant]
        )
        assert record["recoverable"] == source[variant][0]["recoverable"]
        assert record["custom_lr"] == variant
        assert record["difficult"]
    for item in repairs:
        assert item["after"] == group[item["variant"]][0]["points"]


def write_group(root, group: dict) -> None:
    """Write desktop-format fixtures only under a temporary dataset root."""
    for variant, records in group.items():
        folder = root / "annotations" / variant
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "000000.json").write_text(
            json.dumps(
                {
                    "shapes": records,
                    "imagePath": "000000.png",
                    "realisr": {"schema_version": 3, "attribute": "text"},
                }
            )
        )


def test_scan_repairs_formal_and_draft_without_source_writes(settings) -> None:
    """Recover both saved versions and expose their separate repair provenance."""
    root = settings.datasets["text"].root
    source = group_fixture()
    write_group(root, source)
    draft = copy.deepcopy(source)
    draft["HR"][0]["description"] = "Draft text"
    draft["HR"][0]["points"] = [[-0.5, 15.75], [121, 89.5]]
    path = root / "annotations" / ".realisr_draft.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "attribute": "text",
                "samples": {"000000.png": draft},
            }
        )
    )
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    report = scan_dataset(root, "text")
    assert report["errors"] == []
    sample = next(s for s in report["samples"] if s["id"] == "000000.png")
    assert sample["formal"]["HR"][0]["points"] == [[0, 10.25], [120, 90]]
    assert sample["group"]["HR"][0]["points"] == [[0, 15.75], [120, 89.5]]
    assert sample["group"]["HR"][0]["description"] == "Draft text"
    assert {entry["source"] for entry in report["repairs"]} == {
        "formal",
        "draft",
    }
    assert {
        p: p.read_bytes() for p in root.rglob("*") if p.is_file()
    } == before
    assert scan_dataset(root, "text") == report


def test_scan_persists_repairs_and_protects_online_edits(
    settings, service
) -> None:
    """Import repaired snapshots, while preserving samples already edited online."""
    root = settings.datasets["text"].root
    write_group(root, group_fixture())
    report = service.scan("text")
    assert report["errors"] == [] and len(report["repairs"]) == 2
    token = (settings.state_dir / "owner.token").read_text().strip()
    session = digest(service.exchange(token, "test")[0])
    sample = service.get_sample(session, "text", "000000.png")
    assert sample["formal"]["HR"][0]["points"] == [[0, 10.25], [120, 90]]
    tab = secrets.token_hex(16)
    lease = service.lease(session, "text", "000000.png", tab, "acquire")
    hr = copy.deepcopy(sample["draft"]["HR"])
    hr[0]["description"] = "Online edit"
    service.save(
        session,
        "text",
        "000000.png",
        {
            "tab_id": tab,
            "lease_id": lease["id"],
            "lease_generation": lease["generation"],
            "operation_id": secrets.token_hex(16),
            "base_revision": sample["revision"],
            "hr": hr,
            "recoverability": {},
        },
    )
    service.lease(session, "text", "000000.png", tab, "release", lease["id"])
    assert service.scan("text")["repairs"] == []
    assert (
        service.get_sample(session, "text", "000000.png")["draft"]["HR"][0][
            "description"
        ]
        == "Online edit"
    )
