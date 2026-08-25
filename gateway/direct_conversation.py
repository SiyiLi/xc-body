"""Bounded one-turn mailbox and direct-answer body orchestration."""

from __future__ import annotations

import asyncio
import base64
import json
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass

from gateway.pending_thought_runtime import PendingThoughtRuntime
from gateway.speech_preparation import prepare_speech

_MAX_AUDIO_BYTES = 768 * 1024
_CAPTURE_TTL_SECONDS = 120
_ACTIVE_TURN_TTL_SECONDS = 10 * 60
_MAX_METRIC_VALUE = 10**16
_TURN_METRIC_NAMES = frozenset(
    (
        "capture_started_uptime_us",
        "capture_stopped_uptime_us",
        "gateway_capture_started_ms",
        "gateway_capture_stopped_ms",
        "gateway_upload_started_ms",
        "server_capture_request_started_ms",
        "server_capture_received_ms",
        "server_capture_claimed_ms",
        "plugin_audio_decode_ms",
        "plugin_transcription_ms",
        "plugin_question_delivery_ms",
        "plugin_agent_ms",
        "plugin_answer_delivery_ms",
        "plugin_projection_ms",
        "plugin_total_before_answer_ms",
    )
)
_PLUGIN_STAGE_NAMES = frozenset(
    (
        "audio_decode",
        "transcription",
        "question_delivery",
        "agent",
        "answer_delivery",
        "projection",
        "answer_post",
    )
)
_DERIVED_METRICS = (
    (
        "device_recording_ms",
        "capture_started_uptime_us",
        "capture_stopped_uptime_us",
        1000,
    ),
    (
        "gateway_recording_ms",
        "gateway_capture_started_ms",
        "gateway_capture_stopped_ms",
        1,
    ),
    (
        "gateway_upload_ms",
        "gateway_upload_started_ms",
        "server_capture_received_ms",
        1,
    ),
    (
        "server_queue_ms",
        "server_capture_received_ms",
        "server_capture_claimed_ms",
        1,
    ),
    (
        "submit_to_speech_start_ms",
        "gateway_capture_stopped_ms",
        "gateway_playback_started_ms",
        1,
    ),
    (
        "submit_to_complete_ms",
        "gateway_capture_stopped_ms",
        "server_completed_ms",
        1,
    ),
    (
        "end_to_end_ms",
        "gateway_capture_started_ms",
        "server_completed_ms",
        1,
    ),
)


class DirectConversationError(RuntimeError):
    """A direct robot turn could not complete safely."""


def _unix_ms() -> int:
    return time.time_ns() // 1_000_000


def parse_plugin_metrics(
    value: object,
) -> tuple[dict[str, int], str | None]:
    """Validate content-free phase durations reported by OpenClaw."""

    if value is None:
        return {}, None
    if not isinstance(value, Mapping) or value.get("version") != 1:
        raise DirectConversationError("direct turn metrics are invalid")
    raw_metrics = value.get("values")
    if not isinstance(raw_metrics, Mapping):
        raise DirectConversationError("direct turn metrics are invalid")
    if not set(raw_metrics).issubset(_TURN_METRIC_NAMES):
        raise DirectConversationError("direct turn metrics are invalid")
    metrics: dict[str, int] = {}
    for name, raw_metric in raw_metrics.items():
        if (
            isinstance(raw_metric, bool)
            or not isinstance(raw_metric, int)
            or raw_metric < 0
            or raw_metric > _MAX_METRIC_VALUE
        ):
            raise DirectConversationError("direct turn metrics are invalid")
        metrics[str(name)] = raw_metric
    failed_stage = value.get("failed_stage")
    if failed_stage is not None and failed_stage not in _PLUGIN_STAGE_NAMES:
        raise DirectConversationError("direct turn metrics are invalid")
    return metrics, failed_stage


