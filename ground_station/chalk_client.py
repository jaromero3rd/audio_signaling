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

from chalk.client import ChalkClient
from dotenv import load_dotenv

load_dotenv()

FS = "field_survey"  # FieldSurvey feature namespace

SURVEY_OUTPUTS = [
    f"{FS}.health_flag",
    f"{FS}.severity",
    f"{FS}.recommended_action",
]

_client_instance: ChalkClient | None = None


def get_client() -> ChalkClient:
    """Shared client. Reads CHALK_CLIENT_ID/SECRET, CHALK_ENVIRONMENT and
    optional CHALK_API_SERVER from the environment (raises a descriptive
    ChalkAuthException if credentials are missing)."""
    global _client_instance
    if _client_instance is None:
        _client_instance = ChalkClient()
    return _client_instance


def evaluate_survey(
    field_id: str,
    health_score: float,
    analysis_summary: str,
    photo_url: str = "",
    drone_id: str = "",
) -> dict:
    """Score one drone photo analysis through Chalk's feature engine.

    Returns {survey_id, field_survey.health_flag, field_survey.severity,
    field_survey.recommended_action} for the Droid session to act on.
    Raises RuntimeError if resolvers report errors - a failed evaluation
    must be loud, not silently return None values.
    """
    survey_id = str(uuid.uuid4())
    res = get_client().query(
        input={
            f"{FS}.id": survey_id,
            f"{FS}.field_id": field_id,
            f"{FS}.drone_id": drone_id,
            f"{FS}.photo_url": photo_url,
            f"{FS}.health_score": health_score,
            f"{FS}.analysis_summary": analysis_summary,
        },
        output=SURVEY_OUTPUTS,
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
