import unittest
from unittest.mock import patch

from ground_station.chalk_client import FS, SURVEY_OUTPUTS
from ground_station.chalk_eval import CASES, EvalCase, run_eval
from ground_station.chalk_quality import (
    EVALUATE_SURVEY_QUERY, EVALUATE_SURVEY_WEATHER_QUERY, evaluate_survey_named,
)


class FakeResponse:
    def __init__(self, values=None, errors=None):
        self.values = values or {}
        self.errors = errors or []

    def get_feature_value(self, feature):
        return self.values.get(feature)

    def to_dict(self):
        return self.values


class FakeClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def query(self, **kwargs):
        self.calls.append(kwargs)
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


def response_for(case):
    return FakeResponse(
        {
            f"{FS}.health_flag": case.health_flag,
            f"{FS}.severity": case.severity,
            f"{FS}.recommended_action": case.recommended_action,
        }
    )


class ChalkEvalTests(unittest.TestCase):
    def test_all_labeled_cases_pass(self):
        client = FakeClient(response_for(case) for case in CASES)

        results = run_eval(client)

        self.assertTrue(all(result.passed for result in results))
        self.assertEqual(len(results), len(CASES))
        self.assertEqual(client.calls[0]["output"], SURVEY_OUTPUTS)

    def test_reports_mismatches_resolver_errors_and_query_failures(self):
        case = EvalCase(0.4, "watch", 0.6, "reimage")
        bad = FakeResponse(
            {
                f"{FS}.health_flag": "healthy",
                f"{FS}.severity": 0.3,
                f"{FS}.recommended_action": "continue",
            }
        )
        client = FakeClient([bad, FakeResponse(errors=["boom"]), RuntimeError("offline")])

        results = run_eval(client, [case, case, case])

        self.assertFalse(any(result.passed for result in results))
        self.assertEqual(len(results[0].errors), 3)
        self.assertIn("resolver errors", results[1].errors[0])
        self.assertIn("query failed", results[2].errors[0])

    def test_named_query_and_branch_are_forwarded(self):
        case = CASES[0]
        client = FakeClient([response_for(case)])

        results = run_eval(client, [case], branch="dev", named_query=True)

        self.assertTrue(results[0].passed)
        self.assertEqual(client.calls[0]["query_name"], EVALUATE_SURVEY_QUERY)
        self.assertEqual(client.calls[0]["branch"], "dev")
        self.assertNotIn("output", client.calls[0])

    def test_boolean_severity_is_rejected_as_a_schema_regression(self):
        case = CASES[0]
        response = response_for(case)
        response.values[f"{FS}.severity"] = True

        result = run_eval(FakeClient([response]), [case])[0]

        self.assertFalse(result.passed)
        self.assertIn("severity expected 1.0, got True", result.errors)

    def test_weather_uses_a_separate_named_query_without_breaking_image_only_calls(self):
        case = CASES[0]
        client = FakeClient([response_for(case), response_for(case)])
        with patch("ground_station.chalk_quality.get_client", return_value=client):
            evaluate_survey_named("field-1", case.health_score, "canopy", branch="dev")
            evaluate_survey_named("field-1", case.health_score, "canopy", branch="dev",
                                  include_weather=True, latitude=42.0, longitude=-71.0)
        image_call, weather_call = client.calls
        self.assertEqual(image_call["query_name"], EVALUATE_SURVEY_QUERY)
        self.assertNotIn(f"{FS}.latitude", image_call["input"])
        self.assertEqual(weather_call["query_name"], EVALUATE_SURVEY_WEATHER_QUERY)
        self.assertEqual(weather_call["input"][f"{FS}.latitude"], 42.0)
        self.assertEqual(weather_call["input"][f"{FS}.longitude"], -71.0)
        self.assertEqual(weather_call["branch"], "dev")


if __name__ == "__main__":
    unittest.main()
