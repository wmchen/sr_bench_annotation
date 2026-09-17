import copy
import json
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from anylabeling.views.labeling import realisr_dataset as dataset_module
from anylabeling.views.labeling.realisr_dataset import (
    BACKUP_SUFFIX,
    DRAFT_FILENAME,
    METADATA_FILENAME,
    SCHEMA_VERSION,
    RealISRDataset,
    RealISRDatasetError,
    VARIANTS,
    scale_points,
)


def write_png_header(path, width, height):
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", 13)
        + b"IHDR"
        + struct.pack(">II", width, height)
        + b"\x08\x02\x00\x00\x00"
    )


class RealISRDatasetTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.dimensions = {
            "HR": (12, 12),
            "LR2": (6, 6),
            "LR3": (4, 4),
            "LR4": (3, 3),
        }
        for variant, size in self.dimensions.items():
            directory = self.root / variant
            directory.mkdir()
            write_png_header(directory / "000001.png", *size)

    def tearDown(self):
        self.tempdir.cleanup()

    @staticmethod
    def text_record(points=None, description="Floor"):
        return {
            "label": "text",
            "score": None,
            "points": points or [[0, 0], [12, 0], [12, 12], [0, 12]],
            "group_id": None,
            "description": description,
            "difficult": False,
            "shape_type": "quadrilateral",
            "flags": {},
            "attributes": {},
            "kie_linking": [],
        }

    @staticmethod
    def face_record(points=None):
        return {
            "label": "face",
            "score": None,
            "points": points or [[1, 1], [11, 11]],
            "group_id": None,
            "description": "",
            "difficult": False,
            "shape_type": "rectangle",
            "flags": {},
            "attributes": {},
            "kie_linking": [],
        }

    def add_record(self, dataset, record):
        dataset.set_hr_records("000001.png", [record])
        return dataset.records_for("000001.png", "HR")[0]["region_id"]

    def complete(self, dataset, region_id, values=(0, 1, 1, 2)):
        for variant, value in zip(VARIANTS, values):
            dataset.set_recoverable("000001.png", variant, region_id, value)

    def write_formal_group(
        self,
        attribute,
        record,
        *,
        schema_version=SCHEMA_VERSION,
        include_attribute=True,
    ):
        annotation_root = self.root / "annotations"
        for variant in VARIANTS:
            width, height = self.dimensions[variant]
            variant_record = copy.deepcopy(record)
            variant_record["points"] = scale_points(
                record["points"], self.dimensions["HR"], (width, height)
            )
            variant_record["region_id"] = "000001.png#0000"
            variant_record["recoverable"] = 0
            realisr = {
                "schema_version": schema_version,
                "variant": variant,
                "master": "HR",
            }
            if include_attribute:
                realisr["attribute"] = attribute
            payload = {
                "version": "4.0.2",
                "flags": {},
                "checked": True,
                "shapes": [variant_record],
                "imagePath": "000001.png",
                "imageData": None,
                "imageHeight": height,
                "imageWidth": width,
                "realisr": realisr,
            }
            directory = annotation_root / variant
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "000001.json").write_text(
                json.dumps(payload), encoding="utf-8"
            )

    def test_new_text_dataset_binds_attribute_and_ignores_label_txt(self):
        (self.root / "HR" / "Label.txt").write_text(
            'HR/000001.png\t[{"transcription": "ignored"}]\n',
            encoding="utf-8",
        )
        dataset = RealISRDataset(self.root, "text")
        self.assertEqual(dataset.records_for("000001.png", "HR"), [])
        metadata = json.loads(
            (self.root / "annotations" / METADATA_FILENAME).read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(metadata["schema_version"], SCHEMA_VERSION)
        self.assertEqual(metadata["attribute"], "text")

    def boundary_dataset(self, attribute: str) -> RealISRDataset:
        """Create an isolated dataset for each boundary-test attribute."""
        root = self.root / attribute
        for variant, size in self.dimensions.items():
            folder = root / variant
            folder.mkdir(parents=True, exist_ok=True)
            write_png_header(folder / "000001.png", *size)
        return RealISRDataset(root, attribute)

    def test_clipped_hr_round_trips_draft_formal_and_lr(self) -> None:
        """Preserve metadata and fractional precision while bounding all output."""
        sample = "000001.png"
        original = [[-0.6, 1.25], [12.6, 1.25], [12.6, 11], [-0.6, 11]]
        expected = [[0, 1.25], [12, 1.25], [12, 11], [0, 11]]
        for attribute in ("text", "face"):
            with self.subTest(attribute=attribute):
                dataset = self.boundary_dataset(attribute)
                record = (
                    self.text_record(original)
                    if attribute == "text"
                    else self.face_record(original)
                )
                record["custom"] = {"keep": True}
                before = copy.deepcopy(record)
                region_id = self.add_record(dataset, record)
                self.complete(dataset, region_id)
                self.assertEqual(record, before)
                dataset.save_draft()
                restored = RealISRDataset(dataset.root, attribute)
                self.assertEqual(
                    restored.records_for(sample, "HR")[0]["points"], expected
                )
                restored.commit_sample(sample)
                for variant in VARIANTS:
                    payload = json.loads(
                        Path(
                            restored.json_path_for(variant, sample)
                        ).read_text()
                    )
                    actual = payload["shapes"][0]
                    target = (
                        expected
                        if variant == "HR"
                        else scale_points(
                            expected, (12, 12), self.dimensions[variant]
                        )
                    )
                    self.assertEqual(actual["points"], target)
                    self.assertEqual(actual["region_id"], region_id)
                    self.assertEqual(actual["custom"], {"keep": True})
                    self.assertEqual(
                        actual["description"], record["description"]
                    )
                self.assertEqual(
                    RealISRDataset(dataset.root, attribute).group(sample),
                    restored.group(sample),
                )

    def test_invalid_hr_coordinates_do_not_change_previous_draft(self) -> None:
        """Reject malformed or non-finite geometry before mutating any group."""
        dataset = self.boundary_dataset("text")
        self.add_record(dataset, self.text_record())
        dataset.save_draft()
        previous = dataset.group("000001.png")
        path = dataset.annotation_root / DRAFT_FILENAME
        before = path.read_bytes()
        for point in (
            [float("nan"), 1],
            [float("inf"), 1],
            [-float("inf"), 1],
            [True, 1],
            ["2", 1],
            [None, 1],
            [1],
            [1, 2, 3],
        ):
            with self.subTest(point=point):
                record = self.text_record([point, [8, 1], [8, 8], [1, 8]])
                with self.assertRaises(RealISRDatasetError):
                    dataset.set_hr_records("000001.png", [record])
                self.assertEqual(dataset.group("000001.png"), previous)
                self.assertEqual(path.read_bytes(), before)

    def test_clipping_cannot_silently_collapse_a_region(self) -> None:
        """Report fully outside boxes and quadrilaterals losing a vertex."""
        for attribute, points in (
            ("face", [[-5, 1], [-1, 5]]),
            ("face", [[13, 1], [15, 5]]),
            ("text", [[-5, -3], [-1, -2], [13, 13], [14, 14]]),
            ("text", [[-1, -1], [-2, -2], [8, 8], [0, 8]]),
        ):
            with self.subTest(attribute=attribute, points=points):
                dataset = self.boundary_dataset(attribute)
                record = (
                    self.text_record(points)
                    if attribute == "text"
                    else self.face_record(points)
                )
                with self.assertRaisesRegex(
                    RealISRDatasetError, "degenerates after boundary clipping"
                ):
                    self.add_record(dataset, record)
                self.assertEqual(dataset.records_for("000001.png", "HR"), [])

    def test_loading_legacy_bounds_repairs_memory_before_saving(self) -> None:
        """Repair formal and draft imports without rewriting them during open."""
        sample = "000001.png"
        for attribute in ("text", "face"):
            with self.subTest(attribute=attribute):
                dataset = self.boundary_dataset(attribute)
                record = (
                    self.text_record()
                    if attribute == "text"
                    else self.face_record()
                )
                region_id = self.add_record(dataset, record)
                self.complete(dataset, region_id)
                dataset.commit_sample(sample)
                hr_path = Path(dataset.json_path_for("HR", sample))
                formal = json.loads(hr_path.read_text())
                formal["shapes"][0]["points"] = [
                    [-0.5, 1.25],
                    [12.5, 1.25],
                    [12.5, 11],
                    [-0.5, 11],
                ]
                hr_path.write_text(json.dumps(formal))
                formal_bytes = hr_path.read_bytes()
                restored = RealISRDataset(dataset.root, attribute)
                expected = [[0, 1.25], [12, 1.25], [12, 11], [0, 11]]
                self.assertEqual(
                    restored.records_for(sample, "HR")[0]["points"], expected
                )
                self.assertEqual(hr_path.read_bytes(), formal_bytes)
                draft_group = restored.group(sample)
                draft_group["HR"][0]["points"] = [
                    [-1, 2.75],
                    [13, 2.75],
                    [13, 12.5],
                    [-1, 12.5],
                ]
                path = restored.annotation_root / DRAFT_FILENAME
                path.write_text(
                    json.dumps(
                        {
                            "schema_version": SCHEMA_VERSION,
                            "attribute": attribute,
                            "samples": {sample: draft_group},
                        }
                    )
                )
                draft_bytes = path.read_bytes()
                restored = RealISRDataset(dataset.root, attribute)
                expected = [[0, 2.75], [12, 2.75], [12, 12], [0, 12]]
                self.assertEqual(path.read_bytes(), draft_bytes)
                self.assertEqual(hr_path.read_bytes(), formal_bytes)
                self.assertTrue(restored.save_draft())
                saved = json.loads(path.read_text())["samples"][sample]
                self.assertEqual(saved["HR"][0]["points"], expected)
                for variant in VARIANTS[1:]:
                    self.assertEqual(
                        saved[variant][0]["points"],
                        scale_points(
                            expected, (12, 12), self.dimensions[variant]
                        ),
                    )

    def test_text_geometry_is_scaled_using_actual_dimensions(self):
        dataset = RealISRDataset(self.root, "text")
        region_id = self.add_record(dataset, self.text_record())
        hr = dataset.records_for("000001.png", "HR")[0]
        lr3 = dataset.records_for("000001.png", "LR3")[0]
        self.assertEqual(hr["label"], "text")
        self.assertEqual(hr["description"], "Floor")
        self.assertEqual(hr["recoverable"], 0)
        self.assertEqual(lr3["region_id"], region_id)
        self.assertEqual(lr3["points"], [[0, 0], [4, 0], [4, 4], [0, 4]])
        self.assertIsNone(lr3["recoverable"])

    def test_face_records_are_canonical_and_scaled(self):
        dataset = RealISRDataset(self.root, "face")
        source = self.face_record()
        source["label"] = ""
        source["description"] = "discarded while drawing"
        region_id = self.add_record(dataset, source)
        hr = dataset.records_for("000001.png", "HR")[0]
        lr2 = dataset.records_for("000001.png", "LR2")[0]
        self.assertEqual(hr["label"], "face")
        self.assertEqual(hr["description"], "")
        self.assertEqual(hr["shape_type"], "rectangle")
        self.assertEqual(lr2["points"], [[0, 0], [6, 6]])
        self.assertEqual(lr2["region_id"], region_id)

    def test_face_rejects_non_rectangle_live_geometry(self):
        dataset = RealISRDataset(self.root, "face")
        record = self.face_record()
        record["shape_type"] = "quadrilateral"
        with self.assertRaisesRegex(
            RealISRDatasetError, "non-rectangle face region"
        ):
            self.add_record(dataset, record)

    def test_face_hr_unset_survives_draft_and_blocks_commit(self):
        sample = "000001.png"
        dataset = RealISRDataset(self.root, "face")
        region_id = self.add_record(dataset, self.face_record())
        for variant in VARIANTS[1:]:
            dataset.set_recoverable(sample, variant, region_id, 2)
        dataset.save_draft()
        dataset = RealISRDataset(self.root, "face")
        self.assertIsNone(dataset.records_for(sample, "HR")[0]["recoverable"])
        self.assertEqual(dataset.missing_counts(sample)["HR"], 1)
        self.assertEqual(dataset.dashboard_stats()["completed_instances"], 0)
        self.assertEqual(
            dataset.dashboard_stats()["recoverability_assigned"], 3
        )
        with self.assertRaisesRegex(
            RealISRDatasetError, "unset recoverability"
        ):
            dataset.commit_sample(sample)
        dataset.set_recoverable(sample, "HR", region_id, 1)
        self.assertEqual(dataset.dashboard_stats()["completed_instances"], 1)
        self.assertEqual(
            dataset.dashboard_stats(), dataset._dashboard_stats_uncached()
        )
        dataset.commit_sample(sample)
        restored = RealISRDataset(self.root, "face")
        self.assertEqual(
            restored.records_for(sample, "HR")[0]["recoverable"], 1
        )
        self.assertTrue(restored.is_complete(sample, formal=True))

    def test_face_hr_preserves_explicit_values_and_does_not_fill_missing(self):
        dataset = RealISRDataset(self.root, "face")
        for value in (None, 0, 1, 2, 9):
            with self.subTest(value=value):
                source = self.face_record()
                source["recoverable"] = value
                normalized = dataset._normalize_hr("000001.png", [source])[0]
                expected = value if value in (0, 1, 2) else None
                self.assertEqual(normalized["recoverable"], expected)
        region_id = self.add_record(dataset, self.face_record())
        dataset.set_recoverable("000001.png", "HR", region_id, 2)
        edited = dataset.records_for("000001.png", "HR")[0]
        edited.pop("recoverable")
        edited["points"] = [[2, 2], [10, 10]]
        dataset.set_hr_records("000001.png", [edited])
        self.assertEqual(
            dataset.records_for("000001.png", "HR")[0]["recoverable"], 2
        )

    def test_stable_ids_survive_reordering(self):
        dataset = RealISRDataset(self.root, "text")
        first = self.text_record()
        second = self.text_record(
            points=[[1, 1], [2, 1], [2, 2], [1, 2]],
            description="Second",
        )
        dataset.set_hr_records("000001.png", [first, second])
        records = dataset.records_for("000001.png", "HR")
        second_id = records[1]["region_id"]
        dataset.set_hr_records("000001.png", [records[1], records[0]])
        self.assertEqual(
            dataset.records_for("000001.png", "HR")[0]["region_id"],
            second_id,
        )

    def test_face_draft_and_commit_round_trip(self):
        dataset = RealISRDataset(self.root, "face")
        region_id = self.add_record(dataset, self.face_record())
        dataset.set_recoverable("000001.png", "LR2", region_id, 1)
        dataset.save_draft()
        draft_path = self.root / "annotations" / DRAFT_FILENAME
        draft = json.loads(draft_path.read_text(encoding="utf-8"))
        self.assertEqual(draft["attribute"], "face")

        restored = RealISRDataset(self.root, "face")
        self.assertEqual(
            restored.records_for("000001.png", "LR2")[0]["recoverable"],
            1,
        )
        self.complete(restored, region_id)
        restored.commit_sample("000001.png")
        self.assertFalse(draft_path.exists())
        for variant in VARIANTS:
            payload = json.loads(
                Path(restored.json_path_for(variant, "000001.png")).read_text(
                    encoding="utf-8"
                )
            )
            shape = payload["shapes"][0]
            self.assertEqual(payload["realisr"]["attribute"], "face")
            self.assertEqual(payload["realisr"]["schema_version"], 3)
            self.assertEqual(shape["label"], "face")
            self.assertEqual(shape["description"], "")
            self.assertEqual(shape["shape_type"], "rectangle")

    def test_schema_two_without_attribute_migrates_as_text(self):
        self.write_formal_group(
            "text",
            self.text_record(),
            schema_version=2,
            include_attribute=False,
        )
        dataset = RealISRDataset(self.root, "text")
        record = dataset.records_for("000001.png", "HR")[0]
        self.assertEqual(record["label"], "text")
        self.assertEqual(record["description"], "Floor")
        metadata = json.loads(
            (self.root / "annotations" / METADATA_FILENAME).read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(metadata["attribute"], "text")

    def test_schema_one_migrates_text_from_label_to_description(self):
        record = self.text_record(description="")
        record["label"] = "Floor"
        self.write_formal_group(
            "text",
            record,
            schema_version=1,
            include_attribute=False,
        )
        dataset = RealISRDataset(self.root, "text")
        restored = dataset.records_for("000001.png", "HR")[0]
        self.assertEqual(restored["label"], "text")
        self.assertEqual(restored["description"], "Floor")

    def test_bound_attribute_mismatch_is_rejected(self):
        annotation_root = self.root / "annotations"
        annotation_root.mkdir()
        (annotation_root / METADATA_FILENAME).write_text(
            json.dumps(
                {
                    "schema_version": 3,
                    "format": "x-anylabeling-json",
                    "attribute": "face",
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            RealISRDatasetError, "bound to attribute 'face'"
        ):
            RealISRDataset(self.root, "text")

    def test_draft_attribute_mismatch_is_rejected_before_rebinding(self):
        annotation_root = self.root / "annotations"
        annotation_root.mkdir()
        (annotation_root / DRAFT_FILENAME).write_text(
            json.dumps(
                {
                    "schema_version": 3,
                    "attribute": "face",
                    "samples": {},
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            RealISRDatasetError, "draft is bound to attribute 'face'"
        ):
            RealISRDataset(self.root, "text")
        self.assertFalse((annotation_root / METADATA_FILENAME).exists())

    def test_face_formal_json_validation(self):
        invalid_records = []
        wrong_label = self.face_record()
        wrong_label["label"] = "person"
        invalid_records.append((wrong_label, "non-face label"))
        described = self.face_record()
        described["description"] = "identity"
        invalid_records.append((described, "non-empty face description"))
        rotated = self.face_record([[1, 1], [10, 2], [9, 10], [0, 9]])
        invalid_records.append((rotated, "non-horizontal face rectangle"))

        for record, message in invalid_records:
            with self.subTest(message=message):
                for path in (self.root / "annotations").glob("*/*.json"):
                    path.unlink()
                self.write_formal_group("face", record)
                with self.assertRaisesRegex(RealISRDatasetError, message):
                    RealISRDataset(self.root, "face")

    def test_dashboard_uses_generic_instance_count(self):
        dataset = RealISRDataset(self.root, "face")
        region_id = self.add_record(dataset, self.face_record())
        self.assertEqual(
            dataset.dashboard_stats(),
            {
                "sample_groups": 1,
                "image_files": 4,
                "instances": 1,
                "completed_instances": 0,
                "recoverability_assigned": 0,
                "recoverability_total": 4,
                "committed_samples": 0,
            },
        )
        for variant, value in zip(VARIANTS[1:], (0, 1, 2)):
            dataset.set_recoverable("000001.png", variant, region_id, value)
        stats = dataset.dashboard_stats()
        self.assertEqual(stats["instances"], 1)
        self.assertEqual(stats["completed_instances"], 0)
        self.assertEqual(stats["recoverability_assigned"], 3)
        dataset.set_recoverable("000001.png", "HR", region_id, 0)
        self.assertEqual(dataset.dashboard_stats()["completed_instances"], 1)
        self.assertEqual(
            dataset.dashboard_stats()["recoverability_assigned"], 4
        )

    def test_change_results_and_dashboard_cache_track_real_mutations(self):
        dataset = RealISRDataset(self.root, "text")
        self.assertTrue(
            dataset.set_hr_records("000001.png", [self.text_record()])
        )
        records = dataset.records_for("000001.png", "HR")
        self.assertFalse(dataset.set_hr_records("000001.png", records))
        region_id = records[0]["region_id"]

        self.assertTrue(
            dataset.set_recoverable("000001.png", "LR2", region_id, 1)
        )
        self.assertFalse(
            dataset.set_recoverable("000001.png", "LR2", region_id, 1)
        )
        self.assertEqual(
            dataset.dashboard_stats(), dataset._dashboard_stats_uncached()
        )

    def test_batch_recoverability_is_atomic_and_refreshes_stats_once(self):
        dataset = RealISRDataset(self.root, "text")
        dataset.set_hr_records(
            "000001.png",
            [
                self.text_record(description="First"),
                self.text_record(
                    points=[[1, 1], [2, 1], [2, 2], [1, 2]],
                    description="Second",
                ),
            ],
        )
        region_ids = [
            record["region_id"]
            for record in dataset.records_for("000001.png", "HR")
        ]
        revision = dataset._draft_revision

        with (
            mock.patch.object(
                dataset, "mark_draft", wraps=dataset.mark_draft
            ) as mark_draft,
            mock.patch.object(
                dataset,
                "_refresh_sample_stats",
                wraps=dataset._refresh_sample_stats,
            ) as refresh_stats,
        ):
            self.assertTrue(
                dataset.set_recoverable_many(
                    "000001.png", "LR2", region_ids, 1
                )
            )

        self.assertEqual(
            [
                record["recoverable"]
                for record in dataset.records_for("000001.png", "LR2")
            ],
            [1, 1],
        )
        self.assertEqual(dataset._draft_revision, revision + 1)
        mark_draft.assert_called_once_with("000001.png")
        refresh_stats.assert_called_once()
        self.assertFalse(
            dataset.set_recoverable_many("000001.png", "LR2", region_ids, 1)
        )
        self.assertEqual(dataset._draft_revision, revision + 1)

        with self.assertRaises(KeyError):
            dataset.set_recoverable_many(
                "000001.png",
                "LR3",
                [region_ids[0], "missing-region"],
                2,
            )
        self.assertEqual(
            [
                record["recoverable"]
                for record in dataset.records_for("000001.png", "LR3")
            ],
            [None, None],
        )

    def test_save_draft_is_noop_without_a_new_revision(self):
        dataset = RealISRDataset(self.root, "text")
        self.add_record(dataset, self.text_record())
        self.assertTrue(dataset.save_draft())

        with mock.patch.object(dataset_module, "_atomic_write") as write:
            self.assertFalse(dataset.save_draft())

        write.assert_not_called()

    def test_opening_selection_classifies_drafts_and_uses_priority_order(self):
        dataset = RealISRDataset.__new__(RealISRDataset)
        dataset.samples = ["000001.png", "000002.png", "000010.png"]
        dataset.formal_json_samples = {"000001.png", "000002.png"}
        dataset.formal = {
            variant: {
                "000001.png": [{"recoverable": 0}],
                "000002.png": [{"recoverable": 1}],
            }
            for variant in VARIANTS
        }
        dataset._sample_stats_cache = {
            "000001.png": {"committed_samples": 1},
            "000002.png": {"committed_samples": 1},
            "000010.png": {"committed_samples": 0},
        }

        redundant_group = {
            variant: [{"recoverable": 1}] for variant in VARIANTS
        }
        pending_group = {
            variant: [{"recoverable": None}] for variant in VARIANTS
        }
        dataset.drafts = {
            "000002.png": redundant_group,
            "000010.png": pending_group,
        }
        self.assertEqual(
            dataset.opening_selection(),
            ("000010.png", ["000002.png"]),
        )

        changed_formal_group = copy.deepcopy(redundant_group)
        changed_formal_group["LR4"][0]["recoverable"] = 2
        dataset.drafts = {"000002.png": changed_formal_group}
        self.assertEqual(dataset.opening_selection(), ("000002.png", []))

        dataset.drafts = {"000002.png": redundant_group}
        self.assertEqual(
            dataset.opening_selection(),
            ("000010.png", ["000002.png"]),
        )

        dataset.drafts = {}
        self.assertEqual(dataset.opening_selection(), ("000010.png", []))

        dataset._sample_stats_cache["000010.png"]["committed_samples"] = 1
        self.assertEqual(dataset.opening_selection(), ("000001.png", []))

    def test_monotonicity_is_warning_not_completeness_error(self):
        dataset = RealISRDataset(self.root, "text")
        region_id = self.add_record(dataset, self.text_record())
        self.complete(dataset, region_id, values=(0, 2, 1, 2))
        self.assertTrue(dataset.is_complete("000001.png"))
        self.assertEqual(
            dataset.monotonic_violations("000001.png"),
            [(region_id, [0, 2, 1, 2])],
        )

    def test_failed_commit_rolls_back_and_keeps_draft(self):
        dataset = RealISRDataset(self.root, "text")
        region_id = self.add_record(dataset, self.text_record())
        self.complete(dataset, region_id)
        dataset.save_draft()
        real_atomic_write = dataset_module._atomic_write

        def fail_on_lr3(path, payload):
            path = Path(path)
            if path.parent.name == "LR3" and path.suffix == ".json":
                raise OSError("simulated write failure")
            return real_atomic_write(path, payload)

        with mock.patch.object(
            dataset_module, "_atomic_write", side_effect=fail_on_lr3
        ):
            with self.assertRaisesRegex(OSError, "simulated write failure"):
                dataset.commit_sample("000001.png")
        for variant in VARIANTS:
            self.assertFalse(
                Path(dataset.json_path_for(variant, "000001.png")).exists()
            )
        self.assertTrue((self.root / "annotations" / DRAFT_FILENAME).exists())

    def test_successful_overwrite_removes_backups(self):
        dataset = RealISRDataset(self.root, "text")
        region_id = self.add_record(dataset, self.text_record())
        self.complete(dataset, region_id)
        dataset.commit_sample("000001.png")
        restored = RealISRDataset(self.root, "text")
        restored.set_recoverable("000001.png", "LR2", region_id, 2)
        restored.save_draft()
        restored.commit_sample("000001.png")
        for variant in VARIANTS:
            path = Path(restored.json_path_for(variant, "000001.png"))
            self.assertFalse(Path(f"{path}{BACKUP_SUFFIX}").exists())

    def test_partial_json_group_blocks_opening(self):
        directory = self.root / "annotations" / "HR"
        directory.mkdir(parents=True)
        (directory / "000001.json").write_text(
            json.dumps({"shapes": [], "imagePath": "000001.png"}),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            RealISRDatasetError, "Partial annotation JSON group"
        ):
            RealISRDataset(self.root, "text")

    def test_missing_counterpart_blocks_opening(self):
        (self.root / "LR4" / "000001.png").unlink()
        with self.assertRaisesRegex(RealISRDatasetError, "LR4 missing"):
            RealISRDataset(self.root, "text")


if __name__ == "__main__":
    unittest.main()
