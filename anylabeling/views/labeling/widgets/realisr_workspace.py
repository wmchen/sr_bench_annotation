"""Four-pane synchronized workspace used by Real-ISR annotation mode."""

from __future__ import annotations

import functools
import math

from PyQt6 import QtCore, QtWidgets
from PyQt6.QtCore import Qt

from anylabeling.views.labeling.realisr_dataset import VARIANTS
from anylabeling.views.labeling.utils.theme import get_theme
from anylabeling.views.labeling.widgets.canvas import Canvas


class RealISRCanvas(Canvas):
    """Canvas that reports activation and supports selection-only panes."""

    activated = QtCore.pyqtSignal()
    blank_double_clicked = QtCore.pyqtSignal()
    recoverability_requested = QtCore.pyqtSignal(int)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.realisr_read_only = False
        self._realisr_pan_position = None
        self._realisr_cross_line_enabled = self.cross_line_show

    def set_editing(self, value=True):
        """Show the crosshair only while drawing in Real-ISR mode."""
        super().set_editing(value)
        self.cross_line_show = (
            self._realisr_cross_line_enabled and self.drawing()
        )
        self.update()

    def set_cross_line(self, show, width, color, opacity):
        """Apply crosshair settings without showing it in edit mode."""
        self._realisr_cross_line_enabled = show
        super().set_cross_line(show and self.drawing(), width, color, opacity)

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
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self.editing()
            and not self.selected_shapes
        ):
            position = self.transform_pos(event.position())
            if next(iter(self._shape_hit_candidates(position)), None) is None:
                self.blank_double_clicked.emit()
                event.accept()
                return
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
        super().keyPressEvent(event)


