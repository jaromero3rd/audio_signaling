"""Persistent mission/session state for the embedded Droid session.

Chalk "Agents" (controller-managed agent sessions) is not exposed by
chalkpy 2.163.1, and this environment has no online/offline store - so
this module persists state as JSON files on a Chalk Volume instead via
the chalk.volume.v1.VolumeService gRPC API. Volumes are durable object
storage, so mission state survives ground-station restarts.

Access path: the volume API is not on the public ChalkClient surface, so
we reuse the client's authenticated server channel
(client._get_grpc_client()._stub_refresher._stub.server_channel). If a
future chalkpy release exposes a volume client, swap _volume_stub().

Layout inside the volume (name from CHALK_STATE_VOLUME, default
"droid-mission-state"):
  mission_state.json      - latest mission snapshot (dict)
  decision_log.jsonl      - one JSON decision entry per line, append-only
  fields/<field_id>.json  - last survey result per field
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import grpc
from chalk._gen.chalk.volume.v1 import volume_pb2, volume_pb2_grpc

from ground_station.chalk_client import get_client

VOLUME = os.environ.get("CHALK_STATE_VOLUME", "droid-mission-state")
STATE_PATH = "mission_state.json"
LOG_PATH = "decision_log.jsonl"
FIELDS_PREFIX = "fields/"

_stub = None


def _volume_stub():
    """Shared VolumeService stub on the ChalkClient's authenticated
    channel; ensures the state volume exists on first use."""
    global _stub
    if _stub is None:
        channel = get_client()._get_grpc_client()._stub_refresher._stub.server_channel
        stub = volume_pb2_grpc.VolumeServiceStub(channel)
        try:
            stub.GetVolume(volume_pb2.GetVolumeRequest(name=VOLUME))
        except grpc.RpcError as e:
            if e.code() != grpc.StatusCode.NOT_FOUND:
                raise
            stub.CreateVolume(volume_pb2.CreateVolumeRequest(name=VOLUME))
        _stub = stub
    return _stub


def _read_file(path: str) -> bytes | None:
    try:
        res = _volume_stub().GetFile(
            volume_pb2.GetFileRequest(volume_name=VOLUME, path=path)
        )
        return res.data
    except grpc.RpcError as e:
        if e.code() == grpc.StatusCode.NOT_FOUND:
            return None
        raise


def _write_file(path: str, data: bytes) -> None:
    _volume_stub().PutFile(
        volume_pb2.PutFileRequest(volume_name=VOLUME, path=path, data=data)
    )


def load_mission_state() -> dict:
    """Latest mission snapshot, or {} if the session has never saved."""
    data = _read_file(STATE_PATH)
    return json.loads(data) if data else {}


def save_mission_state(state: dict) -> None:
    """Overwrite the mission snapshot the Droid session reasons over."""
    _write_file(STATE_PATH, json.dumps(state).encode())


def append_decision(entry: dict) -> None:
    """Append one decision entry to the JSONL log (adds a UTC `ts` if the
    caller did not set one). Read-modify-write is fine at log sizes a
    mission produces."""
    entry = {"ts": datetime.now(timezone.utc).isoformat(), **entry}
    data = (_read_file(LOG_PATH) or b"") + json.dumps(entry).encode() + b"\n"
    _write_file(LOG_PATH, data)


def decision_log() -> list[dict]:
    """All recorded decisions, oldest first - feed into the session as
    history so Droid does not repeat itself."""
    data = _read_file(LOG_PATH)
    if not data:
        return []
    return [json.loads(line) for line in data.splitlines() if line.strip()]


def get_last_survey(field_id: str) -> dict | None:
    """Cached result of the most recent evaluate_survey() for a field."""
    data = _read_file(f"{FIELDS_PREFIX}{field_id}.json")
    return json.loads(data) if data else None


def set_last_survey(field_id: str, result: dict) -> None:
    """Cache a field's latest survey result (e.g. the dict returned by
    chalk_client.evaluate_survey) keyed by field_id."""
    _write_file(f"{FIELDS_PREFIX}{field_id}.json", json.dumps(result).encode())
