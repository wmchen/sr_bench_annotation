"""Domain invariants including parity with the desktop scale function."""

import ast
import copy
import json
from pathlib import Path

import pytest

from remote_labeling.backend.domain.rules import (
    DomainError,
    completion,
    edit_group,
    scale_points,
)
from remote_labeling.backend.infrastructure.datasets.source import (
    import_group,
    scan_dataset,
)

DIMS = {"HR": [13, 11], "LR2": [6, 5], "LR3": [4, 4], "LR4": [3, 3]}


def test_desktop_rounding_parity() -> None:
    """Load only the desktop pure function without importing the Qt package."""
    path = (
        Path(__file__).parents[3]
        / "anylabeling/views/labeling/realisr_dataset.py"
    )
    if not path.is_file():
        pytest.skip(
            "desktop source reference is available only in a repository checkout"
        )
    tree = ast.parse(path.read_text())
    function = next(
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "scale_points"
    )
    namespace = {"RealISRDatasetError": ValueError}
    exec(
        compile(
            ast.Module(body=[function], type_ignores=[]), str(path), "exec"
        ),
        namespace,
    )
    for source, target in [([12, 12], [6, 6]), ([13, 11], [6, 5])]:
        points = [[0, 0], [1, 3], [5, 7], [-1, -2], [100, 100]]
        assert scale_points(points, source, target) == namespace[
            "scale_points"
        ](points, source, target)
    assert scale_points([[1, 3], [5, 7]], [12, 12], [6, 6]) == [[0, 2], [2, 4]]


def test_defaults_inheritance_and_deletion() -> None:
    """Geometry edits retain LR values and unknown extension metadata."""
    region = {
        "region_id": "r",
        "points": [[1, 1], [12, 10]],
        "shape_type": "rectangle",
    }
    group = edit_group([region], {}, DIMS, "text", {})
    assert group["HR"][0]["recoverable"] == 0
    assert group["LR2"][0]["recoverable"] is None
    group["LR2"][0].update(recoverable=2, legacy_field="kept", difficult=True)
    region["points"] = [[2, 2], [10, 9]]
    changed = edit_group([region], group, DIMS, "text", {})
    assert changed["LR2"][0]["recoverable"] == 2
    assert changed["LR2"][0]["legacy_field"] == "kept"
    assert changed["LR2"][0]["difficult"] is True
    assert edit_group([], changed, DIMS, "text", {}) == {v: [] for v in DIMS}
    face = edit_group([region], {}, DIMS, "face", {})
    assert face["HR"][0]["recoverable"] is None


@pytest.mark.parametrize(
    "change",
    [
        {"points": [[float("nan"), 0], [5, 5]]},
        {"points": [[0, 0], [100, 100]]},
        {"shape_type": "polygon"},
        {"recoverable": True},
        {"region_id": ""},
    ],
)
def test_invalid_live_shapes(change: dict) -> None:
    """Reject malformed browser writes rather than silently changing data."""
    region = {
        "region_id": "r",
        "points": [[1, 1], [12, 10]],
        "shape_type": "rectangle",
    }
    with pytest.raises(DomainError):
        edit_group([region | change], {}, DIMS, "text", {})


def test_legacy_text_and_id_matching() -> None:
    """Migrate schema-one transcription and ID-less ordered LR counterparts."""
    region = {
        "label": "Old OCR",
        "points": [[1, 1], [12, 10]],
        "shape_type": "rectangle",
    }
    group = import_group(
        {
            v: [
                dict(
                    copy.deepcopy(region),
                    points=scale_points(region["points"], DIMS["HR"], size),
                )
            ]
            for v, size in DIMS.items()
        },
        DIMS,
        "text",
        "a.png",
        1,
        strict=True,
    )
    assert group["HR"][0]["description"] == "Old OCR"
    assert group["HR"][0]["label"] == "text"
    assert len({group[v][0]["region_id"] for v in DIMS}) == 1


def test_scan_read_only_and_partial_group(settings) -> None:
    """Import never creates metadata or deletes pre-desktop backups."""
    root = settings.datasets["text"].root
    before = {
        str(p.relative_to(root)): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file()
    }
    assert not scan_dataset(root, "text")["errors"]
    assert before == {
        str(p.relative_to(root)): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file()
    }
    folder = root / "annotations" / "HR"
    folder.mkdir(parents=True)
    (folder / "000000.json").write_text(json.dumps({"shapes": []}))
    report = scan_dataset(root, "text")
    assert report["errors"][0]["sample"] == "000000.png"


def test_monotonicity_is_advisory() -> None:
    """Completeness does not reject a deliberately confirmed warning."""
    region = {
        "region_id": "r",
        "points": [[1, 1], [12, 10]],
        "shape_type": "rectangle",
    }
    group = edit_group(
        [region],
        {},
        DIMS,
        "text",
        {v: {"r": x} for v, x in zip(DIMS, [0, 2, 1, 0])},
    )
    check = completion(group)
    assert check["missing"] == []
    assert check["violations"] == ["r"]


def test_ocr_output_shape_matches_desktop_postprocessing() -> None:
    """Preserve the desktop distance test and opposite-corner normalization."""
    from remote_labeling.backend.domain.rules import ocr_geometry

    path = (
        Path(__file__).parents[3]
        / "anylabeling/views/labeling/utils/general.py"
    )
    if not path.is_file():
        pytest.skip(
            "desktop source reference is available only in a repository checkout"
        )
    tree = ast.parse(path.read_text())
    functions = [
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef)
        and n.name in ("is_possible_rectangle", "square_dist")
    ]
    namespace = {}
    exec(
        compile(
            ast.Module(body=functions, type_ignores=[]), str(path), "exec"
        ),
        namespace,
    )
    for box in (
        [[1, 2], [9, 2], [9, 8], [1, 8]],
        [[1, 2], [9, 3], [8, 8], [2, 9]],
        [[3, 1], [7, 3], [6, 5], [2, 3]],
    ):
        kind, points = ocr_geometry(box)
        expected = namespace["is_possible_rectangle"](box)
        assert (kind == "rectangle") == expected
        if expected:
            assert points == [
                box[0],
                [box[2][0], box[0][1]],
                box[2],
                [box[0][0], box[2][1]],
            ]


def test_shared_browser_coordinate_fixture() -> None:
    """Use the same golden boundary cases as the TypeScript preview."""
    path = Path(__file__).parents[1] / "fixtures/coordinates.json"
    for case in json.loads(path.read_text()):
        assert (
            scale_points(case["points"], case["source"], case["target"])
            == case["expected"]
        ), case["name"]
