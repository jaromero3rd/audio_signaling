"""Hands-free survey: takeoff → find wheat/tag → photo → home AprilTag → land."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ground_station.apriltag import AprilTagDetector
from ground_station.control import yaw_forward
from ground_station.vision import DEFAULT_WEIGHTS, Box, union_boxes

SEARCH_YAW = 22
SEARCH_TIMEOUT_S = 40.0
APPROACH_TIMEOUT_S = 25.0
DWELL_S = 0.8
FRAME_STALE_S = 0.5


@dataclass
class Gains:
    kp_yaw: float = 160.0
    kp_fwd: float = 80.0
    kp_up: float = 120.0
    max_yaw: int = 40
    max_fwd: int = 20
    max_up: int = 20
    deadband: float = 0.08
    target_area: float = 0.12


@dataclass
class Tick:
    yaw: int = 0
    fwd: int = 0
    up: int = 0
    action: str | None = None  # takeoff | land | photo | None
    box: Box | None = None
    err_x: float | None = None
    area: float | None = None
    stale: bool = False


def _make_target_detector(kind: str, family: str, target_tag: int | None, weights: Path):
    if kind == "wheat":
        from ground_station.vision import YoloWheatHeadDetector

        return YoloWheatHeadDetector(weights=weights)
    return AprilTagDetector(family=family, tag_id=target_tag)


def resolve_target(kind: str, family: str, target_tag: int | None, weights: Path):
    if kind == "auto":
        if weights.is_file():
            kind = "wheat"
        else:
            kind = "tag"
            print("wheat weights missing; target is AprilTag (same pipeline as home)")
    if kind == "wheat" and not weights.is_file():
        print(f"no {weights}; falling back to AprilTag target")
        kind = "tag"
    detector = _make_target_detector(kind, family, target_tag, weights)
    return kind, detector


class SurveyMission:
    """Thread-safe state machine. Grok (or --auto) calls request_start / request_abort."""

    def __init__(
        self,
        *,
        target_kind: str,
        family: str,
        target_tag: int | None,
        home_tag: int,
        gains: Gains,
        weights: Path = DEFAULT_WEIGHTS,
        on_phase: Callable[[str, str], None] | None = None,
    ) -> None:
        self.gains = gains
        self.home_tag = home_tag
        self.target_kind, self.target_det = resolve_target(
            target_kind, family, target_tag, weights
        )
        self.home_det = AprilTagDetector(family=family, tag_id=home_tag)
        self.same_mark = self.target_kind == "tag" and target_tag == home_tag
        self.on_phase = on_phase
        self._lock = threading.Lock()
        self.phase = "idle"
        self.flying = False
        self.target_seen = False
        self.home_seen = False
        self.last_photo: str | None = None
        self._phase_t = time.time()
        self._dwell_t: float | None = None
        self._takeoff_sent = False
        self._land_sent = False
        self._photo_sent = False

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "phase": self.phase,
                "flying": self.flying,
                "target": self.target_kind,
                "home_tag": self.home_tag,
                "target_seen": self.target_seen,
                "home_seen": self.home_seen,
                "last_photo": self.last_photo,
            }

    def request_start(self) -> dict[str, Any]:
        with self._lock:
            if self.phase not in ("idle", "done"):
                return {"ok": False, "reason": f"already {self.phase}", **self.snapshot()}
            self._set_phase_locked("takeoff")
            self._takeoff_sent = False
            self._land_sent = False
            self._photo_sent = False
            self._dwell_t = None
            return {"ok": True, "phase": self.phase}

    def request_abort(self) -> dict[str, Any]:
        with self._lock:
            self._set_phase_locked("abort")
            self._land_sent = False
            return {"ok": True, "phase": self.phase}

    def note_photo(self, path: Path | None) -> None:
        with self._lock:
            self.last_photo = str(path) if path else None

    def _set_phase_locked(self, phase: str) -> None:
        if phase == self.phase:
            return
        prev = self.phase
        self.phase = phase
        self._phase_t = time.time()
        self._dwell_t = None
        print(f"mission {prev} → {phase}")
        if self.on_phase:
            self.on_phase(prev, phase)

    def tick(self, frame, now: float) -> Tick:
        g = self.gains
        stale = frame is None
        if frame is None:
            target_box = None
            home_box = None
        elif self.target_kind == "wheat":
            target_box = union_boxes(self.target_det.detect_all(frame))
            home_box = self.home_det.detect(frame)
        else:
            target_box = self.target_det.detect(frame)
            home_box = self.home_det.detect(frame)

        with self._lock:
            self.target_seen = target_box is not None
            self.home_seen = home_box is not None
            phase = self.phase
            elapsed = now - self._phase_t

            if stale and self.flying and phase not in ("idle", "done", "land", "abort"):
                self._set_phase_locked("abort")
                phase = "abort"

            action = None
            box = None
            yaw = fwd = up = 0
            err_x = area = None
            seeking = False

            if phase == "idle" or phase == "done":
                pass

            elif phase == "takeoff":
                if not self._takeoff_sent:
                    action = "takeoff"
                    self._takeoff_sent = True
                    self.flying = True
                elif elapsed > 1.2:
                    self._set_phase_locked("search_target")

            elif phase == "search_target":
                box = target_box
                seeking = True
                if target_box is not None:
                    self._set_phase_locked("approach_target")
                elif elapsed > SEARCH_TIMEOUT_S:
                    self._set_phase_locked("search_home")

            elif phase == "approach_target":
                box = target_box
                seeking = True
                if target_box is None:
                    self._set_phase_locked("search_target")
                elif elapsed > APPROACH_TIMEOUT_S:
                    self._set_phase_locked("survey")
                else:
                    if self._close_enough(target_box, frame.shape):
                        if self._dwell_t is None:
                            self._dwell_t = now
                        elif now - self._dwell_t >= DWELL_S:
                            self._set_phase_locked("survey")
                    else:
                        self._dwell_t = None

            elif phase == "survey":
                box = target_box
                if not self._photo_sent:
                    action = "photo"
                    self._photo_sent = True
                elif elapsed > 0.4:
                    if self.same_mark:
                        self._set_phase_locked("land")
                    else:
                        self._set_phase_locked("search_home")

            elif phase == "search_home":
                box = home_box
                seeking = True
                if home_box is not None:
                    self._set_phase_locked("approach_home")
                elif elapsed > SEARCH_TIMEOUT_S:
                    self._set_phase_locked("abort")

            elif phase == "approach_home":
                box = home_box
                seeking = True
                if home_box is None:
                    self._set_phase_locked("search_home")
                elif elapsed > APPROACH_TIMEOUT_S:
                    self._set_phase_locked("land")
                else:
                    if self._close_enough(home_box, frame.shape):
                        if self._dwell_t is None:
                            self._dwell_t = now
                        elif now - self._dwell_t >= DWELL_S:
                            self._set_phase_locked("land")
                    else:
                        self._dwell_t = None

            elif phase == "land":
                if not self._land_sent:
                    action = "land"
                    self._land_sent = True
                    self.flying = False
                elif elapsed > 1.0:
                    self._set_phase_locked("done")

            elif phase == "abort":
                if self.flying and not self._land_sent:
                    action = "land"
                    self._land_sent = True
                    self.flying = False
                else:
                    self._set_phase_locked("done")

            if box is not None and frame is not None and seeking:
                yaw, fwd, up, err_x, area = yaw_forward(
                    box,
                    frame.shape,
                    kp_yaw=g.kp_yaw,
                    kp_fwd=g.kp_fwd,
                    max_yaw=g.max_yaw,
                    max_fwd=g.max_fwd,
                    deadband=g.deadband,
                    target_area=g.target_area,
                    kp_up=g.kp_up,
                    max_up=g.max_up,
                )
            elif seeking and box is None and self.flying:
                yaw = SEARCH_YAW

            return Tick(
                yaw=yaw,
                fwd=fwd,
                up=up,
                action=action,
                box=box,
                err_x=err_x,
                area=area,
                stale=stale,
            )

    def _close_enough(self, box: Box, shape) -> bool:
        g = self.gains
        err_x = (box.cx / shape[1]) - 0.5
        return abs(err_x) <= g.deadband and box.area_frac(shape) >= g.target_area
