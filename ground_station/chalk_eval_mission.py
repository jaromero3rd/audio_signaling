"""Thin mission wrapper: evaluate one photo analysis through Chalk + log decision."""
from __future__ import annotations

from ground_station.agent.store import append_decision, set_last_survey
from ground_station.chalk_client import evaluate_survey

_FS = "field_survey"


def evaluate_and_log(photo_url: str, health_score: float, field_id: str) -> dict:
    """Evaluate photo + health via Chalk. Log decision. Return {health_flag, severity, action}."""
    try:
        raw = evaluate_survey(
            field_id=field_id,
            health_score=health_score,
            analysis_summary=f"health_score={health_score:.3f} photo={photo_url}",
            photo_url=photo_url,
        )
    except Exception as exc:
        result = {"health_flag": "unknown", "severity": 0.0, "action": "continue", "error": str(exc)}
        append_decision({"type": "chalk_eval_error", "field_id": field_id, "error": str(exc)})
        return result

    result = {
        "health_flag": raw.get(f"{_FS}.health_flag", "unknown"),
        "severity": raw.get(f"{_FS}.severity", 0.0),
        "action": raw.get(f"{_FS}.recommended_action", "continue"),
    }
    append_decision({
        "type": "chalk_eval",
        "field_id": field_id,
        "photo_url": photo_url,
        "health_score": health_score,
        **result,
    })
    set_last_survey(field_id, {**result, "photo_url": photo_url, "health_score": health_score})
    return result
