"""Bounded one-turn mailbox and direct-answer body orchestration."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections import deque
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from threading import Condition

from gateway.pending_thought_runtime import (
    PendingThoughtRuntime,
    PendingThoughtRuntimeError,
)
from gateway.speech_preparation import (
    EDGE_TTS_CONNECT_TIMEOUT_SECONDS,
    EDGE_TTS_RECEIVE_TIMEOUT_SECONDS,
    stream_speech_pcm,
)

_MAX_AUDIO_BYTES = 768 * 1024
_CAPTURE_TTL_SECONDS = 120
_ACTIVE_TURN_TTL_SECONDS = 48 * 60 * 60
_MAX_METRIC_VALUE = 10**16
_PCM_SAMPLE_BYTES = 2
_PCM_PREBUFFER_BYTES = 16_000 * _PCM_SAMPLE_BYTES * 240 // 1000
_PCM_BUFFER_MAX_BYTES = 16_000 * _PCM_SAMPLE_BYTES
_PCM_PROGRESS_TIMEOUT_SECONDS = (
    EDGE_TTS_CONNECT_TIMEOUT_SECONDS
    + EDGE_TTS_RECEIVE_TIMEOUT_SECONDS
    + 5
)
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

_monotonic = time.monotonic


class DirectConversationError(RuntimeError):
    """A direct robot turn could not complete safely."""

    def __init__(
        self,
        message: str,
        *,
        metrics: Mapping[str, int] | None = None,
    ) -> None:
        super().__init__(message)
        self.metrics = dict(metrics or {})


class DirectPcmBuffer:
    """Bounded PCM FIFO shared by the async producer and body worker."""

    def __init__(
        self,
        *,
        prebuffer_bytes: int = _PCM_PREBUFFER_BYTES,
        max_bytes: int = _PCM_BUFFER_MAX_BYTES,
        progress_timeout_seconds: float = _PCM_PROGRESS_TIMEOUT_SECONDS,
    ) -> None:
        if not 0 < prebuffer_bytes <= max_bytes:
            raise ValueError("PCM buffer bounds are invalid")
        if progress_timeout_seconds <= 0:
            raise ValueError("PCM progress timeout must be positive")
        self._prebuffer_bytes = prebuffer_bytes
        self._max_bytes = max_bytes
        self._progress_timeout_seconds = progress_timeout_seconds
        self._chunks: deque[bytes] = deque()
        self._buffered_bytes = 0
        self._condition = Condition()
        self._finished = False
        self._failure: Exception | None = None
        self._aborted = False
        self._first_pcm_ready_ms: int | None = None
        self._completed_ms: int | None = None
        self._last_progress = time.monotonic()

    async def put(self, chunk: bytes) -> None:
        """Append PCM, applying backpressure once the FIFO is full."""

        await asyncio.to_thread(self.put_blocking, chunk)

    def put_blocking(self, chunk: bytes) -> None:
        """Blocking half of :meth:`put`, used by the async bridge."""

        if not chunk:
            return
        offset = 0
        with self._condition:
            while offset < len(chunk):
                while self._buffered_bytes >= self._max_bytes:
                    self._raise_terminal_locked()
                    self._condition.wait()
                self._raise_terminal_locked()
                count = min(
                    self._max_bytes - self._buffered_bytes,
                    len(chunk) - offset,
                )
                part = chunk[offset : offset + count]
                self._chunks.append(part)
                self._buffered_bytes += count
                offset += count
                self._last_progress = time.monotonic()
                if self._first_pcm_ready_ms is None:
                    self._first_pcm_ready_ms = _unix_ms()
                self._condition.notify_all()

    def finish(self) -> None:
        """Mark clean producer EOS without discarding an incomplete tail."""

        with self._condition:
            if self._failure is None and not self._aborted:
                self._finished = True
                self._completed_ms = _unix_ms()
            self._condition.notify_all()

    def fail(self, error: Exception) -> None:
        """Abort unread PCM so a partial utterance is never completed."""

        with self._condition:
            if self._failure is None and not self._aborted:
                self._failure = error
                self._chunks.clear()
                self._buffered_bytes = 0
            self._condition.notify_all()

    def abort(self) -> None:
        """Release a blocked producer after an independent body failure."""

        with self._condition:
            self._aborted = True
            self._chunks.clear()
            self._buffered_bytes = 0
            self._condition.notify_all()

    def wait_for_playable(self) -> None:
        """Wait for the initial prebuffer or a nonempty final PCM tail."""

        with self._condition:
            while True:
                self._raise_terminal_locked()
                if self._buffered_bytes >= self._prebuffer_bytes:
                    return
                if self._finished:
                    if self._buffered_bytes >= _PCM_SAMPLE_BYTES:
                        return
                    raise DirectConversationError(
                        "speech synthesis produced no audio"
                    )
                self._wait_for_progress_locked()

    def iter_pcm_chunks(self) -> Iterator[bytes]:
        """Drain PCM in order and surface producer failures to the upload."""

        while True:
            with self._condition:
                while not self._chunks:
                    self._raise_terminal_locked()
                    if self._finished:
                        return
                    self._wait_for_progress_locked()
                chunk = self._chunks.popleft()
                self._buffered_bytes -= len(chunk)
                self._condition.notify_all()
            yield chunk

    def metrics(self) -> dict[str, int]:
        """Return content-free production timestamps for the turn timeline."""

        metrics: dict[str, int] = {}
        if self._first_pcm_ready_ms is not None:
            metrics["tts_first_pcm_ready_ms"] = self._first_pcm_ready_ms
        if self._completed_ms is not None:
            metrics["tts_completed_ms"] = self._completed_ms
        return metrics

    def _raise_terminal_locked(self) -> None:
        if self._failure is not None:
            if isinstance(self._failure, DirectConversationError):
                raise self._failure
            raise DirectConversationError(
                "speech synthesis failed"
            ) from self._failure
        if self._aborted:
            raise DirectConversationError("speech stream was aborted")

    def _wait_for_progress_locked(self) -> None:
        """Wait only until new PCM must arrive to keep the stream alive."""

        remaining = self._progress_timeout_seconds - (
            time.monotonic() - self._last_progress
        )
        if remaining <= 0:
            error = DirectConversationError("speech synthesis stalled")
            self._failure = error
            self._chunks.clear()
            self._buffered_bytes = 0
            self._condition.notify_all()
            raise error
        self._condition.wait(remaining)


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
    first_frame = metrics.get("gateway_first_audio_frame_sent_ms")
    capture_stopped = metrics.get("gateway_capture_stopped_ms")
    if (
        isinstance(first_frame, int)
        and isinstance(capture_stopped, int)
        and first_frame >= capture_stopped
    ):
        metrics["submit_to_speech_start_ms"] = first_frame - capture_stopped
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
    metrics: dict[str, int]


class VoiceMailbox:
    """Keep at most one unclaimed capture; track claimed turns independently."""

    def __init__(self) -> None:
        self._capture: VoiceCapture | None = None
        self._capture_expires_at: float | None = None
        self._active_turns: dict[str, float] = {}
        self._answer_started_turn_id: str | None = None
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
            if self._capture is not None:
                raise DirectConversationError("another robot turn is pending")
            turn_id = f"robot:{uuid.uuid4()}"
            now = _monotonic()
            self._capture = VoiceCapture(
                turn_id,
                audio,
                dict(metrics or {}),
            )
            self._capture_expires_at = (
                None if self._active_turns else now + _CAPTURE_TTL_SECONDS
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
            self._capture_expires_at = None
            if capture is not None:
                capture.metrics["server_capture_claimed_ms"] = _unix_ms()
                self._active_turns[capture.turn_id] = (
                    _monotonic() + _ACTIVE_TURN_TTL_SECONDS
                )
            return capture

    async def begin_answer(self, turn_id: str) -> bool:
        async with self._condition:
            self._expire_locked()
            if (
                turn_id not in self._active_turns
                or self._answer_started_turn_id is not None
            ):
                return False
            self._answer_started_turn_id = turn_id
            return True

    async def abandon(self, turn_id: str) -> bool:
        """Release one claimed turn unless body playback has started."""

        async with self._condition:
            self._expire_locked()
            if (
                turn_id not in self._active_turns
                or self._answer_started_turn_id == turn_id
            ):
                return False
            del self._active_turns[turn_id]
            self._arm_capture_expiry_locked()
            return True

    async def finish_answer(self, turn_id: str) -> None:
        async with self._condition:
            self._active_turns.pop(turn_id, None)
            if self._answer_started_turn_id == turn_id:
                self._answer_started_turn_id = None
            self._arm_capture_expiry_locked()

    def _arm_capture_expiry_locked(self, now: float | None = None) -> None:
        if (
            self._capture is not None
            and self._capture_expires_at is None
            and not self._active_turns
        ):
            self._capture_expires_at = (
                _monotonic() if now is None else now
            ) + _CAPTURE_TTL_SECONDS

    def _expire_locked(self) -> None:
        now = _monotonic()
        expired = [
            tid for tid, exp in self._active_turns.items() if now >= exp
        ]
        for tid in expired:
            del self._active_turns[tid]
            if self._answer_started_turn_id == tid:
                self._answer_started_turn_id = None
        self._arm_capture_expiry_locked(now)
        if (
            self._capture is not None
            and self._capture_expires_at is not None
            and now >= self._capture_expires_at
        ):
            self._capture = None
            self._capture_expires_at = None


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
    """Generate speech during attention and stream it after the safe gate."""

    answer = answer.strip()
    if not answer:
        raise DirectConversationError("direct answer is empty")
    pcm = DirectPcmBuffer()

    async def produce() -> None:
        try:
            await stream_speech_pcm(
                answer,
                voice,
                pcm.put,
                on_failure=pcm.fail,
            )
        except asyncio.CancelledError:
            pcm.abort()
            raise
        except Exception as exc:
            pcm.fail(exc)
            raise
        else:
            pcm.finish()

    producer = asyncio.create_task(produce())
    try:
        body_metrics = await runtime.tell_direct_stream(turn_id, pcm)
        await producer
    except asyncio.CancelledError:
        pcm.abort()
        producer.cancel()
        await asyncio.gather(producer, return_exceptions=True)
        raise
    except Exception as exc:
        pcm.abort()
        producer.cancel()
        await asyncio.gather(producer, return_exceptions=True)
        metrics = pcm.metrics()
        if isinstance(exc, PendingThoughtRuntimeError):
            metrics.update(exc.metrics)
        if isinstance(exc, DirectConversationError):
            metrics.update(exc.metrics)
        raise DirectConversationError(
            "direct answer playback failed",
            metrics=metrics,
        ) from exc
    return {**pcm.metrics(), **body_metrics}
