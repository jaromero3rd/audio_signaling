"""Optional lightweight nav-assist stub — NOT AprilTag training.

If Arduino / Edge Impulse tooling later hosts a tiny visual-servoing helper
(e.g. “is target left/right/center”), plug it here. Offline autonomy today
uses real OpenCV AprilTag detection in apriltag_seek.py.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class NavAssistHint:
    """Coarse heading hint in [-1, 1] (left…right). None = no opinion."""

    bias_x: float | None
    confidence: float
    note: str


class NavAssistStub:
    def __init__(self, enabled: bool = False) -> None:
        self.enabled = enabled

    def hint(self, frame_shape: tuple[int, ...] | None = None) -> NavAssistHint:
        if not self.enabled:
            return NavAssistHint(None, 0.0, "disabled")
        # Placeholder: no model loaded. Keep honest for demos/judges.
        return NavAssistHint(
            None,
            0.0,
            "stub only — load a real assist model here; AprilTags are detected separately",
        )
