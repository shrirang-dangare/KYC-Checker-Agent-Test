"""Fail-open ledger for The Spine: one metering event + one outcome per run.

    POST {SPINE_CENTRAL_INVOCATION_URL}/metering/events
    POST {SPINE_CENTRAL_INVOCATION_URL}/outcomes
    Header: X-Tenant-ID: <SPINE_TENANT_ID, default bgo_internal>

The two records are cross-linked: they share run_id, the metering event carries
outcome_id and the outcome carries metering_event_id.

Fail-open: if SPINE_CENTRAL_INVOCATION_URL is unset this is a silent no-op; if the
Spine is unreachable or returns an error, a one-line warning goes to stderr and the
run carries on. The ledger never raises and never sends application text or full IDs.
Standard library only.
"""

import json
import os
import re
import sys
import urllib.request
import uuid
from datetime import datetime, timezone
from typing import Any

AGENT_ID = "kyc-checker"
AOP_ID = "kyc_review"
DEFAULT_TENANT = "bgo_internal"
TIMEOUT_SECONDS = 3
REF_RE = re.compile(r"\bREF\s+([A-Z0-9][A-Z0-9-]{2,40})\b")


def _post(base_url: str, path: str, tenant: str, body: dict) -> bool:
    req = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", "X-Tenant-ID": tenant},
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            return 200 <= resp.status < 300
    except Exception as exc:  # fail open: never break the run
        print(f"[spine] WARN: POST {path} failed ({type(exc).__name__}: {exc}); continuing",
              file=sys.stderr, flush=True)
        return False


def record_run(kyc_text: str, result: dict | None, stats: dict[str, Any],
               error: BaseException | None = None) -> dict | None:
    """Send the run's metering event and outcome. Returns the ids, or None if disabled."""
    base_url = os.environ.get("SPINE_CENTRAL_INVOCATION_URL", "").strip()
    if not base_url:
        return None  # standalone mode: silent no-op
    try:
        tenant = os.environ.get("SPINE_TENANT_ID", DEFAULT_TENANT)
        run_id, event_id, outcome_id = (str(uuid.uuid4()) for _ in range(3))
        now = datetime.now(timezone.utc).isoformat()
        ref = REF_RE.search(kyc_text or "")
        report = (result or {}).get("report") or {}

        metering = {
            "event_id": event_id,
            "run_id": run_id,
            "outcome_id": outcome_id,
            "tenant_id": tenant,
            "agent_id": AGENT_ID,
            "aop_id": AOP_ID,
            "timestamp": now,
            "model": stats.get("model"),
            "model_calls": stats.get("model_calls", 0),
            "input_tokens": stats.get("input_tokens", 0),
            "output_tokens": stats.get("output_tokens", 0),
            "tool_calls": stats.get("tool_calls", {}),
            "duration_ms": stats.get("duration_ms"),
        }
        outcome = {
            "outcome_id": outcome_id,
            "run_id": run_id,
            "metering_event_id": event_id,
            "tenant_id": tenant,
            "agent_id": AGENT_ID,
            "aop_id": AOP_ID,
            "timestamp": now,
            "application_ref": ref.group(1) if ref else None,
            "outcome": "error" if error else (result or {}).get("outcome"),
            "recommendation": report.get("recommendation"),
            "decision_status": (result or {}).get("decision_status"),
            "route_to": (result or {}).get("route_to"),
            "requires_human_approval": True,  # Step 8: nothing is final without a person
            "failed_checks": [f["check"] for f in report.get("findings", []) if f.get("status") == "fail"],
            "missing_items_count": len(report.get("missing_items", [])),
            "customer_message_drafts_pending": len((result or {}).get("customer_message_drafts", [])),
            "guardrail_violations": (result or {}).get("guardrail_violations", []),
            "error": f"{type(error).__name__}" if error else None,
        }
        _post(base_url, "/metering/events", tenant, metering)
        _post(base_url, "/outcomes", tenant, outcome)
        return {"run_id": run_id, "event_id": event_id, "outcome_id": outcome_id}
    except Exception as exc:  # fail open, even on our own bugs
        print(f"[spine] WARN: ledger skipped ({type(exc).__name__}: {exc})", file=sys.stderr, flush=True)
        return None
