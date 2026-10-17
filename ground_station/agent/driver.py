"""Host-owned mission commands. No SDK or hardware imports in this module."""

from __future__ import annotations

import threading
import time
from typing import Any, Literal, Protocol

Action = Literal["start_survey", "capture_photo", "land", "abort"]
ACTIONS = frozenset(("start_survey", "capture_photo", "land", "abort"))
APPROVAL_TTL_S = 60.0
MAX_EXTRA_PHOTOS = 2


class Mission(Protocol):
    def snapshot(self) -> dict[str, Any]: ...
    def request_start(self) -> dict[str, Any]: ...
    def request_abort(self) -> dict[str, Any]: ...


class MissionDriver:
    """One survey per process; approval and emergency stop belong to the host.

    Requests only change the mission state or a bounded photo mailbox. The
    existing camera/RC loop, never an SDK callback, owns all hardware calls.
    """

    def __init__(self, mission: Mission, *, mock: bool, drone_id: str = "drone-1"):
        self.mission = mission
        self.mock = mock
        self.drone_id = drone_id
        self._lock = threading.RLock()
        self._pending: tuple[Action, float] | None = None
        self._photo = False
        self._photo_count = 0
        self._started = False
        self._stopped = False

    def _expire_locked(self) -> None:
        if self._pending and time.monotonic() >= self._pending[1]:
            self._pending = None

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            self._expire_locked()
            return {
                "drone_id": self.drone_id,
                "mock": self.mock,
                "stopped": self._stopped,
                "survey_started": self._started,
                "extra_photos_remaining": MAX_EXTRA_PHOTOS - self._photo_count,
                "pending_approval": self._pending[0] if self._pending else None,
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
            if action == "start_survey":
                if self._started:
                    return {"ok": False, "reason": "one_survey_per_process"}
                if self.mission.snapshot()["phase"] not in ("idle", "done"):
                    return {"ok": False, "reason": "mission_busy"}
            if self.mock:
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
            if self._started:
                return {"ok": False, "reason": "one_survey_per_process"}
            result = self.mission.request_start()
            if result.get("ok"):
                self._started = True
            return result
        if action == "capture_photo":
            if self._photo:
                return {"ok": True, "status": "queued", "action": action}
            if self._photo_count >= MAX_EXTRA_PHOTOS:
                return {"ok": False, "reason": "extra_photo_limit_reached"}
            self._photo_count += 1
            self._photo = True
            return {"ok": True, "status": "queued", "action": action}
        raise ValueError("Unsupported approved action")

    def take_photo_request(self) -> bool:
        with self._lock:
            photo, self._photo = self._photo and not self._stopped, False
            return photo

    def stop(self) -> dict[str, Any]:
        with self._lock:
            self._stopped = True
            self._pending = None
            self._photo = False
            if self.mission.snapshot()["phase"] not in ("idle", "done"):
                self.mission.request_abort()
            return {"ok": True, "status": "landing_requested", "stopped": True}
