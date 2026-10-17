"""Thin adapter: photo file path → health dict, errors → safe default (FR-2).

Wraps vision_grok.analyze_photo so callers always receive a plain dict and
never see an exception.
"""

from __future__ import annotations

from pathlib import Path


def analyze_photo_with_grok(photo_path: str) -> dict:
    """Send photo to Grok, extract health_score (0.0–1.0) and analysis_summary.

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
