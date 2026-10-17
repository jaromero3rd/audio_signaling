"""Chalk feature definitions for AgriDrone (FR-7).

This environment has no online or offline store configured, so features are
computed by @online resolvers at query time rather than persisted. Each drone
photo analysis is passed in as input features; Chalk computes the decision
features (health_flag, severity, recommended_action) consumed by
evaluate_survey() in ground_station/chalk_client.py.

Deploy:  cd chalk && chalk apply --force --await
"""
import datetime as dt

from chalk.features import features, online


@features
class FieldSurvey:
    id: str  # survey uuid (primary key, passed in)
    field_id: str
    drone_id: str
    photo_url: str
    health_score: float
    analysis_summary: str
    ts: dt.datetime
    # resolver-computed outputs
    health_flag: str  # "healthy" | "watch" | "critical"
    severity: float  # 0.0 - 1.0
    recommended_action: str  # "continue" | "reimage" | "alert-operator"


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
