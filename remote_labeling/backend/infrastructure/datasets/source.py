"""Read-only dataset scan; never instantiate the desktop data manager."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path

from PIL import Image

from ...domain.rules import (
    DomainError,
    VARIANTS,
    completion,
    bound_points,
    new_region_id,
    normalize_record,
    synchronize,
)


def contained(root: Path, path: Path) -> Path:
    """Resolve source paths and reject traversal and symlink escapes."""
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise DomainError("path", "路径超出登记目录", 403)
    return resolved


def read_json(path: Path) -> dict:
    """Decode a JSON object without changing its source."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("expected object")
        json.dumps(value, allow_nan=False)
        return value
    except (OSError, ValueError) as exc:
        raise DomainError("json", f"{path.name}: 无效 JSON") from exc


def fingerprint(path: Path) -> str:
    """Hash immutable source bytes in bounded chunks."""
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def schema(payload: dict, attribute: str, *, document: bool = False) -> int:
    """Check task binding and accepted legacy schema versions."""
    data = payload.get("realisr") if document else payload
    data = data if isinstance(data, dict) else {}
    version = data.get("schema_version", 2 if document and not data else 1)
    if version not in (1, 2, 3):
        raise DomainError("schema", "仅支持 Real-ISR schema 1/2/3")
    if data.get("attribute", "text") != attribute:
        raise DomainError("attribute", "标注属性与登记数据集不一致")
    return version


def import_group(
    raw: dict,
    dimensions: dict,
    attribute: str,
    sample: str,
    version: int | dict,
    *,
    strict: bool,
    repairs: list[dict] | None = None,
    origin: str = "formal",
) -> dict:
    """Reconcile old IDs conservatively, then apply desktop LR inheritance."""
    if not isinstance(raw, dict) or any(
        not isinstance(raw.get(v, []), list) for v in VARIANTS
    ):
        raise DomainError("group", "四倍率标注必须为区域数组")
    used, hr, adjusted = set(), [], []
    hr_version = version.get("HR", 3) if isinstance(version, dict) else version
    for source in raw.get("HR", []):
        try:
            record = normalize_record(
                source,
                attribute,
                dimensions["HR"],
                legacy=True,
                migrate_text=hr_version == 1,
                clip_to_bounds=True,
            )
        except DomainError as exc:
            exc.details = {
                **(exc.details or {}),
                "variant": "HR",
                "region_id": (
                    source.get("region_id")
                    if isinstance(source, dict)
                    else None
                ),
            }
            raise
        # Schema 3 explicitly represents unfinished HR evidence as null.
        if (
            hr_version == 3
            and "recoverable" in source
            and source["recoverable"] is None
        ):
            record["recoverable"] = None
        rid = record.get("region_id")
        if not rid:
            rid = new_region_id(sample, used)
        if not isinstance(rid, str) or rid in used:
            raise DomainError("region_id", "HR 区域 ID 重复或无效")
        record["region_id"] = rid
        if record["points"] != source["points"]:
            adjusted.append(
                {
                    "sample": sample,
                    "variant": "HR",
                    "region_id": rid,
                    "source": origin,
                    "before": copy.deepcopy(source["points"]),
                }
            )
        used.add(rid)
        hr.append(record)
    existing = {}
    for variant in VARIANTS[1:]:
        records = copy.deepcopy(raw.get(variant, []))
        if any(not isinstance(r, dict) for r in records):
            raise DomainError("record", f"{variant}: 区域必须是对象")
        ids = [r.get("region_id") for r in records]
        if records and not (
            all(isinstance(x, str) and x for x in ids)
            and len(set(ids)) == len(ids)
        ):
            migrated = []
            lv = (
                version.get(variant, 3)
                if isinstance(version, dict)
                else version
            )
            for r in records:
                # LR can legitimately collapse after integer rounding.
                r = copy.deepcopy(r)
                text = r.pop("transcription", None)
                if text is not None and not r.get("description"):
                    r["description"] = text
                elif (
                    lv == 1
                    and not r.get("description")
                    and r.get("label") not in (None, "", "text")
                ):
                    r["description"] = r["label"]
                migrated.append(r)
            if (
                attribute != "text"
                or len(migrated) != len(hr)
                or any(
                    r.get("description", "") != hr[i]["description"]
                    for i, r in enumerate(migrated)
                )
            ):
                raise DomainError(
                    "region_id", f"{variant}: 无法安全匹配旧区域"
                )
            records = migrated
            for r, master in zip(records, hr):
                r["region_id"] = master["region_id"]
        if strict and {r.get("region_id") for r in records} != used:
            raise DomainError(
                "region_id", f"{variant}: 区域 ID 集合与 HR 不一致"
            )
        for r in records:
            kind = r.get("shape_type")
            points = r.get("points", [])
            detail = {"variant": variant, "region_id": r.get("region_id")}
            if (
                kind not in (None, "rectangle", "quadrilateral")
                or not isinstance(points, list)
                or len(points) not in (2, 4)
                or (kind == "quadrilateral" and len(points) != 4)
            ):
                raise DomainError(
                    "shape_type",
                    f"{variant}: 网页不支持该旧形状",
                    details=detail,
                )
            try:
                bounded = bound_points(points, dimensions[variant], clip=True)
            except DomainError as exc:
                exc.details = {**(exc.details or {}), **detail}
                raise
            if bounded != points:
                adjusted.append(
                    {
                        "sample": sample,
                        "variant": variant,
                        "region_id": r["region_id"],
                        "source": origin,
                        "before": copy.deepcopy(points),
                    }
                )
            r["points"] = bounded
            if attribute == "face" and (
                r.get("label") not in (None, "", "face")
                or r.get("description") not in (None, "")
                or r.get("shape_type") not in (None, "rectangle")
            ):
                raise DomainError("attribute", f"{variant}: 人脸标注字段冲突")
            if r.get("recoverable") not in (0, 1, 2):
                r["recoverable"] = None
        existing[variant] = records
    result = synchronize(hr, existing, dimensions)
    if repairs is not None:
        by_variant = {
            v: {r["region_id"]: r for r in result[v]} for v in VARIANTS
        }
        for item in adjusted:
            record = by_variant[item["variant"]].get(item["region_id"])
            if record is not None:
                repairs.append(
                    {**item, "after": copy.deepcopy(record["points"])}
                )
    return result


