"""Minimal eval for kyc-checker. Exits non-zero on any failure (CI-friendly).

    python evals/run_eval.py            # summary only
    python evals/run_eval.py -v         # also show each agent turn

Each case in evals/cases.json has an officer's recommendation plus optional rules.
Every case must also pass these general rules:
  - the agent produced a report (not blocked by guardrails, not stopped by the cap)
  - the report passes schema.validate_report (the Step 2 validator)
  - no full PAN / Aadhaar number anywhere in the output
Target (from the spec): a compliance officer agrees with the recommendation on
9 of 10 applications -> min_agreement 0.9.
"""

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import main  # noqa: E402
from guardrails import AADHAAR_RE, PAN_RE  # noqa: E402
from schema import ReportValidationError, validate_report  # noqa: E402

EVAL_DIR = Path(__file__).resolve().parent


def _any_in(keywords: list[str], texts: list[str]) -> bool:
    return any(k.lower() in t.lower() for k in keywords for t in texts)


def check_case(case: dict, result: dict) -> list[str]:
    """Return a list of failure reasons (empty = pass)."""
    fails: list[str] = []
    dumped = json.dumps(result, ensure_ascii=False)
    if PAN_RE.search(dumped) or AADHAAR_RE.search(dumped):
        fails.append("output contains a full ID number")

    if result.get("outcome") != "report":
        return fails + [f"no report: outcome={result.get('outcome')} {result.get('guardrail_violations')}"]

    report = result["report"]
    try:
        validate_report(report)  # Step 2 validator
    except ReportValidationError as exc:
        return fails + [f"schema: {e}" for e in exc.errors]

    rec = report["recommendation"]
    if rec != case["officer_recommendation"]:
        fails.append(f"recommendation {rec!r}, officer says {case['officer_recommendation']!r}")
    if rec in case.get("forbid_recommendations", []):
        fails.append(f"forbidden recommendation {rec!r}")

    failed = [f"{f['check']} {f['detail']}" for f in report["findings"] if f["status"] == "fail"]
    if "must_fail_any_of" in case and not _any_in(case["must_fail_any_of"], failed):
        fails.append(f"no failed finding about {case['must_fail_any_of']}")
    if "max_failed_findings" in case and len(failed) > case["max_failed_findings"]:
        fails.append(f"{len(failed)} failed findings, expected at most {case['max_failed_findings']}")
    if "max_missing_items" in case and len(report["missing_items"]) > case["max_missing_items"]:
        fails.append(f"{len(report['missing_items'])} missing items, expected at most {case['max_missing_items']}")
    if "missing_items_any_of" in case and not _any_in(case["missing_items_any_of"], report["missing_items"]):
        fails.append(f"missing_items does not mention {case['missing_items_any_of']}")

    expected_route = "human_compliance_officer" if rec == "escalate" else "human_kyc_reviewer"
    if result.get("route_to") != expected_route or result.get("decision_status") != "pending_human_review":
        fails.append(f"routing: {result.get('decision_status')} -> {result.get('route_to')}")
    return fails


def main_eval() -> int:
    verbose = "-v" in sys.argv[1:]
    spec = json.loads((EVAL_DIR / "cases.json").read_text(encoding="utf-8"))
    os.environ.setdefault("KYC_AS_OF", spec["as_of"])  # bill-age checks stay stable over time
    main.load_env()
    if not verbose:
        main.log = lambda text="": None

    passed = agreed = 0
    cases = spec["cases"]
    for case in cases:
        text = (EVAL_DIR / "cases" / f"{case['id']}.txt").read_text(encoding="utf-8")
        try:
            result = main.check_kyc(text)
            fails = check_case(case, result)
            rec = (result.get("report") or {}).get("recommendation")
        except Exception as exc:  # API errors etc. count as failures, not crashes
            fails, rec = [f"error: {type(exc).__name__}: {str(exc)[:160]}"], None
        agreed += rec == case["officer_recommendation"]
        passed += not fails
        tag = " [adversarial]" if case.get("adversarial") else ""
        print(f"{'PASS' if not fails else 'FAIL'}  {case['id']}{tag}  "
              f"(agent: {rec or '-'}, officer: {case['officer_recommendation']})")
        for f in fails:
            print(f"        - {f}")

    agreement = agreed / len(cases)
    print(f"\nTotal: {passed}/{len(cases)} passed | officer agreement {agreed}/{len(cases)} "
          f"= {agreement:.0%} (target >= {spec['min_agreement']:.0%})")
    ok = passed == len(cases) and agreement >= spec["min_agreement"]
    print("RESULT: PASS" if ok else "RESULT: FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main_eval())
