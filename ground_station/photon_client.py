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
from urllib.parse import urlparse

from ground_station.photon_alert import send_photon_alert as _shell_send

log = logging.getLogger(__name__)

PHOTO_TIMEOUT_S = 30


def _sniff_image_ext(data: bytes) -> str:
    """Sniff image format from magic bytes. Default to .jpg if unknown."""
    if data.startswith(b"\x89PNG"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"RIFF") and b"WEBP" in data[:12]:
        return ".webp"
    return ".jpg"


def send_alert(message: str, photo_url: str, photo_base64: str | None = None) -> dict:
    """Send alert to operator via Photon 2.0 API.

    Args:
        message: Alert text sent as an iMessage to the configured operator.
        photo_url: Remote URL or local path to photo (used when photo_base64 is None).
        photo_base64: Base64-encoded JPEG/PNG/WebP bytes; takes priority over photo_url.

    Returns:
        {"ok": True, "stdout": ...} on success, {"ok": False, "error": ...} on failure.
    """
    tmp_path: Path | None = None
    try:
        photo_path: str | None = None

        if photo_base64:
            photo_bytes = base64.b64decode(photo_base64)
            ext = _sniff_image_ext(photo_bytes)
            with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
                tmp.write(photo_bytes)
                tmp_path = Path(tmp.name)
            photo_path = str(tmp_path)
        elif photo_url and photo_url.startswith(("http://", "https://")):
            # Try to extract extension from URL; sniff if missing/wrong
            parsed = urlparse(photo_url)
            url_ext = Path(parsed.path).suffix.lower()
            ext = url_ext if url_ext in (".jpg", ".jpeg", ".png", ".webp") else None
            with tempfile.NamedTemporaryFile(suffix=ext or ".jpg", delete=False) as tmp:
                tmp_path = Path(tmp.name)
            # Download with timeout (matching vision_grok.py pattern)
            urllib.request.urlretrieve(photo_url, str(tmp_path), timeout=PHOTO_TIMEOUT_S)
            # If extension was guessed, sniff the actual format
            if not ext:
                with open(tmp_path, "rb") as f:
                    magic = f.read(12)
                new_ext = _sniff_image_ext(magic)
                if new_ext != ".jpg":
                    new_path = tmp_path.with_suffix(new_ext)
                    tmp_path.rename(new_path)
                    tmp_path = new_path
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
