"""Data model and persistence for grouped Real-ISR annotations.

The module deliberately has no Qt dependency. It owns dataset discovery,
attribute binding, HR-to-LR geometry synchronization, draft recovery, and
coordinated X-AnyLabeling JSON commits.
"""

from __future__ import annotations

import copy
import json
import os
import os.path as osp
import re
import shutil
import struct
import tempfile
from pathlib import Path

from anylabeling.app_info import __version__

VARIANTS = ("HR", "LR2", "LR3", "LR4")
SCALE_FACTORS = {"HR": 1, "LR2": 2, "LR3": 3, "LR4": 4}
IMAGE_EXTENSIONS = {
    ".bmp",
    ".gif",
    ".jpeg",
    ".jpg",
    ".png",
    ".tif",
    ".tiff",
    ".webp",
}
SCHEMA_VERSION = 3
SUPPORTED_SCHEMA_VERSIONS = (1, 2, SCHEMA_VERSION)
SUPPORTED_ATTRIBUTES = ("text", "face")
ANNOTATIONS_DIRNAME = "annotations"
DRAFT_FILENAME = ".realisr_draft.json"
METADATA_FILENAME = "RealISRMeta.json"
BACKUP_SUFFIX = ".pre_realisr.bak"
DEFAULT_TEXT_LABEL = "text"
DEFAULT_FACE_LABEL = "face"
STANDARD_SHAPE_FIELDS = {
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


class RealISRDatasetError(ValueError):
    """Raised when a grouped dataset cannot be reconciled safely."""


def _natural_key(value):
    return [
        int(part) if part.isdigit() else part.lower()
        for part in re.split(r"(\d+)", value)
    ]


def _atomic_write(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


def image_dimensions(path):
    """Read common image dimensions, falling back to Pillow when available."""
    path = Path(path)
    with path.open("rb") as handle:
        header = handle.read(32)
        if header.startswith(b"\x89PNG\r\n\x1a\n"):
            return struct.unpack(">II", header[16:24])
        if header[:2] == b"BM" and len(header) >= 26:
            width, height = struct.unpack("<ii", header[18:26])
            return abs(width), abs(height)
        if header[:6] in (b"GIF87a", b"GIF89a"):
            return struct.unpack("<HH", header[6:10])
        if header[:2] == b"\xff\xd8":
            handle.seek(2)
            while True:
                marker_start = handle.read(1)
                if not marker_start:
                    break
                if marker_start != b"\xff":
                    continue
                marker = handle.read(1)
                while marker == b"\xff":
                    marker = handle.read(1)
                if marker in (b"\xd8", b"\xd9"):
                    continue
                length_bytes = handle.read(2)
                if len(length_bytes) != 2:
                    break
                length = struct.unpack(">H", length_bytes)[0]
                if marker and marker[0] in {
                    0xC0,
                    0xC1,
                    0xC2,
                    0xC3,
                    0xC5,
                    0xC6,
                    0xC7,
                    0xC9,
                    0xCA,
                    0xCB,
                    0xCD,
                    0xCE,
                    0xCF,
                }:
                    payload = handle.read(5)
                    if len(payload) == 5:
                        height, width = struct.unpack(">HH", payload[1:5])
                        return width, height
                    break
                handle.seek(max(0, length - 2), os.SEEK_CUR)

    try:
        from PIL import Image

        with Image.open(path) as image:
            return image.size
    except Exception as exc:
        raise RealISRDatasetError(
            f"Could not determine image dimensions: {path}"
        ) from exc


def scale_points(points, source_size, target_size):
    source_width, source_height = source_size
    target_width, target_height = target_size
    if source_width <= 0 or source_height <= 0:
        raise RealISRDatasetError("Source image has invalid dimensions")
    scaled = []
    for point in points:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise RealISRDatasetError(
                "Each polygon point must contain x and y"
            )
        x = max(
            0,
            min(
                target_width,
                int(round(float(point[0]) * target_width / source_width)),
            ),
        )
        y = max(
            0,
            min(
                target_height,
                int(round(float(point[1]) * target_height / source_height)),
            ),
        )
        scaled.append([x, y])
    return scaled


def _json_bytes(payload):
    return (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode(
        "utf-8"
    )


class RealISRDataset:
    """In-memory representation of a four-variant Real-ISR dataset."""

    variants = VARIANTS

    def __init__(self, root, attribute):
        if attribute not in SUPPORTED_ATTRIBUTES:
            raise RealISRDatasetError(
                f"Unsupported Real-ISR attribute: {attribute}"
            )
        self.root = Path(root).expanduser().resolve()
        self.annotation_root = self.root / ANNOTATIONS_DIRNAME
        self.attribute = attribute
        self.samples = []
        self.dimensions = {}
        self.formal = {variant: {} for variant in VARIANTS}
        self.formal_json_samples = set()
        self.records = {}
        self.drafts = {}
        self.backup_cleanup_failures = []
        self._discover()
        self._validate_attribute_binding()
        self._load_sources()
        self._initialize_records()
        self._load_draft()
        self._remove_stale_committed_backups()
        self.bind_attribute()
        self._draft_revision = 0
        self._saved_draft_revision = 0
        self._sample_stats_cache = {}
        self._dashboard_stats_cache = {}
        self._rebuild_dashboard_stats_cache()

    def _validate_attribute_binding(self):
        path = self.annotation_root / METADATA_FILENAME
        if not path.exists():
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RealISRDatasetError(
                f"Invalid Real-ISR metadata: {path}"
            ) from exc
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") not in SUPPORTED_SCHEMA_VERSIONS
        ):
            raise RealISRDatasetError(
                f"Unsupported Real-ISR metadata schema: {path}"
            )
        stored_attribute = payload.get("attribute", "text")
        if stored_attribute not in SUPPORTED_ATTRIBUTES:
            raise RealISRDatasetError(
                f"Unsupported Real-ISR attribute in {path}: "
                f"{stored_attribute}"
            )
        if stored_attribute != self.attribute:
            raise RealISRDatasetError(
                f"Real-ISR dataset is bound to attribute "
                f"'{stored_attribute}', not '{self.attribute}'"
            )

    def bind_attribute(self):
        """Persist the selected attribute after a successful validation."""
        _atomic_write(
            self.annotation_root / METADATA_FILENAME,
            _json_bytes(self._metadata()),
        )

    def _discover(self):
        errors = []
        file_sets = {}
        for variant in VARIANTS:
            directory = self.root / variant
            if not directory.is_dir():
                errors.append(f"Missing required directory: {directory}")
                continue
            file_sets[variant] = {
                path.name
                for path in directory.iterdir()
                if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
            }
        if errors:
            raise RealISRDatasetError("\n".join(errors))

        reference = file_sets["HR"]
        if not reference:
            raise RealISRDatasetError(
                "HR directory contains no supported images"
            )
        for variant in VARIANTS[1:]:
            missing = sorted(reference - file_sets[variant], key=_natural_key)
            extra = sorted(file_sets[variant] - reference, key=_natural_key)
            if missing:
                errors.append(f"{variant} missing: {', '.join(missing[:12])}")
            if extra:
                errors.append(f"{variant} extra: {', '.join(extra[:12])}")
        if errors:
            raise RealISRDatasetError("\n".join(errors))

        self.samples = sorted(reference, key=_natural_key)
        for sample in self.samples:
            for variant in VARIANTS:
                try:
                    self.dimensions[(variant, sample)] = image_dimensions(
                        self.root / variant / sample
                    )
                except (OSError, RealISRDatasetError) as exc:
                    errors.append(str(exc))
            if any(
                (variant, sample) not in self.dimensions
                for variant in VARIANTS
            ):
                continue
            hr_width, hr_height = self.dimensions[("HR", sample)]
            for variant in VARIANTS[1:]:
                factor = SCALE_FACTORS[variant]
                lr_width, lr_height = self.dimensions[(variant, sample)]
                if (
                    abs(lr_width - hr_width / factor) > 1.0
                    or abs(lr_height - hr_height / factor) > 1.0
                ):
                    errors.append(
                        f"{variant}/{sample} has size "
                        f"{lr_width}x{lr_height}; expected approximately "
                        f"HR/{factor} from {hr_width}x{hr_height}"
                    )
        if errors:
            raise RealISRDatasetError("\n".join(errors[:40]))

    def _json_path(self, variant, sample):
        return (
            self.annotation_root
            / variant
            / (f"{osp.splitext(sample)[0]}.json")
        )

    @staticmethod
    def _normalize_text_fields(record, migrate_misplaced_label=False):
        """Map OCR transcription to X-AnyLabeling's description field."""
        transcription = record.pop("transcription", None)
        if transcription is not None:
            if not record.get("description"):
                record["description"] = transcription
        elif (
            migrate_misplaced_label
            and not record.get("description")
            and record.get("label")
            not in (
                None,
                "",
                DEFAULT_TEXT_LABEL,
            )
        ):
            # Compatibility with Real-ISR JSON/drafts produced before OCR
            # text was stored in ``description``.
            record["description"] = record["label"]
        else:
            record.setdefault("description", "")
        record["label"] = DEFAULT_TEXT_LABEL
        return record

    @staticmethod
    def _is_horizontal_rectangle(points):
        if len(points) == 2:
            return (
                points[0][0] != points[1][0] and points[0][1] != points[1][1]
            )
        if len(points) != 4:
            return False
        coordinates = {(float(point[0]), float(point[1])) for point in points}
        xs = {point[0] for point in coordinates}
        ys = {point[1] for point in coordinates}
        return len(coordinates) == 4 and len(xs) == 2 and len(ys) == 2

    def _normalize_record(
        self,
        record,
        *,
        migrate_misplaced_label=False,
        strict=False,
        path=None,
    ):
        if self.attribute == "text":
            return self._normalize_text_fields(
                record,
                migrate_misplaced_label=migrate_misplaced_label,
            )

        location = str(path) if path is not None else "face annotation"
        label = record.get("label")
        description = record.get("description")
        shape_type = record.get("shape_type")
        points = record.get("points", [])
        if strict and label not in (None, "", DEFAULT_FACE_LABEL):
            raise RealISRDatasetError(
                f"{location} contains a non-face label: {label}"
            )
        if strict and description not in (None, ""):
            raise RealISRDatasetError(
                f"{location} contains a non-empty face description"
            )
        if shape_type not in (None, "rectangle"):
            raise RealISRDatasetError(
                f"{location} contains a non-rectangle face region"
            )
        if strict and not self._is_horizontal_rectangle(points):
            raise RealISRDatasetError(
                f"{location} contains a non-horizontal face rectangle"
            )
        record.pop("transcription", None)
        record["label"] = DEFAULT_FACE_LABEL
        record["description"] = ""
        record["shape_type"] = "rectangle"
        return record

    def _load_json_records(self, variant, sample):
        path = self._json_path(variant, sample)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RealISRDatasetError(
                f"Invalid annotation JSON: {path}"
            ) from exc
        if not isinstance(payload, dict) or not isinstance(
            payload.get("shapes"), list
        ):
            raise RealISRDatasetError(
                f"Invalid annotation JSON schema: {path}"
            )
        image_path = payload.get("imagePath")
        if image_path and osp.basename(str(image_path)) != sample:
            raise RealISRDatasetError(
                f"{path} references a different image: {image_path}"
            )
        realisr_metadata = payload.get("realisr")
        is_realisr_document = isinstance(realisr_metadata, dict)
        realisr_schema_version = (
            realisr_metadata.get("schema_version", 1)
            if is_realisr_document
            else 2
        )
        if realisr_schema_version not in SUPPORTED_SCHEMA_VERSIONS:
            raise RealISRDatasetError(
                f"Unsupported Real-ISR JSON schema version: {path}"
            )
        document_attribute = (
            realisr_metadata.get("attribute", "text")
            if is_realisr_document
            else "text"
        )
        if document_attribute != self.attribute:
            raise RealISRDatasetError(
                f"{path} is bound to attribute '{document_attribute}', "
                f"not '{self.attribute}'"
            )
        needs_text_field_migration = realisr_schema_version == 1
        records = copy.deepcopy(payload["shapes"])
        records = [
            (
                self._normalize_record(
                    record,
                    migrate_misplaced_label=needs_text_field_migration,
                    strict=True,
                    path=path,
                )
                if isinstance(record, dict)
                else record
            )
            for record in records
        ]
        return records

    def _load_sources(self):
        for sample in self.samples:
            existing = {
                variant: self._json_path(variant, sample).exists()
                for variant in VARIANTS
            }
            count = sum(existing.values())
            if count not in (0, len(VARIANTS)):
                present = ", ".join(
                    variant for variant, value in existing.items() if value
                )
                missing = ", ".join(
                    variant for variant, value in existing.items() if not value
                )
                raise RealISRDatasetError(
                    f"Partial annotation JSON group for {sample}; "
                    f"present: {present}; missing: {missing}"
                )
            for variant in VARIANTS:
                if count == len(VARIANTS):
                    records = self._load_json_records(variant, sample)
                else:
                    records = []
                if records or count == len(VARIANTS):
                    self.formal[variant][sample] = records
            if count == len(VARIANTS):
                self.formal_json_samples.add(sample)

    @staticmethod
    def _new_region_id(sample, used_ids):
        prefix = f"{sample}#"
        numbers = []
        for region_id in used_ids:
            if str(region_id).startswith(prefix):
                suffix = str(region_id)[len(prefix) :]
                if suffix.isdigit():
                    numbers.append(int(suffix))
        number = max(numbers, default=-1) + 1
        candidate = f"{prefix}{number:04d}"
        while candidate in used_ids:
            number += 1
            candidate = f"{prefix}{number:04d}"
        return candidate

    def _normalize_hr(self, sample, source):
        normalized = []
        used = set()
        for source_record in source:
            if not isinstance(source_record, dict):
                raise RealISRDatasetError(
                    f"HR/{sample} contains a non-object region"
                )
            record = copy.deepcopy(source_record)
            record = self._normalize_record(record)
            region_id = record.get("region_id")
            if not region_id or region_id in used:
                region_id = self._new_region_id(sample, used)
            used.add(region_id)
            record["region_id"] = region_id
            value = record.get("recoverable", 0)
            record["recoverable"] = value if value in (0, 1, 2) else 0
            record.setdefault("points", [])
            record["points"] = [list(point) for point in record["points"]]
            if self.attribute == "text":
                record.setdefault(
                    "shape_type",
                    (
                        "quadrilateral"
                        if len(record["points"]) == 4
                        else "polygon"
                    ),
                )
            elif not self._is_horizontal_rectangle(record["points"]):
                raise RealISRDatasetError(
                    f"HR/{sample} contains a non-horizontal face rectangle"
                )
            record.setdefault("score", None)
            record.setdefault("group_id", None)
            record.setdefault("difficult", False)
            record.setdefault("flags", {})
            record.setdefault("attributes", {})
            record.setdefault("kie_linking", [])
            normalized.append(record)
        return normalized

    def _synchronize_variant(
        self, sample, variant, master, existing, strict=False
    ):
        by_id = {}
        if existing:
            ids = [
                record.get("region_id")
                for record in existing
                if isinstance(record, dict)
            ]
            if (
                len(ids) == len(existing)
                and all(ids)
                and len(set(ids)) == len(ids)
            ):
                by_id = {record["region_id"]: record for record in existing}
                if strict and set(ids) != {
                    record["region_id"] for record in master
                }:
                    raise RealISRDatasetError(
                        f"{variant}/{sample} region_id set does not match HR"
                    )
            elif (
                self.attribute == "text"
                and len(existing) == len(master)
                and all(
                    isinstance(record, dict)
                    and (
                        record.get("description")
                        or record.get("transcription", "")
                    )
                    == master[index].get("description", "")
                    for index, record in enumerate(existing)
                )
            ):
                by_id = {
                    master[index]["region_id"]: record
                    for index, record in enumerate(existing)
                }
            else:
                raise RealISRDatasetError(
                    f"{variant}/{sample} cannot be matched safely to HR annotations"
                )

        synchronized = []
        source_size = self.dimensions[("HR", sample)]
        target_size = self.dimensions[(variant, sample)]
        for master_record in master:
            region_id = master_record["region_id"]
            previous = copy.deepcopy(by_id.get(region_id, {}))
            record = copy.deepcopy(master_record)
            # Geometry and text always come from HR.  Keep LR-specific
            # ``difficult`` and unknown extension fields when importing or
            # reopening an existing four-file group.
            if "difficult" in previous:
                record["difficult"] = previous["difficult"]
            for key, value in previous.items():
                if key not in STANDARD_SHAPE_FIELDS:
                    record[key] = value
            record["region_id"] = region_id
            record["label"] = master_record.get("label", "")
            record["points"] = scale_points(
                master_record.get("points", []), source_size, target_size
            )
            value = by_id.get(region_id, {}).get("recoverable")
            record["recoverable"] = value if value in (0, 1, 2) else None
            synchronized.append(record)
        return synchronized

    def _initialize_records(self):
        for sample in self.samples:
            hr = self._normalize_hr(sample, self.formal["HR"].get(sample, []))
            group = {"HR": hr}
            for variant in VARIANTS[1:]:
                group[variant] = self._synchronize_variant(
                    sample,
                    variant,
                    hr,
                    self.formal[variant].get(sample, []),
                    strict=sample in self.formal[variant],
                )
            self.records[sample] = group
            if sample in self.formal_json_samples:
                for variant in VARIANTS:
                    self.formal[variant][sample] = copy.deepcopy(
                        group[variant]
                    )

    def _load_draft(self):
        path = self.annotation_root / DRAFT_FILENAME
        if not path.exists():
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RealISRDatasetError(
                f"Invalid Real-ISR draft: {path}"
            ) from exc
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") not in SUPPORTED_SCHEMA_VERSIONS
        ):
            raise RealISRDatasetError(
                "Unsupported Real-ISR draft schema version"
            )
        draft_samples = payload.get("samples", {})
        if not isinstance(draft_samples, dict):
            raise RealISRDatasetError(
                "Real-ISR draft samples must be an object"
            )
        draft_attribute = payload.get("attribute", "text")
        if draft_attribute != self.attribute:
            raise RealISRDatasetError(
                f"Real-ISR draft is bound to attribute "
                f"'{draft_attribute}', not '{self.attribute}'"
            )
        needs_text_field_migration = payload.get("schema_version") == 1
        for sample, group in draft_samples.items():
            if sample not in self.records or not isinstance(group, dict):
                continue
            hr_source = group.get("HR", self.records[sample]["HR"])
            hr_source = [
                (
                    self._normalize_record(
                        copy.deepcopy(record),
                        migrate_misplaced_label=needs_text_field_migration,
                        strict=True,
                        path=path,
                    )
                    if isinstance(record, dict)
                    else record
                )
                for record in hr_source
            ]
            hr = self._normalize_hr(sample, hr_source)
            restored = {"HR": hr}
            for variant in VARIANTS[1:]:
                variant_source = group.get(variant, [])
                if self.attribute == "face":
                    variant_source = [
                        (
                            self._normalize_record(
                                copy.deepcopy(record),
                                strict=True,
                                path=path,
                            )
                            if isinstance(record, dict)
                            else record
                        )
                        for record in variant_source
                    ]
                restored[variant] = self._synchronize_variant(
                    sample, variant, hr, variant_source
                )
            self.records[sample] = restored
            self.drafts[sample] = copy.deepcopy(restored)

    def path_for(self, variant, sample):
        return str(self.root / variant / sample)

    def json_path_for(self, variant, sample):
        return str(self._json_path(variant, sample))

    def group(self, sample):
        return copy.deepcopy(self.records[sample])

    def records_for(self, sample, variant):
        return copy.deepcopy(self.records[sample][variant])

    def set_hr_records(self, sample, records):
        previous_group = self.records[sample]
        old_by_id = {
            record["region_id"]: record
            for record in previous_group["HR"]
            if record.get("region_id")
        }
        used = set(old_by_id)
        current_ids = set()
        normalized = []
        for source in records:
            record = copy.deepcopy(source)
            region_id = record.get("region_id")
            if not region_id or region_id in current_ids:
                region_id = self._new_region_id(sample, used | current_ids)
            used.add(region_id)
            current_ids.add(region_id)
            previous = old_by_id.get(region_id, {})
            record["region_id"] = region_id
            value = record.get("recoverable", previous.get("recoverable", 0))
            record["recoverable"] = value if value in (0, 1, 2) else 0
            record.setdefault(
                "label",
                (
                    DEFAULT_FACE_LABEL
                    if self.attribute == "face"
                    else DEFAULT_TEXT_LABEL
                ),
            )
            record.setdefault("points", [])
            record.setdefault("difficult", previous.get("difficult", False))
            normalized.append(record)
        hr_records = self._normalize_hr(sample, normalized)
        updated_group = {"HR": hr_records}
        for variant in VARIANTS[1:]:
            updated_group[variant] = self._synchronize_variant(
                sample,
                variant,
                hr_records,
                previous_group.get(variant, []),
            )
        if updated_group == previous_group:
            return False
        previous_stats = self._sample_stats_cache.get(sample)
        self.records[sample] = updated_group
        self.mark_draft(sample)
        self._refresh_sample_stats(sample, previous_stats)
        return True

    def set_recoverable(self, sample, variant, region_id, value):
        if variant not in VARIANTS:
            raise KeyError(variant)
        if value not in (0, 1, 2):
            raise ValueError("recoverable must be 0, 1, or 2")
        for record in self.records[sample][variant]:
            if record.get("region_id") == region_id:
                if record.get("recoverable") == value:
                    return False
                previous_stats = self._sample_stats_cache.get(sample)
                record["recoverable"] = value
                self.mark_draft(sample)
                self._refresh_sample_stats(sample, previous_stats)
                return True
        raise KeyError(region_id)

    def mark_draft(self, sample):
        # records is already the authoritative in-memory draft.  Retaining the
        # sample group by reference avoids copying all four variants after
        # every recoverability click; JSON serialization snapshots it on save.
        self.drafts[sample] = self.records[sample]
        self._draft_revision += 1

    def missing_counts(self, sample):
        return {
            variant: sum(
                record.get("recoverable") not in (0, 1, 2)
                for record in self.records[sample][variant]
            )
            for variant in VARIANTS
        }

    def monotonic_violations(self, sample):
        group = self.records[sample]
        values = {
            variant: {
                record["region_id"]: record.get("recoverable")
                for record in group[variant]
            }
            for variant in VARIANTS
        }
        violations = []
        for record in group["HR"]:
            region_id = record["region_id"]
            sequence = [values[v].get(region_id) for v in VARIANTS]
            if all(value in (0, 1, 2) for value in sequence) and any(
                left > right for left, right in zip(sequence, sequence[1:])
            ):
                violations.append((region_id, sequence))
        return violations

    def is_complete(self, sample, formal=False):
        if formal:
            if sample not in self.formal_json_samples:
                return False
            if any(sample not in self.formal[v] for v in VARIANTS):
                return False
            group = {v: self.formal[v][sample] for v in VARIANTS}
        else:
            group = self.records[sample]
        ids = None
        for variant in VARIANTS:
            records = group[variant]
            current_ids = [record.get("region_id") for record in records]
            if ids is None:
                ids = current_ids
            if current_ids != ids or any(
                record.get("recoverable") not in (0, 1, 2)
                for record in records
            ):
                return False
            if formal:
                expected = self.records[sample][variant]
                if records != expected:
                    return False
        return True

    def _sample_dashboard_stats(self, sample):
        group = self.records[sample]
        lr_values = {
            variant: {
                record["region_id"]: record.get("recoverable")
                for record in group[variant]
            }
            for variant in VARIANTS[1:]
        }
        return {
            "instances": len(group["HR"]),
            "completed_instances": sum(
                all(
                    lr_values[variant].get(record["region_id"]) in (0, 1, 2)
                    for variant in VARIANTS[1:]
                )
                for record in group["HR"]
            ),
            "recoverability_assigned": sum(
                record.get("recoverable") in (0, 1, 2)
                for variant in VARIANTS[1:]
                for record in group[variant]
            ),
            "recoverability_total": sum(
                len(group[variant]) for variant in VARIANTS[1:]
            ),
            "committed_samples": int(self.is_complete(sample, formal=True)),
        }

    def _rebuild_dashboard_stats_cache(self):
        self._sample_stats_cache = {
            sample: self._sample_dashboard_stats(sample)
            for sample in self.samples
        }
        self._dashboard_stats_cache = {
            "sample_groups": len(self.samples),
            "image_files": len(self.samples) * len(VARIANTS),
            "instances": sum(
                stats["instances"]
                for stats in self._sample_stats_cache.values()
            ),
            "completed_instances": sum(
                stats["completed_instances"]
                for stats in self._sample_stats_cache.values()
            ),
            "recoverability_assigned": sum(
                stats["recoverability_assigned"]
                for stats in self._sample_stats_cache.values()
            ),
            "recoverability_total": sum(
                stats["recoverability_total"]
                for stats in self._sample_stats_cache.values()
            ),
            "committed_samples": sum(
                stats["committed_samples"]
                for stats in self._sample_stats_cache.values()
            ),
        }

    def _refresh_sample_stats(self, sample, previous=None):
        if not self._dashboard_stats_cache:
            return
        previous = (
            previous
            or self._sample_stats_cache.get(sample)
            or {
                key: 0
                for key in (
                    "instances",
                    "completed_instances",
                    "recoverability_assigned",
                    "recoverability_total",
                    "committed_samples",
                )
            }
        )
        current = self._sample_dashboard_stats(sample)
        self._sample_stats_cache[sample] = current
        for key, value in current.items():
            self._dashboard_stats_cache[key] += value - previous[key]

    def variant_progress(self, sample, variant):
        records = self.records[sample][variant]
        return len(records), sum(
            record.get("recoverable") in (0, 1, 2) for record in records
        )

    def is_committed(self, sample):
        return bool(self._sample_stats_cache[sample]["committed_samples"])

    def classify_draft_samples(self):
        """Split drafts into genuinely changed and redundant samples."""
        pending = []
        redundant = []
        for sample in self.samples:
            if sample not in self.drafts:
                continue
            matches_formal = sample in self.formal_json_samples and all(
                self.drafts[sample].get(variant)
                == self.formal[variant].get(sample)
                for variant in VARIANTS
            )
            (redundant if matches_formal else pending).append(sample)
        return pending, redundant

    def opening_selection(self):
        """Return the opening sample and any redundant draft samples."""
        pending_drafts, redundant_drafts = self.classify_draft_samples()
        if pending_drafts:
            return pending_drafts[0], redundant_drafts
        for sample in self.samples:
            if not self.is_committed(sample):
                return sample, redundant_drafts
        return self.samples[0], redundant_drafts

    def dashboard_stats(self):
        return dict(self._dashboard_stats_cache)

    def _dashboard_stats_uncached(self):
        """Reference implementation retained for cache verification tests."""
        instances = 0
        completed_instances = 0
        assigned = 0
        total = 0
        for sample in self.samples:
            group = self.records[sample]
            instances += len(group["HR"])
            lr_values = {
                variant: {
                    record["region_id"]: record.get("recoverable")
                    for record in group[variant]
                }
                for variant in VARIANTS[1:]
            }
            for variant in VARIANTS[1:]:
                total += len(group[variant])
                assigned += sum(
                    record.get("recoverable") in (0, 1, 2)
                    for record in group[variant]
                )
            completed_instances += sum(
                all(
                    lr_values[v].get(record["region_id"]) in (0, 1, 2)
                    for v in VARIANTS[1:]
                )
                for record in group["HR"]
            )
        return {
            "sample_groups": len(self.samples),
            "image_files": len(self.samples) * len(VARIANTS),
            "instances": instances,
            "completed_instances": completed_instances,
            "recoverability_assigned": assigned,
            "recoverability_total": total,
            "committed_samples": sum(
                self.is_complete(sample, formal=True)
                for sample in self.samples
            ),
        }

    def _metadata(self):
        return {
            "schema_version": SCHEMA_VERSION,
            "format": "x-anylabeling-json",
            "attribute": self.attribute,
            "master": "HR",
            "variants": list(VARIANTS),
            "recoverable": {
                "0": "sufficient evidence",
                "1": "ambiguous evidence",
                "2": "insufficient evidence / generation required",
            },
            "mixed_region_policy": "overall_judgement",
        }

    def save_draft(self):
        if self._draft_revision == self._saved_draft_revision:
            return False
        self.bind_attribute()
        path = self.annotation_root / DRAFT_FILENAME
        if not self.drafts:
            if path.exists():
                path.unlink()
            self._saved_draft_revision = self._draft_revision
            return True
        _atomic_write(
            path,
            _json_bytes(
                {
                    "schema_version": SCHEMA_VERSION,
                    "attribute": self.attribute,
                    "samples": self.drafts,
                }
            ),
        )
        self._saved_draft_revision = self._draft_revision
        return True

    def _document(self, sample, variant, records):
        width, height = self.dimensions[(variant, sample)]
        return {
            "version": __version__,
            "flags": {},
            "checked": True,
            "shapes": copy.deepcopy(records),
            "imagePath": sample,
            "imageData": None,
            "imageHeight": height,
            "imageWidth": width,
            "realisr": {
                "schema_version": SCHEMA_VERSION,
                "attribute": self.attribute,
                "variant": variant,
                "master": "HR",
            },
        }

    @staticmethod
    def _remove_committed_backups(paths):
        """Remove rollback backups only after a fully successful commit."""
        failures = []
        for path in paths.values():
            backup = Path(f"{path}{BACKUP_SUFFIX}")
            try:
                backup.unlink()
            except FileNotFoundError:
                continue
            except OSError as exc:
                failures.append(f"{backup}: {exc}")
        return failures

    def _remove_stale_committed_backups(self):
        """Clean backups left by older successful application versions."""
        for sample in self.formal_json_samples:
            if sample in self.drafts or not self.is_complete(
                sample, formal=True
            ):
                continue
            paths = {
                variant: self._json_path(variant, sample)
                for variant in VARIANTS
            }
            self.backup_cleanup_failures.extend(
                self._remove_committed_backups(paths)
            )

    def commit_sample(self, sample):
        previous_stats = self._sample_stats_cache.get(sample)
        previous_draft_revision = self._draft_revision
        previous_saved_revision = self._saved_draft_revision
        if not self.is_complete(sample):
            raise RealISRDatasetError(
                f"Sample {sample} still has unset recoverability labels"
            )
        paths = {v: self._json_path(v, sample) for v in VARIANTS}
        payloads = {
            v: _json_bytes(self._document(sample, v, self.records[sample][v]))
            for v in VARIANTS
        }
        originals = {
            v: paths[v].read_bytes() if paths[v].exists() else None
            for v in VARIANTS
        }
        for variant, path in paths.items():
            backup = Path(f"{path}{BACKUP_SUFFIX}")
            if path.exists() and not backup.exists():
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, backup)

        written = []
        try:
            for variant in VARIANTS:
                _atomic_write(paths[variant], payloads[variant])
                written.append(variant)
        except Exception:
            for variant in written:
                if originals[variant] is None:
                    try:
                        paths[variant].unlink()
                    except OSError:
                        pass
                else:
                    _atomic_write(paths[variant], originals[variant])
            raise

        old_formal = copy.deepcopy(self.formal)
        old_formal_json_samples = set(self.formal_json_samples)
        old_drafts = copy.deepcopy(self.drafts)
        for variant in VARIANTS:
            self.formal[variant][sample] = copy.deepcopy(
                self.records[sample][variant]
            )
        self.formal_json_samples.add(sample)
        self.drafts.pop(sample, None)
        self._draft_revision += 1
        try:
            self.save_draft()
        except Exception:
            for variant in VARIANTS:
                if originals[variant] is None:
                    try:
                        paths[variant].unlink()
                    except OSError:
                        pass
                else:
                    _atomic_write(paths[variant], originals[variant])
            self.formal = old_formal
            self.formal_json_samples = old_formal_json_samples
            self.drafts = old_drafts
            self._draft_revision = previous_draft_revision
            self._saved_draft_revision = previous_saved_revision
            raise
        # Backups protect the coordinated write above. Once all four JSON
        # files and the draft state are committed, they are obsolete and must
        # not accumulate beside formal annotations.
        cleanup_failures = self._remove_committed_backups(paths)
        self.backup_cleanup_failures = cleanup_failures
        self._refresh_sample_stats(sample, previous_stats)
        return cleanup_failures
