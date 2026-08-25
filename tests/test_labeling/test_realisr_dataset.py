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
                "recoverability_total": 3,
                "committed_samples": 0,
            },
        )
        for variant, value in zip(VARIANTS[1:], (0, 1, 2)):
            dataset.set_recoverable("000001.png", variant, region_id, value)
        stats = dataset.dashboard_stats()
        self.assertEqual(stats["instances"], 1)
        self.assertEqual(stats["completed_instances"], 1)
        self.assertEqual(stats["recoverability_assigned"], 3)

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

    def test_save_draft_is_noop_without_a_new_revision(self):
        dataset = RealISRDataset(self.root, "text")
        self.add_record(dataset, self.text_record())
        self.assertTrue(dataset.save_draft())

        with mock.patch.object(dataset_module, "_atomic_write") as write:
            self.assertFalse(dataset.save_draft())

        write.assert_not_called()

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
