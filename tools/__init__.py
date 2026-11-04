"""Tools the kyc-checker agent can call.

Add a tool in two steps:
  1. Append its JSON schema to TOOLS (name, description, input_schema).
  2. Register a Python function with the same name in HANDLERS.

Example (not enabled yet):

    TOOLS.append({
        "name": "validate_pan_format",
        "description": "Check whether a PAN number matches the AAAAA9999A format.",
        "input_schema": {
            "type": "object",
            "properties": {"pan": {"type": "string"}},
            "required": ["pan"],
        },
    })
    HANDLERS["validate_pan_format"] = lambda pan: str(bool(re.fullmatch(r"[A-Z]{5}[0-9]{4}[A-Z]", pan)))
"""

from typing import Any, Callable

TOOLS: list[dict[str, Any]] = []
HANDLERS: dict[str, Callable[..., str]] = {}


def run_tool(name: str, args: dict[str, Any]) -> str:
    """Dispatch a tool call from the model to its Python handler."""
    handler = HANDLERS.get(name)
    if handler is None:
        return f"Error: unknown tool '{name}'"
    try:
        return handler(**args)
    except Exception as exc:  # report errors back to the model instead of crashing
        return f"Error running {name}: {exc}"
