"""Edge autonomy: remember last good Wi-Fi station and decide when to return.

Runs on the UNO Q Linux MPU. Uses a lightweight policy agent (rules + optional
local heuristic scoring). When App Lab Agent / Edge Impulse bricks are wired
in later, `decide()` is the single hook to replace.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from wifi_link import WifiSnapshot, wifi_strong_enough


class Mode(str, Enum):
    SURVEY = "survey"
    OFFLINE_BUFFER = "offline_buffer"
    RETURN_TO_STATION = "return_to_station"
    RECOVERED = "recovered"


@dataclass
class StationFix:
    """Last place Wi-Fi was known-good."""

    name: str
    ssid: str | None = None
    bssid: str | None = None
    rssi_dbm: float | None = None
    lat: float | None = None
    lon: float | None = None
    home_apriltag_id: int | None = None
    saved_at: float = field(default_factory=time.time)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ssid": self.ssid,
            "bssid": self.bssid,
            "rssi_dbm": self.rssi_dbm,
            "lat": self.lat,
            "lon": self.lon,
            "home_apriltag_id": self.home_apriltag_id,
            "saved_at": self.saved_at,
        }


@dataclass
class Decision:
    mode: Mode
    reason: str
    command: dict[str, Any] | None = None
    confidence: float = 1.0


class ReturnToWifiAgent:
    """Autonomous policy: buffer offline, then head back to last Wi-Fi fix."""

    def __init__(
        self,
        *,
        station_name: str = "home_wifi",
        home_apriltag_id: int | None = 12,
        lat: float | None = None,
        lon: float | None = None,
        min_rssi_dbm: float = -75.0,
        lost_grace_s: float = 8.0,
        return_after_offline_s: float = 15.0,
        enabled: bool = True,
    ) -> None:
        self.enabled = enabled
        self.min_rssi_dbm = min_rssi_dbm
        self.lost_grace_s = lost_grace_s
        self.return_after_offline_s = return_after_offline_s
        self._cfg_lat = lat
        self._cfg_lon = lon
        self._cfg_tag = home_apriltag_id
        self._cfg_name = station_name

        self.mode = Mode.SURVEY
        self.last_fix = StationFix(
            name=station_name,
            lat=lat,
            lon=lon,
            home_apriltag_id=home_apriltag_id,
        )
        self._offline_since: float | None = None
        self._return_issued = False

    def remember_good_link(self, snap: WifiSnapshot) -> None:
        if not wifi_strong_enough(snap, self.min_rssi_dbm):
            return
        self.last_fix = StationFix(
            name=self._cfg_name,
            ssid=snap.ssid,
            bssid=snap.bssid,
            rssi_dbm=snap.rssi_dbm,
            lat=self._cfg_lat,
            lon=self._cfg_lon,
            home_apriltag_id=self._cfg_tag,
            saved_at=time.time(),
        )

    def decide(self, snap: WifiSnapshot) -> Decision:
        if not self.enabled:
            return Decision(Mode.SURVEY, "autonomy disabled")

        now = time.time()
        online = wifi_strong_enough(snap, self.min_rssi_dbm)

        if online:
            self.remember_good_link(snap)
            was_returning = self.mode in (Mode.RETURN_TO_STATION, Mode.OFFLINE_BUFFER)
            self._offline_since = None
            self._return_issued = False
            if was_returning:
                self.mode = Mode.RECOVERED
                return Decision(
                    Mode.RECOVERED,
                    "Wi-Fi restored at remembered station",
                    command={
                        "action": "cancel_return",
                        "station": self.last_fix.as_dict(),
                    },
                    confidence=0.95,
                )
            self.mode = Mode.SURVEY
            return Decision(Mode.SURVEY, "link healthy", confidence=0.99)

        if self._offline_since is None:
            self._offline_since = now

        offline_for = now - self._offline_since

        if offline_for < self.lost_grace_s:
            self.mode = Mode.OFFLINE_BUFFER
            return Decision(
                Mode.OFFLINE_BUFFER,
                f"Wi-Fi weak/gone; grace {offline_for:.1f}s/{self.lost_grace_s}s",
                confidence=0.7,
            )

        if offline_for < self.return_after_offline_s:
            self.mode = Mode.OFFLINE_BUFFER
            return Decision(
                Mode.OFFLINE_BUFFER,
                f"buffering on SD; return in {self.return_after_offline_s - offline_for:.1f}s",
                confidence=0.8,
            )

        self.mode = Mode.RETURN_TO_STATION
        cmd = self._return_command()
        conf = self._score_return(snap)
        if not self._return_issued:
            self._return_issued = True
            return Decision(
                Mode.RETURN_TO_STATION,
                "offline too long — return to last Wi-Fi station",
                command=cmd,
                confidence=conf,
            )
        return Decision(
            Mode.RETURN_TO_STATION,
            "continuing return-to-station",
            command=cmd,
            confidence=conf,
        )

    def _return_command(self) -> dict[str, Any]:
        fix = self.last_fix
        target: dict[str, Any] = {"type": "last_wifi_station", "station": fix.as_dict()}
        if fix.lat is not None and fix.lon is not None:
            target["nav"] = {"mode": "goto_latlon", "lat": fix.lat, "lon": fix.lon}
        elif fix.home_apriltag_id is not None:
            target["nav"] = {
                "mode": "goto_apriltag",
                "tag_id": fix.home_apriltag_id,
            }
        else:
            target["nav"] = {"mode": "rtl_ssid", "ssid": fix.ssid}
        return {
            "action": "return_to_wifi_station",
            "target": target,
            "issued_at": time.time(),
        }

    def _score_return(self, snap: WifiSnapshot) -> float:
        """Heuristic confidence for the return decision (stand-in for on-device AI)."""
        score = 0.55
        if self.last_fix.home_apriltag_id is not None:
            score += 0.2
        if self.last_fix.lat is not None and self.last_fix.lon is not None:
            score += 0.15
        if self.last_fix.ssid:
            score += 0.05
        age = time.time() - self.last_fix.saved_at
        if age < 60:
            score += 0.1
        elif age > 600:
            score -= 0.15
        if snap.rssi_dbm is not None and snap.rssi_dbm < -85:
            score += 0.05
        return max(0.05, min(0.99, score))
