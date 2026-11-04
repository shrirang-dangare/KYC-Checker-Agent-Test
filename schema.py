"""Output contract for kyc-checker: the KYC report and its validator.

Standard library only. A report is a plain dict (for example, parsed from the
model's JSON output) that must match KYCReport exactly.
"""

from typing import Any, Literal, TypedDict, get_args

Recommendation = Literal["verify", "resubmit", "escalate"]
CheckStatus = Literal["pass", "fail"]

RECOMMENDATIONS: tuple[str, ...] = get_args(Recommendation)
CHECK_STATUSES: tuple[str, ...] = get_args(CheckStatus)


class Finding(TypedDict):
    check: str  # name of the check, e.g. "PAN format", "Name matches Aadhaar"
    status: CheckStatus  # "pass" or "fail"
    detail: str  # short reason for the status


class CustomerMessage(TypedDict):
    item: str  # the item to resubmit; must appear in missing_items
    message: str  # polite text telling the customer what to send again and why


class KYCReport(TypedDict):
    recommendation: Recommendation
    findings: list[Finding]  # one per check; at least one
    missing_items: list[str]  # empty if nothing is missing
    customer_messages: list[CustomerMessage]  # one per missing item


class ReportValidationError(ValueError):
    """Raised when a report breaks the contract. `errors` lists every problem."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("Invalid KYC report:\n- " + "\n- ".join(errors))


def _check_keys(obj: Any, expected: set[str], where: str, errors: list[str]) -> bool:
    if not isinstance(obj, dict):
        errors.append(f"{where}: expected an object, got {type(obj).__name__}")
        return False
    for key in sorted(expected - obj.keys()):
        errors.append(f"{where}: missing field '{key}'")
    for key in sorted(obj.keys() - expected):
        errors.append(f"{where}: unknown field '{key}'")
    return True


def _check_text(value: Any, where: str, errors: list[str]) -> bool:
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{where}: must be a non-empty string")
        return False
    return True


def _check_list(value: Any, where: str, errors: list[str]) -> list:
    if not isinstance(value, list):
        errors.append(f"{where}: must be a list")
        return []
    return value


def validate_report(report: Any) -> KYCReport:
    """Return the report unchanged if it is valid; raise ReportValidationError if not.

    Rejects: missing or unknown fields, wrong types, empty strings, values outside
    the allowed sets, and reports whose parts contradict each other.
    """
    errors: list[str] = []
    if not _check_keys(report, set(KYCReport.__annotations__), "report", errors):
        raise ReportValidationError(errors)

    recommendation = report.get("recommendation")
    if recommendation not in RECOMMENDATIONS:
        errors.append(f"recommendation: {recommendation!r} is not one of {list(RECOMMENDATIONS)}")

    findings = _check_list(report.get("findings"), "findings", errors)
    if "findings" in report and isinstance(report["findings"], list) and not findings:
        errors.append("findings: must contain at least one finding")
    failed = 0
    for i, f in enumerate(findings):
        where = f"findings[{i}]"
        if not _check_keys(f, set(Finding.__annotations__), where, errors):
            continue
        _check_text(f.get("check"), f"{where}.check", errors)
        _check_text(f.get("detail"), f"{where}.detail", errors)
        if f.get("status") not in CHECK_STATUSES:
            errors.append(f"{where}.status: {f.get('status')!r} is not one of {list(CHECK_STATUSES)}")
        elif f["status"] == "fail":
            failed += 1

    missing_items = _check_list(report.get("missing_items"), "missing_items", errors)
    missing_set: set[str] = set()
    for i, item in enumerate(missing_items):
        if _check_text(item, f"missing_items[{i}]", errors):
            if item in missing_set:
                errors.append(f"missing_items[{i}]: duplicate item {item!r}")
            missing_set.add(item)

    messages = _check_list(report.get("customer_messages"), "customer_messages", errors)
    messaged: set[str] = set()
    for i, m in enumerate(messages):
        where = f"customer_messages[{i}]"
        if not _check_keys(m, set(CustomerMessage.__annotations__), where, errors):
            continue
        _check_text(m.get("message"), f"{where}.message", errors)
        if _check_text(m.get("item"), f"{where}.item", errors):
            if m["item"] not in missing_set:
                errors.append(f"{where}.item: {m['item']!r} is not in missing_items")
            messaged.add(m["item"])

    # Cross-field rules: the recommendation must agree with the rest of the report.
    if recommendation == "verify":
        if failed:
            errors.append("recommendation: 'verify' is not allowed when a finding has status 'fail'")
        if missing_items:
            errors.append("recommendation: 'verify' is not allowed when missing_items is not empty")
        if messages:
            errors.append("recommendation: 'verify' is not allowed with customer_messages")
    elif recommendation == "resubmit":
        if not missing_items:
            errors.append("recommendation: 'resubmit' needs at least one entry in missing_items")
        for item in sorted(missing_set - messaged):
            errors.append(f"customer_messages: no message for missing item {item!r}")

    if errors:
        raise ReportValidationError(errors)
    return report
