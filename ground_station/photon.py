"""Host-owned Photon Beta iMessage notifications, never exposed as model tools."""

from __future__ import annotations

import argparse
import os
import queue
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
RESOURCE_ID = re.compile(r"pho_[a-z]{2,8}_[0-7][0-9a-hjkmnp-tv-z]{25}")
MESSAGES = {
    "takeoff": "The local drone adapter acknowledged takeoff.",
    "photo": "A survey photo was saved locally. Analysis may still be pending.",
    "analysis": "Survey analysis result",
    "land": "The local drone adapter acknowledged landing.",
    "complete": "The survey state machine completed. See the ground station for results.",
    "abort": "A mission abort was requested. Check the ground station for landing status.",
    "controller_failed": "The Droid controller stopped and mission control is disabled. Check the ground station for landing status.",
    "test": "System-to-user messaging is configured. This is a connectivity test; no flight was started.",
}


class PhotonError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True, repr=False)
class PhotonConfig:
    key: str = field(repr=False)
    project: str
    assignment: str
    agent: str
    user: str
    space_id: str | None = None

    @classmethod
    def load(cls, path: Path | None = None):
        # Parse private files without exporting their keys to child processes.
        values = {**dotenv_values(ROOT / ".env", interpolate=False),
                  **dotenv_values(ROOT / ".env.droid.local", interpolate=False),
                  **os.environ}
        source = path or values.get("PHOTON_ENV_FILE")
        if source:
            source = Path(source).expanduser()
            if not source.is_file():
                raise PhotonError("Photon environment file does not exist")
            values.update(dotenv_values(source, interpolate=False))
        if not source and not values.get("PHOTON_API_KEY"):
            return None

        def required(name):
            value = values.get(name)
            if not isinstance(value, str) or not value.strip():
                raise PhotonError(f"Missing {name}")
            return value.strip()

        config = cls(
            key=required("PHOTON_API_KEY"),
            project=required("PHOTON_PROJECT_ID"),
            assignment=required("PHOTON_ASSIGNMENT_ID"),
            agent=required("PHOTON_AGENT_HANDLE"),
            user=str(values.get("PHOTON_USER_HANDLE") or values.get("PHOTON_TEST_SENDER") or "").strip(),
            space_id=values.get("PHOTON_SPACE_ID") or None,
        )
        for value in (config.project, config.assignment, config.space_id):
            if value is not None and not RESOURCE_ID.fullmatch(value):
                raise PhotonError("Invalid Photon resource identifier")
        if not config.project.startswith("pho_prj_") or not config.assignment.startswith("pho_asg_"):
            raise PhotonError("Use a Photon Beta project and iMessage assignment")
        if any(not re.fullmatch(r"\+[1-9]\d{7,14}", handle) for handle in (config.agent, config.user)):
            raise PhotonError("Photon agent and user must be E.164 phone numbers")
        if config.agent == config.user:
            raise PhotonError("Photon agent and user must be different")
        return config


