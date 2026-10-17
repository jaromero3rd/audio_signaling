"""Simple eval (FR-7): run labeled survey cases through the live Chalk
feature pipeline and check the resolver-computed decision features.

Run:  .venv/bin/python -m ground_station.chalk_eval
"""
from dotenv import load_dotenv

from ground_station.chalk_client import FS, SURVEY_OUTPUTS, get_client

load_dotenv()

CASES = [
    # (health_score, expected flag, expected action)
    (0.92, "healthy", "continue"),
    (0.75, "healthy", "continue"),
    (0.55, "watch", "reimage"),
    (0.40, "watch", "reimage"),
    (0.10, "critical", "alert-operator"),
]


def main() -> None:
    client = get_client()
    passed = 0
    for score, want_flag, want_action in CASES:
        res = client.query(
            input={
                f"{FS}.id": f"eval-{score}",
                f"{FS}.field_id": "eval-field",
                f"{FS}.health_score": score,
            },
            output=SURVEY_OUTPUTS,
        )
        if res.errors:
            print(f"score={score:<5} RESOLVER ERRORS: {res.errors}")
            continue
        got_flag = res.get_feature_value(f"{FS}.health_flag")
        got_action = res.get_feature_value(f"{FS}.recommended_action")
        ok = got_flag == want_flag and got_action == want_action
        passed += ok
        print(f"score={score:<5} flag={got_flag:<9} action={got_action:<15} {'ok' if ok else 'FAIL'}")
    print(f"\n{passed}/{len(CASES)} cases passed")


if __name__ == "__main__":
    main()
