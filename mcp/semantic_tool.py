"""Transport-neutral semantic MCP boundary for Milestone 1."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from gateway.embodiment import DevicePort, IntentRequestError, embody

TOOL_NAME = "embody"
_CONTRACT_PATH = (
    Path(__file__).resolve().parents[1]
    / "contracts"
    / "embodiment-intent.schema.json"
)


class SemanticToolError(ValueError):
    """A semantic MCP request cannot be dispatched."""


class UnknownToolError(SemanticToolError):
    """The requested semantic tool is not exposed."""


class InvalidToolArgumentsError(SemanticToolError):
    """The semantic tool arguments do not match the intent contract."""


def tool_descriptor() -> dict[str, object]:
    """Return the single transport-ready Milestone 1 tool descriptor."""

    with _CONTRACT_PATH.open(encoding="utf-8") as contract_file:
        input_schema = json.load(contract_file)
    return {
        "name": TOOL_NAME,
        "description": (
            "Present one reviewed semantic intention through the physical body."
        ),
        "inputSchema": input_schema,
    }


def handle_tool_call(
    name: str, arguments: Mapping[str, object], device: DevicePort
) -> dict[str, object]:
    """Validate and execute one semantic call without choosing an MCP transport."""

    if name != TOOL_NAME:
        raise UnknownToolError(f"unknown semantic tool: {name!r}")
    try:
        recipe = embody(arguments, device)
    except IntentRequestError as exc:
        raise InvalidToolArgumentsError(str(exc)) from exc
    return {"ok": True, "intent": recipe.intent, "returned_to_idle": True}