class RealISRWorkspace(QtWidgets.QWidget):
    """A 2x2 group of canvases with synchronized pan, zoom and selection."""

    MIN_ZOOM_FACTOR = 0.25
    MAX_ZOOM_FACTOR = 8.0
    DEFAULT_FOCUS_COVERAGE = 0.70

    active_variant_changed = QtCore.pyqtSignal(str)
    selection_changed = QtCore.pyqtSignal(str, list)
    single_view_changed = QtCore.pyqtSignal(bool)
    recoverability_requested = QtCore.pyqtSignal(int)

    def __init__(self, canvas_options, parent=None):
        super().__init__(parent)
        self.canvases = {}
        self.scroll_areas = {}
        self.scroll_bars = {}
        self.panes = {}
        self.images = {}
        self.active_variant = "HR"
        self.zoom_factor = 1.0
        self._single_view = False
        self._syncing_scroll = False
        self._syncing_selection = False
        self._region_shape_indexes = {variant: {} for variant in VARIANTS}

        self._layout = QtWidgets.QGridLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(4)
        for index in range(2):
            self._layout.setRowStretch(index, 1)
            self._layout.setColumnStretch(index, 1)
        for index, variant in enumerate(VARIANTS):
            canvas = RealISRCanvas(parent=parent, **canvas_options)
            # Real-ISR canvases show only annotation geometry. Keep label and
            # description data intact, but reserve text display for the label
            # list (region IDs) and the read-only LR description panel.
            canvas.show_labels = False
            canvas.show_texts = variant == "HR"
            canvas.activated.connect(
                functools.partial(self.set_active_variant, variant)
            )
            canvas.blank_double_clicked.connect(
                functools.partial(self.show_single_view, variant)
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
            self._layout.addWidget(pane, index // 2, index % 2)

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

    @property
    def is_single_view(self):
        return self._single_view

    def _displayed_variants(self):
        return (self.active_variant,) if self._single_view else VARIANTS

    def _apply_layout_mode(self):
        for pane in self.panes.values():
            self._layout.removeWidget(pane)
            pane.hide()
        if self._single_view:
            pane = self.panes[self.active_variant]
            self._layout.addWidget(pane, 0, 0, 2, 2)
            pane.show()
            return
        for index, variant in enumerate(VARIANTS):
            pane = self.panes[variant]
            self._layout.addWidget(pane, index // 2, index % 2)
            pane.show()

    def _fit_and_restore_scroll_ratios(self, ratios):
        self.fit_canvases()
        QtCore.QTimer.singleShot(
            0, functools.partial(self._restore_scroll_ratios, ratios)
        )

    def _schedule_layout_fit(self, ratios):
        QtCore.QTimer.singleShot(
            0, functools.partial(self._fit_and_restore_scroll_ratios, ratios)
        )

    def show_single_view(self, variant):
        if variant not in VARIANTS or self._single_view:
            return False
        ratios = self._scroll_ratios(self.active_variant)
        self.set_active_variant(variant)
        changed = not self._single_view
        self._single_view = True
        self._apply_layout_mode()
        self._schedule_layout_fit(ratios)
        if changed:
            self.single_view_changed.emit(True)
        return True

    def show_tiled_views(self, preserve_view=True):
        ratios = (
            self._scroll_ratios(self.active_variant)
            if preserve_view and self.images
            else {
                Qt.Orientation.Horizontal: 0.0,
                Qt.Orientation.Vertical: 0.0,
            }
        )
        changed = self._single_view
        self._single_view = False
        self._apply_layout_mode()
        if self.images:
            self._schedule_layout_fit(ratios)
        if changed:
            self.single_view_changed.emit(False)
        return changed

    def set_active_variant(self, variant):
        if variant not in VARIANTS:
            return
        changed = variant != self.active_variant
        ratios = (
            self._scroll_ratios(self.active_variant)
            if changed and self._single_view
            else None
        )
        self.active_variant = variant
        if changed:
            if self._single_view:
                self._apply_layout_mode()
                self._schedule_layout_fit(ratios)
            self._update_active_style()
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
        self.show_tiled_views(preserve_view=False)
        for variant, canvas in self.canvases.items():
            canvas.reset_state()
            self._region_shape_indexes[variant] = {}

    def rebuild_region_index(self, variant=None):
        """Rebuild region lookups after a canvas shape collection changes."""
        variants = VARIANTS if variant is None else (variant,)
        for current_variant in variants:
            index = {}
            for shape in self.canvases[current_variant].shapes:
                region_id = self._region_id(shape)
                if region_id is not None:
                    index[region_id] = shape
            self._region_shape_indexes[current_variant] = index

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
                self.rebuild_region_index(variant)
                canvas.realisr_read_only = variant != "HR"
                canvas.setEnabled(True)
                canvas.set_editing(True)
        finally:
            self._syncing_selection = False
        self.zoom_factor = 1.0
        self.show_tiled_views(preserve_view=False)
        self.set_active_variant("HR")
        QtCore.QTimer.singleShot(0, self.fit_canvases)

    def fit_canvases(self):
        if not self.images:
            return
        displayed = self._displayed_variants()
        widths = [
            self.scroll_areas[variant].viewport().width()
            for variant in displayed
        ]
        heights = [
            self.scroll_areas[variant].viewport().height()
            for variant in displayed
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
            for variant in displayed:
                canvas = self.canvases[variant]
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
            for variant in self._displayed_variants():
                bars = self.scroll_bars[variant]
                for orientation, ratio in ratios.items():
                    bar = bars[orientation]
                    bar.setValue(round(ratio * bar.maximum()))
        finally:
            self._syncing_scroll = False

    def _zoom_request(self, variant, delta, _position):
        self.set_active_variant(variant)
        ratios = self._scroll_ratios(variant)
        self.zoom_factor = min(
            self.MAX_ZOOM_FACTOR,
            max(
                self.MIN_ZOOM_FACTOR,
                self.zoom_factor * (1.1 if delta > 0 else 0.9),
            ),
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
        displayed = self._displayed_variants()
        if self._syncing_scroll or variant not in displayed:
            return
        source = self.scroll_bars[variant][orientation]
        ratio = source.value() / source.maximum() if source.maximum() else 0.0
        self._syncing_scroll = True
        try:
            for other_variant in displayed:
                if other_variant == variant:
                    continue
                bars = self.scroll_bars[other_variant]
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
        region_ids = [
            self._region_id(shape)
            for shape in shapes
            if self._region_id(shape) is not None
        ]
        self._syncing_selection = True
        try:
            for current_variant, canvas in self.canvases.items():
                for shape in canvas.selected_shapes:
                    shape.selected = False
                index = self._region_shape_indexes[current_variant]
                selected = [
                    index[region_id]
                    for region_id in region_ids
                    if region_id in index
                ]
                for shape in selected:
                    shape.selected = True
                canvas.selected_shapes = selected
                if current_variant in self._displayed_variants():
                    canvas.update()
        finally:
            self._syncing_selection = False
        self.selection_changed.emit(variant, list(shapes))

    def select_region(self, region_id, notify=True):
        self.select_regions([region_id], notify=notify)

    def select_regions(self, region_ids, notify=True):
        region_ids = list(region_ids)
        index = self._region_shape_indexes[self.active_variant]
        shapes = [
            index[region_id] for region_id in region_ids if region_id in index
        ]
        if notify:
            self._selection_request(self.active_variant, shapes)
            return
        self._syncing_selection = True
        try:
            for current_variant, canvas in self.canvases.items():
                for old_shape in canvas.selected_shapes:
                    old_shape.selected = False
                index = self._region_shape_indexes[current_variant]
                selected = [
                    index[region_id]
                    for region_id in region_ids
                    if region_id in index
                ]
                for current_shape in selected:
                    current_shape.selected = True
                canvas.selected_shapes = selected
                if current_variant in self._displayed_variants():
                    canvas.update()
        finally:
            self._syncing_selection = False

    def _selected_region_shapes(self):
        """Return one corresponding selected region for every pane."""
        selected = self.active_canvas.selected_shapes
        if len(selected) != 1:
            return None
        region_id = self._region_id(selected[0])
        if region_id is None:
            return None
        region_shapes = {}
        for variant, canvas in self.canvases.items():
            shape = self._region_shape_indexes[variant].get(region_id)
            # Canvas.shapes is intentionally public and a few integration
            # paths replace or clear it directly.  Reject a stale cached entry
            # rather than changing focus semantics.
            if shape is None or shape not in canvas.shapes:
                return None
            region_shapes[variant] = shape
        return region_shapes

    def can_focus_selected_object(self):
        return self._selected_region_shapes() is not None

    def focus_selected_object(self, coverage=DEFAULT_FOCUS_COVERAGE):
        """Zoom and center all panes on their selected matching region."""
        if not 0 < coverage <= 1 or not self.images:
            return False
        region_shapes = self._selected_region_shapes()
        if region_shapes is None:
            return False

        rectangles = {}
        displayed = self._displayed_variants()
        for variant in displayed:
            shape = region_shapes[variant]
            try:
                rectangle = shape.bounding_rect()
            except (IndexError, TypeError, ValueError):
                return False
            dimensions = (rectangle.width(), rectangle.height())
            if (
                not all(math.isfinite(value) for value in dimensions)
                or rectangle.width() <= 0
                or rectangle.height() <= 0
            ):
                return False
            rectangles[variant] = rectangle

        # Re-establish each canvas' current base scale before deriving one
        # shared zoom factor.  A shared factor preserves the four-pane visual
        # correspondence while the minimum candidate guarantees every box
        # fits inside its pane's requested coverage.
        self.fit_canvases()
        candidates = []
        for variant, rectangle in rectangles.items():
            canvas = self.canvases[variant]
            viewport = self.scroll_areas[variant].viewport()
            if viewport.width() <= 0 or viewport.height() <= 0:
                return False
            base_scale = canvas.scale / max(self.zoom_factor, 1e-9)
            if not math.isfinite(base_scale) or base_scale <= 0:
                return False
            # Focusing normally introduces both scrollbars. Account for
            # their footprint before choosing the scale so the final visible
            # viewport still honors the requested coverage.
            target_width = max(
                1,
                viewport.width()
                - self.scroll_bars[variant][Qt.Orientation.Vertical]
                .sizeHint()
                .width(),
            )
            target_height = max(
                1,
                viewport.height()
                - self.scroll_bars[variant][Qt.Orientation.Horizontal]
                .sizeHint()
                .height(),
            )
            candidate = min(
                target_width * coverage / (rectangle.width() * base_scale),
                target_height * coverage / (rectangle.height() * base_scale),
            )
            if not math.isfinite(candidate) or candidate <= 0:
                return False
            candidates.append(candidate)

        self.zoom_factor = min(
            self.MAX_ZOOM_FACTOR,
            max(self.MIN_ZOOM_FACTOR, min(candidates)),
        )
        self.fit_canvases()
        QtCore.QTimer.singleShot(
            0,
            functools.partial(
                self._center_region_shapes, region_shapes, rectangles
            ),
        )
        return True

    def _center_region_shapes(self, region_shapes, rectangles):
        """Center matching regions after scrollbar ranges are recalculated."""
        self._syncing_scroll = True
        try:
            for variant in self._displayed_variants():
                shape = region_shapes[variant]
                canvas = self.canvases[variant]
                if shape not in canvas.shapes:
                    continue
                rectangle = rectangles[variant]
                center = rectangle.center() + canvas.offset_to_center()
                viewport = self.scroll_areas[variant].viewport()
                horizontal = self.scroll_bars[variant][
                    Qt.Orientation.Horizontal
                ]
                vertical = self.scroll_bars[variant][Qt.Orientation.Vertical]
                horizontal.setValue(
                    round(center.x() * canvas.scale - viewport.width() / 2)
                )
                vertical.setValue(
                    round(center.y() * canvas.scale - viewport.height() / 2)
                )
        finally:
            self._syncing_scroll = False

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QtCore.QTimer.singleShot(0, self.fit_canvases)
