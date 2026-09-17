"""Qt-free wrappers around the existing OCR and SCRFD numerical pipelines."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import onnxruntime as ort
import yaml

from ...config import ModelConfig
from ...domain.rules import ocr_geometry
from .vendor.ocr_defaults import parse_args
from .vendor.scrfd_core import ScrfdCore
from .vendor.text_system import TextSystem


def make_session(
    path: Path, device: str, threads: int, cache: Path
) -> ort.InferenceSession:
    """Construct an explicit provider session with observable warm-up."""
    options = ort.SessionOptions()
    options.intra_op_num_threads = threads
    options.inter_op_num_threads = 1
    options.log_severity_level = 3
    options.enable_profiling = True
    options.profile_file_prefix = str(cache / ("ort-" + path.stem))
    providers = (
        ["CPUExecutionProvider"]
        if device == "cpu"
        else [
            ("CUDAExecutionProvider", {"device_id": 0}),
            "CPUExecutionProvider",
        ]
    )
    if (
        device != "cpu"
        and "CUDAExecutionProvider" not in ort.get_available_providers()
    ):
        raise RuntimeError("CUDAExecutionProvider 不可用；未降级到 CPU")
    session = ort.InferenceSession(
        str(path), sess_options=options, providers=providers
    )
    session.disable_fallback()
    if (
        device != "cpu"
        and session.get_providers()[0] != "CUDAExecutionProvider"
    ):
        raise RuntimeError("GPU 模型加载失败；未降级到 CPU")
    return session


def verify_warmup(sessions: dict, device: str) -> list[dict]:
    """Run each ONNX graph and verify CUDA kernels in the actual profile."""
    details = []
    for key, session in sessions.items():
        defaults = (
            [1, 3, 640, 640]
            if key in ("model", "det")
            else [1, 3, 48, 320 if key == "rec" else 192]
        )
        info = session.get_inputs()[0]
        shape = [
            value if isinstance(value, int) and value > 0 else defaults[i]
            for i, value in enumerate(info.shape)
        ]
        session.run(None, {info.name: np.zeros(shape, dtype=np.float32)})
        profile = Path(session.end_profiling())
        try:
            events = json.loads(profile.read_text())
            providers = sorted(
                {
                    e.get("args", {}).get("provider")
                    for e in events
                    if e.get("args", {}).get("provider")
                }
            )
            if device != "cpu" and "CUDAExecutionProvider" not in providers:
                raise RuntimeError(f"{key}: warm-up 未记录 CUDA 运算")
            details.append({"component": key, "providers": providers})
        finally:
            profile.unlink(missing_ok=True)
    return details


class OCRPredictor:
    """PPOCR v6 pipeline with immutable per-request confidence semantics."""

    def __init__(
        self, config: ModelConfig, device: str, threads: int, cache: Path
    ) -> None:
        self.sessions = {
            key: make_session(config.files[key], device, threads, cache)
            for key in ("det", "rec", "cls")
        }
        values = SimpleNamespace(
            det_net=self.sessions["det"],
            rec_net=self.sessions["rec"],
            cls_net=self.sessions["cls"],
            rec_char_dict_path=str(config.files["dictionary"]),
            current_dir=str(cache),
            drop_score=config.parameters.get("drop_score", 0.5),
            use_angle_cls=config.parameters.get("use_angle_cls", True),
        )
        args = parse_args(values)
        # Preserve v6 inference.yml defaults, overridden by registered config.
        parameters = {}
        yml = config.files.get("det_config")
        if yml:
            post = (yaml.safe_load(yml.read_text()) or {}).get(
                "PostProcess", {}
            )
            for key in (
                "thresh",
                "box_thresh",
                "unclip_ratio",
                "max_candidates",
            ):
                if key in post:
                    parameters["det_db_" + key] = post[key]
        parameters.update(config.parameters)
        for key, value in parameters.items():
            setattr(args, key, value)
        self.system = TextSystem(args)
        self.drop_score = args.drop_score

    def predict(
        self, image: np.ndarray, regions: list[dict] | None
    ) -> list[dict]:
        """Return detections or ID-addressed transcription-only replacements."""
        boxes = [r["points"] for r in regions] if regions else None
        self.system.drop_score = self.drop_score
        output = self.system(
            cv2.cvtColor(image, cv2.COLOR_RGB2BGR), dt_boxes=boxes
        )
        if not output or len(output) != 4 or output[0] is None:
            return []
        points, recognized, scores, order = output
        if regions:
            return [
                {
                    "region_id": regions[order[i]]["region_id"],
                    "description": result[0],
                }
                for i, result in enumerate(recognized)
            ]
        result = []
        for index, box in enumerate(points):
            kind, geometry = ocr_geometry(box)
            result.append(
                {
                    "points": geometry,
                    "description": recognized[index][0],
                    "score": float(scores[index]),
                    "shape_type": kind,
                    "group_id": index,
                    "label": "text",
                    "recoverable": 0,
                }
            )
        return result


class SCRFDPredictor(ScrfdCore):
    """Keep desktop RGB preprocessing, NMS and rectangle output ordering."""

    def __init__(
        self, config: ModelConfig, device: str, threads: int, cache: Path
    ) -> None:
        session = make_session(config.files["model"], device, threads, cache)
        self.sessions = {"model": session}
        self.net = SimpleNamespace(
            get_ort_inference=lambda inputs, extract=False: session.run(
                None, inputs
            )
        )
        self.input_name = session.get_inputs()[0].name
        self.input_width = config.parameters.get("input_width", 640)
        self.input_height = config.parameters.get("input_height", 640)
        self.conf_thres = config.parameters.get("conf_threshold", 0.5)
        self.iou_thres = config.parameters.get("iou_threshold", 0.4)
        self.max_det = config.parameters.get("max_det", 0)
        self.center_cache = {}
        count = len(session.get_outputs())
        if count not in (6, 9, 10, 15):
            raise ValueError(f"Unsupported SCRFD output count: {count}")
        self.use_kps = count in (9, 15)
        self.fmc = 5 if count in (10, 15) else 3
        self.feat_stride_fpn = [8, 16, 32, 64, 128][: self.fmc]
        self.num_anchors = 1 if self.fmc == 5 else 2

    def predict(
        self, image: np.ndarray, regions: list[dict] | None
    ) -> list[dict]:
        """Detect only horizontal face rectangles; leave evidence unset."""
        blob, scale = self.preprocess(image)
        detections, _ = self.postprocess(blob, scale)
        height, width = image.shape[:2]
        result = []
        for index, box in enumerate(reversed(detections)):
            x1, y1, x2, y2 = [int(x) for x in box[:4]]
            x1, x2 = max(0, min(width, x1)), max(0, min(width, x2))
            y1, y2 = max(0, min(height, y1)), max(0, min(height, y2))
            if x1 >= x2 or y1 >= y2:
                continue
            result.append(
                {
                    "label": "face",
                    "group_id": index,
                    "description": "",
                    "shape_type": "rectangle",
                    "points": [[x1, y1], [x2, y1], [x2, y2], [x1, y2]],
                    "score": float(box[4]),
                    "recoverable": None,
                }
            )
        return result
