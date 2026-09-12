"""Translate symbolic intent recipes to saved XC Body expressions."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from stackchan.recipes import RecipeStep


class StackChanClient(Protocol):
    def get_status(self) -> Mapping[str, object]: ...

    def perform_expression(self, expression: str) -> Mapping[str, object]: ...


class StackChanAdapterError(RuntimeError):
    """A typed adapter failure with operation context."""

    def __init__(self, operation: str, message: str):
        self.operation = operation
        super().__init__(f"{operation}: {message}")


class CalibrationError(StackChanAdapterError):
    pass


class DeviceUnavailableError(StackChanAdapterError):
    pass


class ClientOperationError(StackChanAdapterError):
    pass


_EXPRESSION_BY_STEP: dict[tuple[str, str], str] = {
    ("neutral", "relaxed_center"): "idle",
    ("attentive", "restrained_side_glance"): "curious",
    ("happy", "single_small_nod"): "pleased",
    ("concerned", "restrained_head_tilt"): "concerned",
}


class StackChanAdapter:
    """Run semantic steps through firmware-owned expression recipes."""

    def __init__(
        self,
        client: StackChanClient,
        *,
        verified_session_id: str | None = None,
    ):
        self._client = client
        self._verified_session_id = verified_session_id

    def prepare(self, steps: tuple[RecipeStep, ...]) -> None:
        """Resolve the full recipe before the first device call."""
        for step in steps:
            self._resolve(step.face, step.motion)

    @classmethod
    def preflight(cls, steps: tuple[RecipeStep, ...]) -> None:
        """Reject any step without a saved-expression mapping."""
        for step in steps:
            cls._resolve(step.face, step.motion)

    def present(self, *, face: str, motion: str) -> None:
        expression = self._resolve(face, motion)
        status = self._call("get_status", self._client.get_status)
        if status.get("connected") is not True:
            raise DeviceUnavailableError("get_status", "device is not connected")
        if self._verified_session_id is not None and (
            status.get("initialized") is not True
            or status.get("session_id") != self._verified_session_id
        ):
            raise DeviceUnavailableError(
                "get_status",
                "device is not ready for the current session",
            )
        self._call(
            "perform_expression",
            self._client.perform_expression,
            expression,
        )

    @staticmethod
    def _resolve(face: str, motion: str) -> str:
        try:
            return _EXPRESSION_BY_STEP[(face, motion)]
        except KeyError as exc:
            raise CalibrationError(
                "expression_mapping",
                f"no saved expression for face={face!r}, motion={motion!r}",
            ) from exc

    @staticmethod
    def _ensure_mapping(operation: str, result: object) -> Mapping[str, object]:
        if not isinstance(result, Mapping):
            raise ClientOperationError(
                operation, "client returned a non-mapping result"
            )
        return result

    def _call(self, operation: str, function, *args) -> Mapping[str, object]:
        try:
            return self._ensure_mapping(operation, function(*args))
        except StackChanAdapterError:
            raise
        except Exception as exc:
            raise ClientOperationError(operation, str(exc)) from exc
