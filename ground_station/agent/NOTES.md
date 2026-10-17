# Chalk "Agents" capability — investigation notes

## Verdict: Agents API not available in this environment; Chalk Volumes used instead

### What "Agents" means in Chalk

Chalk's agent capability (docs.chalk.ai/docs/compute/*) runs agent workloads
as Chalk Compute functions/sandboxes via a Controller API and the
`chalkcompute` Python package — it is *compute*, not a key-value session
store. There is no hosted "agent session state" API.

Evidence it is not usable here:

- `chalkpy 2.163.1` has no agents surface: `ChalkClient` has no
  `create_agent`/`get_agent`/session methods (see
  `.venv/.../chalk/client/client.py`), and `chalk/functions` is SQL
  expression helpers, not compute functions.
- The only "agent" protos are `chalk._gen.chalk.auth.v1.agent_pb2` —
  auth *identities* (`AGENT_KIND_SERVICE_TOKEN`, `UserAgent`). Our JWT's
  `agents.read`/`agents.write` permissions cover those identities
  (`TeamService/GetAgent`), not AI sessions.
- `chalkcompute` is not installed (`ModuleNotFoundError`) and is not in
  requirements.txt.
- CLI has no `agent` subcommand. `chalk sandbox`/`session`/`container`
  exist (compute ops, visible in `chalk --help`) but spawn interactive
  containers — not a state API for our embedded session.

### What DOES work: Chalk Volumes (no online/offline store required)

`chalk.volume.v1.VolumeService` (gRPC on api.chalk.ai) gives durable file
storage — verified live with our service token:

- `CreateVolume` / `GetVolume` / `ListVolumes` / `ListFiles` /
  `GetFile` / `PutFile` / `RemoveFile` — all succeed via
  `VolumeServiceStub` bound to the client's authenticated server channel.
- CLI equivalent verified: `chalk volume create/ls/cp/cat` work against
  env `production` (e.g. `{"test":1}` round-tripped through a probe file).

`store.py` in this package wraps this for the Droid session:
`load_mission_state` / `save_mission_state` / `append_decision` /
`decision_log` / `get_last_survey` / `set_last_survey`, all backed by
files in volume `droid-mission-state` (auto-created; override with
`CHALK_STATE_VOLUME`).

### Caveats

- The channel path uses chalkpy internals
  (`client._get_grpc_client()._stub_refresher._stub.server_channel`) —
  private API, could break on a chalkpy upgrade. If a future release adds
  a public volume client, swap `_volume_stub()`.
- Writes are whole-file read-modify-write: fine for mission-scale JSON,
  not a high-throughput log.
- A v2 volume API (`chalk.volume.v2`, used by `chalk volume` CLI) exists
  with refs/versioning, but writes need a signed-URL upload + commit flow —
  deliberately not used; v1 is sufficient.
