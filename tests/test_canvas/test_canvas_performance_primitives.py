import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtCore, QtGui, QtWidgets

from anylabeling.views.labeling.shape import Shape
from anylabeling.views.labeling.widgets.canvas import Canvas


class TestCanvasPerformancePrimitives(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(
            []
        )

    def setUp(self):
        self.canvas = Canvas(parent=object())
        pixmap = QtGui.QPixmap(400, 300)
        pixmap.fill(QtGui.QColor("black"))
        self.canvas.load_pixmap(pixmap)

    @staticmethod
    def rectangle(label, x):
        shape = Shape(label=label, shape_type="rectangle")
        shape.points = [
            QtCore.QPointF(x, 10),
            QtCore.QPointF(x + 20, 10),
            QtCore.QPointF(x + 20, 30),
            QtCore.QPointF(x, 30),
        ]
        shape.close()
        return shape

    def test_geometry_cache_detects_in_place_point_mutation(self):
        shape = self.rectangle("box", 10)
        original = shape.bounding_rect()

        shape.points[2].setX(80)

        updated = shape.bounding_rect()
        self.assertEqual(original.right(), 30)
        self.assertEqual(updated.right(), 80)

    def test_hit_test_rejects_far_shape_before_exact_geometry(self):
        shape = self.rectangle("box", 10)
        self.canvas.shapes = [shape]

        with (
            mock.patch.object(shape, "nearest_vertex") as nearest,
            mock.patch.object(shape, "contains_point") as contains,
        ):
            self.assertEqual(
                self.canvas._shape_hit_candidates(QtCore.QPointF(300, 250)), []
            )

        nearest.assert_not_called()
        contains.assert_not_called()

    def test_history_copies_only_changed_shape_and_restores_it(self):
        shapes = [self.rectangle(str(index), index * 40) for index in range(3)]
        self.canvas.shapes = shapes
        self.canvas.store_shapes()
        unchanged = (shapes[0], shapes[2])

        shapes[1].move_by(QtCore.QPointF(7, 0))
        self.assertTrue(self.canvas.store_shapes())
        command = self.canvas._history_commands[-1]

        self.assertEqual(len(command["before"]), 1)
        self.assertEqual(command["added"], set())
        self.canvas.restore_shape()
        self.assertEqual(self.canvas.shapes[1].points[0].x(), 40)
        self.assertIs(self.canvas.shapes[0], unchanged[0])
        self.assertIs(self.canvas.shapes[2], unchanged[1])

    def test_history_restores_add_delete_and_order(self):
        first = self.rectangle("first", 10)
        second = self.rectangle("second", 50)
        self.canvas.shapes = [first, second]
        self.canvas.store_shapes()

        added = self.rectangle("added", 90)
        self.canvas.shapes.append(added)
        self.canvas.store_shapes()
        self.canvas.shapes.remove(first)
        self.canvas.store_shapes()

        self.canvas.restore_shape()
        self.assertEqual(
            [shape.label for shape in self.canvas.shapes],
            ["first", "second", "added"],
        )
        self.canvas.restore_shape()
        self.assertEqual(
            [shape.label for shape in self.canvas.shapes], ["first", "second"]
        )

    def test_empty_baseline_can_undo_first_shape(self):
        self.canvas.shapes = []
        self.canvas.store_shapes()
        self.canvas.shapes.append(self.rectangle("first", 10))
        self.canvas.store_shapes()

        self.assertTrue(self.canvas.is_shape_restorable)
        self.canvas.restore_shape()
        self.assertEqual(self.canvas.shapes, [])


if __name__ == "__main__":
    unittest.main()
