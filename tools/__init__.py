"""Tools the kyc-checker agent can call.

Add a tool in two steps:
  1. Append its JSON schema to TOOLS (name, description, input_schema).
  2. Register a Python function with the same name in HANDLERS.

Registered tools:
  - validate_kyc_fields: deterministic PAN / name / DOB / expiry / address checks.
"""

from typing import Any, Callable

from . import validate_kyc_fields as _vkf

TOOLS: list[dict[str, Any]] = [_vkf.SCHEMA]
HANDLERS: dict[str, Callable[..., str]] = {
    "validate_kyc_fields": _vkf.validate_kyc_fields,
}


def run_tool(name: str, args: dict[str, Any]) -> str:
    """Dispatch a tool call from the model to its Python handler."""
    handler = HANDLERS.get(name)
    if handler is None:
        return f"Error: unknown tool '{name}'"
    try:
        return handler(**args)
    except Exception as exc:  # report errors back to the model instead of crashing
        return f"Error running {name}: {exc}"
