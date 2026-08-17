"""Transport-neutral Milestone 2 pending-thought boundary."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from gateway.pending_thought import KnockWaitTell, PendingThoughtError

TOOL_NAME = "consider_thought"
STACKCHAN_EVENT_METHOD = "stackchan/event"
_CONTRACT_PATH = (
    Path(__file__).resolve().parents[1]
    / "contracts"
    / "pending-thought.schema.json"
)


class PendingThoughtToolError(ValueError):
    """A pending-thought tool request cannot be dispatched."""


class UnknownPendingThoughtToolError(PendingThoughtToolError):
    """The requested tool is not exposed by Milestone 2."""


class InvalidPendingThoughtArgumentsError(PendingThoughtToolError):
    """The arguments do not match the pending-thought contract."""


def tool_descriptor() -> dict[str, object]:
    """Return the single Milestone 2 classification tool descriptor."""

    with _CONTRACT_PATH.open(encoding="utf-8") as contract_file:
        input_schema = json.load(contract_file)
    return {
        "name": TOOL_NAME,
        "description": (
            "Classify one background result as ignore, remember, or offer."
        ),
        "inputSchema": input_schema,
    }


def handle_tool_call(
    name: str,
    arguments: Mapping[str, object],
    machine: KnockWaitTell,
) -> dict[str, object]:
    """Validate and submit one background-result decision."""

    if name != TOOL_NAME:
        raise UnknownPendingThoughtToolError(
            f"unknown pending-thought tool: {name!r}"
        )
    try:
        outcome = machine.submit(arguments)
    except PendingThoughtError as exc:
        raise InvalidPendingThoughtArgumentsError(str(exc)) from exc
    return {
        "ok": True,
        "thought_id": outcome.thought_id,
        "decision": outcome.decision,
        "state": outcome.state,
        "pending_thought_id": machine.pending_thought_id,
    }


def handle_stackchan_event(
    event: Mapping[str, object], machine: KnockWaitTell
) -> dict[str, object]:
    """Route one upstream event without treating unrelated events as consent."""

    try:
        outcome = machine.handle_stackchan_event(event)
    except PendingThoughtError as exc:
        raise InvalidPendingThoughtArgumentsError(str(exc)) from exc
    if outcome is None:
        return {
            "ok": True,
            "acknowledged": False,
            "pending_thought_id": machine.pending_thought_id,
        }
    return {
        "ok": True,
        "acknowledged": True,
        "thought_id": outcome.thought_id,
        "state": outcome.state,
        "pending_thought_id": machine.pending_thought_id,
    }


def handle_mcp_notification(
    notification: Mapping[str, object], machine: KnockWaitTell
) -> dict[str, object]:
    """Route only the pinned upstream StackChan event notification."""

    if not isinstance(notification, Mapping):
        raise InvalidPendingThoughtArgumentsError(
            "MCP notification must be an object"
        )
    if notification.get("method") != STACKCHAN_EVENT_METHOD:
        return {
            "ok": True,
            "acknowledged": False,
            "pending_thought_id": machine.pending_thought_id,
        }
    params = notification.get("params")
    if not isinstance(params, Mapping):
        raise InvalidPendingThoughtArgumentsError(
            "stackchan/event params must be an object"
        )
    return handle_stackchan_event(params, machine)
