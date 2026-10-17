"""ONLINE → host relay; OFFLINE → SD log + AprilTag seek."""

from __future__ import annotations

import time
from typing import Any

from .bridge_rpc import BridgeClient
from .config import Config
from .connectivity import check_connectivity
from .host_client import HostClient
from .nav_assist_stub import NavAssistStub
from .sd_logger import SdLogger
from .sensors import SensorSample, make_sensor_source
from .state_machine import LinkState, LinkTracker

# Lazy: apriltag_seek imports OpenCV — only needed in OFFLINE mode.
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .apriltag_seek import AprilTagSeeker

# LED modes match sensor_bridge.ino
LED_BOOT, LED_ONLINE, LED_OFFLINE, LED_ERROR = 0, 1, 2, 3


class Coordinator:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.bridge = BridgeClient(mock=cfg.mock or cfg.sensor_backend == "mock")
        self.sensors = make_sensor_source(cfg, self.bridge)
        self.host = HostClient(cfg.host_url)
        self.sd = SdLogger(cfg.sd_dir)
        self.link = LinkTracker(grace_s=cfg.offline_grace_s)
        self.seeker: AprilTagSeeker | None = None  # type: ignore[name-defined]
        self.nav_assist = NavAssistStub(enabled=cfg.nav_assist)
        self._mission: dict[str, Any] = {
            "phase": "boot",
            "mode": "ONLINE",
            "apriltag_family": cfg.apriltag_family,
            "apriltag_ids": list(cfg.apriltag_ids),
        }
        self._last_state: LinkState | None = None
        self._flushed_once = False

    def _ensure_seeker(self):
        if self.seeker is None:
            from .apriltag_seek import AprilTagSeeker

            self.seeker = AprilTagSeeker(self.cfg)
        return self.seeker

    def _set_state_side_effects(self, state: LinkState) -> None:
        if state == self._last_state:
            return
        self._last_state = state
        self._mission["mode"] = state.value
        if state == LinkState.ONLINE:
            self.bridge.set_led_mode(LED_ONLINE)
            self._mission["phase"] = "relay_host"
            if self.cfg.flush_backlog and not self._flushed_once:
                n = self.sd.flush_to(self.host.post_sample)
                self._mission["backlog_flushed"] = n
                self._flushed_once = True
        else:
            self.bridge.set_led_mode(LED_OFFLINE)
            self._mission["phase"] = "apriltag_seek"
            self._flushed_once = False  # allow flush again after next reconnect

    def tick(self) -> dict[str, Any]:
        report = check_connectivity(self.cfg)
        state = self.link.update(
            report.host_reachable,
            force_offline=self.cfg.force_offline,
            force_online=self.cfg.force_online,
        )
        self._set_state_side_effects(state)

        seek_info: dict[str, Any] | None = None
        if state == LinkState.OFFLINE:
            cmd = self._ensure_seeker().tick()
            assist = self.nav_assist.hint()
            seek_info = {
                "yaw": cmd.yaw,
                "forward": cmd.forward,
                "up": cmd.up,
                "tag": cmd.tag_label,
                "err_x": cmd.err_x,
                "area": cmd.area,
                "holding": cmd.holding,
                "nav_assist": {
                    "bias_x": assist.bias_x,
                    "confidence": assist.confidence,
                    "note": assist.note,
                },
            }
            self._mission["seek"] = seek_info
            # VERIFY on airframe: map yaw/forward to Tello / FC RC or MAVLink.
            print(
                f"[OFFLINE seek] yaw={cmd.yaw} fwd={cmd.forward} tag={cmd.tag_label} "
                f"hold={cmd.holding}"
            )
        else:
            self._mission.pop("seek", None)

        sample: SensorSample = self.sensors.read(mission=self._mission)
        record = sample.to_dict()
        record["link"] = {
            "state": state.value,
            "host_reachable": report.host_reachable,
            "method": report.method,
            "wifi_ssid": report.wifi_ssid,
        }

        delivered = False
        if state == LinkState.ONLINE:
            delivered = self.host.post_sample(record)
            if not delivered:
                # Prefer host, but don't drop the sample — SD safety net.
                self.sd.append(record)
                print(f"[ONLINE] host POST failed ({self.host.last_error}); logged SD")
            else:
                print(
                    f"[ONLINE] T={sample.temperature_c}C RH={sample.humidity_pct}% → host"
                )
        else:
            self.sd.append(record)
            # Also try MCU SD via Bridge when available (dual path).
            self.bridge.sd_write_line(sample.to_json())
            print(
                f"[OFFLINE] T={sample.temperature_c}C RH={sample.humidity_pct}% → SD {self.sd.path}"
            )

        return {
            "state": state.value,
            "delivered": delivered,
            "sample": record,
            "seek": seek_info,
        }

    def run(self, *, once: bool = False, max_ticks: int | None = None) -> None:
        ticks = 0
        try:
            while True:
                self.tick()
                ticks += 1
                if once or (max_ticks is not None and ticks >= max_ticks):
                    break
                time.sleep(self.cfg.sample_period_s)
        finally:
            if self.seeker is not None:
                self.seeker.close()


def run_coordinator(cfg: Config, *, once: bool = False, max_ticks: int | None = None) -> None:
    Coordinator(cfg).run(once=once, max_ticks=max_ticks)
