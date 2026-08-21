"""Transient summary-to-audio boundary for native OpenClaw integration."""

from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

from gateway.pending_thought import (
    PendingThoughtError,
    decode_prepared_audio,
    validate_thought_id,
)
from gateway.pending_thought_runtime import (
    PendingThoughtRuntime,
    PendingThoughtRuntimeError,
)
from gateway.speech_preparation import (
    DEFAULT_VOICE,
    VOICE_ENV,
    prepare_speech,
)

_ALLOWED_FIELDS = frozenset(("version", "thought_id", "summary"))
_REQUIRED_FIELDS = _ALLOWED_FIELDS
_MAX_SUMMARY_CHARS = 150
SpeechPreparer = Callable[[str, str], Awaitable[str]]
logger = logging.getLogger(__name__)


class ThoughtSummaryError(ValueError):
    """A native OpenClaw summary request is invalid."""


@dataclass(frozen=True)
class ThoughtSummary:
    version: str
    thought_id: str
    summary: str


def _contains_chinese(text: str) -> bool:
    return any(
        "\u3400" <= character <= "\u4dbf"
        or "\u4e00" <= character <= "\u9fff"
        or "\uf900" <= character <= "\ufaff"
        for character in text
    )


def parse_thought_summary(payload: Mapping[str, object]) -> ThoughtSummary:
    """Validate the separate plaintext request contract."""

    if not isinstance(payload, Mapping):
        raise ThoughtSummaryError("summary request must be a JSON object")
    fields = set(payload)
    if fields != _REQUIRED_FIELDS:
        raise ThoughtSummaryError("summary request fields are invalid")
    if payload["version"] != "v1":
        raise ThoughtSummaryError("summary request version is unsupported")
    try:
        thought_id = validate_thought_id(payload["thought_id"])
    except PendingThoughtError as exc:
        raise ThoughtSummaryError("thought_id has an invalid format") from exc
    summary = payload["summary"]
    if not isinstance(summary, str):
        raise ThoughtSummaryError("summary must be a string")
    summary = summary.strip()
    if (
        not summary
        or len(summary) > _MAX_SUMMARY_CHARS
        or not _contains_chinese(summary)
    ):
        raise ThoughtSummaryError("summary must be bounded Chinese text")
    return ThoughtSummary(
        version="v1",
        thought_id=thought_id,
        summary=summary,
    )


def load_summary_voice(
    environ: Mapping[str, str] | None = None,
) -> str:
    """Load the accepted voice without introducing another default."""

    values = os.environ if environ is None else environ
    voice = values.get(VOICE_ENV, DEFAULT_VOICE).strip()
    if not voice:
        raise ThoughtSummaryError(f"{VOICE_ENV} must not be empty")
    return voice


async def handle_summary_request(
    runtime: PendingThoughtRuntime,
    payload: Mapping[str, object],
    *,
    voice: str,
    speech_preparer: SpeechPreparer = prepare_speech,
) -> tuple[int, dict[str, object]]:
    """Prepare audio before entering the existing offer transition."""

    try:
        request = parse_thought_summary(payload)
    except ThoughtSummaryError:
        return 400, {"ok": False, "error": "invalid_request"}

    if await runtime.pending_thought_id() is not None:
        return 200, {
            "ok": True,
            "thought_id": request.thought_id,
            "state": "ignored",
        }
    if not await runtime.is_ready():
        return 503, {"ok": False, "error": "body_unavailable"}

    try:
        audio_base64 = await speech_preparer(request.summary, voice)
        decode_prepared_audio(audio_base64)
    except Exception as exc:
        logger.warning(
            "speech preparation failed (%s)",
            type(exc).__name__,
        )
        return 502, {"ok": False, "error": "speech_preparation_failed"}

    try:
        outcome = await runtime.consider_thought(
            {
                "version": request.version,
                "thought_id": request.thought_id,
                "decision": "offer",
                "audio_base64": audio_base64,
            }
        )
    except PendingThoughtRuntimeError:
        return 503, {"ok": False, "error": "body_unavailable"}
    except PendingThoughtError:
        return 409, {"ok": False, "error": "offer_rejected"}
    return 200, {
        "ok": True,
        "thought_id": outcome.thought_id,
        "state": outcome.state,
    }
