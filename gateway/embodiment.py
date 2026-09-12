"""Strict intent validation and deterministic, fail-safe orchestration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, Protocol, cast

from stackchan.recipes import (
    SUPPORTED_INTENTS,
    Intent,
    IntentRecipe,
    RecipeStep,
    recipe_for,
)

_ALLOWED_FIELDS = frozenset(("version", "intent", "speech"))
_REQUIRED_FIELDS = frozenset(("version", "intent"))


class IntentRequestError(ValueError):
    """The request does not match the Milestone 1 intent contract."""


@dataclass(frozen=True)
class IntentRequest:
    version: Literal["v1"]
    intent: Intent
    speech: None = None


class DevicePort(Protocol):
    def prepare(self, steps: tuple[RecipeStep, ...]) -> None:
        """Reject an unsupported complete execution plan before device work."""

    def present(self, *, face: str, motion: str) -> None:
        """Present one symbolic face-and-motion action."""


def parse_intent_request(payload: Mapping[str, object]) -> IntentRequest:
    if not isinstance(payload, Mapping):
        raise IntentRequestError("intent request must be a JSON object")
    fields = set(payload)
    if not all(isinstance(field, str) for field in fields):
        raise IntentRequestError("intent request field names must be strings")
    extra_fields = fields - _ALLOWED_FIELDS
    if extra_fields:
        names = ", ".join(sorted(cast(set[str], extra_fields)))
        raise IntentRequestError(f"unexpected intent request field(s): {names}")
    missing_fields = _REQUIRED_FIELDS - fields
    if missing_fields:
        names = ", ".join(sorted(missing_fields))
        raise IntentRequestError(f"missing required intent request field(s): {names}")
    version = payload["version"]
    if version != "v1":
        raise IntentRequestError(f"unsupported intent contract version: {version!r}")
    intent = payload["intent"]
    if intent not in SUPPORTED_INTENTS:
        raise IntentRequestError(f"unsupported intent: {intent!r}")
    if payload.get("speech") is not None:
        raise IntentRequestError(
            "speech must be null because speech is disabled in Milestone 1"
        )
    return IntentRequest(version="v1", intent=cast(Intent, intent))


def execute_intent(request: IntentRequest, device: DevicePort) -> IntentRecipe:
    recipe = recipe_for(request.intent)
    device.prepare(recipe.steps)
    for step in recipe.steps:
        _present(device, step)
    return recipe


def planned_steps_for(request: IntentRequest) -> tuple[RecipeStep, ...]:
    """Return the single firmware-owned expression operation."""

    return recipe_for(request.intent).steps


def embody(payload: Mapping[str, object], device: DevicePort) -> IntentRecipe:
    """Validate before any device work, then execute with mandatory safe return."""

    return execute_intent(parse_intent_request(payload), device)


def _present(device: DevicePort, step: RecipeStep) -> None:
    device.present(face=step.face, motion=step.motion)
