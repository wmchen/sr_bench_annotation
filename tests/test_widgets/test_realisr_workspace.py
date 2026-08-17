import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6 import QtCore, QtGui, QtWidgets

    from anylabeling.views.labeling.shape import Shape
    from anylabeling.views.labeling.widgets.realisr_workspace import (
        RealISRWorkspace,
    )

    PYQT_AVAILABLE = True
except ImportError:
    PYQT_AVAILABLE = False


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 is required for workspace tests")
class RealISRWorkspaceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance()
        if cls.app is None:
            cls.app = QtWidgets.QApplication([])

    def setUp(self):
        options = {
            "epsilon": 10.0,
            "double_click": "close",
            "num_backups": 10,
            "wheel_rectangle_editing": {},
            "auto_highlight_shape": False,
            "attributes": {},
            "rotation": {},
            "mask": {},
            "brush": {},
            "magic_wand": {},
            "cuboid": {},
            "double_click_edit_label": True,
        }
        self.parent = QtWidgets.QWidget()
        self.workspace = RealISRWorkspace(options, self.parent)
        self.workspace.resize(800, 600)
        self.workspace.show()

        pixmaps, shapes = self.make_group("000001.png#0000")
        self.workspace.load_group(pixmaps, shapes)
        self.app.processEvents()
        self.workspace.fit_canvases()
        self.app.processEvents()

    @staticmethod
    def make_group(region_id, include_shapes=True):
        sizes = {
            "HR": (400, 240),
            "LR2": (200, 120),
            "LR3": (133, 80),
            "LR4": (100, 60),
        }
        pixmaps = {}
        shapes = {}
        for variant, (width, height) in sizes.items():
            pixmap = QtGui.QPixmap(width, height)
            pixmap.fill(QtGui.QColor("white"))
            pixmaps[variant] = pixmap
            if not include_shapes:
                shapes[variant] = []
                continue
            shape = Shape(
                label="text",
                description="truth",
                shape_type="quadrilateral",
            )
            shape.points = [
                QtCore.QPointF(1, 1),
                QtCore.QPointF(width - 1, 1),
                QtCore.QPointF(width - 1, height - 1),
                QtCore.QPointF(1, height - 1),
            ]
            shape.close()
            shape.other_data = {
                "region_id": region_id,
                "recoverable": None if variant != "HR" else 0,
                "realisr_text": shape.description,
            }
            if variant != "HR":
                shape.label = ""
                shape.description = ""
                shape.locked = True
            shapes[variant] = [shape]
        return pixmaps, shapes

    def tearDown(self):
        self.workspace.close()
        self.parent.close()

    def test_four_panes_have_equal_display_width(self):
        widths = [canvas.width() for canvas in self.workspace.canvases.values()]
        self.assertLessEqual(max(widths) - min(widths), 1)

    def test_activation_and_selection_are_synchronized(self):
        self.workspace.set_active_variant("LR3")
        self.workspace.select_region("000001.png#0000", notify=False)
        self.assertEqual(self.workspace.active_variant, "LR3")
        for canvas in self.workspace.canvases.values():
            self.assertEqual(len(canvas.selected_shapes), 1)

    def test_lr_truth_is_revealed_only_in_active_pane(self):
        self.workspace.set_active_variant("LR2")
        self.workspace.set_lr_text_revealed(True)
        self.assertEqual(
            self.workspace.canvases["LR2"].shapes[0].label, "truth"
        )
        self.assertEqual(
            self.workspace.canvases["LR3"].shapes[0].label,
            "",
        )
        self.workspace.set_lr_text_revealed(False)
        self.assertEqual(
            self.workspace.canvases["LR2"].shapes[0].label,
            "",
        )
        for variant in ("LR2", "LR3", "LR4"):
            self.assertEqual(
                self.workspace.canvases[variant].shapes[0].description,
                "",
            )

    def test_lr_canvases_are_read_only_and_shapes_are_locked(self):
        for variant in ("LR2", "LR3", "LR4"):
            canvas = self.workspace.canvases[variant]
            self.assertTrue(canvas.realisr_read_only)
            self.assertTrue(canvas.shapes[0].locked)

    def test_crosshair_is_visible_only_while_drawing(self):
        for canvas in self.workspace.canvases.values():
            self.assertTrue(canvas.editing())
            self.assertFalse(canvas.cross_line_show)

        canvas = self.workspace.canvases["HR"]
        canvas.set_editing(False)
        self.assertTrue(canvas.drawing())
        self.assertTrue(canvas.cross_line_show)

        canvas.set_editing(True)
        self.assertFalse(canvas.cross_line_show)

    def test_disabled_crosshair_stays_hidden_while_drawing(self):
        canvas = self.workspace.canvases["HR"]
        canvas.set_cross_line(False, 2.0, "#00FF00", 0.5)

        canvas.set_editing(False)

        self.assertTrue(canvas.drawing())
        self.assertFalse(canvas.cross_line_show)

    def test_dragging_over_lr_shape_emits_normalized_pan(self):
        canvas = self.workspace.canvases["LR2"]
        requests = []
        canvas.scroll_request.connect(
            lambda delta, orientation, mode: requests.append(
                (delta, orientation, mode)
            )
        )

        class MoveEvent:
            def position(self):
                return QtCore.QPointF(15, 18)

            def buttons(self):
                return QtCore.Qt.MouseButton.LeftButton

            def accept(self):
                pass

        canvas._realisr_pan_position = QtCore.QPointF(10, 10)
        canvas.mouseMoveEvent(MoveEvent())
        self.assertEqual(len(requests), 2)
        self.assertTrue(all(request[2] == 1 for request in requests))

    def test_loading_group_isolates_undo_history_for_every_canvas(self):
        pixmaps, shapes = self.make_group("000002.png#0000")
        self.workspace.load_group(pixmaps, shapes)

        for canvas in self.workspace.canvases.values():
            self.assertEqual(len(canvas.shapes_backups), 1)
            self.assertFalse(canvas.is_shape_restorable)
            self.assertEqual(
                [shape.other_data["region_id"] for shape in canvas.shapes],
                ["000002.png#0000"],
            )

            canvas.restore_shape()
            self.assertEqual(
                [shape.other_data["region_id"] for shape in canvas.shapes],
                ["000002.png#0000"],
            )

    def test_undo_after_edit_restores_current_group_baseline(self):
        pixmaps, shapes = self.make_group("000002.png#0000")
        self.workspace.load_group(pixmaps, shapes)
        canvas = self.workspace.canvases["HR"]
        original_point = QtCore.QPointF(canvas.shapes[0].points[0])

        canvas.shapes[0].points[0] = QtCore.QPointF(20, 20)
        canvas.store_shapes()

        self.assertTrue(canvas.is_shape_restorable)
        canvas.restore_shape()
        self.assertEqual(canvas.shapes[0].points[0], original_point)
        self.assertEqual(
            canvas.shapes[0].other_data["region_id"],
            "000002.png#0000",
        )

    def test_loading_empty_group_clears_stale_selection(self):
        self.workspace.select_region("000001.png#0000", notify=False)
        self.assertTrue(
            all(
                canvas.selected_shapes
                for canvas in self.workspace.canvases.values()
            )
        )
        pixmaps, shapes = self.make_group(
            "unused-region", include_shapes=False
        )

        self.workspace.load_group(pixmaps, shapes)

        for canvas in self.workspace.canvases.values():
            self.assertEqual(canvas.shapes, [])
            self.assertEqual(canvas.selected_shapes, [])
            self.assertEqual(len(canvas.shapes_backups), 1)
            self.assertFalse(canvas.is_shape_restorable)


if __name__ == "__main__":
    unittest.main()
