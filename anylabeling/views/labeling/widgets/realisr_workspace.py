"""Four-pane synchronized workspace used by Real-ISR annotation mode."""

from __future__ import annotations

import functools

from PyQt6 import QtCore, QtWidgets
from PyQt6.QtCore import Qt

from anylabeling.views.labeling.realisr_dataset import VARIANTS
from anylabeling.views.labeling.utils.theme import get_theme
from anylabeling.views.labeling.widgets.canvas import Canvas


class RealISRCanvas(Canvas):
    """Canvas that reports activation and supports selection-only panes."""

    activated = QtCore.pyqtSignal()
    recoverability_requested = QtCore.pyqtSignal(int)
    reveal_text_changed = QtCore.pyqtSignal(bool)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.realisr_read_only = False
        self._realisr_pan_position = None

    def mousePressEvent(self, event):
        self.activated.emit()
        if (
            self.realisr_read_only
            and event.button() == Qt.MouseButton.LeftButton
        ):
            self._realisr_pan_position = event.position()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (
            self.realisr_read_only
            and self._realisr_pan_position is not None
            and event.buttons() & Qt.MouseButton.LeftButton
            and self.pixmap is not None
            and not self.pixmap.isNull()
        ):
            delta = event.position() - self._realisr_pan_position
            self._realisr_pan_position = event.position()
            width = max(1.0, self.pixmap.width() * self.scale)
            height = max(1.0, self.pixmap.height() * self.scale)
            self.scroll_request.emit(
                delta.x() / width,
                Qt.Orientation.Horizontal,
                1,
            )
            self.scroll_request.emit(
                delta.y() / height,
                Qt.Orientation.Vertical,
                1,
            )
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._realisr_pan_position = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        self.activated.emit()
        if self.realisr_read_only:
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event):
        key_map = {
            Qt.Key.Key_0: 0,
            Qt.Key.Key_1: 1,
            Qt.Key.Key_2: 2,
        }
        if event.key() in key_map:
            self.recoverability_requested.emit(key_map[event.key()])
            event.accept()
            return
        if event.key() == Qt.Key.Key_R and not event.isAutoRepeat():
            self.reveal_text_changed.emit(True)
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if event.key() == Qt.Key.Key_R and not event.isAutoRepeat():
            self.reveal_text_changed.emit(False)
            event.accept()
            return
        super().keyReleaseEvent(event)


