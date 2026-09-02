import copy
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6 import QtCore, QtWidgets

    from anylabeling.views.labeling.label_widget import LabelingWidget
    from anylabeling.views.labeling.shape import Shape
    from anylabeling.views.labeling.widgets.realisr_workspace import (
        RealISRWorkspace,
    )
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

    def test_redundant_draft_warning_lists_samples_and_recovery_action(self):
        widget = SimpleNamespace(tr=lambda text: text)
        samples = ["000004.png", "000017.png"]

        with patch.object(QtWidgets.QMessageBox, "warning") as warning:
            LabelingWidget.warn_redundant_realisr_drafts(widget, samples)

        warning.assert_called_once_with(
            widget,
            "Redundant Real-ISR drafts",
            "These drafts are identical to their formal annotations:\n"
            "000004.png\n000017.png\n\n"
            "Open each corresponding image and save it again to clear the "
            "redundant draft.",
        )

    def test_redundant_draft_warning_is_skipped_when_empty(self):
        widget = SimpleNamespace(tr=lambda text: text)
        with patch.object(QtWidgets.QMessageBox, "warning") as warning:
            LabelingWidget.warn_redundant_realisr_drafts(widget, [])
        warning.assert_not_called()

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

    def test_multiview_button_tracks_workspace_mode(self):
        button = SimpleNamespace(setEnabled=Mock())
        widget = SimpleNamespace(
            realisr_mode=False,
            realisr_multiview_button=button,
        )

        LabelingWidget.update_realisr_multiview_button(widget, True)
        button.setEnabled.assert_called_with(False)

        widget.realisr_mode = True
        LabelingWidget.update_realisr_multiview_button(widget, True)
        button.setEnabled.assert_called_with(True)
        LabelingWidget.update_realisr_multiview_button(widget, False)
        button.setEnabled.assert_called_with(False)

    def test_restore_multiview_is_realisr_only(self):
        workspace = SimpleNamespace(show_tiled_views=Mock(return_value=True))
        widget = SimpleNamespace(
            realisr_mode=False,
            realisr_workspace=workspace,
        )

        self.assertFalse(LabelingWidget.restore_realisr_multiview(widget))
        workspace.show_tiled_views.assert_not_called()

        widget.realisr_mode = True
        self.assertTrue(LabelingWidget.restore_realisr_multiview(widget))
        workspace.show_tiled_views.assert_called_once_with()

    def test_variant_buttons_switch_workspace_in_realisr_mode(self):
        workspace = SimpleNamespace(
            active_variant="HR",
            set_active_variant=Mock(),
        )
        buttons = {
            variant: SimpleNamespace(setChecked=Mock())
            for variant in ("HR", "LR2", "LR3", "LR4")
        }
        widget = SimpleNamespace(
            realisr_mode=False,
            realisr_workspace=workspace,
            realisr_variant_buttons=buttons,
            update_realisr_variant_buttons=Mock(),
        )

        self.assertFalse(LabelingWidget.select_realisr_variant(widget, "LR3"))
        workspace.set_active_variant.assert_not_called()

        widget.realisr_mode = True
        self.assertTrue(LabelingWidget.select_realisr_variant(widget, "LR3"))
        workspace.set_active_variant.assert_called_once_with("LR3")
        widget.update_realisr_variant_buttons.assert_called_once_with()

    def test_variant_button_highlight_matches_workspace_active_variant(self):
        buttons = {
            variant: SimpleNamespace(setChecked=Mock())
            for variant in ("HR", "LR2", "LR3", "LR4")
        }
        widget = SimpleNamespace(
            realisr_workspace=SimpleNamespace(active_variant="LR3"),
            realisr_variant_buttons=buttons,
        )

        LabelingWidget.update_realisr_variant_buttons(widget)

        for variant, button in buttons.items():
            button.setChecked.assert_called_once_with(variant == "LR3")

    def test_recoverability_wraps_to_next_missing_shape_and_focuses_it(self):
        next_shape = SimpleNamespace(
            other_data={"region_id": "region-next", "recoverable": None}
        )
        current_shape = SimpleNamespace(
            other_data={"region_id": "region-current", "recoverable": None}
        )
        workspace = Mock()
        widget = SimpleNamespace(
            realisr_mode=True,
            realisr_variant="LR2",
            realisr_sample="sample.png",
            canvas=SimpleNamespace(
                shapes=[next_shape, current_shape],
                selected_shapes=[current_shape],
            ),
            realisr_dataset=SimpleNamespace(
                set_recoverable=Mock(return_value=True)
            ),
            realisr_workspace=workspace,
            _realisr_region_id=lambda shape: shape.other_data.get("region_id"),
            _realisr_recoverable=lambda shape: shape.other_data.get(
                "recoverable"
            ),
            apply_realisr_shape_color=Mock(),
            dirty=False,
            _realisr_draft_dirty=False,
            actions=SimpleNamespace(save=SimpleNamespace(setEnabled=Mock())),
            realisr_draft_timer=SimpleNamespace(start=Mock()),
            update_realisr_ui=Mock(),
            refresh_realisr_file_item=Mock(),
        )

        LabelingWidget.set_realisr_recoverable(widget, 1)

        self.assertEqual(current_shape.other_data["recoverable"], 1)
        self.assertEqual(
            workspace.method_calls,
            [
                call.select_region("region-next"),
                call.focus_selected_object(),
            ],
        )

    def test_recoverability_does_not_focus_when_advancing_lr_variant(self):
        current_shape = SimpleNamespace(
            other_data={"region_id": "region-current", "recoverable": None}
        )
        workspace = Mock()
        widget = SimpleNamespace(
            realisr_mode=True,
            realisr_variant="LR2",
            realisr_sample="sample.png",
            canvas=SimpleNamespace(
                shapes=[current_shape],
                selected_shapes=[current_shape],
            ),
            realisr_dataset=SimpleNamespace(
                set_recoverable=Mock(return_value=True)
            ),
            realisr_workspace=workspace,
            _realisr_region_id=lambda shape: shape.other_data.get("region_id"),
            _realisr_recoverable=lambda shape: shape.other_data.get(
                "recoverable"
            ),
            apply_realisr_shape_color=Mock(),
            dirty=False,
            _realisr_draft_dirty=False,
            actions=SimpleNamespace(save=SimpleNamespace(setEnabled=Mock())),
            realisr_draft_timer=SimpleNamespace(start=Mock()),
            update_realisr_ui=Mock(),
            refresh_realisr_file_item=Mock(),
            advance_realisr_variant=Mock(),
        )

        with patch.object(QtCore.QTimer, "singleShot") as single_shot:
            LabelingWidget.set_realisr_recoverable(widget, 1)

        workspace.focus_selected_object.assert_not_called()
        single_shot.assert_called_once()

    def test_next_uncommitted_sample_skips_committed_without_wrapping(self):
        committed = {"000002.png", "000003.png", "000005.png"}
        widget = SimpleNamespace(
            realisr_dataset=SimpleNamespace(
                samples=[
                    "000001.png",
                    "000002.png",
                    "000003.png",
                    "000004.png",
                    "000005.png",
                ],
                is_committed=lambda sample: sample in committed,
            )
        )

        self.assertEqual(
            LabelingWidget._next_uncommitted_realisr_sample(widget, 0),
            "000004.png",
        )
        self.assertIsNone(
            LabelingWidget._next_uncommitted_realisr_sample(widget, 3)
        )

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
                _sync_canvas_view_actions=Mock(),
                refresh_realisr_active_label_list=Mock(),
                refresh_realisr_label_display=Mock(),
                apply_realisr_action_state=Mock(),
                update_realisr_ui=Mock(),
                update_progress_title=Mock(),
            )

        clean = make_widget(False)
        LabelingWidget.activate_realisr_variant(clean, "LR2")
        clean.flush_realisr_draft.assert_not_called()
        self.assertIs(clean.canvas, clean.realisr_workspace.canvases["LR2"])
        clean._sync_canvas_view_actions.assert_called_once_with()

        dirty = make_widget(True)
        LabelingWidget.activate_realisr_variant(dirty, "LR2")
        dirty.flush_realisr_draft.assert_called_once_with()
        dirty._sync_canvas_view_actions.assert_called_once_with()

    def test_canvas_view_actions_match_active_canvas_state(self):
        states = {
            "show_masks": False,
            "show_texts": True,
            "show_labels": False,
            "show_scores": True,
            "show_degrees": False,
            "show_attributes": True,
            "show_linking": False,
            "show_groups": True,
        }
        actions = {}
        for name, checked in states.items():
            actions[name] = SimpleNamespace(
                isChecked=Mock(
                    return_value=(
                        checked if name == "show_masks" else not checked
                    )
                ),
                setChecked=Mock(),
            )
        widget = SimpleNamespace(
            canvas=SimpleNamespace(**states),
            actions=SimpleNamespace(**actions),
        )

        LabelingWidget._sync_canvas_view_actions(widget)

        for name, checked in states.items():
            if name == "show_masks":
                actions[name].setChecked.assert_not_called()
            else:
                actions[name].setChecked.assert_called_once_with(checked)

    def test_leaving_realisr_syncs_view_actions_to_normal_canvas(self):
        normal_canvas = SimpleNamespace(reset_state=Mock())
        widget = Mock()
        widget.realisr_mode = True
        widget._realisr_description_was_visible = False
        widget._normal_canvas = normal_canvas
        widget.fn_to_index = {}

        result = LabelingWidget.leave_realisr_mode(widget, flush=False)

        self.assertTrue(result)
        self.assertIs(widget.canvas, normal_canvas)
        widget._sync_canvas_view_actions.assert_called_once_with()
        self.assertFalse(widget.realisr_mode)

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

    def test_text_new_shape_applies_hr_description_color_immediately(self):
        draft_shape = Shape(label="", shape_type="quadrilateral")
        final_shape = Shape(label="", shape_type="quadrilateral")
        canvas = SimpleNamespace(
            shapes=[draft_shape],
            set_last_label=Mock(return_value=final_shape),
            drawing=Mock(return_value=False),
        )
        widget = SimpleNamespace(
            realisr_mode=True,
            realisr_variant="HR",
            realisr_dataset=SimpleNamespace(attribute="text"),
            canvas=canvas,
            unique_label_list=SimpleNamespace(
                selectedItems=Mock(return_value=[])
            ),
            _config={
                "display_label_popup": True,
                "auto_use_last_gid": False,
                "auto_use_last_label": False,
            },
            find_last_label=Mock(return_value=None),
            digit_to_label=None,
            label_dialog=SimpleNamespace(
                edit=SimpleNamespace(
                    text=Mock(return_value=""), setText=Mock()
                ),
                pop_up=Mock(
                    return_value=("text", {}, None, "", False, [])
                ),
            ),
            validate_label=Mock(return_value=True),
            attributes={},
            label_list=SimpleNamespace(clearSelection=Mock()),
            add_label=Mock(),
            apply_realisr_shape_color=Mock(),
            actions=SimpleNamespace(
                edit_mode=SimpleNamespace(setEnabled=Mock()),
                undo_last_point=SimpleNamespace(setEnabled=Mock()),
                undo=SimpleNamespace(setEnabled=Mock()),
            ),
            set_dirty=Mock(),
        )

        LabelingWidget.new_shape(widget)

        self.assertEqual(final_shape.label, "text")
        self.assertEqual(final_shape.description, "")
        self.assertEqual(final_shape.other_data["recoverable"], 0)
        widget.apply_realisr_shape_color.assert_called_once_with(
            final_shape, variant="HR"
        )

    def test_hr_text_color_depends_on_non_whitespace_description(self):
        widget = SimpleNamespace(
            realisr_variant="HR",
            realisr_dataset=SimpleNamespace(attribute="text"),
            _realisr_recoverable=lambda shape: shape.other_data.get(
                "recoverable"
            ),
            label_list=SimpleNamespace(
                find_item_by_shape=Mock(return_value=None)
            ),
            realisr_mode=False,
        )

        for description in (None, "", " \n\t"):
            shape = Shape(label="text", description=description)
            shape.other_data["recoverable"] = 0
            LabelingWidget.apply_realisr_shape_color(
                widget, shape, variant="HR"
            )
            self.assertEqual(shape.line_color.getRgb()[:3], (128, 128, 128))

        described = Shape(label="text", description="Floor")
        described.other_data["recoverable"] = 0
        LabelingWidget.apply_realisr_shape_color(
            widget, described, variant="HR"
        )
        self.assertEqual(described.line_color.getRgb()[:3], (46, 160, 67))

    def test_empty_description_does_not_change_lr_or_face_colors(self):
        widget = SimpleNamespace(
            realisr_variant="HR",
            realisr_dataset=SimpleNamespace(attribute="text"),
            _realisr_recoverable=lambda shape: shape.other_data.get(
                "recoverable"
            ),
            label_list=SimpleNamespace(
                find_item_by_shape=Mock(return_value=None)
            ),
            realisr_mode=False,
        )
        expected_lr_colors = {
            None: (128, 128, 128),
            0: (46, 160, 67),
            1: (230, 126, 34),
            2: (220, 53, 69),
        }
        for recoverable, expected_color in expected_lr_colors.items():
            lr_shape = Shape(label="", description="")
            lr_shape.other_data["recoverable"] = recoverable
            LabelingWidget.apply_realisr_shape_color(
                widget, lr_shape, variant="LR2"
            )
            self.assertEqual(
                lr_shape.line_color.getRgb()[:3], expected_color
            )

        widget.realisr_dataset.attribute = "face"
        face_shape = Shape(label="face", description="")
        face_shape.other_data["recoverable"] = 0
        LabelingWidget.apply_realisr_shape_color(
            widget, face_shape, variant="HR"
        )
        self.assertEqual(face_shape.line_color.getRgb()[:3], (46, 160, 67))

    def test_hr_text_edit_recolors_on_add_and_clear(self):
        shape = Shape(label="text", description="")
        shape.other_data["recoverable"] = 0
        canvas_update = Mock()
        text_edit = SimpleNamespace(toPlainText=Mock(return_value="Floor"))

        def apply_color(target, variant=None):
            LabelingWidget.apply_realisr_shape_color(
                widget, target, variant=variant
            )

        widget = SimpleNamespace(
            realisr_mode=True,
            realisr_variant="HR",
            realisr_dataset=SimpleNamespace(attribute="text"),
            shape_text_edit=text_edit,
            canvas=SimpleNamespace(
                current=None,
                editing=Mock(return_value=True),
                selected_shapes=[shape],
            ),
            other_data={},
            _realisr_recoverable=lambda target: target.other_data.get(
                "recoverable"
            ),
            label_list=SimpleNamespace(
                find_item_by_shape=Mock(return_value=None)
            ),
            realisr_workspace=SimpleNamespace(
                canvases={"HR": SimpleNamespace(update=canvas_update)}
            ),
            apply_realisr_shape_color=apply_color,
            set_dirty=Mock(),
        )

        LabelingWidget.shape_text_changed(widget)
        self.assertEqual(shape.description, "Floor")
        self.assertEqual(shape.line_color.getRgb()[:3], (46, 160, 67))

        text_edit.toPlainText.return_value = "  "
        LabelingWidget.shape_text_changed(widget)
        self.assertEqual(shape.description, "  ")
        self.assertEqual(shape.line_color.getRgb()[:3], (128, 128, 128))
        self.assertEqual(canvas_update.call_count, 2)

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

    def test_instance_result_matches_multiple_descriptions_by_region_id(self):
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
        unselected = copy.deepcopy(other)
        unselected.description = "unselected"
        unselected.other_data = {
            "region_id": "sample.png#0002",
            "recoverable": 0,
        }
        canvas = SimpleNamespace(shapes=[target, other, unselected])
        workspace = SimpleNamespace(
            canvases={"HR": canvas},
            select_regions=Mock(),
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
        recognized_target = copy.deepcopy(target)
        recognized_target.description = "new target text"
        recognized_other = copy.deepcopy(other)
        recognized_other.description = "new other text"
        result = AutoLabelingResult(
            [recognized_other, recognized_target]
        )
        context = {
            "mode": "instance",
            "target_region_ids": [
                "sample.png#0000",
                "sample.png#0001",
            ],
        }

        LabelingWidget._apply_realisr_auto_labeling_result(
            widget, result, context
        )

        self.assertEqual(loaded[0].description, "new target text")
        self.assertEqual(loaded[0].points, target.points)
        self.assertEqual(loaded[0].other_data, target.other_data)
        self.assertEqual(loaded[1].description, "new other text")
        self.assertEqual(loaded[1].points, other.points)
        self.assertEqual(loaded[1].other_data, other.other_data)
        self.assertEqual(loaded[2].description, "unselected")
        self.assertEqual(loaded[2].other_data, unselected.other_data)
        workspace.select_regions.assert_called_once_with(
            ["sample.png#0000", "sample.png#0001"], notify=False
        )

    def test_workspace_select_regions_synchronizes_multiple_views(self):
        old_hr = SimpleNamespace(selected=True)
        old_lr = SimpleNamespace(selected=True)
        hr_first = SimpleNamespace(selected=False)
        hr_second = SimpleNamespace(selected=False)
        lr_first = SimpleNamespace(selected=False)
        lr_second = SimpleNamespace(selected=False)
        hr_canvas = SimpleNamespace(
            selected_shapes=[old_hr], update=Mock()
        )
        lr_canvas = SimpleNamespace(
            selected_shapes=[old_lr], update=Mock()
        )
        workspace = SimpleNamespace(
            active_variant="HR",
            canvases={"HR": hr_canvas, "LR2": lr_canvas},
            _region_shape_indexes={
                "HR": {"first": hr_first, "second": hr_second},
                "LR2": {"first": lr_first, "second": lr_second},
            },
            _syncing_selection=False,
            _displayed_variants=lambda: ("HR", "LR2"),
        )

        RealISRWorkspace.select_regions(
            workspace, ["second", "first"], notify=False
        )

        self.assertEqual(hr_canvas.selected_shapes, [hr_second, hr_first])
        self.assertEqual(lr_canvas.selected_shapes, [lr_second, lr_first])
        self.assertFalse(old_hr.selected)
        self.assertFalse(old_lr.selected)
        self.assertTrue(hr_first.selected)
        self.assertTrue(hr_second.selected)
        self.assertTrue(lr_first.selected)
        self.assertTrue(lr_second.selected)
        hr_canvas.update.assert_called_once()
        lr_canvas.update.assert_called_once()

    def test_instance_result_is_atomic_when_recognition_is_missing(self):
        first = Shape(
            label="text", description="first old", shape_type="rectangle"
        )
        second = Shape(
            label="text", description="second old", shape_type="rectangle"
        )
        for shape, region_id in (
            (first, "sample.png#0000"),
            (second, "sample.png#0001"),
        ):
            for x, y in ((1, 2), (9, 2), (9, 8), (1, 8)):
                shape.add_point(QtCore.QPointF(x, y))
            shape.other_data = {"region_id": region_id, "recoverable": 1}
        recognized = copy.deepcopy(first)
        recognized.description = "new text"
        canvas = SimpleNamespace(shapes=[first, second])
        widget = SimpleNamespace(
            _realisr_result_matches_request=Mock(return_value=True),
            realisr_workspace=SimpleNamespace(canvases={"HR": canvas}),
            _realisr_region_id=lambda shape: shape.other_data.get("region_id"),
            label_list=SimpleNamespace(clear=Mock()),
            load_shapes=Mock(),
            set_dirty=Mock(),
            flush_realisr_draft=Mock(return_value=True),
            refresh_realisr_active_label_list=Mock(),
            apply_realisr_action_state=Mock(),
            update_realisr_ui=Mock(),
        )

        duplicate = copy.deepcopy(recognized)
        for invalid_result in (
            AutoLabelingResult([recognized]),
            AutoLabelingResult([recognized, duplicate]),
        ):
            LabelingWidget._apply_realisr_auto_labeling_result(
                widget,
                invalid_result,
                {
                    "mode": "instance",
                    "target_region_ids": [
                        "sample.png#0000",
                        "sample.png#0001",
                    ],
                },
            )

        widget.load_shapes.assert_not_called()
        widget.set_dirty.assert_not_called()

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
