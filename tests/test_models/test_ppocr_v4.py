import os

import numpy as np
from PyQt6 import QtCore

from anylabeling.services.auto_labeling import ppocr_v4 as ppocr_module
from anylabeling.services.auto_labeling.ppocr_v4 import PPOCRv4
from anylabeling.views.labeling.shape import Shape


def test_rec_char_dict_path_uses_existing_relative_path(tmp_path, monkeypatch):
    dict_path = tmp_path / "dict.txt"
    dict_path.write_text("a\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert PPOCRv4.get_rec_char_dict_path(
        {"rec_char_dict_path": "dict.txt"}, "/unused"
    ) == str(dict_path)


def test_rec_char_dict_path_uses_config_relative_path(tmp_path, monkeypatch):
    cwd = tmp_path / "cwd"
    config_dir = tmp_path / "config"
    cwd.mkdir()
    config_dir.mkdir()
    dict_path = config_dir / "dict.txt"
    dict_path.write_text("a\n", encoding="utf-8")
    monkeypatch.chdir(cwd)

    assert PPOCRv4.get_rec_char_dict_path(
        {
            "config_file": str(config_dir / "model.yaml"),
            "rec_char_dict_path": "dict.txt",
        },
        "/unused",
    ) == str(dict_path)


def test_rec_char_dict_path_keeps_lang_default():
    assert PPOCRv4.get_rec_char_dict_path(
        {"lang": "japan"}, "/x/auto_labeling"
    ) == os.path.join(
        "/x/auto_labeling", "configs/ppocr/japan_dict.txt"
    )


def test_recognition_only_preserves_shape_metadata(monkeypatch):
    shape = Shape(
        label="text",
        description="old",
        shape_type="quadrilateral",
        group_id=7,
    )
    for x, y in ((1, 2), (9, 2), (9, 8), (1, 8)):
        shape.add_point(QtCore.QPointF(x, y))
    shape.close()
    shape.selected = True
    shape.locked = True
    shape.other_data = {
        "region_id": "sample.png#0000",
        "recoverable": 2,
        "extension": {"keep": True},
    }

    model = PPOCRv4.__new__(PPOCRv4)
    model.text_sys = lambda _image, dt_boxes=None: (
        np.asarray(dt_boxes),
        [("recognized", 0.99)],
        [0.99],
        [0],
    )
    monkeypatch.setattr(
        ppocr_module,
        "qt_img_to_rgb_cv_img",
        lambda _image, _path: np.zeros((12, 12, 3), dtype=np.uint8),
    )
    monkeypatch.setattr(
        ppocr_module.cv2, "cvtColor", lambda image, _code: image
    )

    result = model.predict_shapes(
        object(), "sample.png", existing_shapes=[shape]
    )

    updated = result.shapes[0]
    assert updated is not shape
    assert updated.description == "recognized"
    assert updated.points == shape.points
    assert updated.group_id == 7
    assert updated.locked is True
    assert updated.other_data == shape.other_data


def test_recognition_only_preserves_region_ids_when_results_are_sorted(
    monkeypatch,
):
    first = Shape(label="text", shape_type="quadrilateral")
    second = Shape(label="text", shape_type="quadrilateral")
    for shape, region_id, offset in (
        (first, "sample.png#0000", 0),
        (second, "sample.png#0001", 20),
    ):
        for x, y in ((1, 2), (9, 2), (9, 8), (1, 8)):
            shape.add_point(QtCore.QPointF(x + offset, y))
        shape.close()
        shape.selected = True
        shape.other_data = {"region_id": region_id}

    model = PPOCRv4.__new__(PPOCRv4)
    model.text_sys = lambda _image, dt_boxes=None: (
        np.asarray([dt_boxes[1], dt_boxes[0]]),
        [("second recognized", 0.99), ("first recognized", 0.98)],
        [0.99, 0.98],
        [1, 0],
    )
    monkeypatch.setattr(
        ppocr_module,
        "qt_img_to_rgb_cv_img",
        lambda _image, _path: np.zeros((40, 40, 3), dtype=np.uint8),
    )
    monkeypatch.setattr(
        ppocr_module.cv2, "cvtColor", lambda image, _code: image
    )

    result = model.predict_shapes(
        object(), "sample.png", existing_shapes=[first, second]
    )

    assert [shape.other_data["region_id"] for shape in result.shapes] == [
        "sample.png#0001",
        "sample.png#0000",
    ]
    assert [shape.description for shape in result.shapes] == [
        "second recognized",
        "first recognized",
    ]
