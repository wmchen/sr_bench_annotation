"""Pure rules adapted from realisr_dataset.py, schema 3.

No desktop import is permitted here. See MIGRATION.md for provenance.
"""

from __future__ import annotations

import copy
import json
import math
from typing import Any

VARIANTS = ("HR", "LR2", "LR3", "LR4")
STANDARD_FIELDS = {
    "label",
    "score",
    "points",
    "group_id",
    "description",
    "difficult",
    "shape_type",
    "flags",
    "attributes",
    "kie_linking",
    "direction",
    "locked",
    "region_id",
    "recoverable",
}
Group = dict[str, list[dict[str, Any]]]


class DomainError(ValueError):
    """Actionable error shared by domain and application boundaries."""

    def __init__(
        self,
        code: str,
        message: str,
        status: int = 422,
        details: Any = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.details = details


def scale_points(
    points: list,
    source_size: tuple | list,
    target_size: tuple | list,
) -> list[list[int]]:
    """Map coordinates using desktop half-even rounding and closed bounds."""
    if min(source_size) <= 0 or min(target_size) <= 0:
        raise DomainError("dimensions", "图像尺寸必须大于零")
    return [
        [
            max(
                0,
                min(
                    target_size[i],
                    int(
                        round(
                            float(point[i]) * target_size[i] / source_size[i]
                        )
                    ),
                ),
            )
            for i in range(2)
        ]
        for point in points
    ]


def bound_points(
    points: list, size: list | tuple, *, clip: bool = False
) -> list[list[int | float]]:
    """Validate finite coordinates and optionally snap them to closed image bounds.

    Args:
        points: Original image coordinates; no rounding is performed.
        size: Image width and height, whose edges are valid coordinates.
        clip: Snap out-of-range values when importing existing annotations.

    Returns:
        New point lists, preserving in-bounds precision and the input data.
    """
    if len(size) != 2 or any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
        for value in size
    ):
        raise DomainError("dimensions", "图像尺寸必须为有限正数")
    bounded = []
    for point_index, point in enumerate(points):
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise DomainError("geometry", "每个顶点需要 x、y 坐标")
        target = []
        for axis, value in enumerate(point):
            detail = {
                "field": f"points.{point_index}.{axis}",
                "value": str(value),
                "bound": size[axis],
            }
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                raise DomainError(
                    "coordinate", "坐标必须是有限数值", details=detail
                )
            if not clip and not 0 <= value <= size[axis]:
                raise DomainError(
                    "coordinate", "坐标必须位于原图范围内", details=detail
                )
            target.append(max(0, min(size[axis], value)) if clip else value)
        bounded.append(target)
    return bounded


