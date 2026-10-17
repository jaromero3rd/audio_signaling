"""Offline AprilTag detection + simple seek/hold (not supervised training).

Reuses ground_station.apriltag / control when the repo is on PYTHONPATH;
falls back to a local OpenCV ArUco path otherwise.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from .config import Config


@dataclass
class SeekCommand:
    yaw: int
    forward: int
    up: int
    tag_label: str | None
    err_x: float | None
    area: float | None
    holding: bool


class FrameSource:
    def read(self) -> np.ndarray | None:
        raise NotImplementedError

    def release(self) -> None:
        pass


class CameraFrameSource(FrameSource):
    def __init__(self, index_or_path: str) -> None:
        src: Any = int(index_or_path) if index_or_path.isdigit() else index_or_path
        self._cap = cv2.VideoCapture(src)
        if not self._cap.isOpened():
            raise RuntimeError(f"cannot open camera: {index_or_path}")

    def read(self) -> np.ndarray | None:
        ok, frame = self._cap.read()
        return frame if ok else None

    def release(self) -> None:
        self._cap.release()


class MockFrameSource(FrameSource):
    """Synthetic frames with a moving dark square (stand-in when no cam/tags).

    For real tag demos, point UNO_Q_MOCK_FRAMES_DIR at printed-tag photos or
    use a USB cam. This mock only exercises the control loop wiring.
    """

    def __init__(self, frames_dir: str | None = None) -> None:
        self._images: list[np.ndarray] = []
        if frames_dir:
            p = Path(frames_dir)
            if p.is_dir():
                for fp in sorted(p.glob("*")):
                    if fp.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp"}:
                        img = cv2.imread(str(fp))
                        if img is not None:
                            self._images.append(img)
        self._i = 0
        self._t0 = time.time()

    def read(self) -> np.ndarray | None:
        if self._images:
            frame = self._images[self._i % len(self._images)]
            self._i += 1
            return frame.copy()
        # Draw a blob that drifts horizontally so yaw error is non-zero.
        h, w = 360, 640
        frame = np.full((h, w, 3), 40, dtype=np.uint8)
        t = time.time() - self._t0
        cx = int(w * (0.35 + 0.3 * abs((t % 6) / 3 - 1)))
        cy = h // 2
        size = 40
        cv2.rectangle(frame, (cx - size, cy - size), (cx + size, cy + size), (220, 220, 220), -1)
        # Put a fake ID text — detector may miss this; seek uses blob fallback in mock.
        cv2.putText(frame, "MOCK", (cx - 30, cy - size - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
        return frame


def make_frame_source(cfg: Config) -> FrameSource:
    if cfg.camera == "mock" or cfg.mock:
        return MockFrameSource(cfg.mock_frames_dir)
    try:
        return CameraFrameSource(cfg.camera)
    except RuntimeError:
        if cfg.mock:
            return MockFrameSource(cfg.mock_frames_dir)
        raise


class AprilTagSeeker:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._detector = None
        self._box_cls = None
        self._yaw_forward = None
        self._init_detector()
        self.frames = make_frame_source(cfg)
        self._search_yaw = 18

    def _init_detector(self) -> None:
        try:
            from ground_station.apriltag import AprilTagDetector
            from ground_station.control import yaw_forward
            from ground_station.vision import Box

            # Prefer first configured ID; detector filters to one ID.
            tag_id = self.cfg.apriltag_ids[0] if self.cfg.apriltag_ids else None
            self._detector = AprilTagDetector(family=self.cfg.apriltag_family, tag_id=tag_id)
            self._detector_all = AprilTagDetector(family=self.cfg.apriltag_family, tag_id=None)
            self._yaw_forward = yaw_forward
            self._box_cls = Box
        except ImportError:
            self._detector = _LocalAprilTag(self.cfg.apriltag_family, self.cfg.apriltag_ids)
            self._detector_all = self._detector
            self._yaw_forward = _local_yaw_forward
            self._box_cls = _LocalBox

    def tick(self) -> SeekCommand:
        frame = self.frames.read()
        if frame is None:
            return SeekCommand(0, 0, 0, None, None, None, False)

        box = None
        if hasattr(self._detector_all, "detect_all"):
            boxes = self._detector_all.detect_all(frame)
            wanted = set(self.cfg.apriltag_ids)
            if wanted:
                boxes = [b for b in boxes if _tag_id_from_label(b.label) in wanted] or boxes
            if boxes:
                box = max(boxes, key=lambda b: b.w * b.h)
        elif hasattr(self._detector, "detect"):
            box = self._detector.detect(frame)

        if box is None and self.cfg.mock:
            box = _largest_bright_blob(frame, self._box_cls)

        if box is None:
            return SeekCommand(self._search_yaw, 0, 0, None, None, None, False)

        yaw, fwd, up, err_x, area = self._yaw_forward(
            box,
            frame.shape,
            kp_yaw=160.0,
            kp_fwd=80.0,
            max_yaw=40,
            max_fwd=20,
            deadband=self.cfg.seek_deadband,
            target_area=self.cfg.seek_target_area,
            kp_up=0.0,
            max_up=0,
        )
        holding = yaw == 0 and fwd == 0
        return SeekCommand(yaw, fwd, up, getattr(box, "label", None), err_x, area, holding)

    def close(self) -> None:
        self.frames.release()


def _tag_id_from_label(label: str) -> int | None:
    # labels like "tag-0"
    if not label:
        return None
    try:
        return int(str(label).split("-")[-1])
    except ValueError:
        return None


@dataclass
class _LocalBox:
    x: int
    y: int
    w: int
    h: int
    conf: float = 1.0
    label: str = "tag"

    @property
    def cx(self) -> float:
        return self.x + self.w / 2.0

    @property
    def cy(self) -> float:
        return self.y + self.h / 2.0

    def area_frac(self, shape: tuple[int, ...]) -> float:
        fh, fw = shape[:2]
        return (self.w * self.h) / float(fw * fh)


class _LocalAprilTag:
    def __init__(self, family: str, ids: list[int]) -> None:
        self.ids = set(ids)
        key = {
            "16h5": "DICT_APRILTAG_16h5",
            "25h9": "DICT_APRILTAG_25h9",
            "36h10": "DICT_APRILTAG_36h10",
            "36h11": "DICT_APRILTAG_36h11",
        }.get(family, family)
        if not hasattr(cv2.aruco, key):
            raise RuntimeError(f"OpenCV missing {key}")
        self._dict = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, key))

    def detect_all(self, frame_bgr: np.ndarray) -> list[_LocalBox]:
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        if hasattr(cv2.aruco, "ArucoDetector"):
            det = cv2.aruco.ArucoDetector(self._dict, cv2.aruco.DetectorParameters())
            corners, ids, _ = det.detectMarkers(gray)
        else:
            corners, ids, _ = cv2.aruco.detectMarkers(gray, self._dict)
        if ids is None:
            return []
        out: list[_LocalBox] = []
        for pts, tid in zip(corners, ids.flatten()):
            tid_i = int(tid)
            if self.ids and tid_i not in self.ids:
                continue
            quad = pts.reshape(-1, 2).astype(np.int32)
            x, y, w, h = cv2.boundingRect(quad)
            out.append(_LocalBox(x, y, w, h, label=f"tag-{tid_i}"))
        return out

    def detect(self, frame_bgr: np.ndarray) -> _LocalBox | None:
        boxes = self.detect_all(frame_bgr)
        return max(boxes, key=lambda b: b.w * b.h) if boxes else None


def _local_yaw_forward(
    box: Any,
    frame_shape: tuple[int, ...],
    *,
    kp_yaw: float,
    kp_fwd: float,
    max_yaw: int,
    max_fwd: int,
    deadband: float,
    target_area: float,
    kp_up: float = 0.0,
    max_up: int = 0,
) -> tuple[int, int, int, float | None, float | None]:
    fh, fw = frame_shape[:2]
    err_x = (box.cx / fw) - 0.5
    area = box.area_frac(frame_shape)
    yaw = int(np.clip(round(kp_yaw * err_x), -max_yaw, max_yaw))
    if abs(err_x) > deadband:
        return yaw, 0, 0, err_x, area
    gap = target_area - area
    if gap <= 0:
        return 0, 0, 0, err_x, area
    fwd = int(np.clip(round(kp_fwd * gap), 0, max_fwd))
    return 0, fwd, 0, err_x, area


def _largest_bright_blob(frame: np.ndarray, box_cls: type) -> Any | None:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    _, th = cv2.threshold(gray, 160, 255, cv2.THRESH_BINARY)
    contours, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    c = max(contours, key=cv2.contourArea)
    x, y, w, h = cv2.boundingRect(c)
    if w * h < 200:
        return None
    return box_cls(x=x, y=y, w=w, h=h, conf=0.5, label="mock-blob")
