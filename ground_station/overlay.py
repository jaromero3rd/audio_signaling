"""Readable OpenCV HUD text (dark panel + single white pass)."""

from __future__ import annotations

import cv2
import numpy as np

_FONT = cv2.FONT_HERSHEY_SIMPLEX
_SCALE = 0.55
_THICK = 1
_LINE_H = 22
_PAD = 8


def draw_hud_lines(
    vis: np.ndarray,
    lines: list[str],
    *,
    x: int = 10,
    y0: int = 8,
) -> None:
    """Draw status lines on a translucent black panel. One color, no outline."""
    rows = [ln for ln in lines if ln]
    if not rows:
        return
    widths = [cv2.getTextSize(ln, _FONT, _SCALE, _THICK)[0][0] for ln in rows]
    bw = max(widths) + _PAD * 2
    bh = _LINE_H * len(rows) + _PAD
    x0, y1 = max(x - 4, 0), y0
    x1, y2 = min(x0 + bw, vis.shape[1]), min(y1 + bh, vis.shape[0])
    panel = vis[y1:y2, x0:x1]
    if panel.size:
        vis[y1:y2, x0:x1] = (panel.astype(np.float32) * 0.35).astype(np.uint8)
    y = y0 + _PAD + 14
    for ln in rows:
        cv2.putText(vis, ln, (x + 4, y), _FONT, _SCALE, (255, 255, 255), _THICK, cv2.LINE_AA)
        y += _LINE_H
