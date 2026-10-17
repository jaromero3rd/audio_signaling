"""Shared visual-servoing commands for Tello RC (strafe / yaw / forward / up)."""

from __future__ import annotations

import math
from typing import NamedTuple

import numpy as np

from ground_station.vision import Box

# Ryze Tello front camera (960x720, ~82.6° diagonal).
TELLO_DIAG_FOV_DEG = 82.6


class RcOut(NamedTuple):
    yaw: int
    fwd: int
    up: int
    lr: int
    err_x: float | None
    area: float | None


def clip_cmd(value: float, limit: int) -> int:
    return int(np.clip(round(value), -limit, limit))


def tag_width_frac(box: Box, frame_shape: tuple[int, ...]) -> float:
    return box.w / max(frame_shape[1], 1)


def width_frac_for_distance(
    tag_size_m: float,
    distance_m: float,
    frame_shape: tuple[int, ...],
    diag_fov_deg: float = TELLO_DIAG_FOV_DEG,
) -> float:
    """Approx. tag width / frame width at `distance_m` (pinhole + Tello FOV)."""
    fh, fw = frame_shape[:2]
    diag_px = math.hypot(fw, fh)
    focal_px = (diag_px / 2) / math.tan(math.radians(diag_fov_deg / 2))
    width_px = focal_px * tag_size_m / max(distance_m, 0.05)
    return float(np.clip(width_px / fw, 0.12, 0.45))


def yaw_forward(
    box: Box | None,
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
    target_width: float | None = None,
    kp_lr: float = 110.0,
    max_lr: int = 30,
    lr_trim: int = 0,
    invert_yaw: bool = False,
    invert_lr: bool = False,
    forward_gate: float = 0.18,
) -> RcOut:
    """Strafe/yaw to center, creep forward once roughly aligned.

    Tello left_right: negative = left, positive = right.
    Tag left of center (err_x < 0) → slide left.
    """
    if box is None:
        return RcOut(0, 0, 0, 0, None, None)
    fh, fw = frame_shape[:2]
    err_x = (box.cx / fw) - 0.5
    err_y = (box.cy / fh) - 0.5
    area = box.area_frac(frame_shape)
    width = tag_width_frac(box, frame_shape)
    yaw = clip_cmd(kp_yaw * err_x, max_yaw)
    lr = clip_cmd(kp_lr * err_x + lr_trim, max_lr)
    if invert_yaw:
        yaw = -yaw
    if invert_lr:
        lr = -lr
    up = clip_cmd(-kp_up * err_y, max_up) if kp_up and max_up else 0
    if abs(err_x) > max(deadband, forward_gate):
        return RcOut(yaw, 0, up, lr, err_x, area)
    if target_width is not None:
        gap = target_width - width
    else:
        gap = target_area - area
    if gap <= 0:
        return RcOut(yaw, 0, up, lr, err_x, area)
    fwd = max(clip_cmd(kp_fwd * gap, max_fwd), 0)
    return RcOut(yaw, fwd, up, lr, err_x, area)
