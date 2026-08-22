"""Bounded one-turn mailbox and direct-answer body orchestration."""

from __future__ import annotations

import asyncio
import base64
import time
import uuid
from dataclasses import dataclass

from gateway.pending_thought_runtime import PendingThoughtRuntime
from gateway.speech_preparation import prepare_speech

_MAX_AUDIO_BYTES = 768 * 1024
_CAPTURE_TTL_SECONDS = 120
_ACTIVE_TURN_TTL_SECONDS = 10 * 60


class DirectConversationError(RuntimeError):
    """A direct robot turn could not complete safely."""


@dataclass(frozen=True)
class VoiceCapture:
    turn_id: str
    audio: bytes
    created_at: float


class VoiceMailbox:
    """Keep at most one unclaimed capture and never redeliver a claim."""

    def __init__(self) -> None:
        self._capture: VoiceCapture | None = None
        self._active_turn_id: str | None = None
        self._active_expires_at: float | None = None
        self._answer_started = False
        self._condition = asyncio.Condition()

    async def submit(self, audio: bytes) -> str:
        if not audio or len(audio) > _MAX_AUDIO_BYTES:
            raise DirectConversationError("audio capture size is invalid")
        async with self._condition:
            self._expire_locked()
            if self._capture is not None or self._active_turn_id is not None:
                raise DirectConversationError("another robot turn is pending")
            turn_id = f"robot:{uuid.uuid4()}"
            self._capture = VoiceCapture(turn_id, audio, time.monotonic())
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


async def speak_direct_answer(
    runtime: PendingThoughtRuntime,
    turn_id: str,
    answer: str,
    voice: str,
) -> None:
    """Prepare speech, perform attention, settle, and play exactly once."""

    answer = answer.strip()
    if not answer:
        raise DirectConversationError("direct answer is empty")
    audio_base64 = await prepare_speech(answer, voice)
    audio = base64.b64decode(audio_base64)
    await runtime.tell_direct(turn_id, audio)
