"""Chalk feature definitions for AgriDrone (FR-7).

This environment has no online or offline store configured, so features are
computed by @online resolvers at query time rather than persisted. Each drone
photo analysis is passed in as input features; Chalk computes the decision
features (health_flag, severity, recommended_action) consumed by
evaluate_survey() in ground_station/chalk_client.py.

The image resolvers are a pure function of health_score. One resolver,
justify_image_with_live_weather, fetches NOAA and NASA readings at query time
and writes insight and justified_action. It does not change recommended_action.

Deploy:  cd chalk && chalk apply --force --await
"""
import datetime as dt

from chalk.features import Features, features, online


@features
class FieldSurvey:
    id: str  # survey uuid (primary key, passed in)
    field_id: str
    drone_id: str
    photo_url: str
    health_score: float
    analysis_summary: str
    latitude: float
    longitude: float
    ts: dt.datetime
    # resolver-computed outputs
    health_flag: str  # "healthy" | "watch" | "critical"
    severity: float  # 0.0 - 1.0
    recommended_action: str  # "continue" | "reimage" | "alert-operator"
    # live NOAA / NASA justification of the image. Null when that source failed.
    nws_temperature_c: float | None
    nws_wind_mph: float | None
    nws_forecast: str | None
    nws_observed_at: str | None
    gfs_precip_mm: float | None
    gfs_cloud_cover_pct: float | None
    gfs_shortwave_wm2: float | None
    nasa_shortwave_wm2: float | None
    nasa_observed_at: str | None
    weather_error: str | None
    insight: str
    justified_action: str  # image action after the live-weather check
    # Empty when this resolver already delivered the phone summary.
    delivery_error: str | None


@online
def compute_health_flag(
    score: FieldSurvey.health_score,
) -> FieldSurvey.health_flag:
    if score >= 0.7:
        return "healthy"
    if score >= 0.4:
        return "watch"
    return "critical"


@online
def compute_severity(
    score: FieldSurvey.health_score,
) -> FieldSurvey.severity:
    return round(1.0 - max(0.0, min(1.0, score)), 3)


@online
def compute_recommended_action(
    flag: FieldSurvey.health_flag,
) -> FieldSurvey.recommended_action:
    return {
        "healthy": "continue",
        "watch": "reimage",
        "critical": "alert-operator",
    }.get(flag, "alert-operator")


@online(timeout="30s")
def justify_image_with_live_weather(
    score: FieldSurvey.health_score,
    summary: FieldSurvey.analysis_summary,
    lat: FieldSurvey.latitude,
    lon: FieldSurvey.longitude,
    photo: FieldSurvey.photo_url,
) -> Features[
    FieldSurvey.nws_temperature_c,
    FieldSurvey.nws_wind_mph,
    FieldSurvey.nws_forecast,
    FieldSurvey.nws_observed_at,
    FieldSurvey.gfs_precip_mm,
    FieldSurvey.gfs_cloud_cover_pct,
    FieldSurvey.gfs_shortwave_wm2,
    FieldSurvey.nasa_shortwave_wm2,
    FieldSurvey.nasa_observed_at,
    FieldSurvey.weather_error,
    FieldSurvey.insight,
    FieldSurvey.justified_action,
    FieldSurvey.delivery_error,
]:
    """Fetch live weather once and explain the image score. Never raises."""
    import weather_live

    try:
        if lat is None:
            lat = weather_live.TREEHOUSE_LAT
        if lon is None:
            lon = weather_live.TREEHOUSE_LON
        reading = weather_live.fetch_weather(lat, lon)
        judged = weather_live.justify(score, summary or "", reading)
    except Exception as exc:
        empty = weather_live.WeatherReading(
            location_name=weather_live.TREEHOUSE_NAME,
            latitude=lat,
            longitude=lon,
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
        judged = weather_live.justify(score, summary or "", empty)
        reading = empty
    delivery_error = _deliver_insight(judged.insight, photo)
    return FieldSurvey(
        nws_temperature_c=reading.nws_temperature_c,
        nws_wind_mph=reading.nws_wind_mph,
        nws_forecast=reading.nws_forecast,
        nws_observed_at=reading.nws_observed_at,
        gfs_precip_mm=reading.gfs_precip_mm,
        gfs_cloud_cover_pct=reading.gfs_cloud_cover_pct,
        gfs_shortwave_wm2=reading.gfs_shortwave_wm2,
        nasa_shortwave_wm2=reading.nasa_shortwave_wm2,
        nasa_observed_at=reading.nasa_observed_at,
        weather_error=reading.weather_error,
        insight=judged.insight,
        justified_action=judged.justified_action,
        delivery_error=delivery_error,
    )


def _deliver_insight(insight: str, photo) -> str:
    """Best-effort phone send. A string here means the laptop should send."""
    try:
        import os

        if not os.environ.get("ELEVENLABS_API_KEY") or not os.environ.get("PHOTON_API_KEY"):
            return "phone delivery skipped: ElevenLabs or Photon credentials are not set"
        import deliver_summary

        photo_path = photo if isinstance(photo, str) and photo else None
        return deliver_summary.deliver(insight, photo_path) or ""
    except Exception as exc:
        return str(exc)[:300]
