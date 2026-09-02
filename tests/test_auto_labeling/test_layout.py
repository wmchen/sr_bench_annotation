import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6 import QtCore, QtWidgets, uic

    import anylabeling.resources.resources  # noqa: F401
    from anylabeling import config
    from anylabeling.config import get_config
    from anylabeling.views.labeling.utils.style import (
        get_model_selection_scroll_area_style,
    )
    from anylabeling.views.labeling.widgets.auto_labeling.auto_labeling import (
        AutoLabelingWidget,
        update_model_selection_scroll_area_height,
    )
    from anylabeling.views.labeling.widgets.searchable_model_dropdown import (
        SearchableModelDropdownPopup,
    )

    PYQT_AVAILABLE = True
except Exception:
    PYQT_AVAILABLE = False


@unittest.skipUnless(
    PYQT_AVAILABLE, "PyQt6 is required for auto labeling layout tests"
)
class TestAutoLabelingLayout(unittest.TestCase):
    def setUp(self):
        self.app = QtWidgets.QApplication.instance()
        if self.app is None:
            self.app = QtWidgets.QApplication([])
        self._widgets = []

    def tearDown(self):
        for widget in self._widgets:
            widget.close()
        self.app.processEvents()

    def test_model_selection_uses_horizontal_scroll_area(self):
        form = QtWidgets.QWidget()
        self._widgets.append(form)
        ui_path = (
            Path(__file__).resolve().parents[2]
            / "anylabeling/views/labeling/widgets/auto_labeling/auto_labeling.ui"
        )

        uic.loadUi(str(ui_path), form)

        scroll_area = form.findChild(
            QtWidgets.QScrollArea, "model_selection_scroll_area"
        )
        scroll_area.setStyleSheet(get_model_selection_scroll_area_style())
        container = form.findChild(
            QtWidgets.QWidget, "model_selection_container"
        )

        self.assertIsNotNone(scroll_area)
        self.assertIs(scroll_area.widget(), container)
        self.assertTrue(scroll_area.widgetResizable())
        self.assertEqual(
            scroll_area.horizontalScrollBarPolicy(),
            QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded,
        )
        self.assertEqual(
            scroll_area.verticalScrollBarPolicy(),
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff,
        )
        self.assertIsNotNone(container.layout())
        spacer = next(
            (
                container.layout().itemAt(index).spacerItem()
                for index in range(container.layout().count())
                if container.layout().itemAt(index).spacerItem() is not None
            ),
            None,
        )
        self.assertIsNotNone(spacer)
        self.assertEqual(
            spacer.sizePolicy().horizontalPolicy(),
            QtWidgets.QSizePolicy.Policy.Expanding,
        )

        slider = form.findChild(QtWidgets.QSlider, "mask_fineness_slider")
        self.assertGreaterEqual(slider.minimumWidth(), 120)

        form.resize(5000, 100)
        form.show()
        self.app.processEvents()
        update_model_selection_scroll_area_height(scroll_area)

        self.assertEqual(scroll_area.horizontalScrollBar().maximum(), 0)
        self.assertEqual(scroll_area.height(), container.sizeHint().height())

        form.resize(320, 100)
        self.app.processEvents()
        update_model_selection_scroll_area_height(scroll_area)

        self.assertGreater(scroll_area.horizontalScrollBar().maximum(), 0)
        self.assertEqual(
            scroll_area.horizontalScrollBar().sizeHint().height(), 16
        )
        self.assertEqual(
            scroll_area.height(),
            container.sizeHint().height()
            + scroll_area.horizontalScrollBar().sizeHint().height(),
        )

    def test_default_hidden_controls_do_not_stretch_buttons(self):
        form = QtWidgets.QWidget()
        self._widgets.append(form)
        ui_path = (
            Path(__file__).resolve().parents[2]
            / "anylabeling/views/labeling/widgets/auto_labeling/auto_labeling.ui"
        )
        uic.loadUi(str(ui_path), form)

        hidden_widget_names = (
            "button_run",
            "button_add_point",
            "button_remove_point",
            "button_add_rect",
            "add_pos_rect",
            "add_neg_rect",
            "button_run_rect",
            "button_clear",
            "button_finish_object",
            "button_send",
            "edit_text",
            "edit_conf",
            "edit_iou",
            "input_box_thres",
            "input_conf",
            "input_iou",
            "output_label",
            "output_select_combobox",
            "toggle_preserve_existing_annotations",
            "button_set_api_token",
            "button_classes_filter",
            "button_reset_tracker",
            "upn_select_combobox",
            "gd_select_combobox",
            "florence2_select_combobox",
            "remote_server_select_combobox",
            "remote_task_select_combobox",
            "button_auto_decode",
            "button_cropping",
            "button_skip_detection",
            "mask_fineness_slider",
            "mask_fineness_value_label",
            "realisr_inference_mode_label",
            "realisr_inference_mode_combobox",
        )
        for widget_name in hidden_widget_names:
            getattr(form, widget_name).hide()

        form.resize(1600, 100)
        form.show()
        self.app.processEvents()

        self.assertLessEqual(
            form.model_selection_button.width(),
            form.model_selection_button.sizeHint().width() + 2,
        )
        self.assertLessEqual(
            form.button_close.width(), form.button_close.sizeHint().width() + 2
        )

    def test_amg_uses_compact_button_without_inline_settings(self):
        form = QtWidgets.QWidget()
        self._widgets.append(form)
        ui_path = (
            Path(__file__).resolve().parents[2]
            / "anylabeling/views/labeling/widgets/auto_labeling/auto_labeling.ui"
        )

        uic.loadUi(str(ui_path), form)

        self.assertEqual(form.button_segment_everything.text(), "AMG")
        self.assertIsNone(
            form.findChild(QtWidgets.QSpinBox, "input_points_per_side")
        )
        self.assertIsNone(form.findChild(QtWidgets.QSpinBox, "input_min_area"))

    def test_amg_requires_confirmation_once_per_session(self):
        widget = Mock()
        widget.tr.side_effect = lambda text: text
        widget._amg_warning_confirmed = False

        warning_path = (
            "anylabeling.views.labeling.widgets.auto_labeling."
            "auto_labeling.QMessageBox.warning"
        )
        with patch(
            warning_path,
            side_effect=[
                QtWidgets.QMessageBox.StandardButton.No,
                QtWidgets.QMessageBox.StandardButton.Yes,
            ],
        ) as warning:
            AutoLabelingWidget.on_segment_everything_clicked(widget)

            widget.model_manager.set_auto_labeling_marks.assert_not_called()
            widget.run_prediction.assert_not_called()
            self.assertFalse(widget._amg_warning_confirmed)

            AutoLabelingWidget.on_segment_everything_clicked(widget)
            self.assertTrue(widget._amg_warning_confirmed)

            AutoLabelingWidget.on_segment_everything_clicked(widget)

        self.assertEqual(warning.call_count, 2)
        self.assertEqual(
            widget.model_manager.set_auto_labeling_marks.call_count, 2
        )
        widget.model_manager.set_auto_labeling_marks.assert_called_with(
            [{"type": "auto_grid"}]
        )
        self.assertEqual(widget.run_prediction.call_count, 2)

    def test_extract_shapes_for_recognition_accepts_supported_types(self):
        shapes = [
            SimpleNamespace(shape_type=shape_type)
            for shape_type in (
                "rectangle",
                "rotation",
                "polygon",
                "quadrilateral",
            )
        ]
        widget = Mock()
        widget.parent.canvas.shapes = shapes

        extracted = AutoLabelingWidget._extract_shapes_for_recognition(widget)

        self.assertEqual(extracted, shapes)
        widget.model_manager.new_model_status.emit.assert_not_called()

    def test_extract_shapes_for_recognition_rejects_unsupported_type(self):
        widget = Mock()
        widget.parent.canvas.shapes = [SimpleNamespace(shape_type="circle")]
        widget.tr.side_effect = lambda text: text

        with self.assertRaisesRegex(
            ValueError,
            "Only rectangle, rotation, polygon and quadrilateral shapes",
        ):
            AutoLabelingWidget._extract_shapes_for_recognition(widget)

        widget.model_manager.new_model_status.emit.assert_called_once()

    def test_skip_detection_passes_quadrilateral_to_recognition(self):
        quadrilateral = SimpleNamespace(shape_type="quadrilateral")
        widget = Mock()
        widget.parent.filename = "example.jpg"
        widget.parent.image = object()
        widget.parent.canvas.shapes = [quadrilateral]
        widget.button_skip_detection.isChecked.return_value = True
        widget.model_manager.loaded_model_config = {"type": "ppocr_v4"}
        widget._extract_shapes_for_recognition.side_effect = lambda: (
            AutoLabelingWidget._extract_shapes_for_recognition(widget)
        )

        AutoLabelingWidget.run_prediction(widget)

        widget.model_manager.predict_shapes_threading.assert_called_once_with(
            widget.parent.image,
            widget.parent.filename,
            existing_shapes=[quadrilateral],
        )

    def test_realisr_model_compatibility_is_task_specific(self):
        widget = SimpleNamespace(
            REALISR_TEXT_MODEL_TYPES={"ppocr_v4", "ppocr_v5", "ppocr_v6"},
            REALISR_FACE_MODEL_TYPES={"scrfd", "yolov6_face"},
            _realisr_task=lambda: "text",
            model_manager=SimpleNamespace(loaded_model_config=None),
        )

        self.assertTrue(
            AutoLabelingWidget._is_realisr_model_compatible(
                widget, {"type": "ppocr_v6"}
            )
        )
        self.assertFalse(
            AutoLabelingWidget._is_realisr_model_compatible(
                widget, {"type": "scrfd"}
            )
        )
        widget._realisr_task = lambda: "face"
        self.assertTrue(
            AutoLabelingWidget._is_realisr_model_compatible(
                widget, {"type": "yolov6_face"}
            )
        )

    def test_realisr_instance_requires_hr_and_at_least_one_shape(self):
        selected = SimpleNamespace(
            shape_type="quadrilateral",
            other_data={"region_id": "sample.png#0000"},
        )
        second = SimpleNamespace(
            shape_type="rectangle",
            other_data={"region_id": "sample.png#0001"},
        )
        canvas = SimpleNamespace(shapes=[selected], selected_shapes=[])
        widget = SimpleNamespace(
            REALISR_FULL_IMAGE_MODE="full_image",
            _is_realisr_context=lambda: True,
            _prediction_running=False,
            model_manager=SimpleNamespace(
                is_model_download_running=lambda: False,
                loaded_model_config={"type": "ppocr_v4"},
            ),
            _is_realisr_model_compatible=lambda _config=None: True,
            parent=SimpleNamespace(
                realisr_variant="LR2",
                realisr_sample="sample.png",
                filename="/data/HR/sample.png",
            ),
            _realisr_hr_canvas=lambda: canvas,
            _realisr_mode=lambda: "instance",
            tr=lambda text: text,
        )

        enabled, reason = AutoLabelingWidget._realisr_run_eligibility(widget)
        self.assertFalse(enabled)
        self.assertIn("only available in HR", reason)

        widget.parent.realisr_variant = "HR"
        enabled, reason = AutoLabelingWidget._realisr_run_eligibility(widget)
        self.assertFalse(enabled)
        self.assertIn("at least one", reason)

        canvas.selected_shapes = [selected]
        enabled, reason = AutoLabelingWidget._realisr_run_eligibility(widget)
        self.assertTrue(enabled)
        self.assertEqual(reason, "")

        canvas.selected_shapes = [selected, second]
        enabled, reason = AutoLabelingWidget._realisr_run_eligibility(widget)
        self.assertTrue(enabled)
        self.assertEqual(reason, "")

        second.shape_type = "circle"
        enabled, reason = AutoLabelingWidget._realisr_run_eligibility(widget)
        self.assertFalse(enabled)
        self.assertIn("type cannot be recognized", reason)

        second.shape_type = "rectangle"
        second.other_data = {}
        enabled, reason = AutoLabelingWidget._realisr_run_eligibility(widget)
        self.assertFalse(enabled)
        self.assertIn("no region ID", reason)

        second.other_data = {"region_id": "sample.png#0000"}
        enabled, reason = AutoLabelingWidget._realisr_run_eligibility(widget)
        self.assertFalse(enabled)
        self.assertIn("unique region IDs", reason)

        canvas.selected_shapes = [selected]
        widget._realisr_mode = lambda: "full_image"
        enabled, reason = AutoLabelingWidget._realisr_run_eligibility(widget)
        self.assertFalse(enabled)
        self.assertIn("already has annotations", reason)

    def test_realisr_full_image_confirmation_defaults_to_cancel(self):
        canvas = SimpleNamespace(shapes=[], selected_shapes=[])
        manager = SimpleNamespace(predict_shapes_threading=Mock())
        widget = SimpleNamespace(
            REALISR_FULL_IMAGE_MODE="full_image",
            REALISR_INSTANCE_MODE="instance",
            _realisr_run_eligibility=Mock(return_value=(True, "")),
            _realisr_mode=lambda: "full_image",
            _realisr_task=lambda: "text",
            _realisr_hr_canvas=lambda: canvas,
            tr=lambda text: text,
            parent=SimpleNamespace(
                realisr_sample="sample.png",
                filename="/data/HR/sample.png",
                image=object(),
                flush_realisr_draft=Mock(return_value=True),
            ),
            model_manager=manager,
            _pending_realisr_request=None,
        )

        with patch.object(
            QtWidgets.QMessageBox,
            "question",
            return_value=QtWidgets.QMessageBox.StandardButton.No,
        ) as question:
            AutoLabelingWidget._run_realisr_prediction(widget)

        question.assert_called_once()
        self.assertEqual(
            question.call_args.args[-1],
            QtWidgets.QMessageBox.StandardButton.No,
        )
        manager.predict_shapes_threading.assert_not_called()

    def test_realisr_instance_runs_multiple_shapes_without_confirmation(self):
        first = SimpleNamespace(
            shape_type="quadrilateral",
            other_data={"region_id": "sample.png#0000"},
            selected=True,
        )
        second = SimpleNamespace(
            shape_type="rectangle",
            other_data={"region_id": "sample.png#0001"},
            selected=True,
        )
        canvas = SimpleNamespace(
            shapes=[first, second], selected_shapes=[first, second]
        )
        manager = SimpleNamespace(predict_shapes_threading=Mock())
        widget = SimpleNamespace(
            REALISR_FULL_IMAGE_MODE="full_image",
            REALISR_INSTANCE_MODE="instance",
            _realisr_run_eligibility=Mock(return_value=(True, "")),
            _realisr_mode=lambda: "instance",
            _realisr_task=lambda: "text",
            _realisr_hr_canvas=lambda: canvas,
            tr=lambda text: text,
            parent=SimpleNamespace(
                realisr_sample="sample.png",
                filename="/data/HR/sample.png",
                image=object(),
                flush_realisr_draft=Mock(return_value=True),
            ),
            model_manager=manager,
            _pending_realisr_request=None,
        )

        with patch.object(QtWidgets.QMessageBox, "question") as question:
            AutoLabelingWidget._run_realisr_prediction(widget)

        question.assert_not_called()
        call = manager.predict_shapes_threading.call_args
        self.assertEqual(call.args[1], "/data/HR/sample.png")
        self.assertEqual(len(call.kwargs["existing_shapes"]), 2)
        self.assertTrue(
            all(shape.selected for shape in call.kwargs["existing_shapes"])
        )
        self.assertIsNot(call.kwargs["existing_shapes"][0], first)
        self.assertEqual(
            widget._pending_realisr_request["target_region_ids"],
            ["sample.png#0000", "sample.png#0001"],
        )

    def test_realisr_restores_model_remembered_for_task(self):
        settings = SimpleNamespace(
            value=Mock(return_value=":/auto_labeling/text.yaml"),
            remove=Mock(),
        )
        manager = SimpleNamespace(
            loaded_model_config=None,
            get_model_configs=Mock(
                return_value=[
                    {
                        "config_file": ":/auto_labeling/text.yaml",
                        "display_name": "Text OCR",
                    }
                ]
            ),
            load_model=Mock(),
        )
        widget = SimpleNamespace(
            _is_realisr_context=lambda: True,
            _is_realisr_model_compatible=lambda _config=None: False,
            _realisr_task=lambda: "text",
            parent=SimpleNamespace(settings=settings),
            model_manager=manager,
            model_selection_button=SimpleNamespace(
                setText=Mock(), setEnabled=Mock()
            ),
            button_run=SimpleNamespace(setEnabled=Mock()),
        )

        AutoLabelingWidget._restore_realisr_model(widget)

        settings.value.assert_called_once_with(
            "realisr/auto_labeling_model/text", ""
        )
        manager.load_model.assert_called_once_with(
            ":/auto_labeling/text.yaml"
        )
        widget.model_selection_button.setText.assert_called_once_with(
            "Text OCR"
        )

    def test_realisr_text_mode_defaults_from_annotation_state(self):
        config.current_config_file = (
            "anylabeling/configs/xanylabeling_config.yaml"
        )
        canvas = SimpleNamespace(shapes=[], selected_shapes=[])
        parent = SimpleNamespace(
            _config=get_config(),
            realisr_mode=True,
            realisr_dataset=SimpleNamespace(attribute="text"),
            realisr_variant="HR",
            realisr_sample="sample.png",
            filename="/data/HR/sample.png",
            realisr_workspace=SimpleNamespace(canvases={"HR": canvas}),
            settings=SimpleNamespace(
                value=Mock(return_value=""), remove=Mock(), setValue=Mock()
            ),
            new_shapes_from_auto_labeling=Mock(),
        )
        widget = AutoLabelingWidget(parent)
        self._widgets.append(widget)

        widget.configure_realisr_context()
        self.assertEqual(
            widget.realisr_inference_mode_combobox.currentData(),
            widget.REALISR_FULL_IMAGE_MODE,
        )

        canvas.shapes.append(SimpleNamespace(shape_type="quadrilateral"))
        widget.refresh_realisr_auto_labeling_state()

        self.assertEqual(
            widget.realisr_inference_mode_combobox.currentData(),
            widget.REALISR_INSTANCE_MODE,
        )
        full_index = widget.realisr_inference_mode_combobox.findData(
            widget.REALISR_FULL_IMAGE_MODE
        )
        self.assertFalse(
            widget.realisr_inference_mode_combobox.model()
            .item(full_index)
            .isEnabled()
        )

    def test_initial_show_reflows_model_selection_row(self):
        config.current_config_file = (
            "anylabeling/configs/xanylabeling_config.yaml"
        )
        parent = type(
            "Parent",
            (),
            {
                "_config": get_config(),
                "new_shapes_from_auto_labeling": lambda _self, _result: None,
            },
        )()
        root = QtWidgets.QWidget()
        self._widgets.append(root)
        layout = QtWidgets.QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QtWidgets.QLabel("Mode"))
        layout.addSpacing(5)
        widget = AutoLabelingWidget(parent)
        widget.hide()
        layout.addWidget(widget)
        layout.addWidget(QtWidgets.QFrame(), 1)

        root.resize(1600, 300)
        root.show()
        self.app.processEvents()
        widget.show()
        self.app.processEvents()
        self.app.processEvents()

        button_top = widget.model_selection_button.mapTo(
            widget, widget.model_selection_button.rect().topLeft()
        ).y()
        scroll_top = widget.model_selection_scroll_area.geometry().top()
        button_bottom = (
            button_top + widget.model_selection_button.geometry().height()
        )
        status_top = widget.model_status_label.geometry().top()

        self.assertEqual(button_top, scroll_top)
        self.assertLessEqual(button_bottom, status_top)
        self.assertTrue(widget.button_segment_everything.isEnabled())
        widget.model_manager.prediction_started.emit()
        self.assertFalse(widget.button_segment_everything.isEnabled())
        widget.model_manager.prediction_finished.emit()
        self.assertTrue(widget.button_segment_everything.isEnabled())

    def test_model_dropdown_search_matches_display_names(self):
        dropdown = SearchableModelDropdownPopup(
            {
                "Meta": {
                    "sam2_hiera_base_video-r20240901": {
                        "display_name": "Segment Anything 2 Video (Base)"
                    },
                    "sam2_hiera_base-r20240801": {
                        "display_name": "Segment Anything 2.1 (Base)"
                    },
                    "sam_hq_vit_b-r20231111": {
                        "display_name": "SAM-HQ (ViT-Base)"
                    },
                }
            }
        )
        self._widgets.append(dropdown)
        dropdown.show()
        self.app.processEvents()

        dropdown.filter_models("seg")
        self.app.processEvents()

        visible_names = [
            item.display_name
            for item in dropdown.model_items.values()
            if item.isVisible()
        ]

        self.assertIn("Segment Anything 2 Video (Base)", visible_names)
        self.assertIn("Segment Anything 2.1 (Base)", visible_names)
        self.assertNotIn("SAM-HQ (ViT-Base)", visible_names)
