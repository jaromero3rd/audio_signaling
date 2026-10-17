"""Regression evaluation for the Chalk FieldSurvey feature pipeline (FR-7).

The default command evaluates the deployed feature resolvers directly::

    python -m ground_station.chalk_eval

Use ``--named-query`` to attribute requests to ``evaluate_field_survey`` and
``--branch`` when evaluating a non-production deployment. The command exits
non-zero if a resolver errors or any returned feature differs from the labeled
expectation, which makes it suitable for CI after a Chalk deployment.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import math
import sys
from typing import Any, Iterable, Sequence

from dotenv import load_dotenv

from ground_station.chalk_client import FS, SURVEY_OUTPUTS, get_client
from ground_station.chalk_quality import EVALUATE_SURVEY_QUERY

load_dotenv()


@dataclass(frozen=True)
class EvalCase:
    health_score: float
    health_flag: str
    severity: float
    recommended_action: str


@dataclass(frozen=True)
class EvalResult:
    case: EvalCase
    passed: bool
    actual: dict[str, Any]
    errors: tuple[str, ...] = ()


# Include both sides of each decision threshold, plus the valid range edges.
CASES: tuple[EvalCase, ...] = (
    EvalCase(0.0, "critical", 1.0, "alert-operator"),
    EvalCase(0.399, "critical", 0.601, "alert-operator"),
    EvalCase(0.4, "watch", 0.6, "reimage"),
    EvalCase(0.55, "watch", 0.45, "reimage"),
    EvalCase(0.699, "watch", 0.301, "reimage"),
    EvalCase(0.7, "healthy", 0.3, "continue"),
    EvalCase(0.92, "healthy", 0.08, "continue"),
    EvalCase(1.0, "healthy", 0.0, "continue"),
)


def _query_kwargs(case: EvalCase, index: int, branch: str | None, named_query: bool) -> dict[str, Any]:
    inputs = {
        f"{FS}.id": f"feature-eval-{index}-{case.health_score}",
        f"{FS}.field_id": "feature-eval-field",
        f"{FS}.drone_id": "feature-eval-drone",
        f"{FS}.photo_url": "eval://labeled-case",
        f"{FS}.health_score": case.health_score,
        f"{FS}.analysis_summary": "Labeled Chalk feature evaluation case",
    }
    kwargs: dict[str, Any] = {"input": inputs}
    if named_query:
        kwargs["query_name"] = EVALUATE_SURVEY_QUERY
    else:
        kwargs["output"] = SURVEY_OUTPUTS
    if branch:
        kwargs["branch"] = branch
    return kwargs


def _check_response(case: EvalCase, response: Any) -> EvalResult:
    resolver_errors = getattr(response, "errors", None)
    if resolver_errors:
        return EvalResult(case, False, {}, (f"resolver errors: {resolver_errors}",))

    actual = {
        "health_flag": response.get_feature_value(f"{FS}.health_flag"),
        "severity": response.get_feature_value(f"{FS}.severity"),
        "recommended_action": response.get_feature_value(f"{FS}.recommended_action"),
    }
    errors: list[str] = []
    if actual["health_flag"] != case.health_flag:
        errors.append(f"health_flag expected {case.health_flag!r}, got {actual['health_flag']!r}")
    severity = actual["severity"]
    if isinstance(severity, bool) or not isinstance(severity, (int, float)) or not math.isclose(
        severity, case.severity, rel_tol=0.0, abs_tol=1e-9
    ):
        errors.append(f"severity expected {case.severity!r}, got {severity!r}")
    if actual["recommended_action"] != case.recommended_action:
        errors.append(
            "recommended_action expected "
            f"{case.recommended_action!r}, got {actual['recommended_action']!r}"
        )
    return EvalResult(case, not errors, actual, tuple(errors))


def run_eval(
    client: Any,
    cases: Iterable[EvalCase] = CASES,
    *,
    branch: str | None = None,
    named_query: bool = False,
) -> list[EvalResult]:
    """Run labeled cases using a Chalk-compatible client."""
    results: list[EvalResult] = []
    for index, case in enumerate(cases):
        try:
            response = client.query(**_query_kwargs(case, index, branch, named_query))
            results.append(_check_response(case, response))
        except Exception as exc:  # preserve the rest of the eval after one failed query
            results.append(EvalResult(case, False, {}, (f"query failed: {exc}",)))
    return results


def _print_results(results: Sequence[EvalResult]) -> None:
    for result in results:
        case = result.case
        if result.passed:
            print(
                f"PASS score={case.health_score:<5g} "
                f"flag={result.actual['health_flag']:<8} "
                f"severity={result.actual['severity']:<5g} "
                f"action={result.actual['recommended_action']}"
            )
        else:
            print(f"FAIL score={case.health_score:<5g} {'; '.join(result.errors)}")
    passed = sum(result.passed for result in results)
    print(f"\n{passed}/{len(results)} cases passed")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--branch", help="Chalk branch deployment to evaluate")
    parser.add_argument(
        "--named-query",
        action="store_true",
        help=f"use the {EVALUATE_SURVEY_QUERY!r} named query",
    )
    args = parser.parse_args(argv)
    results = run_eval(get_client(), branch=args.branch, named_query=args.named_query)
    _print_results(results)
    return 0 if results and all(result.passed for result in results) else 1


if __name__ == "__main__":
    sys.exit(main())
