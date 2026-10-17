"""ONLINE / OFFLINE connectivity state with simple hysteresis."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import time


class LinkState(str, Enum):
    ONLINE = "ONLINE"
    OFFLINE = "OFFLINE"


@dataclass
class LinkTracker:
    """Flip to OFFLINE only after grace_s of failed probes; ONLINE on first success."""

    grace_s: float = 3.0
    state: LinkState = LinkState.OFFLINE
    _down_since: float | None = None

    def update(self, host_reachable: bool, *, force_offline: bool = False, force_online: bool = False) -> LinkState:
        if force_offline and not force_online:
            self.state = LinkState.OFFLINE
            self._down_since = time.monotonic()
            return self.state
        if force_online:
            self.state = LinkState.ONLINE
            self._down_since = None
            return self.state

        now = time.monotonic()
        if host_reachable:
            self._down_since = None
            self.state = LinkState.ONLINE
            return self.state

        if self._down_since is None:
            self._down_since = now
        if self.state == LinkState.ONLINE and (now - self._down_since) < self.grace_s:
            return self.state
        self.state = LinkState.OFFLINE
        return self.state
