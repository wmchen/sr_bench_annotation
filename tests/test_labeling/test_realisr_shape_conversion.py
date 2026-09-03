"""Exercise conversions through the real widget, shortcuts and persistence."""

import copy
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtCore, QtGui, QtTest, QtWidgets

import anylabeling.resources.resources  # noqa: F401
from anylabeling.views.labeling.label_widget import LabelingWidget
from anylabeling.views.labeling.realisr_dataset import (
    RealISRDataset,
    VARIANTS,
    scale_points,
)
from anylabeling.views.labeling.settings.controller import (
    SettingsValidationError,
)
from anylabeling.views.labeling.settings.schema import load_template_config
from anylabeling.views.labeling.widgets.quadrilateral_conversion_dialog import (
    QuadrilateralConversionDialog,
)

_DIALOG_EXEC = QuadrilateralConversionDialog.exec


class RealISRShapeConversionTest(unittest.TestCase):
    def setUp(self):
        self.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(
            []
        )
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.sample = "sample.png"
        self.sizes = dict(
            zip(VARIANTS, ((120, 120), (60, 60), (40, 40), (30, 30)))
        )
        for variant, (width, height) in self.sizes.items():
            directory = self.root / variant
            directory.mkdir()
            image = QtGui.QImage(
                width, height, QtGui.QImage.Format.Format_RGB32
            )
            image.fill(QtCore.Qt.GlobalColor.white)
            self.assertTrue(image.save(str(directory / self.sample)))
        self.dataset = RealISRDataset(self.root, "text")
        self.original = {
            "label": "text",
            "description": "原始文本",
            "shape_type": "quadrilateral",
            "points": [
                [-1.25, 5.5],
                [20.75, 3.25],
                [23.5, 19.5],
                [2.5, 22.75],
            ],
            "region_id": "sample.png#0000",
            "score": 0.875,
            "group_id": 7,
            "flags": {"verified": True},
            "attributes": {"language": "Chinese"},
            "difficult": True,
            "kie_linking": [[7, 8]],
            "extension": {"value": 42},
            "recoverable": 0,
        }
        second = dict(self.original, region_id="sample.png#0001", group_id=8)
        self.dataset.set_hr_records(self.sample, [self.original, second])
        for variant, value in zip(VARIANTS[1:], (1, 1, 2)):
            self.dataset.set_recoverable_many(
                self.sample,
                variant,
                [self.original["region_id"], second["region_id"]],
                value,
            )

        settings = QtCore.QSettings(
            str(self.root / "settings.ini"), QtCore.QSettings.Format.IniFormat
        )
        self.conversion_start = 0
        self.conversion_clockwise = True
        self.conversion_cancel = False
        self.before_conversion_confirm = None
        # Isolate application preferences and model discovery from the test.
        self.patches = [
            patch(
                "anylabeling.services.auto_labeling.model_manager.ModelManager.load_model_configs"
            ),
            patch(
                "anylabeling.views.labeling.label_widget.QtCore.QSettings",
                return_value=settings,
            ),
            patch("anylabeling.views.labeling.label_widget.save_config"),
            patch(
                "anylabeling.views.labeling.settings.controller.save_config",
                return_value=True,
            ),
            patch.object(
                QuadrilateralConversionDialog,
                "exec",
                new=lambda dialog: self.choose_vertex_order(dialog),
            ),
        ]
        for patcher in self.patches:
            patcher.start()
        self.window = QtWidgets.QMainWindow()
        self.widget = LabelingWidget(
            parent=SimpleNamespace(parent=self.window),
            config=load_template_config(),
        )
        self.window.setCentralWidget(self.widget)
        self.window.show()
        self.widget.enter_realisr_mode(self.dataset)
        self.select_first()

    def tearDown(self):
        self.widget.realisr_draft_timer.stop()
        self.widget._settings_controller.flush()
        QtTest.QTest.qWait(150)
        self.widget.realisr_draft_timer.stop()
        self.window.hide()
        self.window.deleteLater()
        self.app.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
        for patcher in reversed(self.patches):
            patcher.stop()
        self.temp.cleanup()

    @property
    def canvas(self):
        return self.widget.realisr_workspace.canvases["HR"]

    def select_first(self):
        self.widget.realisr_workspace.select_region(self.original["region_id"])

    def choose_vertex_order(self, dialog):
        def choose():
            if self.conversion_cancel:
                QtTest.QTest.keyClick(dialog, QtCore.Qt.Key.Key_Escape)
                return
            QtTest.QTest.mouseClick(
                dialog.preview.corner_buttons[self.conversion_start],
                QtCore.Qt.MouseButton.LeftButton,
            )
            button = (
                dialog.clockwise_button
                if self.conversion_clockwise
                else dialog.counterclockwise_button
            )
            QtTest.QTest.mouseClick(button, QtCore.Qt.MouseButton.LeftButton)
            if self.before_conversion_confirm is not None:
                self.before_conversion_confirm()
            QtTest.QTest.mouseClick(
                dialog.confirm_button, QtCore.Qt.MouseButton.LeftButton
            )

        QtCore.QTimer.singleShot(0, choose)
        return _DIALOG_EXEC(dialog)

    def key(self, key, modifiers=QtCore.Qt.KeyboardModifier.NoModifier):
        self.window.activateWindow()
        self.widget.canvas.setFocus()
        self.app.processEvents()
        QtTest.QTest.keyClick(self.widget.canvas, key, modifiers)
        self.app.processEvents()

    def test_quadrilateral_bounds_preserve_precision_metadata_and_selection(
        self,
    ):
        shape = self.canvas.shapes[0]
        untouched = self.canvas.shapes[1].to_dict()
        original = copy.deepcopy(shape.to_dict())
        shape.bounding_rect()  # Populate the cache before changing geometry.
        self.assertTrue(self.widget.convert_realisr_shape("rectangle"))
        self.assertIs(self.canvas.shapes[0], shape)
        self.assertEqual(self.canvas.selected_shapes, [shape])
        self.assertEqual(
            shape.to_dict(),
            dict(
                original,
                shape_type="rectangle",
                points=[
                    (-1.25, 3.25),
                    (23.5, 3.25),
                    (23.5, 22.75),
                    (-1.25, 22.75),
                ],
            ),
        )
        self.assertEqual(self.canvas.shapes[1].to_dict(), untouched)
        self.assertEqual(
            shape.bounding_rect(), QtCore.QRectF(-1.25, 3.25, 24.75, 19.5)
        )
        self.assertFalse(
            self.widget.actions.realisr_convert_to_rectangle.isEnabled()
        )
        self.assertTrue(
            self.widget.actions.realisr_convert_to_quadrilateral.isEnabled()
        )

    def test_reversed_two_and_four_point_rectangles_have_canonical_corners(
        self,
    ):
        for points in (
            [(23.5, 22.75), (-1.25, 3.25)],
            [(23.5, 22.75), (23.5, 3.25), (-1.25, 3.25), (-1.25, 22.75)],
        ):
            with self.subTest(points=points):
                shape = self.canvas.shapes[0]
                shape.shape_type = "rectangle"
                shape.points = [QtCore.QPointF(*point) for point in points]
                self.assertTrue(
                    self.widget.convert_realisr_shape("quadrilateral")
                )
                self.assertEqual(
                    shape.to_dict()["points"],
                    [
                        (-1.25, 3.25),
                        (23.5, 3.25),
                        (23.5, 22.75),
                        (-1.25, 22.75),
                    ],
                )
                self.assertTrue(shape.is_closed())

    def test_chosen_start_and_direction_control_saved_vertex_order(self):
        corners = [(-1.25, 3.25), (23.5, 3.25), (23.5, 22.75), (-1.25, 22.75)]
        for start, clockwise, order in (
            (0, True, [0, 1, 2, 3]),
            (1, True, [1, 2, 3, 0]),
            (2, True, [2, 3, 0, 1]),
            (3, True, [3, 0, 1, 2]),
            (0, False, [0, 3, 2, 1]),
            (1, False, [1, 0, 3, 2]),
            (2, False, [2, 1, 0, 3]),
            (3, False, [3, 2, 1, 0]),
        ):
            with self.subTest(start=start, clockwise=clockwise):
                self.widget.convert_realisr_shape("rectangle")
                self.conversion_start = start
                self.conversion_clockwise = clockwise
                self.assertTrue(
                    self.widget.convert_realisr_shape("quadrilateral")
                )
                expected = [corners[index] for index in order]
                self.assertEqual(
                    self.canvas.shapes[0].to_dict()["points"], expected
                )
                self.assertTrue(self.widget.flush_realisr_draft())
                restored = RealISRDataset(self.root, "text")
                self.assertEqual(
                    restored.records_for(self.sample, "HR")[0]["points"],
                    [list(point) for point in expected],
                )

    def test_cancelling_dialog_keeps_rectangle_selection_and_history(self):
        self.widget.convert_realisr_shape("rectangle")
        self.assertTrue(self.widget.flush_realisr_draft())
        shape = self.canvas.shapes[0]
        original = copy.deepcopy(shape.to_dict())
        history_count = len(self.canvas.shapes_backups)
        group = self.dataset.group(self.sample)
        self.conversion_cancel = True
        self.assertFalse(self.widget.convert_realisr_shape("quadrilateral"))
        self.assertEqual(shape.to_dict(), original)
        self.assertEqual(self.canvas.selected_shapes, [shape])
        self.assertEqual(len(self.canvas.shapes_backups), history_count)
        self.assertEqual(self.dataset.group(self.sample), group)
        self.assertFalse(self.widget._realisr_hr_dirty)

    def test_modal_confirmation_rechecks_target(self):
        self.widget.convert_realisr_shape("rectangle")
        history_count = len(self.canvas.shapes_backups)
        self.before_conversion_confirm = (
            lambda: self.widget.select_realisr_variant("LR2")
        )
        self.assertFalse(self.widget.convert_realisr_shape("quadrilateral"))
        self.assertEqual(self.canvas.shapes[0].shape_type, "rectangle")
        self.assertEqual(len(self.canvas.shapes_backups), history_count)

    def assert_conversion_blocked(self):
        before = repr([shape.to_dict() for shape in self.canvas.shapes])
        history_count = len(self.canvas.shapes_backups)
        self.widget.update_realisr_conversion_action_state()
        for target in ("rectangle", "quadrilateral"):
            self.assertFalse(
                getattr(
                    self.widget.actions, f"realisr_convert_to_{target}"
                ).isEnabled()
            )
            self.assertFalse(self.widget.convert_realisr_shape(target))
        for key in (QtCore.Qt.Key.Key_R, QtCore.Qt.Key.Key_T):
            self.key(key, QtCore.Qt.KeyboardModifier.AltModifier)
        self.assertEqual(
            repr([shape.to_dict() for shape in self.canvas.shapes]), before
        )
        self.assertEqual(len(self.canvas.shapes_backups), history_count)

    def test_zero_multiple_locked_and_wrong_type_are_blocked(self):
        self.widget.realisr_workspace.select_regions([])
        self.assert_conversion_blocked()
        self.widget.realisr_workspace.select_regions(
            [s.other_data["region_id"] for s in self.canvas.shapes]
        )
        self.assert_conversion_blocked()
        self.select_first()
        self.widget._set_shapes_locked([self.canvas.shapes[0]], True)
        self.assert_conversion_blocked()
        self.widget._set_shapes_locked([self.canvas.shapes[0]], False)
        self.canvas.shapes[0].shape_type = "polygon"
        self.assert_conversion_blocked()

    def test_lr_face_and_normal_mode_are_blocked(self):
        for variant in VARIANTS[1:]:
            self.widget.select_realisr_variant(variant)
            self.assert_conversion_blocked()
        self.widget.select_realisr_variant("HR")
        self.dataset.attribute = "face"
        self.assert_conversion_blocked()
        self.dataset.attribute = "text"
        self.assertTrue(self.widget.leave_realisr_mode())
        self.assertFalse(
            self.widget.actions.realisr_convert_to_rectangle.isEnabled()
        )
        self.assert_conversion_blocked()

    def test_drawing_mode_and_incomplete_shape_are_blocked(self):
        self.widget.toggle_draw_mode(False, "quadrilateral")
        self.assert_conversion_blocked()
        self.widget.toggle_draw_mode(True)
        self.select_first()
        self.canvas.current = self.canvas.shapes[0].copy()
        self.assert_conversion_blocked()
        self.canvas.current = None

    def test_invalid_geometry_and_same_type_are_noops(self):
        shape = self.canvas.shapes[0]
        for shape_type, points in (
            ("quadrilateral", [(0, 0)] * 3),
            ("rectangle", [(0, 0)]),
            ("quadrilateral", [(0, 0), (0, 1), (0, 2), (0, 3)]),
            ("rectangle", [(0, 0), (1, 0)]),
            ("quadrilateral", [(0, 0), (1, 0), (float("inf"), 1), (0, 1)]),
            ("rectangle", [(float("nan"), 0), (1, 1)]),
        ):
            with self.subTest(shape_type=shape_type, points=points):
                shape.shape_type = shape_type
                shape.points = [QtCore.QPointF(*point) for point in points]
                # Avoid asking Qt to paint malformed geometry.
                before = repr(shape.to_dict())
                history_count = len(self.canvas.shapes_backups)
                self.widget.update_realisr_conversion_action_state()
                for target in ("rectangle", "quadrilateral"):
                    self.assertFalse(
                        getattr(
                            self.widget.actions, f"realisr_convert_to_{target}"
                        ).isEnabled()
                    )
                    self.assertFalse(self.widget.convert_realisr_shape(target))
                self.assertEqual(repr(shape.to_dict()), before)
                self.assertEqual(
                    len(self.canvas.shapes_backups), history_count
                )
        shape.load_from_dict(self.original)
        self.assertFalse(self.widget.convert_realisr_shape("quadrilateral"))
        self.assertFalse(self.widget.convert_realisr_shape("polygon"))
        self.assertFalse(self.canvas.is_shape_restorable)

    def test_each_conversion_can_be_undone_and_resynchronized(self):
        initial = copy.deepcopy(self.canvas.shapes[0].to_dict())
        self.widget.convert_realisr_shape("rectangle")
        rectangle = copy.deepcopy(self.canvas.shapes[0].to_dict())
        self.widget.convert_realisr_shape("quadrilateral")
        self.assertEqual(len(self.canvas.shapes_backups), 3)
        self.assertTrue(self.widget.flush_realisr_draft())
        self.widget.undo_shape_edit()
        self.assertEqual(self.canvas.shapes[0].to_dict(), rectangle)
        self.widget.undo_shape_edit()
        self.assertEqual(self.canvas.shapes[0].to_dict(), initial)
        self.assertFalse(self.canvas.is_shape_restorable)
        self.assertTrue(self.widget.flush_realisr_draft())
        for variant in VARIANTS:
            record = self.dataset.records_for(self.sample, variant)[0]
            self.assertEqual(record["shape_type"], "quadrilateral")
            expected = (
                initial["points"]
                if variant == "HR"
                else scale_points(
                    initial["points"], self.sizes["HR"], self.sizes[variant]
                )
            )
            self.assertEqual(record["points"], [list(p) for p in expected])

    def test_draft_commit_reopen_and_lr_flags_survive_both_conversions(self):
        self.conversion_start = 2
        self.conversion_clockwise = False
        for target in ("rectangle", "quadrilateral"):
            with self.subTest(target=target):
                self.assertTrue(self.widget.convert_realisr_shape(target))
                self.assertTrue(self.widget.flush_realisr_draft())
                expected = self.dataset.group(self.sample)
                self.assertEqual(
                    RealISRDataset(self.root, "text").group(self.sample),
                    expected,
                )
                for variant, recoverable in zip(VARIANTS, (0, 1, 1, 2)):
                    record = expected[variant][0]
                    self.assertEqual(record["shape_type"], target)
                    self.assertEqual(record["recoverable"], recoverable)
                    for field in (
                        "region_id",
                        "description",
                        "attributes",
                        "flags",
                        "group_id",
                        "extension",
                    ):
                        self.assertEqual(record[field], self.original[field])
                    points = expected["HR"][0]["points"]
                    if variant != "HR":
                        self.assertEqual(
                            record["points"],
                            scale_points(
                                points, self.sizes["HR"], self.sizes[variant]
                            ),
                        )
                    canvas_shape = self.widget.realisr_workspace.canvases[
                        variant
                    ].shapes[0]
                    self.assertEqual(canvas_shape.shape_type, target)
                    self.assertEqual(
                        canvas_shape.points,
                        [QtCore.QPointF(*point) for point in record["points"]],
                    )
                self.assertEqual(
                    self.canvas.selected_shapes, [self.canvas.shapes[0]]
                )
                self.dataset.commit_sample(self.sample)
                reopened = RealISRDataset(self.root, "text")
                self.assertEqual(reopened.group(self.sample), expected)
                for variant in VARIANTS:
                    saved = json.loads(
                        Path(
                            reopened.json_path_for(variant, self.sample)
                        ).read_text()
                    )
                    self.assertEqual(saved["shapes"][0]["shape_type"], target)

    def test_actual_shortcuts_and_runtime_rebinding(self):
        self.key(QtCore.Qt.Key.Key_R, QtCore.Qt.KeyboardModifier.AltModifier)
        self.assertEqual(self.canvas.shapes[0].shape_type, "rectangle")
        self.key(QtCore.Qt.Key.Key_T, QtCore.Qt.KeyboardModifier.AltModifier)
        self.assertEqual(self.canvas.shapes[0].shape_type, "quadrilateral")
        controller = self.widget._settings_controller
        with self.assertRaises(SettingsValidationError):
            controller.update_field(
                "shortcuts.realisr_convert_to_rectangle",
                "Alt+T",
                schedule_save=False,
            )
        controller.update_field(
            "shortcuts.realisr_convert_to_rectangle",
            "Alt+Shift+R",
            schedule_save=False,
        )
        controller.save_now()
        self.key(QtCore.Qt.Key.Key_R, QtCore.Qt.KeyboardModifier.AltModifier)
        self.assertEqual(self.canvas.shapes[0].shape_type, "quadrilateral")
        self.key(
            QtCore.Qt.Key.Key_R,
            QtCore.Qt.KeyboardModifier.AltModifier
            | QtCore.Qt.KeyboardModifier.ShiftModifier,
        )
        self.assertEqual(self.canvas.shapes[0].shape_type, "rectangle")
        for key, mode in (
            (QtCore.Qt.Key.Key_R, "rectangle"),
            (QtCore.Qt.Key.Key_T, "quadrilateral"),
        ):
            self.widget.toggle_draw_mode(True)
            self.key(key)
            self.assertEqual(self.canvas.create_mode, mode)
            self.assertTrue(self.canvas.drawing())
            self.assertFalse(
                self.widget.actions.realisr_convert_to_quadrilateral.isEnabled()
            )


if __name__ == "__main__":
    unittest.main()
