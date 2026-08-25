import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6 import QtCore, QtWidgets

    from anylabeling.views.labeling.label_widget import LabelingWidget
    from anylabeling.views.labeling.shape import Shape
    from anylabeling.services.auto_labeling.types import AutoLabelingResult

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
        with (
            patch.object(
                QtWidgets.QInputDialog,
                "getItem",
                side_effect=select_attribute,
            ),
            patch.object(
                QtWidgets.QFileDialog,
                "getExistingDirectory",
                side_effect=select_directory,
            ),
            patch(
                "anylabeling.views.labeling.label_widget.RealISRDataset",
                return_value=dataset,
            ) as dataset_class,
        ):
            LabelingWidget.open_realisr_folder_dialog(widget)

        self.assertEqual(call_order, ["attribute", "directory"])
        dataset_class.assert_called_once_with("/dataset", "face")
        widget.enter_realisr_mode.assert_called_once_with(dataset)

    def test_canceling_attribute_does_not_open_directory(self):
        widget = self.make_open_widget()
        with (
            patch.object(
                QtWidgets.QInputDialog,
                "getItem",
                return_value=("Text", False),
            ),
            patch.object(
                QtWidgets.QFileDialog, "getExistingDirectory"
            ) as directory_dialog,
        ):
            LabelingWidget.open_realisr_folder_dialog(widget)
        directory_dialog.assert_not_called()
        widget.enter_realisr_mode.assert_not_called()

    def test_canceling_directory_does_not_create_dataset(self):
        widget = self.make_open_widget()
        with (
            patch.object(
                QtWidgets.QInputDialog,
                "getItem",
                return_value=("Text", True),
            ),
            patch.object(
                QtWidgets.QFileDialog,
                "getExistingDirectory",
                return_value="",
            ),
            patch(
                "anylabeling.views.labeling.label_widget.RealISRDataset"
            ) as dataset_class,
        ):
            LabelingWidget.open_realisr_folder_dialog(widget)
        dataset_class.assert_not_called()
        widget.enter_realisr_mode.assert_not_called()

    def test_focus_selected_object_is_realisr_only(self):
        workspace = SimpleNamespace(
            focus_selected_object=Mock(return_value=True)
        )
        widget = SimpleNamespace(
            realisr_mode=False,
            realisr_workspace=workspace,
        )

        self.assertFalse(LabelingWidget.focus_selected_realisr_object(widget))
        workspace.focus_selected_object.assert_not_called()

        widget.realisr_mode = True
        self.assertTrue(LabelingWidget.focus_selected_realisr_object(widget))
        workspace.focus_selected_object.assert_called_once_with()

    def test_focus_action_state_requires_focusable_realisr_selection(self):
        action = SimpleNamespace(setEnabled=Mock())
        workspace = SimpleNamespace(
            can_focus_selected_object=Mock(return_value=True)
        )
        widget = SimpleNamespace(
            realisr_mode=False,
            realisr_workspace=workspace,
            actions=SimpleNamespace(focus_selected_object=action),
        )

        LabelingWidget.update_realisr_focus_action_state(widget)
        action.setEnabled.assert_called_with(False)

        widget.realisr_mode = True
        LabelingWidget.update_realisr_focus_action_state(widget)
        action.setEnabled.assert_called_with(True)

    def test_variant_switch_flushes_only_when_hr_is_dirty(self):
        def make_widget(hr_dirty):
            canvas = SimpleNamespace()
            workspace = SimpleNamespace(
                canvases={"LR2": canvas},
                scroll_areas={"LR2": object()},
                scroll_bars={"LR2": {}},
                images={},
                set_active_variant=Mock(),
            )
            return SimpleNamespace(
                realisr_mode=True,
                realisr_variant="HR",
                _realisr_loading=False,
                _realisr_hr_dirty=hr_dirty,
                flush_realisr_draft=Mock(return_value=True),
                realisr_workspace=workspace,
                label_list=SimpleNamespace(canvas=None),
                realisr_sample=None,
                realisr_dataset=SimpleNamespace(),
                refresh_realisr_active_label_list=Mock(),
                refresh_realisr_label_display=Mock(),
                apply_realisr_action_state=Mock(),
                update_realisr_ui=Mock(),
                update_progress_title=Mock(),
            )

        clean = make_widget(False)
        LabelingWidget.activate_realisr_variant(clean, "LR2")
        clean.flush_realisr_draft.assert_not_called()

        dirty = make_widget(True)
        LabelingWidget.activate_realisr_variant(dirty, "LR2")
        dirty.flush_realisr_draft.assert_called_once_with()

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

    def test_face_auto_labeling_filters_landmarks(self):
        rectangle = Shape(label="face", shape_type="rectangle")
        for x, y in ((1, 2), (9, 2), (9, 8), (1, 8)):
            rectangle.add_point(QtCore.QPointF(x, y))
        landmark = Shape(label="left_eye", shape_type="point")
        landmark.add_point(QtCore.QPointF(3, 4))
        widget = SimpleNamespace(
            _is_horizontal_rectangle_shape=(
                LabelingWidget._is_horizontal_rectangle_shape
            )
        )

        shapes = LabelingWidget._normalize_realisr_auto_shapes(
            widget, [rectangle, landmark], "face"
        )

        self.assertEqual(len(shapes), 1)
        self.assertEqual(shapes[0].label, "face")
        self.assertEqual(shapes[0].description, "")
        self.assertEqual(shapes[0].other_data["recoverable"], 0)

    def test_instance_result_overwrites_only_description(self):
        target = Shape(
            label="text",
            description="old text",
            shape_type="quadrilateral",
        )
        for x, y in ((1, 2), (9, 2), (9, 8), (1, 8)):
            target.add_point(QtCore.QPointF(x, y))
        target.other_data = {
            "region_id": "sample.png#0000",
            "recoverable": 2,
            "extension": "keep",
        }
        other = Shape(
            label="text",
            description="untouched",
            shape_type="quadrilateral",
        )
        for x, y in ((11, 12), (19, 12), (19, 18), (11, 18)):
            other.add_point(QtCore.QPointF(x, y))
        other.other_data = {
            "region_id": "sample.png#0001",
            "recoverable": 1,
        }
        canvas = SimpleNamespace(shapes=[target, other])
        workspace = SimpleNamespace(
            canvases={"HR": canvas},
            select_region=Mock(),
        )
        loaded = []

        def load_shapes(shapes, **_kwargs):
            loaded[:] = shapes
            canvas.shapes = list(shapes)

        widget = SimpleNamespace(
            _realisr_result_matches_request=Mock(return_value=True),
            realisr_workspace=workspace,
            _realisr_region_id=lambda shape: shape.other_data.get("region_id"),
            label_list=SimpleNamespace(clear=Mock()),
            load_shapes=Mock(side_effect=load_shapes),
            set_dirty=Mock(),
            flush_realisr_draft=Mock(return_value=True),
            refresh_realisr_active_label_list=Mock(),
            apply_realisr_action_state=Mock(),
            update_realisr_ui=Mock(),
        )
        result = AutoLabelingResult([SimpleNamespace(description="new text")])
        context = {
            "mode": "instance",
            "target_region_id": "sample.png#0000",
        }

        LabelingWidget._apply_realisr_auto_labeling_result(
            widget, result, context
        )

        self.assertEqual(loaded[0].description, "new text")
        self.assertEqual(loaded[0].points, target.points)
        self.assertEqual(loaded[0].other_data, target.other_data)
        self.assertEqual(loaded[1].description, "untouched")
        workspace.select_region.assert_called_once_with(
            "sample.png#0000", notify=False
        )

    def test_realisr_stale_result_is_rejected_after_sample_change(self):
        widget = SimpleNamespace(
            realisr_mode=True,
            realisr_dataset=SimpleNamespace(attribute="text"),
            realisr_variant="HR",
            realisr_sample="current.png",
            filename="/dataset/HR/current.png",
        )
        result = AutoLabelingResult([], image_path="/dataset/HR/previous.png")
        context = {
            "task": "text",
            "sample": "previous.png",
            "image_path": "/dataset/HR/previous.png",
        }

        self.assertFalse(
            LabelingWidget._realisr_result_matches_request(
                widget, result, context
            )
        )


if __name__ == "__main__":
    unittest.main()
