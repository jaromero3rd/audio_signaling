"""Data-quality + named-query helpers for the Chalk FieldSurvey path (FR-7).

Verified (chalkpy 2.163.1, no online/offline store): server-side
DataQualityChecks attach only to offline queries, which fail in this env
("The plan requires offline storage"); ChalkClient.query takes no check
params, so the validation below is client-side and mirrors those gating
checks. The `evaluate_field_survey` NamedQuery + monitoring Chart live in
chalk/named_queries.py (verified end-to-end on branch `schedtest`).
Smoke check:  .venv/bin/python -m ground_station.chalk_quality
"""
import uuid

from ground_station.chalk_client import FS, get_client

EVALUATE_SURVEY_QUERY = "evaluate_field_survey"  # defined in chalk/named_queries.py
EVALUATE_SURVEY_WEATHER_QUERY = "evaluate_field_survey_weather"

VALID_FLAGS = {"healthy", "watch", "critical"}
VALID_ACTIONS = {"continue", "reimage", "alert-operator"}

# flag bands implied by features.py (severity = 1 - score)
_FLAG_SEVERITY_BANDS = {"healthy": (0.0, 0.3), "watch": (0.3, 0.6), "critical": (0.6, 1.0)}


def validate_survey_result(result: dict) -> list[str]:
    """Client-side output checks for one evaluation result.

    Returns violation strings; an empty list means all checks passed.
    """
    violations = []
    flag = result.get(f"{FS}.health_flag")
    severity = result.get(f"{FS}.severity")
    action = result.get(f"{FS}.recommended_action")

    if flag not in VALID_FLAGS:
        violations.append(f"health_flag {flag!r} not in {sorted(VALID_FLAGS)}")
    if action not in VALID_ACTIONS:
        violations.append(f"recommended_action {action!r} not in {sorted(VALID_ACTIONS)}")
    if not isinstance(severity, (int, float)) or not 0.0 <= severity <= 1.0:
        violations.append(f"severity {severity!r} outside [0, 1]")
    elif flag in _FLAG_SEVERITY_BANDS:
        lo, hi = _FLAG_SEVERITY_BANDS[flag]
        # Bands closed at both edges: compute_severity rounds to 3 decimals,
        # so scores at threshold boundaries can round a valid severity onto
        # the band edge.
        if not lo <= severity <= hi:
            violations.append(f"severity {severity} inconsistent with flag {flag!r} (band {lo}-{hi})")
    return violations


def evaluate_survey_named(
    field_id: str,
    health_score: float,
    analysis_summary: str,
    photo_url: str = "",
    drone_id: str = "",
    branch: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
    include_weather: bool = False,
) -> dict:
    """Run the registered `evaluate_field_survey` named query.

    Same contract as chalk_client.evaluate_survey, invoked by query_name so
    runs are attributed in query monitoring. Applies the input check
    (health_score in [0, 1]) and validate_survey_result() output checks;
    raises RuntimeError on resolver errors or any violation. `branch`
    targets a branch deployment: until `chalk apply` runs against prod, the
    named query exists only on the deployed branch (e.g. "schedtest").

    include_weather defaults off so existing callers, including the Droid
    pipeline, keep working against the deploy that has no latitude input.
    When it is on, the separate evaluate_field_survey_weather query is used
    and the Treehouse point is sent unless latitude and longitude are set.
    Output checks still cover only the image labels.
    """
    if not 0.0 <= health_score <= 1.0:
        raise ValueError(f"input check failed: health_score {health_score} outside [0, 1]")
    survey_id = str(uuid.uuid4())
    query_input = {
        f"{FS}.id": survey_id,
        f"{FS}.field_id": field_id,
        f"{FS}.drone_id": drone_id,
        f"{FS}.photo_url": photo_url,
        f"{FS}.health_score": health_score,
        f"{FS}.analysis_summary": analysis_summary,
    }
    if include_weather:
        from ground_station.chalk_client import load_weather_live

        live = load_weather_live()
        query_input[f"{FS}.latitude"] = live.TREEHOUSE_LAT if latitude is None else latitude
        query_input[f"{FS}.longitude"] = live.TREEHOUSE_LON if longitude is None else longitude
    res = get_client().query(
        input=query_input,
        query_name=EVALUATE_SURVEY_WEATHER_QUERY if include_weather else EVALUATE_SURVEY_QUERY,
        branch=branch,
    )
    if res.errors:
        raise RuntimeError(f"chalk resolver errors: {res.errors}")
    result = {"survey_id": survey_id, **res.to_dict()}
    violations = validate_survey_result(result)
    if violations:
        raise RuntimeError(f"survey output check violations: {violations}")
    return result


if __name__ == "__main__":  # smoke check against the schedtest branch deploy
    print(evaluate_survey_named("smoke-field", 0.9, "healthy canopy", branch="schedtest"))
