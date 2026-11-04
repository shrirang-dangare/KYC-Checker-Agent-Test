"""kyc-checker: entry point.

Usage:
    python main.py path/to/kyc_input.txt
    cat kyc_input.txt | python main.py
"""

import json
import os
import sys
from pathlib import Path

import anthropic

from guardrails import build_result, flag_for_human, mask_ids
from tools import TOOLS, run_tool

ROOT = Path(__file__).parent
SYSTEM_PROMPT_PATH = ROOT / "prompts" / "system_prompt.md"
DEFAULT_MODEL = "claude-opus-5-5"
MAX_TOOL_CALLS = 5  # stop rule: give up after this many tool calls without a report


def load_env(path: Path = ROOT / ".env") -> None:
    """Minimal .env loader (no extra dependency). Existing env vars win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_system_prompt() -> str:
    """Read the system prompt from its own file. Edit that file, not this code."""
    text = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8").strip()
    if not text:
        raise RuntimeError(f"System prompt file is empty: {SYSTEM_PROMPT_PATH}")
    return text


def call_model(client: anthropic.Anthropic, messages: list, max_tokens: int = 4096, **kwargs):
    """The single place that calls the model. Always sends the system prompt and tools."""
    if TOOLS:
        kwargs.setdefault("tools", TOOLS)
    return client.messages.create(
        model=os.environ.get("KYC_MODEL", DEFAULT_MODEL),
        max_tokens=max_tokens,
        system=load_system_prompt(),
        messages=messages,
        **kwargs,
    )


def hello_model() -> None:
    """Smoke test: one message, no tools. Prints the reply."""
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
    response = call_model(
        client,
        [{"role": "user", "content": "Say hello and name the model you are."}],
        max_tokens=200,
    )
    print(f"[requested: {os.environ.get('KYC_MODEL', DEFAULT_MODEL)} | served: {response.model}]")
    print("".join(b.text for b in response.content if b.type == "text"))


def log(text: str = "") -> None:
    """Turn-by-turn trace goes to stderr, so stdout holds only the final result. IDs are masked."""
    print(mask_ids(text), file=sys.stderr, flush=True)


def _indent(text: str, limit: int = 2000) -> str:
    text = text if len(text) <= limit else text[:limit] + f"\n... ({len(text) - limit} more chars)"
    return "\n".join("    " + line for line in text.splitlines())


def check_kyc(kyc_text: str) -> dict:
    """Agent loop with stop rule and guardrails.

    Ends after one final answer (checked by guardrails.build_result) or after
    MAX_TOOL_CALLS tool calls, whichever comes first.
    """
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
    messages = [{"role": "user", "content": kyc_text}]
    tool_calls_made = 0
    tool_findings: list[dict] = []  # deterministic findings, used by the guardrails
    turn = 0

    while True:
        turn += 1
        response = call_model(client, messages)
        log(f"\n=== Turn {turn} | model: {response.model} | stop_reason: {response.stop_reason} ===")

        tool_calls = []
        for block in response.content:
            if block.type == "thinking" and getattr(block, "thinking", ""):
                log("  [thinking]\n" + _indent(block.thinking))
            elif block.type == "text" and block.text.strip():
                label = "[thought]" if response.stop_reason == "tool_use" else "[proposed final answer]"
                log(f"  {label}\n" + _indent(block.text))
            elif block.type == "tool_use":
                tool_calls.append(block)

        if response.stop_reason == "end_turn":
            raw = "".join(b.text for b in response.content if b.type == "text")
            result = build_result(raw, tool_findings)
            _log_guardrails(result)
            return result
        if response.stop_reason != "tool_use":
            log(f"  [stop] unexpected stop_reason={response.stop_reason}")
            return flag_for_human("incomplete", [f"STOP: model stopped unexpectedly (stop_reason={response.stop_reason})"])

        if tool_calls_made + len(tool_calls) > MAX_TOOL_CALLS:
            log(f"  [stop rule] model asked for tool call #{tool_calls_made + 1}; cap is {MAX_TOOL_CALLS}. Stopping.")
            return flag_for_human("incomplete", [
                f"STOP_RULE: agent could not complete within the limit of {MAX_TOOL_CALLS} tool calls "
                "and produced no KYC report"
            ])

        messages.append({"role": "assistant", "content": response.content})
        results = []
        for call in tool_calls:
            tool_calls_made += 1
            log(f"  [tool call {tool_calls_made}/{MAX_TOOL_CALLS}] {call.name}\n"
                + _indent(json.dumps(call.input, indent=2, ensure_ascii=False)))
            output = run_tool(call.name, call.input)
            log("  [tool result]\n" + _indent(output))
            if call.name == "validate_kyc_fields":
                try:
                    tool_findings.extend(json.loads(output).get("findings", []))
                except (json.JSONDecodeError, AttributeError):
                    pass
            results.append({
                "type": "tool_result",
                "tool_use_id": call.id,
                "content": output,
                "is_error": output.startswith("Error"),
            })
        messages.append({"role": "user", "content": results})


def _log_guardrails(result: dict) -> None:
    if result["guardrail_violations"]:
        log("  [guardrail] BLOCKED:")
        for v in result["guardrail_violations"]:
            log(f"    - {v}")
    else:
        log("  [guardrail] passed")
    log(f"  [route] {result['decision_status']} -> {result['route_to']}")


def main() -> None:
    load_env()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is not set. Copy .env.example to .env and add your key.")

    if sys.argv[1:] == ["--hello"]:
        hello_model()
        return

    kyc_text = Path(sys.argv[1]).read_text(encoding="utf-8") if len(sys.argv) > 1 else sys.stdin.read()
    if not kyc_text.strip():
        sys.exit("No KYC input given. Pass a file path or pipe text on stdin.")

    print(json.dumps(check_kyc(kyc_text), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
