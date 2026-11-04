"""Guardrails for kyc-checker, enforced in code on the model's proposed final result.

Rules (from the agent's boundaries):
  1. NO_SELF_VERIFY  The agent never marks a customer as verified by itself.
                     "verify" is only a recommendation for a human; it is blocked if the
                     deterministic checks were not run or any of them failed.
  2. ESCALATE_TO_HUMAN  Every "escalate" is routed to a human compliance officer, and
                     must say why (at least one failed finding).
  3. MASK_IDS        No full PAN or Aadhaar number may appear anywhere in the output.
  4. MESSAGE_APPROVAL  Customer messages are drafts held for approval; they may only ask
                     for resubmission and must not announce a KYC decision.

If any rule is broken, the model's result is blocked (never shown or passed on) and a
flag-for-human result is returned instead.
"""

import json
import re
from typing import Any

from schema import ReportValidationError, validate_report

PAN_RE = re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b")
AADHAAR_RE = re.compile(r"(?<!\d)\d{4}[ -]?\d{4}[ -]?\d{4}(?!\d)")
DECISION_RE = re.compile(
    r"\b(verified|approved|rejected|declined|verification (is )?(complete|successful))\b", re.I
)

COMPLIANCE_OFFICER = "human_compliance_officer"
KYC_REVIEWER = "human_kyc_reviewer"


def mask_ids(text: str) -> str:
    """Replace full PAN / Aadhaar numbers with masked forms (keep last 4)."""
    text = PAN_RE.sub(lambda m: "XXXXXX" + m.group()[-4:], text)
    return AADHAAR_RE.sub(lambda m: "XXXX XXXX " + re.sub(r"\D", "", m.group())[-4:], text)


def _parse(raw: str) -> Any:
    text = raw.strip()
    fence = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.S)
    return json.loads(fence.group(1) if fence else text)


def check_report(raw: str, tool_findings: list[dict]) -> tuple[dict | None, list[str]]:
    """Return (report, violations). report is None if it could not be parsed/validated."""
    violations: list[str] = []

    # MASK_IDS is checked on the raw text, before anything else touches it.
    if PAN_RE.search(raw):
        violations.append("MASK_IDS: output contains a full PAN number")
    if AADHAAR_RE.search(raw):
        violations.append("MASK_IDS: output contains a full Aadhaar number")

    try:
        report = validate_report(_parse(raw))
    except json.JSONDecodeError as exc:
        return None, violations + [f"FORMAT: final answer is not JSON ({exc.msg})"]
    except ReportValidationError as exc:
        return None, violations + [f"FORMAT: {e}" for e in exc.errors]

    rec = report["recommendation"]
    if rec == "verify":
        if not tool_findings:
            violations.append("NO_SELF_VERIFY: 'verify' proposed without running validate_kyc_fields")
        failed = [f["check"] for f in tool_findings if f.get("status") == "fail"]
        if failed:
            violations.append(f"NO_SELF_VERIFY: 'verify' proposed but deterministic checks failed: {', '.join(failed)}")

    if rec == "escalate" and not any(f["status"] == "fail" for f in report["findings"]):
        violations.append("ESCALATE_TO_HUMAN: 'escalate' has no failed finding telling the officer why")

    for m in report["customer_messages"]:
        if DECISION_RE.search(m["message"]):
            violations.append(
                f"MESSAGE_APPROVAL: message for {m['item']!r} announces a KYC decision; "
                "drafts may only request resubmission"
            )

    return report, violations


def build_result(raw: str, tool_findings: list[dict]) -> dict:
    """Apply the guardrails and wrap the outcome. The agent never issues a final decision."""
    report, violations = check_report(raw, tool_findings)
    if violations:
        return flag_for_human("blocked", violations)

    rec = report["recommendation"]
    return {
        "outcome": "report",
        "decision_status": "pending_human_review",  # never "verified": a person decides
        "route_to": COMPLIANCE_OFFICER if rec == "escalate" else KYC_REVIEWER,
        "report": report,
        "customer_message_drafts": [
            {**m, "approval_status": "pending_approval", "sent": False}
            for m in report["customer_messages"]
        ],
        "guardrail_violations": [],
    }


def flag_for_human(outcome: str, reasons: list[str]) -> dict:
    """Safe fallback: no report is released; a compliance officer must handle the case."""
    return {
        "outcome": outcome,  # "blocked" or "incomplete"
        "decision_status": "flagged_for_human",
        "route_to": COMPLIANCE_OFFICER,
        "report": None,
        "customer_message_drafts": [],
        "guardrail_violations": [mask_ids(r) for r in reasons],
        "message": "The agent's result was not released. A compliance officer must review this application manually.",
    }
