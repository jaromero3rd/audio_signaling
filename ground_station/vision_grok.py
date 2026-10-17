"""Grok image understanding for one captured field photo (FR-2).

Sends the still to the xAI Responses API and returns a crop-health result
Chalk can score. This is a regular vision model with image input. It is not
Grok Imagine.
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

RESPONSES_URL = "https://api.x.ai/v1/responses"
DEFAULT_MODEL = "grok-4.7"
MAX_IMAGE_BYTES = 20 * 1024 * 1024
DEFAULT_TIMEOUT_S = 90.0

_MIME = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}
_MAGIC = {
    "image/jpeg": b"\xff\xd8\xff",
    "image/png": b"\x89PNG\r\n\x1a\n",
}

CROP_HEALTH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "health_score": {
            "type": "number",
            "description": "1.0 is a dense even healthy stand. 0.0 is bare soil, severe stress, or no crop.",
        },
        "analysis_summary": {
            "type": "string",
            "description": "One or two sentences an operator can act on. Under 40 words.",
        },
        "observations": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Short visible findings only: color, density, stress, pests, bare soil, or that the frame is not a crop.",
        },
    },
    "required": ["health_score", "analysis_summary", "observations"],
}

PROMPT = """You are an agronomist scoring one drone photo.
Judge only what is visible. Do not invent weather, soil moisture, pests you cannot see, or lab results.
health_score is 1.0 for a dense, even, green wheat stand and 0.0 for bare soil or severe discoloration.
If the frame is an AprilTag, a desk, a person, or otherwise not a crop canopy, set health_score to 0.5 and say the photo is not a crop canopy.
Keep analysis_summary under 40 words.
""".strip()

Poster = Callable[[str, dict[str, Any], str, float], dict[str, Any]]


@dataclass(frozen=True)
class CropHealth:
    health_score: float
    analysis_summary: str
    observations: tuple[str, ...]
    model: str
    photo_path: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "health_score": self.health_score,
            "analysis_summary": self.analysis_summary,
            "observations": list(self.observations),
            "model": self.model,
            "photo_path": self.photo_path,
        }

    def as_chalk_inputs(self) -> dict[str, Any]:
        """Inputs evaluate_survey() expects. This function does not call Chalk."""
        return {
            "health_score": self.health_score,
            "analysis_summary": self.analysis_summary,
            "photo_url": self.photo_path,
        }


def analyze_photo(
    path: Path | str,
    *,
    model: str | None = None,
    api_key: str | None = None,
    poster: Poster | None = None,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> CropHealth:
    """Read one jpg or png and return a validated crop-health result."""
    photo = Path(path)
    key = api_key if api_key is not None else os.environ.get("XAI_API_KEY", "")
    if not key:
        raise RuntimeError("Set XAI_API_KEY for Grok image analysis")
    chosen = model or os.environ.get("XAI_VISION_MODEL") or DEFAULT_MODEL
    mime, raw = _read_image(photo)
    payload = build_request(raw, mime, chosen)
    body = (poster or post_responses)(RESPONSES_URL, payload, key, timeout)
    return parse_crop_health(body, photo_path=str(photo), model=chosen)


def build_request(image: bytes, mime: str, model: str) -> dict[str, Any]:
    if mime not in _MAGIC:
        raise ValueError(f"unsupported image type {mime}")
    encoded = base64.b64encode(image).decode("ascii")
    return {
        "model": model,
        "store": False,
        "reasoning": {"effort": "low"},
        "input": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_image",
                        "image_url": f"data:{mime};base64,{encoded}",
                        "detail": "high",
                    },
                    {"type": "input_text", "text": PROMPT},
                ],
            }
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "crop_health",
                "schema": CROP_HEALTH_SCHEMA,
                "strict": True,
            }
        },
    }


def parse_crop_health(body: dict[str, Any], *, photo_path: str, model: str) -> CropHealth:
    status = body.get("status")
    if status not in (None, "completed"):
        raise RuntimeError(f"xAI image analysis status {status}")
    raw = _output_text(body)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("xAI image analysis did not return JSON") from exc
    if not isinstance(data, dict):
        raise RuntimeError("xAI image analysis JSON was not an object")

    score = data.get("health_score")
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        raise RuntimeError("xAI image analysis health_score was not a number")
    summary = data.get("analysis_summary")
    if not isinstance(summary, str) or not summary.strip():
        raise RuntimeError("xAI image analysis summary was empty")
    observations = data.get("observations")
    if not isinstance(observations, list) or not all(isinstance(item, str) for item in observations):
        raise RuntimeError("xAI image analysis observations were not a list of strings")

    clipped = max(0.0, min(1.0, float(score)))
    notes = tuple(item.strip()[:160] for item in observations[:8] if item.strip())
    return CropHealth(
        health_score=round(clipped, 3),
        analysis_summary=" ".join(summary.split())[:400],
        observations=notes,
        model=model,
        photo_path=photo_path,
    )


def post_responses(url: str, payload: dict[str, Any], api_key: str, timeout: float) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            parsed = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"xAI image analysis HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"xAI image analysis request failed: {exc.reason}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("xAI image analysis response was not an object")
    return parsed


def _read_image(path: Path) -> tuple[str, bytes]:
    mime = _MIME.get(path.suffix.lower())
    if mime is None:
        raise ValueError(f"photo must be jpg or png, got {path.suffix or 'no suffix'}")
    if not path.is_file():
        raise FileNotFoundError(path)
    raw = path.read_bytes()
    if not raw:
        raise ValueError(f"photo is empty: {path}")
    if len(raw) > MAX_IMAGE_BYTES:
        raise ValueError(f"photo is over 20 MiB: {path}")
    if not raw.startswith(_MAGIC[mime]):
        raise ValueError(f"photo bytes do not match {mime}: {path}")
    return mime, raw


def _output_text(body: dict[str, Any]) -> str:
    direct = body.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct
    chunks: list[str] = []
    for item in body.get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for part in item.get("content") or []:
            if isinstance(part, dict) and part.get("type") == "output_text" and isinstance(part.get("text"), str):
                chunks.append(part["text"])
    text = "".join(chunks).strip()
    if not text:
        raise RuntimeError("xAI image analysis returned no text")
    return text


def main() -> int:
    import sys

    from ground_station.envutil import load_dotenv

    load_dotenv()
    if len(sys.argv) != 2:
        print("usage: python -m ground_station.vision_grok path/to/photo.jpg")
        return 2
    result = analyze_photo(Path(sys.argv[1]))
    print(json.dumps(result.as_dict(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
