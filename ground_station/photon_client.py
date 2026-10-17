"""Photon 2.0 alert client for ground station (FR-4).

Resolves a remote photo URL or base64-encoded image to a local temp
file and delegates to photon_alert.send_photon_alert, which shells
out to photon-imessage-agent/src/alert.ts via Bun.

Required env vars (consumed by alert.ts):
    PHOTON_API_KEY, PHOTON_PROJECT_ID, PHOTON_ASSIGNMENT_ID,
    PHOTON_TEST_SENDER, PHOTON_AGENT_HANDLE
"""
from __future__ import annotations

import base64
import logging
import tempfile
import urllib.request
from pathlib import Path

from ground_station.photon_alert import send_photon_alert as _shell_send

log = logging.getLogger(__name__)


def send_alert(message: str, photo_url: str, photo_base64: str | None = None) -> dict:
    """Send alert to operator via Photon 2.0 API.

    Args:
        message: Alert text sent as an iMessage to the configured operator.
        photo_url: Remote URL or local path to photo (used when photo_base64 is None).
        photo_base64: Base64-encoded JPEG/PNG bytes; takes priority over photo_url.

    Returns:
        {"ok": True, "stdout": ...} on success, {"ok": False, "error": ...} on failure.
    """
    tmp_path: Path | None = None
    try:
        photo_path: str | None = None

        if photo_base64:
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                tmp.write(base64.b64decode(photo_base64))
                tmp_path = Path(tmp.name)
            photo_path = str(tmp_path)
        elif photo_url and photo_url.startswith(("http://", "https://")):
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                tmp_path = Path(tmp.name)
            urllib.request.urlretrieve(photo_url, str(tmp_path))
            photo_path = str(tmp_path)
        elif photo_url:
            photo_path = photo_url  # local path — passed through directly

        result = _shell_send(message, photo_path=photo_path)
        return {"ok": True, **result}
    except Exception as exc:
        log.error("Photon alert failed: %s", exc)
        return {"ok": False, "error": str(exc)}
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
