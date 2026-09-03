"""Choose quadrilateral vertex order by clicking a rectangle diagram."""

from PyQt6 import QtCore, QtGui, QtWidgets

from ..utils.style import (
    get_cancel_btn_style,
    get_dialog_style,
    get_ok_btn_style,
)
from ..utils.theme import get_theme


class QuadrilateralOrderPreview(QtWidgets.QWidget):
    start_selected = QtCore.pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.start_corner = None
        self.clockwise = True
        self.setMinimumSize(300, 200)
        self.corner_group = QtWidgets.QButtonGroup(self)
        self.corner_buttons = []
        theme = get_theme()
        for index in range(4):
            button = QtWidgets.QToolButton(self)
            button.setCheckable(True)
            button.setFixedSize(40, 40)
            button.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            button.setAccessibleName(self.tr("Corner %d") % (index + 1))
            button.setToolTip(self.tr("Click to choose the start vertex"))
            button.setStyleSheet(f"""
                QToolButton {{
                    border: 2px solid {theme['primary']};
                    border-radius: 20px;
                    background: {theme['background']};
                    color: {theme['primary']};
                    font-size: 16px;
                    font-weight: 600;
                }}
                QToolButton:hover {{ background: {theme['surface_hover']}; }}
                QToolButton:checked {{
                    background: {theme['primary']};
                    color: {theme['selection_text']};
                }}
                QToolButton:focus {{ border: 3px solid {theme['text']}; }}
            """)
            self.corner_group.addButton(button, index)
            self.corner_buttons.append(button)
        self.corner_group.idClicked.connect(self._select_start)

    def sizeHint(self):
        return QtCore.QSize(380, 240)

    def vertex_order(self):
        if self.start_corner is None:
            return []
        step = 1 if self.clockwise else -1
        return [(self.start_corner + step * index) % 4 for index in range(4)]

    def _select_start(self, index):
        self.start_corner = index
        self._update_numbers()
        self.start_selected.emit()

    def set_clockwise(self, clockwise):
        self.clockwise = clockwise
        self._update_numbers()

    def _update_numbers(self):
        for order, corner in enumerate(self.vertex_order(), start=1):
            self.corner_buttons[corner].setText(str(order))
        self.update()

    def _corners(self):
        return [
            QtCore.QPointF(40, 40),
            QtCore.QPointF(self.width() - 40, 40),
            QtCore.QPointF(self.width() - 40, self.height() - 40),
            QtCore.QPointF(40, self.height() - 40),
        ]

    def resizeEvent(self, event):
        super().resizeEvent(event)
        for point, button in zip(self._corners(), self.corner_buttons):
            button.move(round(point.x()) - 20, round(point.y()) - 20)

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        theme = get_theme()
        corners = self._corners()
        painter.fillRect(
            QtCore.QRectF(corners[0], corners[2]),
            QtGui.QColor(theme["background_secondary"]),
        )
        color = QtGui.QColor(
            theme[
                (
                    "primary"
                    if self.start_corner is not None
                    else "text_secondary"
                )
            ]
        )
        painter.setPen(QtGui.QPen(color, 2))
        painter.setBrush(color)
        order = self.vertex_order() or (
            [0, 1, 2, 3] if self.clockwise else [0, 3, 2, 1]
        )
        for index, corner in enumerate(order):
            start = corners[corner]
            end = corners[order[(index + 1) % 4]]
            painter.drawLine(start, end)
            delta = end - start
            length = QtCore.QLineF(start, end).length()
            unit = delta / length
            normal = QtCore.QPointF(-unit.y(), unit.x())
            midpoint = (start + end) / 2
            painter.drawPolygon(
                QtGui.QPolygonF(
                    [
                        midpoint + unit * 8,
                        midpoint - unit * 6 + normal * 5,
                        midpoint - unit * 6 - normal * 5,
                    ]
                )
            )
        painter.end()


class QuadrilateralConversionDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Rectangle to Quadrilateral"))
        theme = get_theme()
        self.setStyleSheet(
            get_dialog_style()
            + f"QRadioButton {{ color: {theme['text']}; spacing: 6px; }}"
        )
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)
        prompt = QtWidgets.QLabel(
            self.tr(
                "Click a corner in the diagram to choose the start vertex."
            )
        )
        prompt.setWordWrap(True)
        layout.addWidget(prompt)
        self.preview = QuadrilateralOrderPreview(self)
        layout.addWidget(self.preview)
        direction_layout = QtWidgets.QHBoxLayout()
        direction_layout.addWidget(QtWidgets.QLabel(self.tr("Vertex order:")))
        self.clockwise_button = QtWidgets.QRadioButton(self.tr("Clockwise"))
        self.counterclockwise_button = QtWidgets.QRadioButton(
            self.tr("Counterclockwise")
        )
        self.clockwise_button.setChecked(True)
        self.clockwise_button.toggled.connect(self.preview.set_clockwise)
        direction_layout.addWidget(self.clockwise_button)
        direction_layout.addWidget(self.counterclockwise_button)
        direction_layout.addStretch()
        layout.addLayout(direction_layout)
        hint = QtWidgets.QLabel(
            self.tr(
                "The filled point is the start. Follow the arrows from 1 to 4."
            )
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        buttons = QtWidgets.QHBoxLayout()
        buttons.addStretch()
        self.cancel_button = QtWidgets.QPushButton(self.tr("Cancel"))
        self.cancel_button.setStyleSheet(get_cancel_btn_style())
        self.cancel_button.setAutoDefault(False)
        self.cancel_button.clicked.connect(self.reject)
        self.confirm_button = QtWidgets.QPushButton(self.tr("Convert"))
        self.confirm_button.setStyleSheet(get_ok_btn_style() + f"""
                QPushButton:disabled {{
                    background: {theme['surface']};
                    color: {theme['text_secondary']};
                    border: 1px solid {theme['border']};
                }}
            """)
        self.confirm_button.setDefault(True)
        self.confirm_button.setEnabled(False)
        self.confirm_button.clicked.connect(self.accept)
        self.preview.start_selected.connect(
            lambda: self.confirm_button.setEnabled(True)
        )
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.confirm_button)
        layout.addLayout(buttons)
        self.resize(440, 420)
        self.clockwise_button.setFocus()

    def vertex_order(self):
        return self.preview.vertex_order()

    def accept(self):
        if self.vertex_order():
            super().accept()
