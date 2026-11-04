"""validate_kyc_fields: deterministic KYC checks, no network, no model.

The model extracts fields from the form and OCR text and passes them in; this
code runs fixed rules and returns findings in the same shape as schema.Finding
({"check", "status": "pass"|"fail", "detail"}). Full PAN numbers are masked.
"""

import json
import os
import re
from datetime import date, datetime
from typing import Any

PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
PINCODE_RE = re.compile(r"\b\d{6}\b")
DATE_FORMATS = ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d.%m.%Y")
MAX_ADDRESS_PROOF_AGE_DAYS = 60  # utility bills older than ~2 months are not accepted
STATE_NAMES = {
    "ANDHRA", "PRADESH", "ARUNACHAL", "ASSAM", "BIHAR", "CHHATTISGARH", "GOA", "GUJARAT",
    "HARYANA", "HIMACHAL", "JHARKHAND", "KARNATAKA", "KERALA", "MADHYA", "MAHARASHTRA",
    "MANIPUR", "MEGHALAYA", "MIZORAM", "NAGALAND", "ODISHA", "PUNJAB", "RAJASTHAN", "SIKKIM",
    "TAMIL", "NADU", "TELANGANA", "TRIPURA", "UTTAR", "UTTARAKHAND", "WEST", "BENGAL",
    "DELHI", "INDIA",
}

_doc = {"type": "object", "additionalProperties": False}
SCHEMA: dict[str, Any] = {
    "name": "validate_kyc_fields",
    "description": (
        "Run deterministic KYC checks (PAN format, name match, date-of-birth match, "
        "document expiry and address match) on fields you extracted from the form and "
        "OCR text; call it once before deciding, and pass values exactly as written."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "form": {**_doc, "description": "Values the customer typed on the KYC form.", "properties": {
                "name": {"type": "string"},
                "dob": {"type": "string", "description": "Date of birth as written, e.g. 12-03-1990."},
                "pan": {"type": "string", "description": "Full PAN as written on the form."},
                "address": {"type": "string"},
            }},
            "pan_card": {**_doc, "description": "Values read from the PAN card OCR.", "properties": {
                "name": {"type": "string"},
                "dob": {"type": "string"},
                "pan": {"type": "string"},
            }},
            "aadhaar_card": {**_doc, "description": "Values read from the Aadhaar card OCR.", "properties": {
                "name": {"type": "string"},
                "dob": {"type": "string"},
            }},
            "address_proof": {**_doc, "description": "Values read from the address proof OCR.", "properties": {
                "document_type": {"type": "string", "description": "e.g. electricity bill, passport."},
                "name": {"type": "string"},
                "address": {"type": "string"},
                "issue_date": {"type": "string", "description": "Bill or issue date, if shown."},
                "expiry_date": {"type": "string", "description": "Expiry date, if the document has one."},
            }},
            "as_of": {"type": "string", "description": "Date to check expiry against (YYYY-MM-DD). Defaults to today."},
        },
        "required": ["form", "pan_card", "aadhaar_card", "address_proof"],
        "additionalProperties": False,
    },
}


# ---------- helpers ----------

def mask_pan(pan: str) -> str:
    pan = (pan or "").strip().upper()
    return "X" * max(len(pan) - 4, 0) + pan[-4:] if pan else "(none)"


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _norm_name(value: str | None) -> str:
    return " ".join(re.sub(r"[^A-Z ]", " ", (value or "").upper()).split())


def _addr_tokens(value: str) -> set[str]:
    return set(re.sub(r"[^A-Z0-9 ]", " ", value.upper()).split()) - STATE_NAMES


def _finding(check: str, ok: bool, detail: str) -> dict[str, str]:
    return {"check": check, "status": "pass" if ok else "fail", "detail": detail}


# ---------- checks ----------

def check_pan_format(form: dict, pan_card: dict) -> dict:
    form_pan = (form.get("pan") or "").strip().upper()
    card_pan = (pan_card.get("pan") or "").strip().upper()
    if not form_pan or not card_pan:
        return _finding("PAN format", False, "PAN missing from " + ("form" if not form_pan else "PAN card"))
    if not PAN_RE.match(form_pan):
        return _finding("PAN format", False, f"{mask_pan(form_pan)} does not match AAAAA9999A")
    if form_pan[3] != "P":
        return _finding("PAN format", False, f"{mask_pan(form_pan)}: 4th character is not 'P' (not an individual PAN)")
    if form_pan != card_pan:
        return _finding("PAN format", False, f"form PAN {mask_pan(form_pan)} differs from PAN card {mask_pan(card_pan)}")
    return _finding("PAN format", True, f"{mask_pan(form_pan)} is valid and matches the PAN card")


