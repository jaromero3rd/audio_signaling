#!/usr/bin/env python3
"""Minimal host telemetry receiver for Uno Q Linux coordinator.

Usage (laptop):
  python3 -m ground_station.uno_q_receiver --port 8787

Endpoints:
  GET  /health     → {"ok": true}
  POST /telemetry  → JSON sensor/mission sample from the board
"""

from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class TelemetryHandler(BaseHTTPRequestHandler):
    log_path: Path | None = None
    quiet: bool = False

    def log_message(self, fmt: str, *args: Any) -> None:
        if not self.quiet:
            super().log_message(fmt, *args)

    def _send(self, code: int, body: dict[str, Any]) -> None:
        raw = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") in {"/health", ""}:
            self._send(200, {"ok": True, "service": "uno_q_receiver"})
            return
        self._send(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path.rstrip("/") != "/telemetry":
            self._send(404, {"ok": False, "error": "not found"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            self._send(400, {"ok": False, "error": "invalid json"})
            return

        if self.log_path is not None:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(payload, separators=(",", ":")) + "\n")

        temp = payload.get("temperature_c")
        hum = payload.get("humidity_pct")
        link = (payload.get("link") or {}).get("state")
        print(f"[telemetry] link={link} T={temp} RH={hum}")
        self._send(200, {"ok": True})


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Uno Q host telemetry receiver")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8787)
    p.add_argument(
        "--log",
        type=Path,
        default=None,
        help="Optional JSONL path to append received samples",
    )
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args(argv)

    TelemetryHandler.log_path = args.log
    TelemetryHandler.quiet = args.quiet
    server = ThreadingHTTPServer((args.host, args.port), TelemetryHandler)
    print(f"uno_q_receiver listening on http://{args.host}:{args.port}  (POST /telemetry)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
