#!/usr/bin/env python3
"""Photon Beta inbound iMessage → local mission intent (no ElevenLabs).

Subscribes to the project event stream (Socket.IO). Authorized human text in
the direct agent/user space becomes start/abort intents for DroidVoiceGateway.

  start keywords: survey, start, fly, go, takeoff, wheat, mission
  abort keywords: abort, stop, land, emergency, cancel
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from typing import Callable

import socketio

from ground_station.photon import PhotonClient, PhotonConfig, PhotonError

API_VERSION = "2026-07-01"
START_RE = re.compile(
    r"\b(survey|start(?:\s+survey)?|fly|go(?:\s+fly)?|takeoff|wheat|mission)\b",
    re.I,
)
ABORT_RE = re.compile(r"\b(abort|stop|land(?:\s+now)?|emergency|cancel)\b", re.I)


@dataclass(frozen=True)
class InboundCommand:
    kind: str  # "start" | "abort"
    message_id: str
    text: str


def classify_text(text: str) -> str | None:
    raw = (text or "").strip()
    if not raw:
        return None
    if ABORT_RE.search(raw):
        return "abort"
    if START_RE.search(raw):
        return "start"
    return None


def parse_inbound(event: object, *, project: str, agent: str, user: str) -> InboundCommand | None:
    if not isinstance(event, dict):
        return None
    if event.get("type") != "message.received" or event.get("source") != "imessage.photon":
        return None
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    message = data.get("object") if isinstance(data.get("object"), dict) else {}
    if data.get("projectId") != project or message.get("projectId") != project:
        return None
    if message.get("platform") != "imessage" or message.get("status") != "received":
        return None
    sender = message.get("sentBy") if isinstance(message.get("sentBy"), dict) else {}
    receiver = message.get("receivedBy") if isinstance(message.get("receivedBy"), dict) else {}
    space = message.get("space") if isinstance(message.get("space"), dict) else {}
    content = message.get("content") if isinstance(message.get("content"), dict) else {}
    if sender.get("kind") != "human" or sender.get("handle") != user:
        return None
    if receiver.get("kind") != "agent" or receiver.get("handle") != agent:
        return None
    if space.get("kind") != "direct":
        return None
    if content.get("type") != "text" or content.get("format") != "plain":
        return None
    text = content.get("text")
    if not isinstance(text, str) or not text.strip():
        return None
    mid = message.get("id")
    if not isinstance(mid, str) or not mid.startswith("pho_msg_"):
        return None
    kind = classify_text(text)
    if kind is None:
        return None
    return InboundCommand(kind=kind, message_id=mid, text=text.strip()[:500])


class PhotonInbound:
    """Background Socket.IO subscriber; callbacks run off the RC thread."""

    def __init__(
        self,
        client: PhotonClient,
        *,
        on_command: Callable[[InboundCommand], None],
        ack_text: Callable[[str, str], None] | None = None,
    ):
        self.client = client
        self.on_command = on_command
        self.ack_text = ack_text
        self.ready = threading.Event()
        self.failed = threading.Event()
        self._stop = threading.Event()
        self._seen: set[str] = set()
        self._cursor = 0
        self._thread: threading.Thread | None = None
        self._sio: socketio.Client | None = None

    def start(self) -> None:
        if self.client._route is None:
            self.client.check()
        self._thread = threading.Thread(target=self._run, name="agridrone-photon-in", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        sio = self._sio
        if sio is not None:
            try:
                sio.disconnect()
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        cfg = self.client.config
        sio = socketio.Client(
            reconnection=True,
            reconnection_attempts=10,
            reconnection_delay_max=15,
            logger=False,
            engineio_logger=False,
        )
        self._sio = sio
        pending = 0
        lock = threading.Lock()

        @sio.event
        def connect():
            def _ack(data=None):
                ack = data if isinstance(data, dict) else {}
                if ack.get("ok") is True and ack.get("projectId") == cfg.project and ack.get("apiVersion") == API_VERSION:
                    self.ready.set()
                    print("Photon inbound: subscribed; waiting for iMessage commands.")
                else:
                    self.failed.set()
                    print("Photon inbound: subscribe rejected.")
                    sio.disconnect()

            try:
                sio.emit(
                    "subscribe",
                    {"startSeq": self._cursor, "apiVersion": API_VERSION},
                    callback=_ack,
                )
            except Exception:
                self.failed.set()
                print("Photon inbound: subscribe emit failed.")

        @sio.event
        def disconnect():
            if not self._stop.is_set():
                print("Photon inbound: disconnected; reconnecting…")

        @sio.on("delivery_error")
        def _delivery_error(error):
            details = error if isinstance(error, dict) else {}
            print(f"Photon inbound delivery_error: {details.get('code', 'unknown')}")
            if details.get("retryable") is False or details.get("fatal"):
                self.failed.set()
                sio.disconnect()

        @sio.on("delivery")
        def _delivery(event, metadata=None):
            nonlocal pending
            meta = metadata if isinstance(metadata, dict) else {}
            seq = meta.get("seq")
            if not isinstance(seq, int) or seq <= 0:
                return
            with lock:
                if seq <= self._cursor:
                    return
                if pending > 128:
                    sio.disconnect()
                    return
                pending += 1
            try:
                self._handle(event, seq)
            finally:
                with lock:
                    pending -= 1

        url = "https://api.photon.codes"
        path = f"/v1/projects/{cfg.project}/events/ws"
        try:
            sio.connect(
                url,
                socketio_path=path,
                transports=["websocket"],
                headers={
                    "Authorization": f"Bearer {cfg.key}",
                    "User-Agent": "AgriDrone-Mission/0.1",
                },
                wait_timeout=20,
            )
            while not self._stop.is_set() and not self.failed.is_set():
                if not self.ready.wait(timeout=1.0) and time.monotonic():
                    # still waiting for subscribe ack
                    pass
                time.sleep(0.2)
        except Exception as error:
            self.failed.set()
            print(f"Photon inbound connect failed ({type(error).__name__}).")
        finally:
            try:
                sio.disconnect()
            except Exception:
                pass

    def _handle(self, event: object, seq: int) -> None:
        cfg = self.client.config
        cmd = parse_inbound(event, project=cfg.project, agent=cfg.agent, user=cfg.user)
        self._cursor = max(self._cursor, seq)
        if cmd is None:
            return
        if cmd.message_id in self._seen:
            return
        self._seen.add(cmd.message_id)
        # Cap seen set
        if len(self._seen) > 500:
            self._seen = set(list(self._seen)[-250:])
        print(f"Photon inbound: {cmd.kind} command accepted (queued for Droid; not executed).")
        try:
            self.on_command(cmd)
        except Exception:
            print("Photon inbound: command handler failed; no private payload logged.")
            return
        if self.ack_text is not None:
            try:
                if cmd.kind == "start":
                    self.ack_text(
                        "AgriDrone: survey queued for Droid. Local y approval still required before takeoff.",
                        f"photon-cmd-ack:{cmd.message_id}",
                    )
                else:
                    self.ack_text(
                        "AgriDrone: abort requested on the ground station.",
                        f"photon-abort-ack:{cmd.message_id}",
                    )
            except Exception:
                print("Photon inbound: ack send failed; command still applied locally.")


def main() -> int:
    """Smoke: connect and print classified commands (no flight)."""
    from ground_station.envutil import load_dotenv

    load_dotenv()
    config = PhotonConfig.load()
    if config is None:
        raise SystemExit("PHOTON_* not configured")
    client = PhotonClient(config)
    client.check()
    done = threading.Event()

    def on_cmd(cmd: InboundCommand) -> None:
        print(f"CMD {cmd.kind} id={cmd.message_id[:16]}…")
        if cmd.kind == "abort":
            done.set()

    inbound = PhotonInbound(client, on_command=on_cmd)
    inbound.start()
    if not inbound.ready.wait(60):
        print("subscribe not ready")
        inbound.close()
        client.close()
        return 1
    print("Listening — text survey/abort from the Photon user. Ctrl-C to quit.")
    try:
        while not done.wait(1.0):
            if inbound.failed.is_set():
                return 1
    except KeyboardInterrupt:
        pass
    finally:
        inbound.close()
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
