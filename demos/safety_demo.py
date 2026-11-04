"""Offline demo: trip each guardrail and the stop rule with a SCRIPTED fake model.

The model replies below are hand-written to break one rule each. The loop,
the validate_kyc_fields tool and the guardrails are the real code.

    python demos/safety_demo.py
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import main  # noqa: E402

INPUT = (ROOT / "samples" / "kyc-2026-10482.txt").read_text(encoding="utf-8")
FIELDS = json.loads((ROOT / "samples" / "kyc-2026-10482.tool_input.json").read_text(encoding="utf-8"))

PASSING = [
    {"check": "PAN format", "status": "pass", "detail": "XXXXXX234F valid"},
    {"check": "Name match", "status": "pass", "detail": "RAVI KUMAR on all sources"},
]
DOB_FAIL = {"check": "DOB match", "status": "fail", "detail": "PAN card 12-03-1991 vs form/Aadhaar 12-03-1990"}


def tool_turn(n=1):
    return NS(model="scripted", stop_reason="tool_use", content=[
        NS(type="text", text="Running deterministic checks."),
        *[NS(type="tool_use", id=f"toolu_{i}", name="validate_kyc_fields", input=FIELDS) for i in range(n)],
    ])


def final(report):
    return NS(model="scripted", stop_reason="end_turn",
              content=[NS(type="text", text=json.dumps(report))])


def report(rec, findings, missing=(), messages=()):
    return {"recommendation": rec, "findings": list(findings), "missing_items": list(missing),
            "customer_messages": list(messages)}


CASES = [
    ("OK: escalate on DOB mismatch (control case)",
     [tool_turn(), final(report("escalate", PASSING + [DOB_FAIL]))]),
    ("OK: resubmit; customer message held as a draft (control case)",
     [tool_turn(), final(report("resubmit", PASSING + [{"check": "Address proof date", "status": "fail", "detail": "bill too old"}],
                                ["recent address proof"],
                                [{"item": "recent address proof", "message": "Please upload a utility bill from the last 60 days."}]))]),
    ("NO_SELF_VERIFY: verify although the tool found a DOB mismatch",
     [tool_turn(), final(report("verify", PASSING))]),
    ("NO_SELF_VERIFY: verify without running the tool",
     [final(report("verify", PASSING))]),
    ("ESCALATE_TO_HUMAN: escalate with no reason given",
     [tool_turn(), final(report("escalate", PASSING))]),
    ("MASK_IDS: full PAN and Aadhaar in a finding",
     [tool_turn(), final(report("escalate", PASSING + [
         {"check": "DOB match", "status": "fail", "detail": "PAN ABCPK1234F, Aadhaar 2345 6789 4821 disagree on DOB"}]))]),
    ("MESSAGE_APPROVAL: customer message announces a decision",
     [tool_turn(), final(report("resubmit", PASSING + [{"check": "PAN card", "status": "fail", "detail": "DOB differs"}],
                                ["PAN card copy"],
                                [{"item": "PAN card copy", "message": "Your KYC is approved; please also send your PAN card."}]))]),
    ("STOP_RULE: model keeps calling the tool (6 calls, cap 5)",
     [tool_turn(), tool_turn(), tool_turn(), tool_turn(), tool_turn(), tool_turn()]),
]

SHOW = ("[tool call", "[guardrail]", "    - ", "[route]", "[stop rule]")


def run(title, script):
    calls = iter(script)
    main.anthropic.Anthropic = lambda: NS(messages=NS(create=lambda **kw: next(calls)))
    main.log = lambda text="": [print("  " + line.strip()) for line in main.mask_ids(text).splitlines()
                                if line.lstrip().startswith(tuple(s.strip() for s in SHOW))]
    print(f"\n### {title}")
    result = main.check_kyc(INPUT)
    print(f"  => outcome={result['outcome']} decision_status={result['decision_status']} route_to={result['route_to']}"
          f" report_released={result['report'] is not None}")
    for d in result["customer_message_drafts"]:
        print(f"  => draft for {d['item']!r}: approval_status={d['approval_status']} sent={d['sent']}")


if __name__ == "__main__":
    for title, script in CASES:
        run(title, script)
