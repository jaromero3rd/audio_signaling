"""Thin adapter: photo file path → health dict, errors → safe default (FR-2).

Wraps vision_grok.analyze_photo so callers always receive a plain dict and
never see an exception. Runs asynchronously off the flight control loop so
the drone keeps receiving RC keepalive commands.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable


def analyze_photo_with_grok_async(
    photo_path: str,
    on_complete: Callable[[dict], None],
) -> None:
    """Send photo to Grok asynchronously (off control loop).

    Runs analysis in a background thread so the main loop can continue
    sending RC keepalive commands to the drone. Calls on_complete callback
    when analysis completes.

    Args:
        photo_path: Path to photo file
        on_complete: Callback(result_dict) called when analysis completes
    """
    def _run():
        try:
            from ground_station.vision_grok import analyze_photo

            result = analyze_photo(Path(photo_path))
            output = {
                "health_score": result.health_score,
                "analysis_summary": result.analysis_summary,
            }
        except Exception as exc:  # noqa: BLE001
            output = {"health_score": 0.5, "analysis_summary": f"error: {exc}"}
        on_complete(output)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()


def analyze_photo_with_grok(photo_path: str) -> dict:
    """Send photo to Grok, extract health_score (0.0–1.0) and analysis_summary.

    BLOCKING: This function blocks for up to 90 seconds. For real-time control
    loops (e.g., drone flight), use analyze_photo_with_grok_async instead.

    Returns: {"health_score": float, "analysis_summary": str}
    On any error returns health_score=0.5 and the error message as summary.
    """
    try:
        from ground_station.vision_grok import analyze_photo

        result = analyze_photo(Path(photo_path))
        return {
            "health_score": result.health_score,
            "analysis_summary": result.analysis_summary,
        }
    except Exception as exc:  # noqa: BLE001
        return {"health_score": 0.5, "analysis_summary": f"error: {exc}"}
