import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6 import QtWidgets

    from anylabeling.views.labeling.label_widget import LabelingWidget

    PYQT_AVAILABLE = True
except Exception:
    PYQT_AVAILABLE = False


@unittest.skipUnless(
    PYQT_AVAILABLE, "PyQt6 is required for Real-ISR label widget tests"
)
class RealISRLabelWidgetTest(unittest.TestCase):
    def make_open_widget(self):
        return SimpleNamespace(
            may_continue=Mock(return_value=True),
            tr=lambda text: text,
            settings=SimpleNamespace(value=Mock(return_value=".")),
            error_message=Mock(),
            enter_realisr_mode=Mock(),
        )

    def test_open_selects_attribute_before_directory(self):
        widget = self.make_open_widget()
        call_order = []

        def select_attribute(*_args, **_kwargs):
            call_order.append("attribute")
            return "Face", True

        def select_directory(*_args, **_kwargs):
            call_order.append("directory")
            return "/dataset"

        dataset = SimpleNamespace(attribute="face")
        with patch.object(
            QtWidgets.QInputDialog,
            "getItem",
            side_effect=select_attribute,
        ), patch.object(
            QtWidgets.QFileDialog,
            "getExistingDirectory",
            side_effect=select_directory,
        ), patch(
            "anylabeling.views.labeling.label_widget.RealISRDataset",
            return_value=dataset,
        ) as dataset_class:
            LabelingWidget.open_realisr_folder_dialog(widget)

        self.assertEqual(call_order, ["attribute", "directory"])
        dataset_class.assert_called_once_with("/dataset", "face")
        widget.enter_realisr_mode.assert_called_once_with(dataset)

    def test_canceling_attribute_does_not_open_directory(self):
        widget = self.make_open_widget()
        with patch.object(
            QtWidgets.QInputDialog,
            "getItem",
            return_value=("Text", False),
        ), patch.object(
            QtWidgets.QFileDialog, "getExistingDirectory"
        ) as directory_dialog:
            LabelingWidget.open_realisr_folder_dialog(widget)
        directory_dialog.assert_not_called()
        widget.enter_realisr_mode.assert_not_called()

    def test_canceling_directory_does_not_create_dataset(self):
        widget = self.make_open_widget()
        with patch.object(
            QtWidgets.QInputDialog,
            "getItem",
            return_value=("Text", True),
        ), patch.object(
            QtWidgets.QFileDialog,
            "getExistingDirectory",
            return_value="",
        ), patch(
            "anylabeling.views.labeling.label_widget.RealISRDataset"
        ) as dataset_class:
            LabelingWidget.open_realisr_folder_dialog(widget)
        dataset_class.assert_not_called()
        widget.enter_realisr_mode.assert_not_called()

    def test_face_new_shape_uses_fixed_label_without_popup(self):
        draft_shape = SimpleNamespace(shape_type="rectangle")
        final_shape = SimpleNamespace(
            label="",
            description="should be cleared",
            group_id=7,
            difficult=True,
            kie_linking=[1],
            other_data={},
        )
        canvas = SimpleNamespace(
            shapes=[draft_shape],
            set_last_label=Mock(return_value=final_shape),
            undo_last_line=Mock(),
        )
        widget = SimpleNamespace(
            realisr_mode=True,
            realisr_variant="HR",
            realisr_dataset=SimpleNamespace(attribute="face"),
            canvas=canvas,
            label_list=SimpleNamespace(clearSelection=Mock()),
            label_dialog=SimpleNamespace(pop_up=Mock()),
            add_label=Mock(),
            apply_realisr_shape_color=Mock(),
            set_dirty=Mock(),
            actions=SimpleNamespace(
                edit_mode=Mock(),
                undo_last_point=Mock(),
                undo=Mock(),
            ),
        )

        LabelingWidget.new_shape(widget)

        canvas.set_last_label.assert_called_once_with("face", {}, None)
        widget.label_dialog.pop_up.assert_not_called()
        self.assertEqual(final_shape.label, "face")
        self.assertEqual(final_shape.description, "")
        self.assertEqual(final_shape.group_id, None)
        self.assertEqual(final_shape.other_data["recoverable"], 0)
        widget.add_label.assert_called_once_with(final_shape)
        widget.set_dirty.assert_called_once_with()

    def test_face_mode_rejects_quadrilateral_draw_mode(self):
        canvas = SimpleNamespace(set_editing=Mock())
        widget = SimpleNamespace(
            realisr_mode=True,
            realisr_variant="HR",
            realisr_dataset=SimpleNamespace(attribute="face"),
            canvas=canvas,
        )
        LabelingWidget.toggle_draw_mode(
            widget, edit=False, create_mode="quadrilateral"
        )
        canvas.set_editing.assert_not_called()


if __name__ == "__main__":
    unittest.main()