def build_direct_turn_report(
    turn_id: str,
    status: str,
    metrics: dict[str, int],
    failed_stage: str | None,
) -> dict[str, object]:
    metrics["server_completed_ms"] = _unix_ms()
    for name, start, end, divisor in _DERIVED_METRICS:
        if start in metrics and end in metrics and metrics[end] >= metrics[start]:
            metrics[name] = round((metrics[end] - metrics[start]) / divisor)
    report: dict[str, object] = {
        "event": "xc_body.direct_turn",
        "version": 1,
        "turn_id": turn_id,
        "status": status,
        "metrics": metrics,
    }
    if failed_stage is not None:
        report["failed_stage"] = failed_stage
    return report


@dataclass(frozen=True)
class VoiceCapture:
    turn_id: str
    audio: bytes
    created_at: float
    metrics: dict[str, int]


class VoiceMailbox:
    """Keep at most one unclaimed capture and never redeliver a claim."""

    def __init__(self) -> None:
        self._capture: VoiceCapture | None = None
        self._active_turn_id: str | None = None
        self._active_expires_at: float | None = None
        self._answer_started = False
        self._condition = asyncio.Condition()

    async def submit(
        self,
        audio: bytes,
        metrics: Mapping[str, int] | None = None,
    ) -> str:
        if not audio or len(audio) > _MAX_AUDIO_BYTES:
            raise DirectConversationError("audio capture size is invalid")
        async with self._condition:
            self._expire_locked()
            if self._capture is not None or self._active_turn_id is not None:
                raise DirectConversationError("another robot turn is pending")
            turn_id = f"robot:{uuid.uuid4()}"
            self._capture = VoiceCapture(
                turn_id,
                audio,
                time.monotonic(),
                dict(metrics or {}),
            )
            self._condition.notify_all()
            return turn_id

    async def claim(self, timeout: float = 25.0) -> VoiceCapture | None:
        async with self._condition:
            self._expire_locked()
            if self._capture is None:
                try:
                    await asyncio.wait_for(self._condition.wait(), timeout)
                except asyncio.TimeoutError:
                    return None
                self._expire_locked()
            capture = self._capture
            self._capture = None
            if capture is not None:
                self._active_turn_id = capture.turn_id
                capture.metrics["server_capture_claimed_ms"] = _unix_ms()
                self._active_expires_at = (
                    time.monotonic() + _ACTIVE_TURN_TTL_SECONDS
                )
                self._answer_started = False
            return capture

    async def begin_answer(self, turn_id: str) -> bool:
        async with self._condition:
            self._expire_locked()
            if self._active_turn_id != turn_id or self._answer_started:
                return False
            self._answer_started = True
            return True

    async def abandon(self, turn_id: str) -> bool:
        """Release one claimed turn unless body playback has started."""

        async with self._condition:
            self._expire_locked()
            if self._active_turn_id != turn_id or self._answer_started:
                return False
            self._active_turn_id = None
            self._active_expires_at = None
            return True

    async def finish_answer(self, turn_id: str) -> None:
        async with self._condition:
            if self._active_turn_id == turn_id:
                self._active_turn_id = None
                self._active_expires_at = None
                self._answer_started = False

    def _expire_locked(self) -> None:
        now = time.monotonic()
        if (
            self._capture is not None
            and now - self._capture.created_at >= _CAPTURE_TTL_SECONDS
        ):
            self._capture = None
        if (
            self._active_expires_at is not None
            and now >= self._active_expires_at
        ):
            self._active_turn_id = None
            self._active_expires_at = None
            self._answer_started = False


def emit_direct_turn_metrics(report: Mapping[str, object]) -> None:
    """Write one machine-readable JSON line to container stdout."""

    print(
        json.dumps(report, separators=(",", ":"), sort_keys=True),
        flush=True,
    )


async def speak_direct_answer(
    runtime: PendingThoughtRuntime,
    turn_id: str,
    answer: str,
    voice: str,
) -> dict[str, int]:
    """Prepare speech, perform attention, settle, and play exactly once."""

    answer = answer.strip()
    if not answer:
        raise DirectConversationError("direct answer is empty")
    started = time.monotonic()
    audio_base64 = await prepare_speech(answer, voice)
    tts_ms = round((time.monotonic() - started) * 1000)
    audio = base64.b64decode(audio_base64)
    body_metrics = await runtime.tell_direct(turn_id, audio)
    return {"tts_ms": tts_ms, **body_metrics}