class PhotonClient:
    def __init__(self, config: PhotonConfig, *, transport=None):
        self.config = config
        self._route: tuple[str, str] | None = None
        self._http = httpx.Client(
            base_url="https://api.photon.codes",
            headers={"Authorization": f"Bearer {config.key}", "User-Agent": "AgriDrone-Mission/0.1"},
            timeout=10,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    def _request(self, method: str, path: str, **kwargs) -> dict:
        try:
            response = self._http.request(method, path, **kwargs)
        except httpx.HTTPError:
            raise PhotonError("Photon transport failed", retryable=True) from None
        if not 200 <= response.status_code < 300:
            raise PhotonError(
                f"Photon request failed (HTTP {response.status_code})",
                retryable=response.status_code in (408, 429) or response.status_code >= 500,
            )
        try:
            data = response.json()
        except ValueError:
            raise PhotonError("Photon returned an invalid response") from None
        if not isinstance(data, dict):
            raise PhotonError("Photon returned an invalid response")
        return data

    def check(self) -> None:
        """Read-only validation; no resource creation and no message send."""
        config = self.config
        self._route = None
        base = f"/v1/projects/{config.project}"
        project = self._request("GET", base)
        assignment = self._request(
            "GET", f"{base}/platforms/imessage/assignments/{config.assignment}"
        )
        if project.get("deletedAt") or project.get("projectId") != config.project:
            raise PhotonError("Photon project is not active")
        if (
            assignment.get("projectId") != config.project
            or assignment.get("platform") != "imessage"
            or assignment.get("releasedAt")
            or assignment.get("handle") != config.user
            or assignment.get("assignedPhoneNumber") != config.agent
        ):
            raise PhotonError("Photon assignment does not match the configured agent and user")
        matches = []
        params = {"platform": "imessage", "participantHandle": config.user, "pageSize": 100}
        deadline = time.monotonic() + 30
        for _ in range(10):
            if time.monotonic() >= deadline:
                raise PhotonError("Photon route discovery timed out")
            if config.space_id:
                page = {"items": [self._request("GET", f"{base}/spaces/{config.space_id}")]}
            else:
                page = self._request("GET", f"{base}/spaces", params=params)
            for space in page.get("items", []):
                if time.monotonic() >= deadline:
                    raise PhotonError("Photon route discovery timed out")
                if (
                    space.get("projectId") != config.project
                    or space.get("platform") != "imessage" or space.get("kind") != "direct"
                ):
                    continue
                ids = space.get("participantIds", [])
                if not isinstance(ids, list) or len(ids) != 2:
                    continue
                if not all(isinstance(value, str) and RESOURCE_ID.fullmatch(value) for value in ids):
                    continue
                if len(set(ids)) != 2:
                    continue
                actors = [self._request("GET", f"{base}/users/{value}") for value in ids]
                agent = next((actor for actor in actors if actor.get("kind") == "agent"
                              and actor.get("handle") == config.agent), None)
                user = next((actor for actor in actors if actor.get("kind") == "human"
                             and actor.get("handle") == config.user), None)
                if agent and user and all(
                    actor.get("projectId") == config.project and actor.get("platform") == "imessage"
                    and actor.get("id") in ids for actor in (agent, user)
                ):
                    space_id = space.get("id")
                    if isinstance(space_id, str) and RESOURCE_ID.fullmatch(space_id):
                        matches.append((space_id, agent["id"]))
            token = page.get("nextPageToken")
            if not token:
                break
            params["pageToken"] = token
        else:
            raise PhotonError("Photon route discovery exceeded its page limit")
        if len(matches) != 1:
            raise PhotonError("Expected one direct agent/user space; set PHOTON_SPACE_ID if needed")
        self._route = matches[0]

    def send(self, event: str, event_id: str, *, mock: bool, analysis: dict | None = None) -> dict:
        if event not in MESSAGES:
            raise PhotonError("Unknown system notification")
        text = MESSAGES[event]
        if event == "analysis":
            text = analysis_text(analysis)
        elif analysis is not None:
            raise PhotonError("Unexpected analysis payload")
        if self._route is None:
            raise PhotonError("Photon routing has not been verified")
        space_id, sender_id = self._route
        result = self._request(
            "POST", f"/v1/projects/{self.config.project}/messages",
            headers={"idempotency-key": f"agridrone-v1:{event_id}"},
            json={
                "spaceId": space_id,
                "senderId": sender_id,
                # Shared iMessage spaces do not support parentMessageId replies.
                "content": {"type": "text", "format": "plain",
                            "text": f"AgriDrone ({'MOCK' if mock else 'TELLO'}): {text}"},
            },
        )
        message_id = result.get("id")
        if not isinstance(message_id, str) or not RESOURCE_ID.fullmatch(message_id):
            raise PhotonError("Photon accepted no valid message identifier")
        return {"id": message_id, "status": result.get("status", "unknown")}

    def delivery_status(self, message_id: str) -> str:
        if not RESOURCE_ID.fullmatch(message_id):
            raise PhotonError("Invalid Photon message identifier")
        result = self._request("GET", f"/v1/projects/{self.config.project}/messages/{message_id}")
        status = result.get("status")
        return status if status in ("queued", "sending", "sent", "delivered", "read", "failed") else "unknown"

    def close(self):
        self._http.close()


def analysis_text(analysis: dict | None) -> str:
    from ground_station.agent.analysis import SurveyAnalysis

    try:
        validated = SurveyAnalysis.model_validate({
            name: analysis[name] for name in ("field_id", "health_score", "analysis_summary")
        })
        source = analysis["vision_source"]
        if source not in ("droid", "fake"):
            raise ValueError("Unknown vision source")
    except (ValueError, TypeError, KeyError):
        raise PhotonError("Invalid host analysis notification") from None
    features = analysis.get("features") if isinstance(analysis, dict) else None
    insight = features.get("insight") if isinstance(features, dict) else None
    if isinstance(insight, str) and insight.strip():
        prefix = "SIMULATED. " if source == "fake" else ""
        return f"Field {validated.field_id}: {prefix}{insight.strip()}"
    label = "SIMULATED, not a crop diagnosis" if source == "fake" else "Droid visual estimate, not a verified diagnosis"
    return (f"Field {validated.field_id}: health score {validated.health_score:.2f}. "
            f"{label}. Findings (untrusted model observations): {validated.analysis_summary}")


class PhotonNotifier:
    """Bounded, best-effort delivery off the RC loop; one notification per event."""

    def __init__(self, client: PhotonClient, *, mock: bool):
        self.client = client
        self.mock = mock
        self._queue: queue.Queue[tuple[str, str, dict | None]] = queue.Queue(maxsize=len(MESSAGES) + 3)
        self._seen: set[str] = set()
        self._lock = threading.Lock()
        self._closing = threading.Event()
        self._thread = threading.Thread(target=self._serve, name="agridrone-photon", daemon=True)
        self._run_id = uuid.uuid4().hex

    def start(self) -> None:
        try:
            self.client.check()
        except BaseException:
            self.client.close()
            raise
        self._thread.start()

    def notify(self, event: str) -> None:
        with self._lock:
            if event not in MESSAGES or event in ("test", "analysis") or event in self._seen or self._closing.is_set():
                return
            self._seen.add(event)
            try:
                self._queue.put_nowait((event, f"{self._run_id}:{event}", None))
            except queue.Full:
                print("Photon notification queue full; flight control was not blocked.")

    def notify_analysis(self, analysis: dict) -> bool:
        analysis_text(analysis)
        receipt = analysis.get("receipt", "")
        if not isinstance(receipt, str) or not re.fullmatch(r"[a-f0-9]{32}:[1-3]", receipt):
            raise PhotonError("Invalid host analysis receipt")
        with self._lock:
            key = f"analysis:{receipt}"
            if key in self._seen or self._closing.is_set():
                return False
            try:
                self._queue.put_nowait(("analysis", f"{self._run_id}:{key}", dict(analysis)))
            except queue.Full:
                return False
            self._seen.add(key)
            return True

    def _serve(self) -> None:
        try:
            while not self._closing.is_set() or not self._queue.empty():
                try:
                    event, event_id, analysis = self._queue.get(timeout=0.1)
                except queue.Empty:
                    continue
                for attempt in range(3):
                    try:
                        result = self.client.send(event, event_id, mock=self.mock, analysis=analysis)
                        print(f"Photon notification accepted ({event}); delivery is not yet confirmed.")
                        if result["status"] in ("delivered", "read"):
                            print(f"Photon delivery confirmed ({event}).")
                        break
                    except PhotonError as error:
                        if not error.retryable or attempt == 2:
                            print(f"Photon notification failed ({event}); no private payload logged.")
                            break
                        time.sleep(0.25 * (attempt + 1))
        except Exception as error:
            print(f"Photon notification worker stopped ({type(error).__name__}); no private payload logged.")
        finally:
            self.client.close()

    def close(self) -> None:
        self._closing.set()
        if self._thread.ident is None:
            self.client.close()
            return
        self._thread.join(timeout=5)
        if self._thread.is_alive():
            print("Photon delivery still pending; this best-effort queue is not durable.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Check Photon routing or explicitly send one setup test.")
    parser.add_argument("--env", type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="read only; sends no messages")
    mode.add_argument("--send-test", action="store_true", help="sends one fixed setup confirmation iMessage")
    args = parser.parse_args()
    client = None
    try:
        config = PhotonConfig.load(args.env)
        if config is None:
            raise PhotonError("Photon is not configured")
        client = PhotonClient(config)
        client.check()
        print("Photon credentials, active assignment and exact direct user/agent routing verified.")
        if args.send_test:
            result = client.send("test", uuid.uuid4().hex, mock=True)
            print(f"Setup confirmation accepted: {result['id']}; delivery is not yet confirmed.")
            for _ in range(15):
                status = client.delivery_status(result["id"])
                if status in ("delivered", "read"):
                    print("Photon confirmed delivery.")
                    return 0
                if status == "failed":
                    raise PhotonError("Photon reported failed delivery")
                time.sleep(2)
            print("Delivery remains pending; no duplicate test was sent.")
            return 2
        print("Read-only configuration check passed. No message sent.")
        return 0
    except PhotonError as error:
        print(str(error))
        return 1
    finally:
        if client:
            client.close()


if __name__ == "__main__":
    raise SystemExit(main())