def scan_dataset(root: Path, attribute: str) -> dict:
    """Fully decode PNGs and collect index records plus located errors."""
    root = root.resolve()
    errors, samples, repairs = [], [], []
    try:
        annotation_root = contained(root, root / "annotations")
        meta = annotation_root / "RealISRMeta.json"
        if meta.exists():
            schema(read_json(contained(root, meta)), attribute)
        draft_path = annotation_root / ".realisr_draft.json"
        draft = (
            read_json(contained(root, draft_path))
            if draft_path.exists()
            else {}
        )
        if draft:
            schema(draft, attribute)
            if not isinstance(draft.get("samples"), dict):
                raise DomainError("draft", "草稿 samples 必须为对象")
        names = {}
        for variant in VARIANTS:
            folder = contained(root, root / variant)
            if not folder.is_dir():
                raise DomainError("pairing", f"缺少 {variant} 目录")
            names[variant] = {
                p.name for p in folder.iterdir() if p.suffix.lower() == ".png"
            }
            unsupported = [
                p.name
                for p in folder.iterdir()
                if p.is_file()
                and p.suffix.lower()
                in (".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif")
            ]
            if unsupported:
                raise DomainError(
                    "image_format",
                    f"{variant}: 第一期只提供 PNG 原图",
                    details=unsupported,
                )
        if len({Path(name).stem for name in names["HR"]}) != len(names["HR"]):
            raise DomainError(
                "annotation_name_collision",
                "不同原图会映射到同一个 JSON 文件名",
            )
        if not names["HR"]:
            raise DomainError("pairing", "HR 没有 PNG 图像")
    except (DomainError, OSError) as exc:
        return {
            "samples": [],
            "errors": [{"sample": None, "message": str(exc)}],
            "repairs": [],
        }
    all_names = set.union(*names.values())
    natural = lambda x: [
        int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", x)
    ]
    for name in sorted(all_names, key=natural):
        variant = None
        sample_repairs = []
        try:
            if any(name not in names[v] for v in VARIANTS):
                raise DomainError("pairing", "缺少对应倍率图像")
            images, dimensions = {}, {}
            for variant in VARIANTS:
                path = contained(root, root / variant / name)
                before = path.stat()
                with Image.open(path) as image:
                    if image.format != "PNG":
                        raise DomainError(
                            "image_format", "PNG 文件名与实际图像格式不符"
                        )
                    image.load()
                    dimensions[variant] = list(image.size)
                digest = fingerprint(path)
                after = path.stat()
                if (before.st_size, before.st_mtime_ns) != (
                    after.st_size,
                    after.st_mtime_ns,
                ):
                    raise DomainError("source_changed", "扫描期间原图发生变化")
                images[variant] = {
                    "path": str(path),
                    "sha256": digest,
                    "bytes": after.st_size,
                    "mtime_ns": after.st_mtime_ns,
                    "size": dimensions[variant],
                }
            for variant, factor in (("LR2", 2), ("LR3", 3), ("LR4", 4)):
                if any(
                    abs(dimensions[variant][i] - dimensions["HR"][i] / factor)
                    > 1
                    for i in (0, 1)
                ):
                    raise DomainError("dimensions", "LR 尺寸与 HR 倍率不一致")
            files = {
                v: contained(
                    root, annotation_root / v / (Path(name).stem + ".json")
                )
                for v in VARIANTS
            }
            exists = sum(p.exists() for p in files.values())
            if exists not in (0, 4):
                raise DomainError("pairing", "正式标注缺少倍率 JSON")
            raw, versions = {}, {}
            for variant, path in files.items():
                if exists:
                    doc = read_json(path)
                    versions[variant] = schema(doc, attribute, document=True)
                    if (
                        doc.get("imagePath")
                        and Path(doc["imagePath"]).name != name
                    ):
                        raise DomainError("imagePath", "JSON 引用了不同图像")
                    if not isinstance(doc.get("shapes"), list):
                        raise DomainError("shapes", "JSON shapes 必须是数组")
                    raw[variant] = doc["shapes"]
                else:
                    raw[variant] = []
            group = import_group(
                raw,
                dimensions,
                attribute,
                name,
                versions,
                strict=bool(exists),
                repairs=sample_repairs,
            )
            formal = copy.deepcopy(group) if exists else None
            if name in draft.get("samples", {}):
                group = import_group(
                    draft["samples"][name],
                    dimensions,
                    attribute,
                    name,
                    draft["schema_version"],
                    strict=False,
                    repairs=sample_repairs,
                    origin="draft",
                )
            samples.append(
                {
                    "id": name,
                    "images": images,
                    "dimensions": dimensions,
                    "group": group,
                    "formal": formal,
                    "complete": formal is not None
                    and not completion(formal)["missing"]
                    and formal == group,
                    "image_version": hashlib.sha256(
                        "".join(images[v]["sha256"] for v in VARIANTS).encode()
                    ).hexdigest(),
                }
            )
            repairs.extend(sample_repairs)
        except (
            DomainError,
            OSError,
            ValueError,
            TypeError,
            KeyError,
            Image.DecompressionBombError,
        ) as exc:
            details = (
                exc.details
                if isinstance(exc, DomainError)
                and isinstance(exc.details, dict)
                else {}
            )
            errors.append(
                {
                    "sample": name,
                    "variant": details.get("variant", variant),
                    "message": str(exc),
                    "details": details,
                }
            )
    for name in draft.get("samples", {}):
        if name not in names["HR"]:
            errors.append({"sample": name, "message": "草稿引用未知样本"})
    return {"samples": samples, "errors": errors, "repairs": repairs}
