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

from tools import TOOLS, run_tool

ROOT = Path(__file__).parent
SYSTEM_PROMPT_PATH = ROOT / "prompts" / "system_prompt.md"
DEFAULT_MODEL = "claude-opus-5-5"
MAX_TURNS = 10


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
    """Turn-by-turn trace goes to stderr, so stdout holds only the final report."""
    print(text, file=sys.stderr, flush=True)


def _indent(text: str, limit: int = 2000) -> str:
    text = text if len(text) <= limit else text[:limit] + f"\n... ({len(text) - limit} more chars)"
    return "\n".join("    " + line for line in text.splitlines())


def check_kyc(kyc_text: str) -> str:
    """Agent loop: call the model, run any tool calls, repeat until a normal message."""
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
    messages = [{"role": "user", "content": kyc_text}]

    for turn in range(1, MAX_TURNS + 1):
        response = call_model(client, messages)
        log(f"\n=== Turn {turn} | model: {response.model} | stop_reason: {response.stop_reason} ===")

        tool_calls = []
        for block in response.content:
            if block.type == "thinking" and getattr(block, "thinking", ""):
                log("  [thinking]\n" + _indent(block.thinking))
            elif block.type == "text" and block.text.strip():
                label = "[thought]" if response.stop_reason == "tool_use" else "[final answer]"
                log(f"  {label}\n" + _indent(block.text))
            elif block.type == "tool_use":
                tool_calls.append(block)

        if response.stop_reason == "end_turn":
            return "".join(b.text for b in response.content if b.type == "text")
        if response.stop_reason != "tool_use":
            raise RuntimeError(f"Model stopped unexpectedly (stop_reason={response.stop_reason})")

        messages.append({"role": "assistant", "content": response.content})
        results = []
        for call in tool_calls:
            log(f"  [tool call] {call.name}\n" + _indent(json.dumps(call.input, indent=2, ensure_ascii=False)))
            output = run_tool(call.name, call.input)
            log("  [tool result]\n" + _indent(output))
            results.append({
                "type": "tool_result",
                "tool_use_id": call.id,
                "content": output,
                "is_error": output.startswith("Error"),
            })
        messages.append({"role": "user", "content": results})

    raise RuntimeError(f"Agent did not finish within {MAX_TURNS} turns")


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

    print(check_kyc(kyc_text))


if __name__ == "__main__":
    main()
