"""Shared visual-servoing commands for Tello RC (yaw / forward / up)."""

from __future__ import annotations

import numpy as np

from ground_station.vision import Box


def clip_cmd(value: float, limit: int) -> int:
    return int(np.clip(round(value), -limit, limit))


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
) -> tuple[int, int, int, float | None, float | None]:
    """Turn (and optionally climb) first, then approach.

    Returns (yaw, forward, up, err_x, area_frac).
    Image y grows downward, so a tag above center produces negative err_y and positive up.
    """
    if box is None:
        return 0, 0, 0, None, None
    fh, fw = frame_shape[:2]
    err_x = (box.cx / fw) - 0.5
    err_y = (box.cy / fh) - 0.5
    area = box.area_frac(frame_shape)
    yaw = clip_cmd(kp_yaw * err_x, max_yaw)
    up = clip_cmd(-kp_up * err_y, max_up) if kp_up and max_up else 0
    centered = abs(err_x) <= deadband and (not kp_up or abs(err_y) <= deadband)
    if not centered:
        return yaw, 0, up, err_x, area
    gap = target_area - area
    if gap <= 0:
        return 0, 0, 0, err_x, area
    fwd = clip_cmd(kp_fwd * gap, max_fwd)
    return 0, max(fwd, 0), 0, err_x, area
