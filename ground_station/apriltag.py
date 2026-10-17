"""AprilTag detection via OpenCV ArUco (DICT_APRILTAG_*), no extra native lib."""

from __future__ import annotations

import cv2
import numpy as np

from ground_station.vision import Box

FAMILIES = {
    "16h5": "DICT_APRILTAG_16h5",
    "25h9": "DICT_APRILTAG_25h9",
    "36h10": "DICT_APRILTAG_36h10",
    "36h11": "DICT_APRILTAG_36h11",
}


def dictionary_for(family: str):
    key = FAMILIES.get(family, family)
    if not hasattr(cv2.aruco, key):
        raise RuntimeError(
            f"OpenCV has no {key}. Need opencv-python>=4.7 with AprilTag dicts."
        )
    return cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, key))


def detect_markers(gray: np.ndarray, dictionary):
    if hasattr(cv2.aruco, "ArucoDetector"):
        params = cv2.aruco.DetectorParameters()
        det = cv2.aruco.ArucoDetector(dictionary, params)
        return det.detectMarkers(gray)
    return cv2.aruco.detectMarkers(gray, dictionary)


class AprilTagDetector:
    def __init__(self, family: str = "36h11", tag_id: int | None = 0) -> None:
        self.family = family
        self.tag_id = tag_id
        self._dict = dictionary_for(family)

    def detect_all(self, frame_bgr: np.ndarray) -> list[Box]:
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        corners, ids, _ = detect_markers(gray, self._dict)
        if ids is None or len(ids) == 0:
            return []
        boxes: list[Box] = []
        for pts, tid in zip(corners, ids.flatten()):
            if self.tag_id is not None and int(tid) != self.tag_id:
                continue
            quad = pts.reshape(-1, 2).astype(np.int32)
            x, y, w, h = cv2.boundingRect(quad)
            boxes.append(
                Box(x=x, y=y, w=w, h=h, conf=1.0, label=f"tag-{int(tid)}")
            )
        return boxes

    def detect(self, frame_bgr: np.ndarray) -> Box | None:
        boxes = self.detect_all(frame_bgr)
        if not boxes:
            return None
        return max(boxes, key=lambda b: b.w * b.h)
