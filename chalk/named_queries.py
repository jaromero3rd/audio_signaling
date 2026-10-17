"""Serving-layer definitions for the FieldSurvey evaluation path (FR-7).

Deployed via `chalk apply`. Store-free pieces:

- NamedQuery `evaluate_field_survey`: alias for the online evaluation query
  (same contract as chalk_client.evaluate_survey); runs are tracked under
  the query name in logs/metrics. Verified live on branch `schedtest`.
- Chart `field-survey-eval-health`: monitors that query's success ratio
  (registers on next prod apply; branches don't surface charts).
- `survey_eval_heartbeat`: cron @online resolver. Deploys + executes on
  schedule, but batch outputs persist only via the online store -- none is
  configured, so heartbeat rows aren't durable yet.
"""
import datetime as dt

from chalk import NamedQuery
from chalk.features import DataFrame, features, online
from chalk.monitoring import Chart, Series

EVALUATE_SURVEY_QUERY = "evaluate_field_survey"

NamedQuery(
    name=EVALUATE_SURVEY_QUERY,
    input=[
        "field_survey.id",
        "field_survey.field_id",
        "field_survey.drone_id",
        "field_survey.photo_url",
        "field_survey.health_score",
        "field_survey.analysis_summary",
    ],
    output=[
        "field_survey.health_flag",
        "field_survey.severity",
        "field_survey.recommended_action",
    ],
)

Chart(name="field-survey-eval-health").with_series(
    Series.query_success_ratio_metric().where(query_name=EVALUATE_SURVEY_QUERY)
)


@features
class EvalHeartbeat:
    id: str  # primary key
    ts: dt.datetime


@online(cron="1d")
def survey_eval_heartbeat() -> DataFrame[EvalHeartbeat.id, EvalHeartbeat.ts]:
    return DataFrame(
        {
            EvalHeartbeat.id: ["daily-heartbeat"],
            EvalHeartbeat.ts: [dt.datetime.now(dt.timezone.utc)],
        }
    )
