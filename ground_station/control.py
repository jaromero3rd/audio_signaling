"""Shared visual-servoing commands for Tello RC (yaw / forward / up)."""

from __future__ import annotations

import math

import numpy as np

from ground_station.vision import Box

# Ryze Tello front camera (960x720, ~82.6° diagonal).
TELLO_DIAG_FOV_DEG = 82.6


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
) -> tuple[int, int, int, float | None, float | None]:
    """Turn (and optionally climb) first, then approach.

    Returns (yaw, forward, up, err_x, area_frac).
    If target_width is set, closeness is tag width / frame width (distance proxy).
    Image y grows downward, so a tag above center produces negative err_y and positive up.
    """
    if box is None:
        return 0, 0, 0, None, None
    fh, fw = frame_shape[:2]
    err_x = (box.cx / fw) - 0.5
    err_y = (box.cy / fh) - 0.5
    area = box.area_frac(frame_shape)
    width = tag_width_frac(box, frame_shape)
    yaw = clip_cmd(kp_yaw * err_x, max_yaw)
    up = clip_cmd(-kp_up * err_y, max_up) if kp_up and max_up else 0
    centered = abs(err_x) <= deadband and (not kp_up or abs(err_y) <= deadband)
    if not centered:
        return yaw, 0, up, err_x, area
    if target_width is not None:
        gap = target_width - width
    else:
        gap = target_area - area
    if gap <= 0:
        return 0, 0, 0, err_x, area
    fwd = clip_cmd(kp_fwd * gap, max_fwd)
    return 0, max(fwd, 0), 0, err_x, area
