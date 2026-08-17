"""Fail-closed translation from symbolic recipes to a small StackChan client."""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import isfinite
from types import MappingProxyType
from typing import Callable, Protocol

from stackchan.recipes import RecipeStep

_YAW_RANGE = (-90.0, 90.0)
_PITCH_RANGE = (5.0, 85.0)


class StackChanClient(Protocol):
    def get_status(self) -> Mapping[str, object]: ...
    def set_avatar(self, face: str) -> Mapping[str, object]: ...
    def move_head(
        self, yaw: float, pitch: float, speed: float
    ) -> Mapping[str, object]: ...


class StackChanAdapterError(RuntimeError):
    """A typed adapter failure with operation context."""

    def __init__(self, operation: str, message: str):
        self.operation = operation
        super().__init__(f"{operation}: {message}")


class CalibrationError(StackChanAdapterError):
    pass


class VisibleFaceVerificationError(CalibrationError):
    """A mapped avatar has not been verified as human-visible."""


class DeviceUnavailableError(StackChanAdapterError):
    pass


class ClientOperationError(StackChanAdapterError):
    pass


@dataclass(frozen=True)
class HeadMove:
    """One explicitly calibrated head command; no production values are supplied."""

    yaw: float
    pitch: float
    speed: float

    def validate(self) -> None:
        values = (self.yaw, self.pitch, self.speed)
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            for value in values
        ):
            raise CalibrationError("calibration", "head values must be numbers")
        if not all(isfinite(float(value)) for value in values):
            raise CalibrationError("calibration", "head values must be finite")
        if not _YAW_RANGE[0] <= self.yaw <= _YAW_RANGE[1]:
            raise CalibrationError(
                "calibration", "head yaw must be within -90..90 degrees"
            )
        if not _PITCH_RANGE[0] <= self.pitch <= _PITCH_RANGE[1]:
            raise CalibrationError(
                "calibration", "head pitch must be within 5..85 degrees"
            )
        if self.speed <= 0:
            raise CalibrationError("calibration", "head speed must be positive")


@dataclass(frozen=True)
class StackChanCalibration:
    """Immutable mappings plus human-verified upstream avatar names."""

    faces: Mapping[str, str]
    motions: Mapping[str, Sequence[HeadMove]]
    hold_seconds: Mapping[str, float] | None = None
    verified_faces: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        faces = MappingProxyType(dict(self.faces))
        motions = MappingProxyType(
            {name: tuple(moves) for name, moves in self.motions.items()}
        )
        hold_seconds = MappingProxyType(dict(self.hold_seconds or {}))
        verified_faces = frozenset(self.verified_faces)
        object.__setattr__(self, "faces", faces)
        object.__setattr__(self, "motions", motions)
        object.__setattr__(self, "hold_seconds", hold_seconds)
        object.__setattr__(self, "verified_faces", verified_faces)
        if any(
            not isinstance(value, str) or not value.strip()
            for value in faces.values()
        ):
            raise CalibrationError(
                "calibration", "avatar mappings must be non-empty strings"
            )
        unknown_verified_faces = verified_faces - set(faces.values())
        if unknown_verified_faces:
            names = ", ".join(sorted(repr(name) for name in unknown_verified_faces))
            raise CalibrationError(
                "calibration",
                f"verified faces have no avatar mapping: {names}",
            )
        unknown_holds = set(hold_seconds) - set(motions)
        if unknown_holds:
            names = ", ".join(sorted(repr(name) for name in unknown_holds))
            raise CalibrationError(
                "calibration", f"hold durations have no motion mapping: {names}"
            )
        for name, duration in hold_seconds.items():
            if (
                isinstance(duration, bool)
                or not isinstance(duration, (int, float))
                or not isfinite(float(duration))
                or duration < 0
            ):
                raise CalibrationError(
                    "calibration",
                    f"hold duration {name!r} must be finite and nonnegative",
                )
        for name, moves in motions.items():
            if not moves or any(not isinstance(move, HeadMove) for move in moves):
                raise CalibrationError(
                    "calibration",
                    f"motion {name!r} must contain HeadMove values",
                )
            for move in moves:
                move.validate()


class StackChanAdapter:
    def __init__(
        self,
        client: StackChanClient,
        calibration: StackChanCalibration | None,
        *,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._client = client
        self._calibration = calibration
        self._sleep = sleep

    def prepare(self, steps: tuple[RecipeStep, ...]) -> None:
        """Resolve the full recipe before the first upstream client call."""

        self.preflight_calibration(self._calibration, steps)

    @classmethod
    def preflight_calibration(
        cls,
        calibration: StackChanCalibration | None,
        steps: tuple[RecipeStep, ...],
    ) -> None:
        """Validate complete calibrated support without constructing a client."""

        if calibration is None:
            raise CalibrationError("calibration", "explicit calibration is required")
        for step in steps:
            cls._resolve(calibration, face=step.face, motion=step.motion)

    def present(self, *, face: str, motion: str) -> None:
        calibration = self._require_calibration()
        avatar, moves = self._resolve(calibration, face=face, motion=motion)
        status = self._call("get_status", self._client.get_status)
        if status.get("connected") is not True:
            raise DeviceUnavailableError("get_status", "device is not connected")
        self._call("set_avatar", self._client.set_avatar, avatar)
        for move in moves:
            self._call(
                "move_head",
                self._client.move_head,
                move.yaw,
                move.pitch,
                move.speed,
            )
        hold_seconds = calibration.hold_seconds.get(motion, 0.0)
        if hold_seconds:
            self._sleep(hold_seconds)

    def _require_calibration(self) -> StackChanCalibration:
        if self._calibration is None:
            raise CalibrationError("calibration", "explicit calibration is required")
        return self._calibration

    @staticmethod
    def _resolve(
        calibration: StackChanCalibration,
        *,
        face: str,
        motion: str,
    ) -> tuple[str, Sequence[HeadMove]]:
        try:
            avatar = calibration.faces[face]
        except KeyError as exc:
            raise CalibrationError(
                "calibration", f"no mapping for {exc.args[0]!r}"
            ) from exc
        if avatar not in calibration.verified_faces:
            raise VisibleFaceVerificationError(
                "visible_face_verification",
                f"upstream avatar {avatar!r} for face {face!r} "
                "requires visible face verification",
            )
        try:
            moves = calibration.motions[motion]
        except KeyError as exc:
            raise CalibrationError(
                "calibration", f"no mapping for {exc.args[0]!r}"
            ) from exc
        return avatar, moves

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
