"""Host-owned mission commands. No SDK or hardware imports in this module."""

from __future__ import annotations

import threading
import time
from typing import Any, Literal, Protocol

Action = Literal["start_survey", "capture_photo", "continue_survey", "land", "abort"]
ACTIONS = frozenset(("start_survey", "capture_photo", "continue_survey", "land", "abort"))
APPROVAL_TTL_S = 60.0
MAX_EXTRA_PHOTOS = 2


class Mission(Protocol):
    def snapshot(self) -> dict[str, Any]: ...
    def request_start(self) -> dict[str, Any]: ...
    def request_abort(self) -> dict[str, Any]: ...
    def analysis_waiting(self) -> bool: ...
    def continue_survey(self) -> dict[str, Any]: ...


class MissionDriver:
    """One survey per process; approval and emergency stop belong to the host.

    Requests only change the mission state or a bounded photo mailbox. The
    existing camera/RC loop, never an SDK callback, owns all hardware calls.
    """

    def __init__(
        self,
        mission: Mission,
        *,
        mock: bool,
        drone_id: str = "drone-1",
        auto_approve: bool = False,
        phone_commands: bool = False,
    ):
        self.mission = mission
        self.mock = mock
        self.drone_id = drone_id
        # When True, start_survey / capture_photo run without local y key.
        self.auto_approve = bool(auto_approve or mock)
        self._lock = threading.RLock()
        self._pending: tuple[Action, float] | None = None
        self._photo = False
        self._photo_count = 0
        self._started = False
        self._stopped = False
        self._stop_generation = 0
        self._capture = 0
        self._analysis_ready = False
        self._phone_commands = phone_commands
        self._phone_start_until = None

    def _expire_locked(self) -> None:
        if self._pending and time.monotonic() >= self._pending[1]:
            self._pending = None
        if self._phone_start_until is not None and time.monotonic() >= self._phone_start_until:
            self._phone_start_until = None

    def authorize_phone_start(self) -> dict:
        """Host-only one-shot authorization, never registered as a model tool."""
        with self._lock:
            self._expire_locked()
            if (not self._phone_commands or self._stopped or self._started
                    or self.mission.snapshot()["phase"] != "idle"):
                return {"ok": False, "reason": "phone_start_unavailable"}
            if self._phone_start_until is not None:
                return {"ok": False, "reason": "phone_start_already_queued"}
            self._phone_start_until = time.monotonic() + APPROVAL_TTL_S
            return {"ok": True}

    def cancel_phone_start(self):
        with self._lock:
            self._phone_start_until = None

    def approve_phone_start(self, *, flight_ready: bool) -> dict | None:
        """Camera loop consumes phone authorization only after local preflight."""
        with self._lock:
            self._expire_locked()
            if (self._phone_start_until is None or self._pending is None
                    or self._pending[0] != "start_survey" or self._stopped or not flight_ready):
                return None
            self._phone_start_until = None
            return self.approve_pending(flight_ready=True)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            self._expire_locked()
            return {
                "drone_id": self.drone_id,
                "mock": self.mock,
                "stopped": self._stopped,
                "stop_generation": self._stop_generation,
                "survey_started": self._started,
                "extra_photos_remaining": MAX_EXTRA_PHOTOS - self._photo_count,
                "pending_approval": self._pending[0] if self._pending else None,
                "capture_id": self._capture,
                "analysis_ready": self._analysis_ready,
                "photo_queued": self._photo,
                "mission": self.mission.snapshot(),
            }

    def issue(self, drone_id: str, action: Action) -> dict[str, Any]:
        if drone_id != self.drone_id:
            raise ValueError("Unknown drone_id")
        if action not in ACTIONS:
            raise ValueError("Unsupported drone action")
        with self._lock:
            self._expire_locked()
            if action in ("abort", "land"):
                return self.stop()
            if self._stopped:
                return {"ok": False, "reason": "operator_stop_latched"}
            if action == "continue_survey":
                if not self._analysis_ready or self._photo or self._pending is not None:
                    return {"ok": False, "reason": "analysis_or_reimage_pending"}
                return self.mission.continue_survey()
            if action == "capture_photo" and getattr(self.mission, "analysis_required", False):
                if not self.mission.analysis_waiting():
                    return {"ok": False, "reason": "no_live_analysis_hold"}
            if action == "start_survey":
                if self._started:
                    return {"ok": False, "reason": "one_survey_per_process"}
                if self.mission.snapshot()["phase"] not in ("idle", "done"):
                    return {"ok": False, "reason": "mission_busy"}
            if self.auto_approve:
                return self._execute_locked(action)
            # Neither the model nor any MCP tool can approve this request.
            if self._pending is None:
                self._pending = (action, time.monotonic() + APPROVAL_TTL_S)
            if self._pending[0] != action:
                return {"ok": False, "reason": "another_approval_pending"}
            return {"ok": False, "reason": "local_approval_required", "action": action}

    def approve_pending(self, *, flight_ready: bool = False) -> dict[str, Any]:
        """Called only by the local operator UI, never registered as a tool."""
        with self._lock:
            self._expire_locked()
            pending, self._pending = self._pending, None
            if pending is None or self._stopped:
                return {"ok": False, "reason": "no_live_approval"}
            if pending[0] == "start_survey" and not flight_ready:
                return {"ok": False, "reason": "camera_and_battery_preflight_required"}
            return self._execute_locked(pending[0])

    def _execute_locked(self, action: Action) -> dict[str, Any]:
        if action == "start_survey":
            self._phone_start_until = None
            if self._started:
                return {"ok": False, "reason": "one_survey_per_process"}
            result = self.mission.request_start()
            if result.get("ok"):
                self._started = True
            return result
        if action == "capture_photo":
            if getattr(self.mission, "analysis_required", False) and not self.mission.analysis_waiting():
                return {"ok": False, "reason": "no_live_analysis_hold"}
            if self._photo:
                return {"ok": True, "status": "queued", "action": action}
            if self._photo_count >= MAX_EXTRA_PHOTOS:
                return {"ok": False, "reason": "extra_photo_limit_reached"}
            self._photo_count += 1
            self._photo = True
            self._analysis_ready = False
            return {"ok": True, "status": "queued", "action": action}
        raise ValueError("Unsupported approved action")

    def take_photo_request(self) -> bool:
        with self._lock:
            photo, self._photo = self._photo and not self._stopped, False
            return photo

    def begin_analysis(self) -> int:
        with self._lock:
            if self._stopped or not self.mission.analysis_waiting():
                raise RuntimeError("No live analysis hold")
            self._capture += 1
            self._analysis_ready = False
            return self._capture

    def accept_analysis(self, capture_id: int) -> bool:
        with self._lock:
            if self._stopped or capture_id != self._capture or not self.mission.analysis_waiting():
                return False
            self._analysis_ready = True
            return True

    def stop(self) -> dict[str, Any]:
        with self._lock:
            analysis_capture = (
                self._capture if not self._stopped and self._analysis_ready
                and self.mission.analysis_waiting() else None
            )
            self._stop_generation += 1
            self._stopped = True
            self._pending = None
            self._phone_start_until = None
            self._photo = False
            if self.mission.snapshot()["phase"] not in ("idle", "done"):
                self.mission.request_abort()
            return {"ok": True, "status": "landing_requested", "stopped": True,
                    "stop_generation": self._stop_generation, "analysis_capture": analysis_capture}
