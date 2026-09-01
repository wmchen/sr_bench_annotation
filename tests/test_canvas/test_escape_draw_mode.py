import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6 import QtCore, QtGui, QtWidgets

    from anylabeling.views.labeling.shape import Shape
    from anylabeling.views.labeling.widgets.canvas import Canvas

    PYQT_AVAILABLE = True
except Exception:
    PYQT_AVAILABLE = False


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 is required for canvas tests")
class TestCanvasEscapeDrawMode(unittest.TestCase):

    def setUp(self):
        self.app = QtWidgets.QApplication.instance()
        if self.app is None:
            self.app = QtWidgets.QApplication([])
        self.canvas = Canvas(parent=None)
        self.canvas.set_editing(False)
        self.edit_mode_requests = []
        self.canvas.edit_mode_requested.connect(
            lambda: self.edit_mode_requests.append(True)
        )

    def tearDown(self):
        self.canvas.close()
        self.app.processEvents()

    @staticmethod
    def escape_event(auto_repeat=False):
        return QtGui.QKeyEvent(
            QtCore.QEvent.Type.KeyPress,
            QtCore.Qt.Key.Key_Escape,
            QtCore.Qt.KeyboardModifier.NoModifier,
            "",
            auto_repeat,
            1,
        )

    def test_escape_requests_edit_mode_when_drawing_is_idle(self):
        event = self.escape_event()

        self.canvas.keyPressEvent(event)

        self.assertTrue(event.isAccepted())
        self.assertEqual(self.edit_mode_requests, [True])

    def test_escape_cancels_current_shape_without_exiting_draw_mode(self):
        self.canvas.current = Shape(shape_type="rectangle")
        self.canvas.current.add_point(QtCore.QPointF(10, 10))
        drawing_states = []
        self.canvas.drawing_polygon.connect(drawing_states.append)
        event = self.escape_event()

        self.canvas.keyPressEvent(event)

        self.assertTrue(event.isAccepted())
        self.assertIsNone(self.canvas.current)
        self.assertTrue(self.canvas.drawing())
        self.assertEqual(drawing_states, [False])
        self.assertEqual(self.edit_mode_requests, [])

    def test_escape_auto_repeat_does_not_exit_after_canceling_shape(self):
        self.canvas.current = Shape(shape_type="polygon")
        self.canvas.current.add_point(QtCore.QPointF(10, 10))

        self.canvas.keyPressEvent(self.escape_event())
        repeated_event = self.escape_event(auto_repeat=True)
        self.canvas.keyPressEvent(repeated_event)

        self.assertTrue(repeated_event.isAccepted())
        self.assertIsNone(self.canvas.current)
        self.assertEqual(self.edit_mode_requests, [])

    def test_escape_does_not_exit_auto_labeling_mode(self):
        self.canvas.is_auto_labeling = True
        event = self.escape_event()

        self.canvas.keyPressEvent(event)

        self.assertTrue(event.isAccepted())
        self.assertEqual(self.edit_mode_requests, [])


if __name__ == "__main__":
    unittest.main()
