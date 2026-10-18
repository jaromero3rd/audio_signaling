"""Chalk helpers for the embedded Droid session (FR-7).

evaluate_survey() runs one photo analysis through Chalk's online feature
pipeline and returns the computed decision features - the Chalk tool the
Droid session calls after each vision result.

NOTE: this environment has no online/offline store configured, so state is
not persisted in Chalk - features are computed by resolvers per query. The
Droid session keeps mission state locally.

Feature namespace (deployed from chalk/ via `chalk apply`):
  field_survey.*  inputs + resolver-computed outputs
"""
from __future__ import annotations

import uuid
from typing import Any

from dotenv import load_dotenv

load_dotenv()

FS = "field_survey"  # FieldSurvey feature namespace

SURVEY_OUTPUTS = [
    f"{FS}.health_flag",
    f"{FS}.severity",
    f"{FS}.recommended_action",
]

# Requested only with include_weather=True. The deployed schedtest query does
# not know these features until `cd chalk && chalk apply`.
WEATHER_OUTPUTS = [
    f"{FS}.nws_temperature_c",
    f"{FS}.nws_wind_mph",
    f"{FS}.nws_forecast",
    f"{FS}.nws_observed_at",
    f"{FS}.gfs_precip_mm",
    f"{FS}.gfs_cloud_cover_pct",
    f"{FS}.gfs_shortwave_wm2",
    f"{FS}.nasa_shortwave_wm2",
    f"{FS}.nasa_observed_at",
    f"{FS}.weather_error",
    f"{FS}.insight",
    f"{FS}.justified_action",
    f"{FS}.delivery_error",
]

_client_instance: Any | None = None


def load_weather_live():
    """Import chalk/weather_live.py.

    The installed chalk package owns the name `chalk`, so this appends the
    project chalk directory instead of importing through that package.
    """
    import sys
    from pathlib import Path

    chalk_dir = str(Path(__file__).resolve().parents[1] / "chalk")
    if chalk_dir not in sys.path:
        sys.path.append(chalk_dir)
    import weather_live

    return weather_live


def get_client() -> Any:
    """Shared client. Reads CHALK_CLIENT_ID/SECRET, CHALK_ENVIRONMENT and
    optional CHALK_API_SERVER from the environment (raises a descriptive
    ChalkAuthException if credentials are missing)."""
    global _client_instance
    if _client_instance is None:
        # Keep schema/eval helpers importable for local unit tests even when
        # the optional cloud SDK has not been installed.
        from chalk.client import ChalkClient

        _client_instance = ChalkClient()
    return _client_instance


def evaluate_survey(
    field_id: str,
    health_score: float,
    analysis_summary: str,
    photo_url: str = "",
    drone_id: str = "",
    latitude: float | None = None,
    longitude: float | None = None,
    include_weather: bool = False,
) -> dict:
    """Score one drone photo analysis through Chalk's feature engine.

    Returns {survey_id, field_survey.health_flag, field_survey.severity,
    field_survey.recommended_action} for the Droid session to act on.
    With include_weather=True, also requests the live NOAA/NASA justification
    and sends the David Rubenstein Treehouse coordinates unless latitude and
    longitude are set. Those inputs stay off the query otherwise, so a deploy
    from before this feature still accepts the call.
    Raises RuntimeError if resolvers report errors - a failed evaluation
    must be loud, not silently return None values.
    """
    survey_id = str(uuid.uuid4())
    query_input = {
        f"{FS}.id": survey_id,
        f"{FS}.field_id": field_id,
        f"{FS}.drone_id": drone_id,
        f"{FS}.photo_url": photo_url,
        f"{FS}.health_score": health_score,
        f"{FS}.analysis_summary": analysis_summary,
    }
    outputs = list(SURVEY_OUTPUTS)
    if include_weather:
        live = load_weather_live()
        query_input[f"{FS}.latitude"] = live.TREEHOUSE_LAT if latitude is None else latitude
        query_input[f"{FS}.longitude"] = live.TREEHOUSE_LON if longitude is None else longitude
        outputs.extend(WEATHER_OUTPUTS)
    res = get_client().query(
        input=query_input,
        output=outputs,
    )
    if res.errors:
        raise RuntimeError(f"chalk resolver errors: {res.errors}")
    return {"survey_id": survey_id, **res.to_dict()}


def query_chalk_features(field_id: str) -> dict:
    """Placeholder for the PRD tool of the same name. No store is configured
    in this environment, so per-field history cannot be served from Chalk -
    the ground station keeps it locally and feeds fresh analyses to
    evaluate_survey() instead."""
    return {
        "field_id": field_id,
        "note": "Chalk env has no store; field history is kept by the "
        "ground station. Feed the latest analysis into evaluate_survey().",
    }
