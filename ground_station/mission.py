"""Hands-free survey: takeoff → find wheat/tag → photo → home AprilTag → land."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ground_station.apriltag import AprilTagDetector
from ground_station.control import tag_width_frac, width_frac_for_distance, yaw_forward
from ground_station.vision import DEFAULT_WEIGHTS, Box, union_boxes, wheat_size_ratio

SEARCH_YAW = 22
SEARCH_TIMEOUT_S = 90.0
SEARCH_HOME_TIMEOUT_S = 90.0
SCOUT_FWD = 14
TARGET_LOST_FRAMES = 12
APPROACH_TIMEOUT_S = 60.0
HOME_APPROACH_TIMEOUT_S = 45.0
MIN_APPROACH_S = 1.2
DWELL_S = 0.6
FRAME_STALE_S = 0.5
ANALYSIS_HOLD_S = 90.0
TURN_HOME_S = 3.5
TURN_HOME_MAX_S = 6.0
TURN_YAW = 55
TARGET_GONE_WIDTH = 0.08
SCOUT_YAW = 30
SCOUT_HALF_S = 2.0
HOME_LOST_FRAMES = 12
CLOSE_FEET_DEFAULT = 2.0
TAG_SIZE_M_DEFAULT = 0.20
# Second tag-12 (base): land once tag fills this much of frame (~0.5–0.7 m for 20 cm print).
HOME_LAND_WIDTH = 0.20
# AprilTags often drop when too close / clipped — still land if already this close.
HOME_LOST_GRACE_S = 1.0


@dataclass
class Gains:
    kp_yaw: float = 90.0
    kp_fwd: float = 80.0
    kp_up: float = 120.0
    kp_lr: float = 110.0
    max_yaw: int = 35
    max_fwd: int = 20
    max_up: int = 20
    max_lr: int = 30
    lr_trim: int = 0
    invert_yaw: bool = False
    invert_lr: bool = False
    deadband: float = 0.08
    arrival_deadband: float = 0.22
    target_area: float = 0.12
    wheat_close_frac: float = 0.015
    tag_size_m: float = TAG_SIZE_M_DEFAULT
    close_m: float = CLOSE_FEET_DEFAULT * 0.3048


@dataclass
class Tick:
    yaw: int = 0
    fwd: int = 0
    up: int = 0
    lr: int = 0
    action: str | None = None  # takeoff | land | photo | None
    box: Box | None = None
    err_x: float | None = None
    area: float | None = None
    width: float | None = None
    need_w: float | None = None
    stale: bool = False


def _make_target_detector(kind: str, family: str, target_tag: int | None, weights: Path):
    if kind == "wheat":
        from ground_station.vision import HsvWheatDetector, YoloWheatHeadDetector

        if weights.is_file():
            return YoloWheatHeadDetector(weights=weights)
        print(f"no {weights}; wheat target: HSV color detector (computer test)")
        return HsvWheatDetector()
    return AprilTagDetector(family=family, tag_id=target_tag)


def resolve_target(kind: str, family: str, target_tag: int | None, weights: Path):
    if kind == "auto":
        kind = "wheat" if weights.is_file() else "tag"
        if kind == "tag":
            print("wheat weights missing; --target auto → AprilTag. Use --target wheat for a webcam wheat test.")
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
        analysis_required: bool = False,
    ) -> None:
        self.gains = gains
        self.home_tag = home_tag
        self.target_kind, self.target_det = resolve_target(
            target_kind, family, target_tag, weights
        )
        self.home_det = AprilTagDetector(family=family, tag_id=home_tag)
        self.same_mark = self.target_kind == "tag" and target_tag == home_tag
        self.on_phase = on_phase
        self.analysis_required = analysis_required
        self._analysis_deadline: float | None = None
        self._lock = threading.RLock()
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
        self._home_miss = 0
        self._last_home_box: Box | None = None
        self._home_best_width = 0.0
        self._home_close_seen_t: float | None = None
        self._target_miss = 0
        self._last_target_box: Box | None = None
        self._dbg_t = 0.0

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
                "analysis_required": self.analysis_required,
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
            self._home_miss = 0
            self._last_home_box = None
            self._home_best_width = 0.0
            self._home_close_seen_t = None
            self._target_miss = 0
            self._last_target_box = None
            return {"ok": True, "phase": self.phase}

    def request_abort(self) -> dict[str, Any]:
        with self._lock:
            self._set_phase_locked("abort")
            self._land_sent = False
            return {"ok": True, "phase": self.phase}

    def note_photo(self, path: Path | None) -> None:
        with self._lock:
            self.last_photo = str(path) if path else None

    def analysis_waiting(self) -> bool:
        with self._lock:
            return (
                self.phase == "await_analysis" and self._analysis_deadline is not None
                and time.time() < self._analysis_deadline
            )

    def continue_survey(self) -> dict[str, Any]:
        with self._lock:
            if not self.analysis_waiting():
                return {"ok": False, "reason": "no_live_analysis_hold"}
            self._set_phase_locked("turn_home")
            return {"ok": True, "phase": self.phase}

    def _set_phase_locked(self, phase: str) -> None:
        if phase == self.phase:
            return
        prev = self.phase
        self.phase = phase
        self._phase_t = time.time()
        self._dwell_t = None
        if phase == "approach_home":
            self._home_best_width = 0.0
            self._home_close_seen_t = None
        print(f"mission {prev} → {phase}")
        if self.on_phase:
            self.on_phase(prev, phase)

    def tick(self, frame, now: float) -> Tick:
        g = self.gains
        stale = frame is None
        wheat_heads: list[Box] = []
        wheat_ratio = 0.0
        if frame is None:
            target_box = None
            home_box = None
        elif self.target_kind == "wheat":
            wheat_heads = self.target_det.detect_all(frame)
            target_box = union_boxes(wheat_heads)
            wheat_ratio = wheat_size_ratio(wheat_heads, frame.shape)
            home_box = self.home_det.detect(frame)
        else:
            target_box = self.target_det.detect(frame)
            home_box = self.home_det.detect(frame)

        with self._lock:
            self.target_seen = target_box is not None
            self.home_seen = home_box is not None
            phase = self.phase
            elapsed = now - self._phase_t

            # Stale video: abort most phases, but approach_home may land (tag often vanishes when close).
            if (
                stale
                and self.flying
                and phase not in ("idle", "done", "land", "abort", "approach_home")
            ):
                self._set_phase_locked("abort")
                phase = "abort"

            action = None
            box = None
            yaw = fwd = up = lr = 0
            err_x = area = None
            seeking = False

            if phase == "idle" or phase == "done":
                # Still expose detections so the HUD proves AprilTag/YOLO work before takeoff.
                box = target_box or home_box

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
                    print("target not found after scout — turning for home")
                    self._set_phase_locked("turn_home")

            elif phase == "approach_target":
                seeking = True
                if target_box is None:
                    self._target_miss += 1
                    box = self._last_target_box
                    if self._target_miss >= TARGET_LOST_FRAMES:
                        print("lost target — scouting again")
                        self._set_phase_locked("search_target")
                        self._target_miss = 0
                        self._last_target_box = None
                        box = None
                else:
                    self._target_miss = 0
                    self._last_target_box = target_box
                    box = target_box
                    if self._reached(
                        target_box, frame.shape, elapsed, now, wheat_ratio=wheat_ratio
                    ):
                        self._set_phase_locked("survey")
                    elif elapsed > APPROACH_TIMEOUT_S:
                        print("approach timeout — turning around for home")
                        self._set_phase_locked("turn_home")

            elif phase == "survey":
                box = target_box
                if not self._photo_sent:
                    action = "photo"
                    self._photo_sent = True
                    if self.analysis_required:
                        self._analysis_deadline = now + ANALYSIS_HOLD_S
                        self._set_phase_locked("await_analysis")
                elif elapsed > 0.4:
                    self._set_phase_locked("turn_home")

            elif phase == "await_analysis":
                # Zero RC while cloud work runs. Reimages never renew this deadline.
                if self._analysis_deadline is None or now >= self._analysis_deadline:
                    self._set_phase_locked("abort")
                    action = "land"
                    self._land_sent = True
                    self.flying = False

            elif phase == "turn_home":
                # Spin in place. Do not lock onto tag 12 while it is still the target in view.
                yaw = -TURN_YAW if g.invert_yaw else TURN_YAW
                gone = True
                if frame is not None and target_box is not None:
                    gone = tag_width_frac(target_box, frame.shape) < TARGET_GONE_WIDTH
                if elapsed >= TURN_HOME_S and (gone or elapsed >= TURN_HOME_MAX_S):
                    self._set_phase_locked("search_home")

            elif phase == "search_home":
                box = home_box
                seeking = True
                if home_box is not None:
                    self._set_phase_locked("approach_home")
                elif elapsed > SEARCH_HOME_TIMEOUT_S:
                    print("home tag not found after scout — landing in place")
                    self._set_phase_locked("land")

            elif phase == "approach_home":
                seeking = True
                if home_box is not None and frame is not None:
                    self._home_miss = 0
                    self._last_home_box = home_box
                    box = home_box
                    w = tag_width_frac(home_box, frame.shape)
                    self._home_best_width = max(self._home_best_width, w)
                    if self._home_close_enough(home_box, frame.shape):
                        if self._home_close_seen_t is None:
                            self._home_close_seen_t = now
                    if self._reached_home(home_box, frame.shape, elapsed, now):
                        print(
                            f"home tag close enough (w={w:.2f} need≈{self._need_width(frame.shape):.2f}) — landing"
                        )
                        self._set_phase_locked("land")
                    elif elapsed > HOME_APPROACH_TIMEOUT_S:
                        print("home approach timeout — landing")
                        self._set_phase_locked("land")
                    if now - self._dbg_t > 0.8 and self.phase == "approach_home":
                        need = max(0.9 * self._need_width(frame.shape), HOME_LAND_WIDTH)
                        print(
                            f"approach_home tag_w={w:.2f} need={need:.2f} "
                            f"err_x={(home_box.cx / frame.shape[1]) - 0.5:+.2f}"
                        )
                        self._dbg_t = now
                else:
                    self._home_miss += 1
                    box = self._last_home_box
                    was_close = self._home_best_width >= (0.85 * HOME_LAND_WIDTH) or (
                        self._home_close_seen_t is not None
                        and now - self._home_close_seen_t <= HOME_LOST_GRACE_S
                    )
                    if was_close:
                        print(
                            f"lost home tag while close (best_w={self._home_best_width:.2f}) — landing"
                        )
                        self._set_phase_locked("land")
                    elif self._home_miss >= HOME_LOST_FRAMES:
                        print("lost home tag — scouting again")
                        self._set_phase_locked("search_home")
                        self._home_miss = 0
                        self._last_home_box = None
                        box = None

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

            need_w = (
                width_frac_for_distance(g.tag_size_m, g.close_m, frame.shape)
                if frame is not None
                else None
            )
            on_wheat = self.target_kind == "wheat" and phase in (
                "search_target",
                "approach_target",
            )
            track_w = None if on_wheat else need_w
            if box is not None and frame is not None and seeking:
                rc = yaw_forward(
                    box,
                    frame.shape,
                    kp_yaw=g.kp_yaw,
                    kp_fwd=g.kp_fwd,
                    max_yaw=g.max_yaw,
                    max_fwd=g.max_fwd,
                    deadband=g.deadband,
                    target_area=g.wheat_close_frac if on_wheat else g.target_area,
                    kp_up=g.kp_up,
                    max_up=g.max_up,
                    target_width=track_w,
                    size_ratio=wheat_ratio if on_wheat else None,
                    kp_lr=g.kp_lr,
                    max_lr=g.max_lr,
                    lr_trim=g.lr_trim,
                    invert_yaw=g.invert_yaw,
                    invert_lr=g.invert_lr,
                )
                yaw, fwd, up, lr, err_x, area = rc
            elif seeking and box is None and self.flying:
                roam = phase == "search_target"
                if phase in ("search_target", "search_home"):
                    yaw, up, fwd = self._scout(elapsed, g.invert_yaw, roam=roam)
                else:
                    yaw = SEARCH_YAW

            if on_wheat:
                width, need_hud = wheat_ratio, g.wheat_close_frac
            else:
                width = (
                    tag_width_frac(box, frame.shape)
                    if box is not None and frame is not None
                    else None
                )
                need_hud = need_w
            return Tick(
                yaw=yaw,
                fwd=fwd,
                up=up,
                lr=lr,
                action=action,
                box=box,
                err_x=err_x,
                area=area,
                width=width,
                need_w=need_hud,
                stale=stale,
            )

    def _need_width(self, shape) -> float:
        return width_frac_for_distance(self.gains.tag_size_m, self.gains.close_m, shape)

    def _close_enough(self, box: Box, shape, wheat_ratio: float = 0.0) -> bool:
        """Wheat: typical head box / image. AprilTag: width in frame (~2 ft)."""
        g = self.gains
        err_x = (box.cx / shape[1]) - 0.5
        if self.target_kind == "wheat" and self.phase == "approach_target":
            return wheat_ratio >= g.wheat_close_frac and abs(err_x) <= g.arrival_deadband
        width = tag_width_frac(box, shape)
        need = self._need_width(shape)
        return width >= (0.9 * need) and abs(err_x) <= g.arrival_deadband

    def _home_close_enough(self, box: Box, shape) -> bool:
        """Base / second ID-12: FOV range estimate or absolute width floor."""
        g = self.gains
        width = tag_width_frac(box, shape)
        need = max(0.9 * self._need_width(shape), HOME_LAND_WIDTH)
        err_x = (box.cx / shape[1]) - 0.5
        return width >= need and abs(err_x) <= max(g.arrival_deadband, 0.30)

    def _reached(
        self,
        box: Box,
        shape,
        elapsed: float,
        now: float,
        min_s: float | None = None,
        wheat_ratio: float = 0.0,
    ) -> bool:
        wait = MIN_APPROACH_S if min_s is None else min_s
        if elapsed < wait or not self._close_enough(box, shape, wheat_ratio=wheat_ratio):
            self._dwell_t = None
            return False
        if self._dwell_t is None:
            self._dwell_t = now
            return False
        return now - self._dwell_t >= DWELL_S

    def _reached_home(self, box: Box, shape, elapsed: float, now: float) -> bool:
        if elapsed < MIN_APPROACH_S or not self._home_close_enough(box, shape):
            self._dwell_t = None
            return False
        if self._dwell_t is None:
            self._dwell_t = now
            return False
        return now - self._dwell_t >= DWELL_S

    def _scout(
        self, elapsed: float, invert_yaw: bool, roam: bool
    ) -> tuple[int, int, int]:
        """Sweep yaw both ways, bob altitude, and (if roam) creep forward to hunt."""
        cycle = int(elapsed / SCOUT_HALF_S)
        sign = 1 if cycle % 2 == 0 else -1
        if invert_yaw:
            sign = -sign
        yaw = SCOUT_YAW * sign
        mod = cycle % 8
        up = 0
        fwd = 0
        if roam:
            if mod in (1, 5):
                fwd = SCOUT_FWD
            elif mod == 3:
                up = 14
            elif mod == 7:
                up = -10
                fwd = 10
        else:
            if mod == 2:
                up = 14
            elif mod == 5:
                up = -12
            elif mod == 6:
                fwd = 8
        return yaw, up, fwd
