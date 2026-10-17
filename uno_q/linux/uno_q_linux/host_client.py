"""HTTP telemetry client → laptop ground_station.uno_q_receiver."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


class HostClient:
    def __init__(self, url: str, timeout_s: float = 2.0) -> None:
        self.url = url
        self.timeout_s = timeout_s
        self.last_error: str | None = None

    def post_sample(self, record: dict[str, Any]) -> bool:
        body = json.dumps(record).encode("utf-8")
        req = urllib.request.Request(
            self.url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                ok = 200 <= getattr(resp, "status", 200) < 300
                if not ok:
                    self.last_error = f"HTTP {resp.status}"
                else:
                    self.last_error = None
                return ok
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            self.last_error = str(exc)
            return False
