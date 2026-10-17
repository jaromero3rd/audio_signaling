"""Tello (and webcam mock) video + RC interface."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np

CAPTURE_DIR = Path("captures")


class Drone(Protocol):
    def connect(self) -> None: ...
    def stream_on(self) -> None: ...
    def takeoff(self) -> None: ...
    def land(self) -> None: ...
    def emergency(self) -> None: ...
    def get_frame(self) -> np.ndarray | None: ...
    def send_rc(self, left_right: int, forward: int, up: int, yaw: int) -> None: ...
    def hover(self) -> None: ...
    def capture_photo(self, path: Path | None = None) -> Path | None: ...
    def battery(self) -> int | None: ...
    def close(self) -> None: ...


def _ensure_bgr(frame: np.ndarray) -> np.ndarray:
    """djitellopy returns RGB; OpenCV drawing expects BGR."""
    if frame.ndim == 3 and frame.shape[2] == 3:
        return cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
    return frame


class TelloDrone:
    def __init__(
        self,
        iface: str | None = None,
        *,
        ssid: str | None = None,
        dongle: int | None = None,
        connect_wifi: bool = True,
    ) -> None:
        from djitellopy import Tello

        from drone_interface.tello_net import prepare_tello_link

        self._link = prepare_tello_link(
            iface, ssid=ssid, dongle=dongle, connect=connect_wifi
        )
        self._tello = Tello()
        self._frame_read = None
        self._last_frame: np.ndarray | None = None
        self._last_frame_ts = 0.0

    def connect(self) -> None:
        self._tello.connect()
        print(f"Tello battery {self._tello.get_battery()}%")

    def stream_on(self) -> None:
        self._tello.streamon()
        self._frame_read = self._tello.get_frame_read()
        time.sleep(0.5)

    def takeoff(self) -> None:
        self._tello.takeoff()

    def land(self) -> None:
        self.hover()
        self._tello.land()

    def emergency(self) -> None:
        self.hover()
        try:
            self._tello.emergency()
        except Exception:
            self._tello.land()

    def get_frame(self) -> np.ndarray | None:
        if self._frame_read is None:
            return None
        frame = self._frame_read.frame
        if frame is None or frame.size == 0:
            return self._last_frame
        bgr = _ensure_bgr(frame)
        self._last_frame = bgr
        self._last_frame_ts = time.time()
        return bgr

    def send_rc(self, left_right: int, forward: int, up: int, yaw: int) -> None:
        self._tello.send_rc_control(
            int(np.clip(left_right, -100, 100)),
            int(np.clip(forward, -100, 100)),
            int(np.clip(up, -100, 100)),
            int(np.clip(yaw, -100, 100)),
        )

    def hover(self) -> None:
        self.send_rc(0, 0, 0, 0)

    def capture_photo(self, path: Path | None = None) -> Path | None:
        frame = self.get_frame()
        if frame is None:
            return None
        CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
        out = path or CAPTURE_DIR / f"tello_{int(time.time())}.jpg"
        cv2.imwrite(str(out), frame)
        return out

    def battery(self) -> int | None:
        try:
            return int(self._tello.get_battery())
        except Exception:
            return None

    def close(self) -> None:
        self.hover()
        try:
            self._tello.streamoff()
        except Exception:
            pass
        try:
            self._tello.end()
        except Exception:
            pass


class MockDrone:
    """Laptop webcam stand-in: prints RC instead of flying."""

    def __init__(self, camera: int = 0) -> None:
        self._camera = camera
        self._cap: cv2.VideoCapture | None = None
        self._last_frame: np.ndarray | None = None

    def connect(self) -> None:
        print("Mock drone: using webcam (no flight)")

    def stream_on(self) -> None:
        self._cap = cv2.VideoCapture(self._camera)
        if not self._cap.isOpened():
            raise RuntimeError(f"Could not open webcam index {self._camera}")

    def takeoff(self) -> None:
        print("mock takeoff")

    def land(self) -> None:
        print("mock land")

    def emergency(self) -> None:
        print("mock emergency")

    def get_frame(self) -> np.ndarray | None:
        if self._cap is None:
            return None
        ok, frame = self._cap.read()
        if not ok:
            return self._last_frame
        self._last_frame = frame
        return frame

    def send_rc(self, left_right: int, forward: int, up: int, yaw: int) -> None:
        print(f"rc lr={left_right:+d} fwd={forward:+d} up={up:+d} yaw={yaw:+d}")

    def hover(self) -> None:
        self.send_rc(0, 0, 0, 0)

    def capture_photo(self, path: Path | None = None) -> Path | None:
        frame = self.get_frame()
        if frame is None:
            return None
        CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
        out = path or CAPTURE_DIR / f"mock_{int(time.time())}.jpg"
        cv2.imwrite(str(out), frame)
        return out

    def battery(self) -> int | None:
        return 100

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None
