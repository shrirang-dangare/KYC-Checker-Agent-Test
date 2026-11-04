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
python main.py path/to/kyc_input.txt
```

## Layout

- `main.py` — entry point: loads `.env`, reads KYC text, runs the agent loop with the Anthropic SDK, prints the report.
- `prompts/system_prompt.md` — the system prompt defining the checks and the KYC report format.
- `tools/__init__.py` — tool schemas (`TOOLS`) and their Python handlers (`HANDLERS`); empty for now.
