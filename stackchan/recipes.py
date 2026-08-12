"""Immutable symbolic StackChan recipes; physical values belong to calibration."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

Intent = Literal["idle", "curious", "pleased", "concerned"]
SUPPORTED_INTENTS = ("idle", "curious", "pleased", "concerned")


@dataclass(frozen=True)
class RecipeStep:
    face: str
    motion: str


@dataclass(frozen=True)
class IntentRecipe:
    intent: Intent
    steps: tuple[RecipeStep, ...]


IDLE_STEP = RecipeStep(face="neutral", motion="relaxed_center")
_RECIPES = MappingProxyType(
    {
        "idle": IntentRecipe(intent="idle", steps=(IDLE_STEP,)),
        "curious": IntentRecipe(
            intent="curious",
            steps=(RecipeStep("attentive", "restrained_side_glance"),),
        ),
        "pleased": IntentRecipe(
            intent="pleased",
            steps=(RecipeStep("happy", "single_small_nod"),),
        ),
        "concerned": IntentRecipe(
            intent="concerned",
            steps=(RecipeStep("concerned", "restrained_head_tilt"),),
        ),
    }
)


def recipe_for(intent: Intent) -> IntentRecipe:
    """Return the fixed symbolic recipe for a validated intent."""

    return _RECIPES[intent]
