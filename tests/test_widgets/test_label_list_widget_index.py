import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from anylabeling.views.labeling.shape import Shape
from anylabeling.views.labeling.widgets.label_list_widget import (
    LabelListWidget,
    LabelListWidgetItem,
)


class TestLabelListWidgetIndex(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(
            []
        )

    def test_index_tracks_add_remove_and_clear(self):
        widget = LabelListWidget()
        first = Shape(label="first")
        second = Shape(label="second")
        first_item = LabelListWidgetItem("first", first)
        second_item = LabelListWidgetItem("second", second)

        widget.add_iem(first_item)
        widget.add_iem(second_item)
        self.assertIs(widget.find_item_by_shape(first), first_item)
        self.assertIs(widget.find_item_by_shape(second), second_item)

        widget.remove_item(first_item)
        self.assertIsNone(widget.find_item_by_shape(first))
        self.assertIs(widget.find_item_by_shape(second), second_item)

        widget.clear()
        self.assertIsNone(widget.find_item_by_shape(second))


if __name__ == "__main__":
    unittest.main()
