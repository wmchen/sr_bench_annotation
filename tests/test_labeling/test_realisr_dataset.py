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


def write_legacy_labels(path, variant, values):
    rows = []
    for sample, records in values.items():
        rows.append(
            f"{variant}/{sample}\t"
            f"{json.dumps(records, ensure_ascii=False)}\n"
        )
    path.write_text("".join(rows), encoding="utf-8")


class RealISRDatasetTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        dimensions = {
            "HR": (12, 12),
            "LR2": (6, 6),
            "LR3": (4, 4),
            "LR4": (3, 3),
        }
        for variant, size in dimensions.items():
            directory = self.root / variant
            directory.mkdir()
            write_png_header(directory / "000001.png", *size)
        self.hr_records = [
            {
                "transcription": "Floor",
                "points": [[0, 0], [12, 0], [12, 12], [0, 12]],
                "difficult": False,
                "hr_extension": "keep-me",
            }
        ]
        write_legacy_labels(
            self.root / "HR" / "Label.txt",
            "HR",
            {"000001.png": self.hr_records},
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def test_imports_legacy_and_scales_using_actual_dimensions(self):
        dataset = RealISRDataset(self.root)
        hr = dataset.records_for("000001.png", "HR")[0]
        lr3 = dataset.records_for("000001.png", "LR3")[0]
        self.assertEqual(hr["label"], "text")
        self.assertEqual(hr["description"], "Floor")
        self.assertEqual(hr["shape_type"], "quadrilateral")
        self.assertEqual(hr["region_id"], "000001.png#0000")
        self.assertEqual(hr["recoverable"], 0)
        self.assertEqual(lr3["points"], [[0, 0], [4, 0], [4, 4], [0, 4]])
        self.assertEqual(lr3["label"], "text")
        self.assertEqual(lr3["description"], "Floor")
        self.assertIsNone(lr3["recoverable"])
        self.assertFalse(dataset.is_complete("000001.png", formal=True))

    def test_point_scaling_rounds_and_clamps(self):
        self.assertEqual(
            scale_points([[-2, -1], [6, 6], [14, 14]], (12, 12), (4, 4)),
            [[0, 0], [2, 2], [4, 4]],
        )

    def test_legacy_difficult_and_extension_fields_are_preserved(self):
        write_legacy_labels(
            self.root / "LR2" / "Label.txt",
            "LR2",
            {
                "000001.png": [
                    {
                        "transcription": "Floor",
                        "points": [[0, 0], [6, 0], [6, 6], [0, 6]],
                        "difficult": True,
                        "lr_extension": {"source": "legacy"},
                    }
                ]
            },
        )
        dataset = RealISRDataset(self.root)
        hr = dataset.records_for("000001.png", "HR")[0]
        lr2 = dataset.records_for("000001.png", "LR2")[0]
        self.assertEqual(hr["hr_extension"], "keep-me")
        self.assertTrue(lr2["difficult"])
        self.assertEqual(lr2["lr_extension"], {"source": "legacy"})

    def test_stable_ids_survive_reordering(self):
        dataset = RealISRDataset(self.root)
        records = dataset.records_for("000001.png", "HR")
        records.append(
            {
                "label": "second",
                "points": [[1, 1], [2, 1], [2, 2], [1, 2]],
                "shape_type": "rectangle",
                "region_id": None,
                "recoverable": 0,
            }
        )
        dataset.set_hr_records("000001.png", records)
        assigned = dataset.records_for("000001.png", "HR")
        second_id = assigned[1]["region_id"]
        dataset.set_hr_records("000001.png", [assigned[1], assigned[0]])
        self.assertEqual(
            dataset.records_for("000001.png", "HR")[0]["region_id"],
            second_id,
        )

    def test_draft_round_trip_preserves_partial_values(self):
        dataset = RealISRDataset(self.root)
        region_id = dataset.records_for("000001.png", "HR")[0]["region_id"]
        dataset.set_recoverable("000001.png", "LR2", region_id, 1)
        dataset.save_draft()
        self.assertTrue((self.root / "annotations" / DRAFT_FILENAME).exists())
        restored = RealISRDataset(self.root)
        self.assertEqual(
            restored.records_for("000001.png", "LR2")[0]["recoverable"],
            1,
        )
        self.assertIsNone(
            restored.records_for("000001.png", "LR3")[0]["recoverable"]
        )

    def test_commit_writes_four_xlabel_json_files(self):
        dataset = RealISRDataset(self.root)
        region_id = dataset.records_for("000001.png", "HR")[0]["region_id"]
        for variant, value in zip(VARIANTS, (0, 1, 1, 2)):
            dataset.set_recoverable("000001.png", variant, region_id, value)
        dataset.commit_sample("000001.png")
        for variant in VARIANTS:
            path = Path(dataset.json_path_for(variant, "000001.png"))
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["imagePath"], "000001.png")
            self.assertEqual(payload["realisr"]["variant"], variant)
            self.assertEqual(payload["shapes"][0]["region_id"], region_id)
        self.assertTrue(dataset.is_complete("000001.png", formal=True))
        restored = RealISRDataset(self.root)
        self.assertTrue(restored.is_complete("000001.png", formal=True))
        self.assertEqual(
            restored.records_for("000001.png", "HR")[0]["label"],
            "text",
        )
        self.assertEqual(
            restored.records_for("000001.png", "HR")[0]["description"],
            "Floor",
        )

    def test_reopens_and_migrates_misplaced_realisr_text(self):
        dataset = RealISRDataset(self.root)
        region_id = dataset.records_for("000001.png", "HR")[0]["region_id"]
        for variant, value in zip(VARIANTS, (0, 1, 1, 2)):
            dataset.set_recoverable("000001.png", variant, region_id, value)
        for variant in VARIANTS:
            dataset.records["000001.png"][variant][0]["label"] = "Floor"
            dataset.records["000001.png"][variant][0]["description"] = ""
        dataset.commit_sample("000001.png")
        for variant in VARIANTS:
            path = Path(dataset.json_path_for(variant, "000001.png"))
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["realisr"]["schema_version"] = 1
            path.write_text(json.dumps(payload), encoding="utf-8")

        restored = RealISRDataset(self.root)
        for variant in VARIANTS:
            record = restored.records_for("000001.png", variant)[0]
            self.assertEqual(record["label"], "text")
            self.assertEqual(record["description"], "Floor")

    def test_formal_status_becomes_dirty_when_hr_geometry_changes(self):
        dataset = RealISRDataset(self.root)
        region_id = dataset.records_for("000001.png", "HR")[0]["region_id"]
        for variant, value in zip(VARIANTS, (0, 1, 1, 2)):
            dataset.set_recoverable("000001.png", variant, region_id, value)
        dataset.commit_sample("000001.png")
        records = dataset.records_for("000001.png", "HR")
        records[0]["points"][0] = [1, 1]
        dataset.set_hr_records("000001.png", records)
        self.assertFalse(dataset.is_complete("000001.png", formal=True))

    def test_formal_status_tracks_non_geometry_shape_fields(self):
        dataset = RealISRDataset(self.root)
        region_id = dataset.records_for("000001.png", "HR")[0]["region_id"]
        for variant, value in zip(VARIANTS, (0, 1, 1, 2)):
            dataset.set_recoverable("000001.png", variant, region_id, value)
        dataset.commit_sample("000001.png")
        records = dataset.records_for("000001.png", "HR")
        records[0]["difficult"] = True
        dataset.set_hr_records("000001.png", records)
        self.assertFalse(dataset.is_complete("000001.png", formal=True))

    def test_successful_commit_removes_backups_and_committed_draft(self):
        dataset = RealISRDataset(self.root)
        region_id = dataset.records_for("000001.png", "HR")[0]["region_id"]
        for variant, value in zip(VARIANTS, (0, 1, 1, 2)):
            dataset.set_recoverable("000001.png", variant, region_id, value)
        dataset.commit_sample("000001.png")
        restored = RealISRDataset(self.root)
        restored.set_recoverable("000001.png", "LR2", region_id, 2)
        restored.save_draft()
        draft = self.root / "annotations" / DRAFT_FILENAME
        self.assertTrue(draft.exists())
        restored.commit_sample("000001.png")
        self.assertFalse(draft.exists())
        for variant in VARIANTS:
            path = Path(restored.json_path_for(variant, "000001.png"))
            self.assertFalse(Path(f"{path}{BACKUP_SUFFIX}").exists())

    def test_reopening_cleans_stale_backups_without_a_draft(self):
        dataset = RealISRDataset(self.root)
        region_id = dataset.records_for("000001.png", "HR")[0]["region_id"]
        for variant, value in zip(VARIANTS, (0, 1, 1, 2)):
            dataset.set_recoverable("000001.png", variant, region_id, value)
        dataset.commit_sample("000001.png")
        backups = []
        for variant in VARIANTS:
            path = Path(dataset.json_path_for(variant, "000001.png"))
            backup = Path(f"{path}{BACKUP_SUFFIX}")
            backup.write_bytes(path.read_bytes())
            backups.append(backup)

        RealISRDataset(self.root)

        self.assertTrue(all(not backup.exists() for backup in backups))

    def test_failed_overwrite_keeps_backups_and_draft(self):
        dataset = RealISRDataset(self.root)
        region_id = dataset.records_for("000001.png", "HR")[0]["region_id"]
        for variant, value in zip(VARIANTS, (0, 1, 1, 2)):
            dataset.set_recoverable("000001.png", variant, region_id, value)
        dataset.commit_sample("000001.png")

        restored = RealISRDataset(self.root)
        restored.set_recoverable("000001.png", "LR2", region_id, 2)
        restored.save_draft()
        real_atomic_write = dataset_module._atomic_write

        def fail_on_lr3(path, payload):
            path = Path(path)
            if path.parent.name == "LR3" and path.suffix == ".json":
                raise OSError("simulated overwrite failure")
            return real_atomic_write(path, payload)

        with mock.patch.object(
            dataset_module, "_atomic_write", side_effect=fail_on_lr3
        ):
            with self.assertRaisesRegex(
                OSError, "simulated overwrite failure"
            ):
                restored.commit_sample("000001.png")

        self.assertTrue(
            (self.root / "annotations" / DRAFT_FILENAME).exists()
        )
        for variant in VARIANTS:
            path = Path(restored.json_path_for(variant, "000001.png"))
            self.assertTrue(Path(f"{path}{BACKUP_SUFFIX}").exists())
        RealISRDataset(self.root)
        for variant in VARIANTS:
            path = Path(restored.json_path_for(variant, "000001.png"))
            self.assertTrue(Path(f"{path}{BACKUP_SUFFIX}").exists())

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
            RealISRDataset(self.root)

    def test_failed_commit_rolls_back_and_keeps_draft(self):
        dataset = RealISRDataset(self.root)
        region_id = dataset.records_for("000001.png", "HR")[0]["region_id"]
        for variant, value in zip(VARIANTS, (0, 1, 1, 2)):
            dataset.set_recoverable("000001.png", variant, region_id, value)
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
        self.assertFalse(
            Path(dataset.json_path_for("HR", "000001.png")).exists()
        )
        self.assertFalse(
            Path(dataset.json_path_for("LR2", "000001.png")).exists()
        )
        self.assertTrue((self.root / "annotations" / DRAFT_FILENAME).exists())

    def test_missing_counterpart_blocks_opening(self):
        (self.root / "LR4" / "000001.png").unlink()
        with self.assertRaisesRegex(RealISRDatasetError, "LR4 missing"):
            RealISRDataset(self.root)


if __name__ == "__main__":
    unittest.main()
