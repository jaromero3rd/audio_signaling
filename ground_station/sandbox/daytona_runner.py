"""Daytona sandbox wrapper for Droid-generated code (FR-6).

All code the embedded Droid session produces runs here, never on the host.
One DroidSandbox per session lets Droid iterate (write, run, patch, re-run)
inside the same isolated environment. The sandbox is ephemeral and
auto-stops after 15 idle minutes as a cleanup safety net.

Usage:
    from ground_station.sandbox.daytona_runner import DroidSandbox

    with DroidSandbox() as sb:
        print(sb.run_code("print('hello')")["stdout"])

Smoke test (allocates then deletes one sandbox):
    .venv/bin/python -m ground_station.sandbox.daytona_runner
"""
from __future__ import annotations

from daytona import CreateSandboxFromSnapshotParams, Daytona, DaytonaConfig, Sandbox
from dotenv import load_dotenv

load_dotenv()


class DroidSandbox:
    """One persistent, ephemeral Daytona sandbox for a Droid session."""

    def __init__(self, env_vars: dict[str, str] | None = None, auto_stop_minutes: int = 15):
        # DaytonaConfig() falls back to DAYTONA_API_KEY / DAYTONA_API_URL /
        # DAYTONA_TARGET env vars and raises a descriptive auth error if unset.
        self._daytona = Daytona(DaytonaConfig())
        self._sandbox: Sandbox = self._daytona.create(
            CreateSandboxFromSnapshotParams(
                env_vars=env_vars or {},
                ephemeral=True,
                auto_stop_interval=auto_stop_minutes,
                labels={"project": "agridrone", "owner": "droid-runtime"},
            )
        )

    @property
    def id(self) -> str:
        return self._sandbox.id

    @staticmethod
    def _result(resp) -> dict:
        stderr = getattr(resp, "stderr", None) or (resp.additional_properties or {}).get("stderr") or ""
        return {"exit_code": resp.exit_code, "stdout": resp.result, "stderr": stderr}

    def run_code(self, code: str, timeout: int = 120) -> dict:
        """Run Python code in the sandbox. Returns {exit_code, stdout, stderr}."""
        return self._result(self._sandbox.process.code_run(code, timeout=timeout))

    def exec(self, command: str, timeout: int = 120) -> dict:
        """Run a shell command in the sandbox (zsh/bash/sh)."""
        return self._result(self._sandbox.process.exec(command, timeout=timeout))

    def write_file(self, remote_path: str, content: str) -> None:
        """Upload a file (e.g. a Droid-generated script) into the sandbox."""
        self._sandbox.fs.upload_file(content.encode(), remote_path)

    def close(self) -> None:
        self._daytona.delete(self._sandbox)

    def __enter__(self) -> "DroidSandbox":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


if __name__ == "__main__":
    with DroidSandbox() as sb:
        print("sandbox:", sb.id)
        print(sb.run_code("import sys; print('daytona ok', sys.version.split()[0])"))
