"""Opt-in, authenticated phone commands. No hardware calls or model-selected paths."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import hmac
import json
import os
from pathlib import Path
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = "127.0.0.1"
PATH = "/command"
MAX_BODY = 1024
MAX_AGE_S = 30


@dataclass(frozen=True)
class BridgeSettings:
    token: str
    port: int = 8787

    @classmethod
    def load(cls, env_file: Path | None = None):
        values = dict(os.environ)
        if env_file is not None:
            from dotenv import dotenv_values

            if not env_file.is_file():
                raise ValueError("Phone command environment file is missing")
            values.update(dotenv_values(env_file))
        token = values.get("COMMAND_BRIDGE_TOKEN") or ""
        if not re.fullmatch(r"[\x21-\x7e]{32,}", token):
            raise ValueError("COMMAND_BRIDGE_TOKEN must contain at least 32 non-whitespace ASCII characters")
        try:
            port = int(values.get("COMMAND_BRIDGE_PORT") or "8787")
        except ValueError:
            raise ValueError("COMMAND_BRIDGE_PORT must be an integer") from None
        if not 1 <= port <= 65535:
            raise ValueError("COMMAND_BRIDGE_PORT must be between 1 and 65535")
        return cls(token, port)


class PhoneCommandGateway:
    """Authorize only a phone start, not unrelated voice/photo requests."""

    def __init__(self, gateway, driver):
        self.gateway = gateway
        self.driver = driver

    def request_start(self):
        result = self.driver.authorize_phone_start()
        if not result["ok"]:
            return result
        try:
            queued = self.gateway.request_start()
        except Exception:
            self.driver.cancel_phone_start()
            raise
        if not queued.get("ok"):
            self.driver.cancel_phone_start()
        return queued

    def request_abort(self):
        return self.gateway.request_abort()


class PhoneCommandBridge:
    def __init__(self, gateway, *, token: str, port: int = 8787):
        if not re.fullmatch(r"[\x21-\x7e]{32,}", token):
            raise ValueError("A phone command bearer token is required")
        self.gateway = gateway
        self.token = token
        self.port = port
        self.ready = threading.Event()
        self.failed = threading.Event()
        self._httpd = None
        self._thread = None
        self._lock = threading.Lock()
        self._receipts = OrderedDict()

    def dispatch(self, authorization: str, body: bytes) -> tuple[int, dict]:
        if not hmac.compare_digest(authorization.encode(), f"Bearer {self.token}".encode()):
            return 401, {"ok": False, "reason": "unauthorized"}
        if len(body) > MAX_BODY:
            return 413, {"ok": False, "reason": "body_too_large"}
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            return 400, {"ok": False, "reason": "invalid_json"}
        if not isinstance(payload, dict) or set(payload) != {"command", "id", "issued_at"}:
            return 400, {"ok": False, "reason": "invalid_command"}
        command, receipt, issued = payload["command"], payload["id"], payload["issued_at"]
        if (command not in ("start", "abort", "land") or not isinstance(receipt, str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", receipt)
                or type(issued) not in (int, float) or not -1e12 <= issued <= 1e12):
            return 400, {"ok": False, "reason": "invalid_command"}
        now = time.time()
        if not now - MAX_AGE_S <= issued <= now + 5:
            return 409, {"ok": False, "reason": "expired_command"}
        with self._lock:
            for key, (expiry, _, _, _) in list(self._receipts.items()):
                if expiry < now:
                    del self._receipts[key]
            if receipt in self._receipts:
                _, previous_command, status, payload = self._receipts[receipt]
                if previous_command != command:
                    return 409, {"ok": False, "reason": "receipt_conflict"}
                return status, {**payload, "duplicate": True}
            if len(self._receipts) >= 256:
                if command == "start":
                    return 429, {"ok": False, "reason": "command_capacity"}
                self._receipts.popitem(last=False)  # Never let a full start cache block stop.
            try:
                # These gateways only enqueue intent or latch host stop, never call a model/hardware.
                result = (self.gateway.request_start() if command == "start"
                          else self.gateway.request_abort())
                status, payload = 200, {
                    "ok": bool(result.get("ok")), "executed": False,
                    "status": result.get("status", "requested"),
                    "message": ("Start queued for Droid; camera/battery checks still apply."
                                if command == "start" and result.get("ok") else
                                "Start rejected." if command == "start" else
                                "Stop latched on the mission laptop; landing is requested, not confirmed."
                                if result.get("ok") else "Stop rejected; use local stop."),
                }
            except Exception:
                status, payload = 503, {"ok": False, "reason": "command_failed"}
            self._receipts[receipt] = (now + MAX_AGE_S + 5, command, status, payload)
            return status, payload

    def start(self):
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def setup(self):
                super().setup()
                self.connection.settimeout(2)

            def do_POST(self):
                if self.path != PATH:
                    self.send_error(404)
                    return
                authorization = self.headers.get("Authorization", "")
                if not hmac.compare_digest(authorization.encode(), f"Bearer {bridge.token}".encode()):
                    self.send_error(401)
                    return
                try:
                    length = int(self.headers.get("Content-Length", ""))
                except ValueError:
                    self.send_error(400)
                    return
                if (self.headers.get("Transfer-Encoding") or
                        len(self.headers.get_all("Content-Length", [])) != 1 or
                        not 0 < length <= MAX_BODY):
                    self.send_error(413)
                    return
                try:
                    status, payload = bridge.dispatch(authorization, self.rfile.read(length))
                except Exception:
                    status, payload = 503, {"ok": False, "reason": "command_failed"}
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        try:
            self._httpd = ThreadingHTTPServer((HOST, self.port), Handler)
            self.port = self._httpd.server_port
            self._thread = threading.Thread(target=self._serve, name="phone-command", daemon=True)
            self._thread.start()
        except Exception:
            self.failed.set()
            self.ready.set()
            if self._httpd is not None:
                self._httpd.server_close()
            raise

    def _serve(self):
        self.ready.set()
        try:
            self._httpd.serve_forever(poll_interval=0.1)
        except Exception:
            self.failed.set()

    def close(self):
        if self._httpd is not None:
            if self._thread is not None and self._thread.is_alive():
                self._httpd.shutdown()
            self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=3)