class RealISRWorkspace(QtWidgets.QWidget):
    """A 2x2 group of canvases with synchronized pan, zoom and selection."""

    active_variant_changed = QtCore.pyqtSignal(str)
    selection_changed = QtCore.pyqtSignal(str, list)
    recoverability_requested = QtCore.pyqtSignal(int)
    reveal_text_changed = QtCore.pyqtSignal(bool)

    def __init__(self, canvas_options, parent=None):
        super().__init__(parent)
        self.canvases = {}
        self.scroll_areas = {}
        self.scroll_bars = {}
        self.panes = {}
        self.images = {}
        self.active_variant = "HR"
        self.zoom_factor = 1.0
        self._syncing_scroll = False
        self._syncing_selection = False

        layout = QtWidgets.QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        for index, variant in enumerate(VARIANTS):
            canvas = RealISRCanvas(parent=parent, **canvas_options)
            canvas.activated.connect(
                functools.partial(self.set_active_variant, variant)
            )
            canvas.zoom_request.connect(
                functools.partial(self._zoom_request, variant)
            )
            canvas.scroll_request.connect(
                functools.partial(self._scroll_request, variant)
            )
            canvas.selection_changed.connect(
                functools.partial(self._selection_request, variant)
            )
            canvas.recoverability_requested.connect(
                self.recoverability_requested.emit
            )
            canvas.reveal_text_changed.connect(
                self.reveal_text_changed.emit
            )

            scroll_area = QtWidgets.QScrollArea()
            scroll_area.setWidget(canvas)
            scroll_area.setWidgetResizable(True)
            bars = {
                Qt.Orientation.Vertical: scroll_area.verticalScrollBar(),
                Qt.Orientation.Horizontal: scroll_area.horizontalScrollBar(),
            }
            for orientation, bar in bars.items():
                bar.valueChanged.connect(
                    functools.partial(
                        self._scroll_bar_changed, variant, orientation
                    )
                )

            pane = QtWidgets.QGroupBox(variant)
            pane_layout = QtWidgets.QVBoxLayout(pane)
            pane_layout.setContentsMargins(3, 3, 3, 3)
            pane_layout.addWidget(scroll_area)
            layout.addWidget(pane, index // 2, index % 2)

            self.canvases[variant] = canvas
            self.scroll_areas[variant] = scroll_area
            self.scroll_bars[variant] = bars
            self.panes[variant] = pane

        self._update_active_style()

    @property
    def active_canvas(self):
        return self.canvases[self.active_variant]

    @property
    def active_scroll_area(self):
        return self.scroll_areas[self.active_variant]

    @property
    def active_scroll_bars(self):
        return self.scroll_bars[self.active_variant]

    def set_active_variant(self, variant):
        if variant not in VARIANTS:
            return
        changed = variant != self.active_variant
        self.active_variant = variant
        self._update_active_style()
        if changed:
            self.active_variant_changed.emit(variant)
        self.canvases[variant].setFocus(Qt.FocusReason.MouseFocusReason)

    def _update_active_style(self):
        theme = get_theme()
        for variant, pane in self.panes.items():
            active = variant == self.active_variant
            pane.setTitle(
                self.tr("%s · Current annotation view") % variant
                if active
                else variant
            )
            pane.setStyleSheet(
                "QGroupBox {"
                f"border: {3 if active else 1}px solid "
                f"{theme['primary'] if active else theme['border']};"
                "margin-top: 8px;"
                f"color: {theme['primary'] if active else theme['text']};"
                f"font-weight: {'bold' if active else 'normal'};"
                "}"
                "QGroupBox::title { subcontrol-origin: margin; left: 8px; "
                "padding: 0 4px; }"
            )

    def clear(self):
        self.images = {}
        for canvas in self.canvases.values():
            canvas.reset_state()

    def load_group(self, pixmaps, shapes):
        self.images = dict(pixmaps)
        self._syncing_selection = True
        try:
            for variant in VARIANTS:
                canvas = self.canvases[variant]
                # Each group is an independent annotation document.  The
                # canvases are reused between groups, so discard the previous
                # group's undo history and any selection references before
                # storing the new group's initial snapshot.
                canvas.reset_state()
                canvas.selected_shapes = []
                canvas.selected_shapes_copy = []
                canvas.load_pixmap(pixmaps[variant])
                canvas.load_shapes(shapes[variant])
                canvas.realisr_read_only = variant != "HR"
                canvas.setEnabled(True)
                canvas.set_editing(True)
        finally:
            self._syncing_selection = False
        self.zoom_factor = 1.0
        self.set_active_variant("HR")
        self.set_lr_text_revealed(False)
        QtCore.QTimer.singleShot(0, self.fit_canvases)

    def fit_canvases(self):
        if not self.images:
            return
        widths = [area.viewport().width() for area in self.scroll_areas.values()]
        heights = [
            area.viewport().height() for area in self.scroll_areas.values()
        ]
        if not widths or min(widths) <= 4 or min(heights) <= 4:
            return
        available_width = min(widths) - 4
        available_height = min(heights) - 4
        hr_pixmap = self.images["HR"]
        aspect = hr_pixmap.width() / max(1, hr_pixmap.height())
        display_width = min(available_width, available_height * aspect)
        display_height = min(available_height, available_width / aspect)

        self._syncing_scroll = True
        try:
            for canvas in self.canvases.values():
                pixmap = canvas.pixmap
                if pixmap is None or pixmap.isNull():
                    continue
                base_scale = min(
                    display_width / pixmap.width(),
                    display_height / pixmap.height(),
                )
                canvas.scale = max(0.01, base_scale * self.zoom_factor)
                canvas.adjustSize()
                canvas.update()
        finally:
            self._syncing_scroll = False

    def _scroll_ratios(self, variant):
        ratios = {}
        for orientation, bar in self.scroll_bars[variant].items():
            ratios[orientation] = (
                bar.value() / bar.maximum() if bar.maximum() else 0.0
            )
        return ratios

    def _restore_scroll_ratios(self, ratios):
        self._syncing_scroll = True
        try:
            for bars in self.scroll_bars.values():
                for orientation, ratio in ratios.items():
                    bar = bars[orientation]
                    bar.setValue(round(ratio * bar.maximum()))
        finally:
            self._syncing_scroll = False

    def _zoom_request(self, variant, delta, _position):
        self.set_active_variant(variant)
        ratios = self._scroll_ratios(variant)
        self.zoom_factor = min(
            8.0,
            max(0.25, self.zoom_factor * (1.1 if delta > 0 else 0.9)),
        )
        self.fit_canvases()
        QtCore.QTimer.singleShot(
            0, functools.partial(self._restore_scroll_ratios, ratios)
        )

    def _scroll_request(self, variant, delta, orientation, mode):
        self.set_active_variant(variant)
        bar = self.scroll_bars[variant][orientation]
        units = -delta * (0.1 if mode == 0 else 1)
        step = bar.singleStep() if mode == 0 else bar.maximum()
        bar.setValue(round(bar.value() + step * units))

    def _scroll_bar_changed(self, variant, orientation, _value):
        if self._syncing_scroll:
            return
        source = self.scroll_bars[variant][orientation]
        ratio = source.value() / source.maximum() if source.maximum() else 0.0
        self._syncing_scroll = True
        try:
            for other_variant, bars in self.scroll_bars.items():
                if other_variant == variant:
                    continue
                bar = bars[orientation]
                bar.setValue(round(ratio * bar.maximum()))
        finally:
            self._syncing_scroll = False

    @staticmethod
    def _region_id(shape):
        return shape.other_data.get("region_id")

    def _selection_request(self, variant, shapes):
        if self._syncing_selection:
            return
        self.set_active_variant(variant)
        region_ids = {
            self._region_id(shape)
            for shape in shapes
            if self._region_id(shape) is not None
        }
        self._syncing_selection = True
        try:
            for canvas in self.canvases.values():
                selected = []
                for shape in canvas.shapes:
                    shape.selected = self._region_id(shape) in region_ids
                    if shape.selected:
                        selected.append(shape)
                canvas.selected_shapes = selected
                canvas.update()
        finally:
            self._syncing_selection = False
        self.selection_changed.emit(variant, list(shapes))

    def select_region(self, region_id, notify=True):
        shapes = [
            shape
            for shape in self.active_canvas.shapes
            if self._region_id(shape) == region_id
        ]
        if notify:
            self._selection_request(self.active_variant, shapes)
            return
        region_ids = {region_id} if region_id is not None else set()
        self._syncing_selection = True
        try:
            for canvas in self.canvases.values():
                selected = []
                for shape in canvas.shapes:
                    shape.selected = self._region_id(shape) in region_ids
                    if shape.selected:
                        selected.append(shape)
                canvas.selected_shapes = selected
                canvas.update()
        finally:
            self._syncing_selection = False

    def set_lr_text_revealed(self, revealed):
        for variant in VARIANTS[1:]:
            canvas = self.canvases[variant]
            active_reveal = revealed and variant == self.active_variant
            for shape in canvas.shapes:
                truth = shape.other_data.get("realisr_text", "")
                shape.label = truth if active_reveal else ""
            canvas.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QtCore.QTimer.singleShot(0, self.fit_canvases)
