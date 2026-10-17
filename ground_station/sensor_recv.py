#!/usr/bin/env python3
"""HTTP receiver for UNO Q field telemetry + mic PCM transfer.

  /telemetry  — DHT11 / light / autonomy JSON
  /mic        — raw microphone chunks from the UNO Q (capture + transfer only)

Farmer voice commands (Grok Voice) stay on this laptop via voice_grok.py —
they are not handled by the UNO Q.

Run:
  python -m ground_station.sensor_recv --host 0.0.0.0 --port 8765
"""

from __future__ import annotations

import argparse
import base64
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOG = ROOT / "data" / "unoq_telemetry.jsonl"
DEFAULT_MIC = ROOT / "data" / "unoq_mic.f32le"


class TelemetryStore:
    def __init__(self, log_path: Path, mic_path: Path) -> None:
        self.log_path = log_path
        self.mic_path = mic_path
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.mic_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.latest: dict[str, Any] | None = None
        self.return_events: list[dict[str, Any]] = []
        self.count = 0
        self.mic_chunks = 0
        self.mic_bytes = 0

    def ingest(self, payload: dict[str, Any]) -> None:
        payload = dict(payload)
        payload.setdefault("recv_ts", time.time())
        with self._lock:
            self.latest = payload
            self.count += 1
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(payload) + "\n")
            if payload.get("event") == "return_to_wifi_station" or (
                isinstance(payload.get("autonomy"), dict)
                and payload["autonomy"].get("mode") == "return_to_station"
            ):
                self.return_events.append(payload)
                self.return_events = self.return_events[-50:]

    def ingest_mic(self, payload: dict[str, Any]) -> dict[str, Any]:
        b64 = payload.get("audio_b64") or ""
        try:
            pcm = base64.b64decode(b64, validate=False)
        except Exception:  # noqa: BLE001
            return {"ok": False, "error": "bad audio_b64"}
        with self._lock:
            with self.mic_path.open("ab") as f:
                f.write(pcm)
            self.mic_chunks += 1
            self.mic_bytes += len(pcm)
            n = self.mic_chunks
            total = self.mic_bytes
        # Metadata only in jsonl (not the PCM).
        meta = {
            "device_id": payload.get("device_id"),
            "source": "unoq_mic",
            "seq": payload.get("seq"),
            "sample_rate": payload.get("sample_rate"),
            "format": payload.get("format"),
            "bytes": len(pcm),
            "recv_ts": time.time(),
        }
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(meta) + "\n")
        return {"ok": True, "chunks": n, "bytes": total}


def make_handler(store: TelemetryStore):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:
            # Mic is high-rate; keep access log quiet except errors.
            if self.path.startswith("/mic"):
                return
            print(f"[http] {self.address_string()} {fmt % args}")

        def _read_json(self) -> dict[str, Any] | None:
            length = int(self.headers.get("Content-Length", "0") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                data = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                return None
            return data if isinstance(data, dict) else None

        def _send(self, code: int, body: dict[str, Any]) -> None:
            raw = json.dumps(body).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path in ("/", "/health"):
                latest = store.latest or {}
                self._send(
                    200,
                    {
                        "ok": True,
                        "samples": store.count,
                        "sensors": {
                            "temp_c": latest.get("temp_c"),
                            "humidity_pct": latest.get("humidity_pct"),
                            "light": latest.get("light"),
                            "light_raw": latest.get("light_raw"),
                        },
                        "hint": "DHT11/light only. Open /latest for full sample. Board POSTs to /telemetry.",
                    },
                )
                return
            if path in ("/latest", "/telemetry"):
                # /telemetry is POST-only for the board; GET shows status so browsers don't 404.
                latest = store.latest or {}
                self._send(
                    200,
                    {
                        "ok": True,
                        "method_for_unoq": "POST",
                        "samples": store.count,
                        "temp_c": latest.get("temp_c"),
                        "humidity_pct": latest.get("humidity_pct"),
                        "light": latest.get("light"),
                        "latest": store.latest,
                    },
                )
                return
            if path == "/returns":
                with store._lock:
                    events = list(store.return_events)
                self._send(200, {"returns": events})
                return
            self._send(404, {"error": "not found", "try": ["/", "/health", "/latest", "/telemetry"]})

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            payload = self._read_json()
            if payload is None:
                self._send(400, {"error": "invalid json"})
                return

            if path == "/mic":
                result = store.ingest_mic(payload)
                if store.mic_chunks % 50 == 1:
                    print(
                        f"[mic] chunks={store.mic_chunks} bytes={store.mic_bytes} "
                        f"seq={payload.get('seq')} rate={payload.get('sample_rate')}"
                    )
                self._send(200 if result.get("ok") else 400, result)
                return

            if path not in ("/telemetry", "/"):
                self._send(404, {"error": "not found"})
                return

            store.ingest(payload)
            src = payload.get("source", "?")
            if payload.get("event") == "return_to_wifi_station":
                target = (payload.get("autonomy") or {}).get("command", {}).get("target")
                print(f"*** RETURN TO WIFI STATION *** target={target}")
            elif "temp_c" in payload:
                print(
                    f"#{store.count} {src} DHT11 "
                    f"T={payload.get('temp_c')}C/{payload.get('temp_f')}F "
                    f"RH={payload.get('humidity_pct')}% "
                    f"HI={payload.get('heat_index_c')}C "
                    f"L={payload.get('light')} dev={payload.get('device_id')}"
                )
            else:
                print(f"#{store.count} {src} keys={list(payload.keys())}")
            self._send(200, {"ok": True, "n": store.count})

    return Handler


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Receive UNO Q telemetry + mic stream")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--log", type=Path, default=DEFAULT_LOG)
    p.add_argument("--mic-file", type=Path, default=DEFAULT_MIC)
    args = p.parse_args(argv)

    store = TelemetryStore(args.log, args.mic_file)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(store))
    print(f"Listening on http://{args.host}:{args.port}")
    print(f"  Browser check:  http://127.0.0.1:{args.port}/latest")
    print(f"  UNO Q POSTs to: http://<THIS-PC-WIFI-IP>:{args.port}/telemetry")
    print(f"  Mic POST:       http://<THIS-PC-WIFI-IP>:{args.port}/mic  -> {args.mic_file}")
    print("Watch this terminal for live T=/RH= lines when the board connects.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