def normalize_record(
    source: dict,
    attribute: str,
    size: list | tuple,
    *,
    legacy: bool = False,
    migrate_text: bool = False,
    clip_to_bounds: bool = False,
) -> dict:
    """Validate editable HR geometry and canonicalize legacy text fields."""
    if not isinstance(source, dict):
        raise DomainError("record", "区域必须是对象")
    record = copy.deepcopy(source)
    try:
        json.dumps(record, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise DomainError("record", "区域扩展字段必须为有限 JSON 值") from exc
    for name in ("flags", "attributes"):
        if (
            name in record
            and record[name] is not None
            and not isinstance(record[name], dict)
        ):
            raise DomainError("field", f"{name} 必须为对象")
    for name in ("difficult", "locked"):
        if (
            name in record
            and record[name] is not None
            and not isinstance(record[name], bool)
        ):
            raise DomainError("field", f"{name} 必须为布尔值")
    if attribute == "text":
        transcription = record.pop("transcription", None)
        if transcription is not None and not record.get("description"):
            record["description"] = transcription
        elif migrate_text and not record.get("description"):
            label = record.get("label")
            if label not in (None, "", "text"):
                record["description"] = label
        if not legacy and record.get("label", "text") != "text":
            raise DomainError("label", "文本类别必须是 text")
    elif attribute == "face":
        if record.get("label") not in (None, "", "face"):
            raise DomainError("label", "人脸类别必须是 face")
        if record.get("description") not in (None, ""):
            raise DomainError("description", "人脸文字必须为空")
        record.pop("transcription", None)
        record["description"] = ""
    else:
        raise DomainError("attribute", "不支持的数据集属性")
    record["label"] = attribute
    record.setdefault("description", "")
    if not isinstance(record["description"], str):
        raise DomainError("description", "文字必须是字符串")
    points = record.get("points")
    if not isinstance(points, list) or len(points) not in (2, 4):
        raise DomainError(
            "geometry", "矩形需要 2 或 4 个点，四边形需要 4 个点"
        )
    bounded = bound_points(points, size, clip=clip_to_bounds)
    clipped = bounded != points
    points = bounded
    kind = record.get(
        "shape_type", "rectangle" if attribute == "face" else "quadrilateral"
    )
    if kind not in ("rectangle", "quadrilateral") or (
        attribute == "face" and kind != "rectangle"
    ):
        raise DomainError("shape_type", "网页不支持该形状，不能静默转换")
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    if max(xs) <= min(xs) or max(ys) <= min(ys):
        raise DomainError(
            "geometry",
            (
                "贴边后区域退化为点或直线，需人工处理"
                if clipped
                else "区域不能退化为点或直线"
            ),
        )
    if kind == "rectangle":
        if len(points) == 4 and (
            len(set(xs)) != 2
            or len(set(ys)) != 2
            or len({tuple(p) for p in points}) != 4
        ):
            raise DomainError("geometry", "矩形必须水平")
    elif len(points) != 4:
        raise DomainError("geometry", "四边形需要 4 个顶点")
    if clipped and kind == "quadrilateral":
        area = sum(
            points[i][0] * points[(i + 1) % 4][1]
            - points[(i + 1) % 4][0] * points[i][1]
            for i in range(4)
        )
        if len({tuple(point) for point in points}) < 4 or area == 0:
            raise DomainError("geometry", "贴边后四边形退化，需人工处理")
    record["shape_type"] = kind
    record["points"] = [list(p) for p in points]
    default = 0 if attribute == "text" else None
    value = record.get("recoverable", default)
    if legacy and value is None:
        value = default
    if value is not None and (
        type(value) is not int or value not in (0, 1, 2)
    ):
        if legacy:
            value = default
        else:
            raise DomainError("recoverable", "可恢复度必须是 0、1、2 或未设置")
    record["recoverable"] = value
    for key, value in {
        "score": None,
        "group_id": None,
        "difficult": False,
        "flags": {},
        "attributes": {},
        "kie_linking": [],
    }.items():
        record.setdefault(key, value)
    return record


def new_region_id(sample: str, used: set[str]) -> str:
    """Allocate a stable legacy-compatible ID."""
    prefix = f"{sample}#"
    numbers = [
        int(v[len(prefix) :])
        for v in used
        if v.startswith(prefix) and v[len(prefix) :].isdigit()
    ]
    return f"{prefix}{max(numbers, default=-1) + 1:04d}"


def synchronize(
    hr: list[dict],
    previous: Group,
    dimensions: dict,
    recoverability: dict | None = None,
) -> Group:
    """Derive LR data from HR, preserving LR-specific extension metadata."""
    result = {"HR": copy.deepcopy(hr)}
    ids = {r["region_id"] for r in hr}
    for variant in VARIANTS[1:]:
        by_id = {r["region_id"]: r for r in previous.get(variant, [])}
        result[variant] = []
        for master in hr:
            old = by_id.get(master["region_id"], {})
            record = copy.deepcopy(master)
            if "difficult" in old:
                record["difficult"] = old["difficult"]
            for key, value in old.items():
                if key not in STANDARD_FIELDS:
                    record[key] = copy.deepcopy(value)
            record["points"] = scale_points(
                master["points"], dimensions["HR"], dimensions[variant]
            )
            record["recoverable"] = old.get("recoverable")
            result[variant].append(record)
    for variant, values in (recoverability or {}).items():
        if variant not in VARIANTS or not isinstance(values, dict):
            raise DomainError("recoverable", "倍率或可恢复度映射无效")
        if set(values) - ids:
            raise DomainError("region_id", "可恢复度引用未知区域")
        for record in result[variant]:
            if record["region_id"] in values:
                value = values[record["region_id"]]
                if value is not None and (
                    type(value) is not int or value not in (0, 1, 2)
                ):
                    raise DomainError("recoverable", "可恢复度取值无效")
                record["recoverable"] = value
    return result


def edit_group(
    hr: list[dict],
    previous: Group,
    dimensions: dict,
    attribute: str,
    recoverability: dict,
) -> Group:
    """Validate a complete HR edit and compute the authoritative group."""
    normalized, used = [], set()
    for source in hr:
        record = normalize_record(source, attribute, dimensions["HR"])
        region_id = record.get("region_id")
        if (
            not isinstance(region_id, str)
            or not region_id
            or len(region_id) > 256
            or region_id in used
        ):
            raise DomainError("region_id", "区域 ID 必须非空且唯一")
        used.add(region_id)
        normalized.append(record)
    return synchronize(normalized, previous, dimensions, recoverability)


def completion(group: Group) -> dict:
    """Report completeness and advisory monotonicity violations."""
    missing, violations = [], []
    for variant in VARIANTS:
        for record in group[variant]:
            if record.get("recoverable") not in (0, 1, 2):
                missing.append(
                    {"variant": variant, "region_id": record["region_id"]}
                )
    for index, record in enumerate(group["HR"]):
        values = [group[v][index].get("recoverable") for v in VARIANTS]
        if all(x in (0, 1, 2) for x in values) and values != sorted(values):
            violations.append(record["region_id"])
    return {
        "missing": missing,
        "violations": violations,
        "empty": not group["HR"],
    }


def metadata(attribute: str) -> dict:
    """Create desktop-compatible dataset metadata."""
    return {
        "schema_version": 3,
        "format": "x-anylabeling-json",
        "attribute": attribute,
        "master": "HR",
        "variants": list(VARIANTS),
        "recoverable": {
            "0": "sufficient evidence",
            "1": "ambiguous evidence",
            "2": "insufficient evidence / generation required",
        },
        "mixed_region_policy": "overall_judgement",
    }


def ocr_geometry(points: list[list[float]]) -> tuple[str, list[list[int]]]:
    """Match desktop is_possible_rectangle and its p1/p3 corner construction."""
    box = [[int(value) for value in point] for point in points]
    if len(box) != 4:
        raise DomainError("model_geometry", "OCR 检测结果必须为四点区域")
    distances = sorted(
        sum(
            (box[index][axis] - box[(index + 1) % 4][axis]) ** 2
            for axis in (0, 1)
        )
        for index in range(4)
    )
    if distances[0] == distances[1] and distances[2] == distances[3]:
        first, third = box[0], box[2]
        return "rectangle", [
            first,
            [third[0], first[1]],
            third,
            [first[0], third[1]],
        ]
    return "quadrilateral", box
