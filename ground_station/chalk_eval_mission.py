"""Thin mission wrapper: evaluate one photo analysis through Chalk + log decision."""
from __future__ import annotations

from ground_station.agent.store import append_decision, set_last_survey
from ground_station.chalk_client import evaluate_survey, load_weather_live

_FS = "field_survey"


def justify_score(health_score: float, analysis_summary: str = "") -> dict:
    """Live NOAA/NASA explanation of one image score. Does not call Chalk."""
    summary = analysis_summary or ""
    try:
        insight, justified = _local_justification(health_score, summary)
        return {
            "insight": insight,
            "justified_action": justified,
            "weather_source": "local live weather",
        }
    except Exception as exc:
        return {
            "insight": "Live weather was unavailable, so the image stands alone.",
            "justified_action": _image_action(health_score),
            "weather_source": "unavailable",
            "weather_error": str(exc),
        }


def evaluate_and_log(
    photo_url: str,
    health_score: float,
    field_id: str,
    analysis_summary: str = "",
    include_weather: bool = False,
) -> dict:
    """Evaluate one image score. Live weather is off unless include_weather is set.

    The Droid SDK survey pipeline calls justify_score() itself. This flag stays
    off so the leftover Grok photo callback does not start a NOAA pull.
    """
    summary = analysis_summary or ""
    if not include_weather:
        try:
            raw = evaluate_survey(
                field_id=field_id,
                health_score=health_score,
                analysis_summary=summary,
                photo_url=photo_url,
            )
        except Exception as exc:
            result = {
                "health_flag": "unknown",
                "severity": 0.0,
                "action": "continue",
                "error": str(exc),
            }
            _log(field_id, photo_url, health_score, result, error=True)
            return result
        result = {
            "health_flag": raw.get(f"{_FS}.health_flag", "unknown"),
            "severity": raw.get(f"{_FS}.severity", 0.0),
            "action": raw.get(f"{_FS}.recommended_action", "continue"),
        }
        _log(field_id, photo_url, health_score, result, error=False)
        return result

    try:
        raw = evaluate_survey(
            field_id=field_id,
            health_score=health_score,
            analysis_summary=summary,
            photo_url=photo_url,
            include_weather=True,
        )
    except Exception:
        try:
            raw = evaluate_survey(
                field_id=field_id,
                health_score=health_score,
                analysis_summary=summary,
                photo_url=photo_url,
            )
        except Exception as exc:
            result = _chalk_down(health_score, summary, exc)
            _log(field_id, photo_url, health_score, result, error=True)
            return result

    result = {
        "health_flag": raw.get(f"{_FS}.health_flag", "unknown"),
        "severity": raw.get(f"{_FS}.severity", 0.0),
        "action": raw.get(f"{_FS}.recommended_action", "continue"),
    }
    insight = raw.get(f"{_FS}.insight")
    justified = raw.get(f"{_FS}.justified_action")
    if insight and justified:
        result["insight"] = insight
        result["justified_action"] = justified
        result["weather_source"] = "chalk"
    else:
        try:
            insight, justified = _local_justification(health_score, summary)
            result["insight"] = insight
            result["justified_action"] = justified
            result["weather_source"] = "local live weather"
        except Exception as exc:
            result["insight"] = "Live weather was unavailable, so the image stands alone."
            result["justified_action"] = result["action"]
            result["weather_source"] = "unavailable"
            result["weather_error"] = str(exc)
    _log(field_id, photo_url, health_score, result, error=False)
    return result


def _chalk_down(health_score: float, summary: str, exc: Exception) -> dict:
    """Safe image result when Chalk itself cannot be queried."""
    result = {
        "health_flag": "unknown",
        "severity": 0.0,
        "action": "continue",
        "error": str(exc),
    }
    try:
        insight, justified = _local_justification(health_score, summary)
        result["insight"] = insight
        result["justified_action"] = justified
        result["weather_source"] = "local live weather"
    except Exception as weather_exc:
        result["insight"] = "Live weather was unavailable, so the image stands alone."
        result["justified_action"] = _image_action(health_score)
        result["weather_source"] = "unavailable"
        result["weather_error"] = str(weather_exc)
    return result


def _local_justification(health_score: float, summary: str) -> tuple[str, str]:
    live = load_weather_live()
    try:
        reading = live.fetch_weather()
    except Exception as exc:
        reading = live.WeatherReading(
            location_name=live.TREEHOUSE_NAME,
            latitude=live.TREEHOUSE_LAT,
            longitude=live.TREEHOUSE_LON,
            used_fallback=False,
            nws_temperature_c=None,
            nws_wind_mph=None,
            nws_forecast=None,
            nws_observed_at=None,
            gfs_precip_mm=None,
            gfs_cloud_cover_pct=None,
            gfs_shortwave_wm2=None,
            nasa_shortwave_wm2=None,
            nasa_observed_at=None,
            weather_error=str(exc),
        )
    judged = live.justify(health_score, summary, reading)
    return judged.insight, judged.justified_action


def _image_action(health_score: float) -> str:
    if health_score >= 0.7:
        return "continue"
    if health_score >= 0.4:
        return "reimage"
    return "alert-operator"


def _log(field_id: str, photo_url: str, health_score: float, result: dict, *, error: bool) -> None:
    try:
        append_decision({
            "type": "chalk_eval_error" if error else "chalk_eval",
            "field_id": field_id,
            "photo_url": photo_url,
            "health_score": health_score,
            **result,
        })
        if not error:
            set_last_survey(field_id, {**result, "photo_url": photo_url, "health_score": health_score})
    except Exception:
        pass
