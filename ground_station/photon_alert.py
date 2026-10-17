"""Photon alert bridge for the embedded Droid session (FR-4).

Backend for the Droid `send_photon_alert` tool: shells out to the
photon-imessage-agent's alert.ts, which uploads the photo as a Photon
attachment and sends it + the text into the operator's direct iMessage
space (saved by the agent's checkpoint after the sender's first message).

Requires Bun and `bun install` inside photon-imessage-agent/.

    from ground_station.photon_alert import send_photon_alert
    send_photon_alert("corn-north critical", photo_path="captures/img.jpg")
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parent.parent / "photon-imessage-agent"


def send_photon_alert(message: str, photo_path: str | None = None) -> dict:
    """Send an iMessage alert to the configured operator.

    photo_path: local image file (jpeg/png/webp) sent as an attachment,
    followed by the text caption. Returns {"stdout": ...}. Raises
    RuntimeError if the agent reports failure.
    """
    bun = shutil.which("bun")
    if not bun:
        raise RuntimeError("bun not found; photon-imessage-agent requires Bun >= 1.3")
    if photo_path and not Path(photo_path).is_file():
        raise FileNotFoundError(photo_path)

    cmd = [bun, "run", "src/alert.ts"]
    if message:
        cmd += ["--text", message]
    if photo_path:
        cmd += ["--image", str(Path(photo_path).resolve())]

    res = subprocess.run(cmd, cwd=AGENT_DIR, capture_output=True, text=True, timeout=90)
    if res.returncode != 0:
        raise RuntimeError(f"photon alert failed: {res.stderr.strip()}")
    return {"stdout": res.stdout.strip()}


if __name__ == "__main__":
    send_photon_alert("AgriDrone alert bridge test")
