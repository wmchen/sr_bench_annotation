"""Small offscreen benchmark for Canvas large-object interaction paths."""

import argparse
import os
import statistics
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtCore, QtGui, QtWidgets

from anylabeling.views.labeling.shape import Shape
from anylabeling.views.labeling.widgets.canvas import Canvas


def median_ms(function, repeats):
    samples = []
    for _ in range(repeats):
        started = time.perf_counter()
        function()
        samples.append((time.perf_counter() - started) * 1000.0)
    return statistics.median(samples)


def make_shapes(count):
    shapes = []
    for index in range(count):
        x = (index * 37) % 1800
        y = (index * 53) % 980
        shape = Shape(label=f"object-{index % 20}", shape_type="quadrilateral")
        shape.points = [
            QtCore.QPointF(x, y),
            QtCore.QPointF(x + 40, y),
            QtCore.QPointF(x + 40, y + 30),
            QtCore.QPointF(x, y + 30),
        ]
        shape.close()
        shapes.append(shape)
    return shapes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--counts", nargs="+", type=int, default=[100, 500, 1000]
    )
    args = parser.parse_args()

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    canvas = Canvas(parent=object())
    canvas.resize(960, 540)
    pixmap = QtGui.QPixmap(1920, 1080)
    pixmap.fill(QtGui.QColor("black"))
    canvas.load_pixmap(pixmap)
    canvas.scale = 0.5
    target = QtGui.QImage(960, 540, QtGui.QImage.Format.Format_ARGB32)

    for count in args.counts:
        canvas.reset_state()
        canvas.pixmap = pixmap
        canvas.shapes = make_shapes(count)
        canvas.store_shapes()
        canvas.render(target)  # warm geometry and Qt font caches
        hit_ms = median_ms(
            lambda: canvas._shape_hit_candidates(QtCore.QPointF(1900, 1050)),
            10,
        )
        paint_ms = median_ms(lambda: canvas.render(target), 5)

        def store_changed_shape():
            canvas.shapes[0].move_by(QtCore.QPointF(1, 0))
            canvas.store_shapes()

        history_ms = median_ms(store_changed_shape, 5)
        print(
            f"objects={count:5d} hit={hit_ms:8.2f}ms "
            f"paint={paint_ms:8.2f}ms history={history_ms:8.2f}ms"
        )

    del app


if __name__ == "__main__":
    main()
