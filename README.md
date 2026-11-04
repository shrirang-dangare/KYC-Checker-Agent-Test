# kyc-checker

An AI agent that checks a customer's KYC details (form data + OCR text from PAN, Aadhaar and address proof) for completeness and consistency, and recommends **verify**, **resubmit** or **escalate**.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then add your ANTHROPIC_API_KEY
```

## Run

```bash
python main.py --hello              # smoke test: key, SDK, model id
python main.py path/to/kyc_input.txt
```

## Layout

- `main.py` — entry point: loads `.env`, reads KYC text, runs the agent loop with the Anthropic SDK, prints the report.
- `prompts/system_prompt.md` — the system prompt defining the checks and the KYC report format.
- `schema.py` — output contract: typed `KYCReport` and `validate_report()`.
- `tools/` — tool registry (`TOOLS`, `HANDLERS`) in `__init__.py`; `validate_kyc_fields.py` runs deterministic PAN / name / DOB / expiry / address checks. Try it offline: `python tools/validate_kyc_fields.py samples/kyc-2026-10482.tool_input.json`.