def check_name_match(sources: dict[str, dict]) -> dict:
    names = {src: _norm_name(d.get("name")) for src, d in sources.items()}
    missing = [src for src, n in names.items() if not n]
    if missing:
        return _finding("Name match", False, "name missing on: " + ", ".join(missing))
    if len(set(names.values())) == 1:
        return _finding("Name match", True, f"'{next(iter(names.values()))}' on all {len(names)} sources")
    return _finding("Name match", False, "; ".join(f"{s}: '{n}'" for s, n in names.items()))


def check_dob_match(sources: dict[str, dict]) -> dict:
    raw = {src: d.get("dob") for src, d in sources.items()}
    parsed = {src: _parse_date(v) for src, v in raw.items()}
    bad = [f"{s} ('{raw[s]}')" if raw[s] else s for s, d in parsed.items() if d is None]
    if bad:
        return _finding("DOB match", False, "missing or unreadable date of birth on: " + ", ".join(bad))
    if len(set(parsed.values())) == 1:
        return _finding("DOB match", True, f"{next(iter(parsed.values())):%d-%m-%Y} on all {len(parsed)} sources")
    return _finding("DOB match", False, "; ".join(f"{s}: {d:%d-%m-%Y}" for s, d in parsed.items()))


def check_document_expiry(address_proof: dict, as_of: date) -> dict:
    doc_type = address_proof.get("document_type") or "address proof"
    expiry = _parse_date(address_proof.get("expiry_date"))
    issued = _parse_date(address_proof.get("issue_date"))
    if expiry:
        if expiry < as_of:
            return _finding("Document expiry", False, f"{doc_type} expired on {expiry:%d-%m-%Y}")
        return _finding("Document expiry", True, f"{doc_type} valid until {expiry:%d-%m-%Y}")
    if issued:
        age = (as_of - issued).days
        if age < 0:
            return _finding("Document expiry", False, f"{doc_type} is dated in the future ({issued:%d-%m-%Y})")
        if age > MAX_ADDRESS_PROOF_AGE_DAYS:
            return _finding("Document expiry", False,
                            f"{doc_type} dated {issued:%d-%m-%Y} is {age} days old (limit {MAX_ADDRESS_PROOF_AGE_DAYS})")
        return _finding("Document expiry", True, f"{doc_type} dated {issued:%d-%m-%Y} is {age} days old")
    return _finding("Document expiry", False, f"no readable issue or expiry date on {doc_type}")


def check_address_match(form: dict, address_proof: dict) -> dict:
    form_addr, proof_addr = form.get("address") or "", address_proof.get("address") or ""
    if not form_addr or not proof_addr:
        return _finding("Address match", False, "address missing from " + ("form" if not form_addr else "address proof"))
    form_pin, proof_pin = PINCODE_RE.findall(form_addr), PINCODE_RE.findall(proof_addr)
    if not form_pin or not proof_pin or form_pin[-1] != proof_pin[-1]:
        return _finding("Address match", False,
                        f"PIN code differs: form {form_pin[-1] if form_pin else 'none'}, "
                        f"proof {proof_pin[-1] if proof_pin else 'none'}")
    form_t, proof_t = _addr_tokens(form_addr), _addr_tokens(proof_addr)
    if form_t <= proof_t or proof_t <= form_t:
        return _finding("Address match", True, f"same address, PIN {form_pin[-1]}")
    diff = sorted(form_t ^ proof_t)
    return _finding("Address match", False, "address words differ: " + ", ".join(diff))


# ---------- entry point ----------

def validate_kyc_fields(form: dict, pan_card: dict, aadhaar_card: dict, address_proof: dict,
                        as_of: str | None = None) -> str:
    as_of = as_of or os.environ.get("KYC_AS_OF")  # KYC_AS_OF pins the date for repeatable evals
    check_date = _parse_date(as_of) if as_of else date.today()
    if check_date is None:
        return json.dumps({"error": f"as_of '{as_of}' is not a valid date"})
    people = {"form": form, "pan_card": pan_card, "aadhaar_card": aadhaar_card}
    findings = [
        check_pan_format(form, pan_card),
        check_name_match({**people, "address_proof": address_proof}),
        check_dob_match(people),
        check_document_expiry(address_proof, check_date),
        check_address_match(form, address_proof),
    ]
    return json.dumps({"as_of": check_date.isoformat(), "findings": findings}, indent=2)


if __name__ == "__main__":  # offline run: python tools/validate_kyc_fields.py samples/<file>.json
    import sys

    with open(sys.argv[1], encoding="utf-8") as fh:
        print(validate_kyc_fields(**json.load(fh)))
