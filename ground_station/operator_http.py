"""Local ingest for phone → Photon → Droid. Loopback only; no public bind."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ground_station.voice_scribe import map_intent

HOST = "127.0.0.1"
PORT = 8766
PATH = "/operator-command"


class OperatorCommandServer:
    def __init__(self, gateway, host: str = HOST, port: int = PORT):
        self.gateway = gateway
        self.host = host
        self.port = port
        self._httpd = None
        self._thread = None

    def start(self) -> None:
        gateway = self.gateway

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                return

            def do_POST(self):
                if self.path.split("?", 1)[0] != PATH:
                    self.send_error(404)
                    return
                length = int(self.headers.get("Content-Length") or "0")
                if length < 0 or length > 8000:
                    self.send_error(413)
                    return
                raw = self.rfile.read(length)
                try:
                    body = json.loads(raw.decode("utf-8"))
                    text = str(body.get("text") or "").strip()
                except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
                    self.send_error(400)
                    return
                if not text:
                    payload = {"ok": False, "reason": "empty", "message": "Empty operator request.", "executed": False}
                else:
                    mapped = map_intent(text, gateway)
                    result = mapped.get("result") or {}
                    payload = {
                        "ok": bool(result.get("ok", True)),
                        "intent": mapped.get("intent"),
                        "status": result.get("status"),
                        "executed": bool(result.get("executed", False)),
                        "message": result.get("message") or _message_for(mapped),
                    }
                data = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self._httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, name="agridrone-operator-http", daemon=True)
        self._thread.start()
        print(f"Photon command ingest on http://{self.host}:{self.port}{PATH}")

    def close(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None


def _message_for(mapped: dict) -> str:
    intent = mapped.get("intent")
    if intent == "abort":
        return "Emergency stop latched on the laptop."
    if intent == "start_survey":
        return "Queued start_survey for Droid. Press y on the laptop if approval is pending."
    if intent == "status":
        return "Status read from the host."
    return "Queued for Droid."
