import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtCore, QtTest, QtWidgets

from anylabeling.views.labeling.widgets.quadrilateral_conversion_dialog import (
    QuadrilateralConversionDialog,
)


class QuadrilateralConversionDialogTest(unittest.TestCase):
    def setUp(self):
        self.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(
            []
        )
        self.dialog = QuadrilateralConversionDialog()
        self.dialog.show()
        self.app.processEvents()

    def tearDown(self):
        self.dialog.close()
        self.dialog.deleteLater()
        self.app.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)

    def test_a_corner_must_be_selected_before_confirmation(self):
        self.assertEqual(self.dialog.vertex_order(), [])
        self.assertFalse(self.dialog.confirm_button.isEnabled())
        self.dialog.accept()
        self.assertTrue(self.dialog.isVisible())
        QtTest.QTest.keyClick(self.dialog, QtCore.Qt.Key.Key_Return)
        self.assertTrue(self.dialog.isVisible())
        self.dialog.counterclockwise_button.click()
        self.assertFalse(self.dialog.confirm_button.isEnabled())
        QtTest.QTest.keyClick(self.dialog, QtCore.Qt.Key.Key_Escape)
        self.assertEqual(
            self.dialog.result(), QtWidgets.QDialog.DialogCode.Rejected
        )
        self.assertFalse(self.dialog.isVisible())

    def test_clicking_diagram_points_updates_order_without_moving_points(self):
        preview = self.dialog.preview
        positions = [button.pos() for button in preview.corner_buttons]
        QtTest.QTest.mouseClick(
            preview.corner_buttons[2], QtCore.Qt.MouseButton.LeftButton
        )
        self.assertTrue(self.dialog.confirm_button.isEnabled())
        self.assertEqual(self.dialog.vertex_order(), [2, 3, 0, 1])
        self.assertEqual(
            [button.text() for button in preview.corner_buttons],
            ["3", "4", "1", "2"],
        )
        QtTest.QTest.mouseClick(
            self.dialog.counterclockwise_button,
            QtCore.Qt.MouseButton.LeftButton,
        )
        self.assertEqual(self.dialog.vertex_order(), [2, 1, 0, 3])
        self.assertEqual(
            [button.text() for button in preview.corner_buttons],
            ["3", "2", "1", "4"],
        )
        QtTest.QTest.mouseClick(
            preview.corner_buttons[1], QtCore.Qt.MouseButton.LeftButton
        )
        self.assertEqual(self.dialog.vertex_order(), [1, 0, 3, 2])
        self.assertEqual(
            [button.isChecked() for button in preview.corner_buttons],
            [False, True, False, False],
        )
        self.assertEqual(
            [button.pos() for button in preview.corner_buttons], positions
        )
        self.dialog.confirm_button.click()
        self.assertEqual(
            self.dialog.result(), QtWidgets.QDialog.DialogCode.Accepted
        )

    def test_cancel_after_choosing_does_not_accept(self):
        self.dialog.preview.corner_buttons[3].click()
        self.dialog.cancel_button.click()
        self.assertEqual(
            self.dialog.result(), QtWidgets.QDialog.DialogCode.Rejected
        )


if __name__ == "__main__":
    unittest.main()
