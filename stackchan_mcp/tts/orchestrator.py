"""TTS orchestration: pick an engine, synthesise, encode, and push.

The orchestrator is the glue between the ``say`` MCP tool (defined in
:mod:`stackchan_mcp.stdio_server`) and the engine implementations
registered in :mod:`stackchan_mcp.tts`. It validates arguments, looks
up an engine, runs the synthesis, encodes the result to Opus, and
hands the frames off to :mod:`stackchan_mcp.audio_stream` for delivery.

The framework half (Engine ABC, registry, validation surface) shipped
in PR1 of Issue #70; PR2 wires the actual VOICEVOX → PCM → Opus →
WebSocket pipeline. The signature stays back-compatible with PR1's
tests: ``gateway`` is keyword-only and may be omitted, in which case
calls that pass validation surface a clear error instead of silently
synthesising audio with no destination.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import nullcontext
from typing import TYPE_CHECKING, Any

from .audio_utils import (
    DEVICE_CHANNELS,
    DEVICE_FRAME_DURATION_MS,
    DEVICE_SAMPLE_RATE,
    encode_opus_frames,
    resample_pcm16_linear,
)
from .base import EngineRegistry, get_registry
from .emoji_expression import detect_emoji_face, strip_emoji_for_plain_tts
from ..esp32_client import stop_tts_after_drain

if TYPE_CHECKING:
    from ..gateway import Gateway

#: Delay between ``tts.start`` and the first audio frame. Firmware arms
#: direct-audio ingress synchronously, while its visible speaking state
#: still settles on the main task.
TTS_START_TRANSITION_DELAY_S = 0.05
DIRECT_PCM_PREROLL_FRAMES = 4

#: Delay after the ``tts.stop`` notification before reasserting an
#: emoji-selected face. Firmware handles stop by scheduling the idle /
#: lip-sync reset on its main task, so this short settle delay lets that
#: queued restore land before the gateway sends the final face update.
TTS_STOP_FACE_REDISPATCH_SETTLE_DELAY_S = 0.05

logger = logging.getLogger(__name__)


#: Built-in default engine name when ``voice`` is omitted from the tool
#: call and ``STACKCHAN_TTS_ENGINE`` is unset. VOICEVOX is the canonical
#: default (Issue #70).
DEFAULT_VOICE = "voicevox"

#: Environment variable that overrides the default engine selected when a
#: ``say`` call omits ``voice``. The per-call ``voice`` argument still
#: takes precedence over this; this only changes the fallback when no
#: ``voice`` is given. Unset → :data:`DEFAULT_VOICE`.
TTS_ENGINE_ENV_VAR = "STACKCHAN_TTS_ENGINE"


class PcmStreamError(RuntimeError):
    """A PCM stream failed after handing audio to the device transport."""

    def __init__(
        self,
        message: str,
        *,
        metrics: dict[str, int] | None = None,
    ) -> None:
        super().__init__(message)
        self.metrics = dict(metrics or {})


def _validate_direct_audio_integrity(
    drain_metrics: dict[str, Any] | None,
    sent: int,
    metrics: dict[str, int],
) -> dict[str, int]:
    integrity_metrics: dict[str, int] = {}
    for name in (
        "accepted_frames",
        "rejected_frames",
        "codec_output_frames",
        "max_codec_write_gap_ms",
    ):
        value = drain_metrics.get(name) if drain_metrics is not None else None
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise PcmStreamError(
                "Firmware returned malformed direct PCM metrics",
                metrics=metrics,
            )
        integrity_metrics[name] = value
    if (
        integrity_metrics["accepted_frames"] != sent
        or integrity_metrics["rejected_frames"] != 0
        or integrity_metrics["codec_output_frames"]
        != integrity_metrics["accepted_frames"]
    ):
        raise PcmStreamError(
            "Direct PCM integrity failed",
            metrics={**metrics, **integrity_metrics},
        )
    return integrity_metrics


def _extract_set_avatar_payload(result: Any) -> dict[str, Any] | None:
    payload = result
    if isinstance(result, dict) and "content" in result:
        content = result.get("content") or []
        if isinstance(content, list) and content:
            text = (
                content[0].get("text")
                if isinstance(content[0], dict)
                else None
            )
            if isinstance(text, str):
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError:
                    return None

    return payload if isinstance(payload, dict) else None


def _set_avatar_payload_error(payload: dict[str, Any]) -> str:
    raw_error = payload.get("error")
    if isinstance(raw_error, dict):
        message = raw_error.get("message")
        if isinstance(message, str) and message.strip():
            return message
    elif isinstance(raw_error, str) and raw_error.strip():
        return raw_error
    elif raw_error:
        return str(raw_error)

    return "set_avatar reported ok=false"


def _resolve_default_engine() -> str:
    """Return the default engine name, honouring ``STACKCHAN_TTS_ENGINE``.

    The environment variable lets an operator make a non-VOICEVOX engine
    (e.g. ``irodori``) the default for ``say`` calls that don't pass an
    explicit ``voice``. A blank or whitespace-only value is ignored so an
    empty export does not silently break engine lookup.
    """
    env_engine = os.getenv(TTS_ENGINE_ENV_VAR)
    if env_engine and env_engine.strip():
        return env_engine.strip()
    return DEFAULT_VOICE


async def _try_set_avatar_face(
    gateway: "Gateway",
    face: str,
) -> tuple[bool, str | None]:
    try:
        result, error = await gateway.esp32.call_tool(
            "self.display.set_avatar", {"face": face}
        )
    except Exception as exc:
        logger.warning("say(): set_avatar(%s) failed: %s", face, exc)
        return False, str(exc)

    if error:
        message = error.get("message", error) if isinstance(error, dict) else error
        logger.warning("say(): set_avatar(%s) failed: %s", face, message)
        return False, str(message)

    payload = _extract_set_avatar_payload(result)
    if payload is not None and payload.get("ok") is False:
        message = _set_avatar_payload_error(payload)
        logger.warning(
            "say(): set_avatar(%s) reported ok=false: %s", face, message
        )
        return False, message

    return True, None


async def _try_set_avatar_face_with_tts_lock(
    gateway: "Gateway",
    face: str,
) -> tuple[bool, str | None]:
    tts_lock = getattr(gateway.esp32, "tts_lock", None)
    lock_ctx = tts_lock if tts_lock is not None else nullcontext()

    async with lock_ctx:
        return await _try_set_avatar_face(gateway, face)


async def synthesize_and_send(
    arguments: dict[str, Any],
    *,
    gateway: "Gateway | None" = None,
    registry: EngineRegistry | None = None,
) -> dict[str, Any]:
    """Synthesise text via a registered engine and push it to the device.

    Args:
        arguments: MCP tool arguments. Recognised keys:

            * ``text`` (required): non-empty string to speak.
            * ``voice``: engine name; when omitted, the default is
              resolved from ``STACKCHAN_TTS_ENGINE`` and otherwise
              :data:`DEFAULT_VOICE`.
            * ``speaker_id``: engine-specific speaker identifier
              (e.g. VOICEVOX speaker).
            * ``reference_audio``: path to a reference audio sample
              (e.g. for Irodori voice cloning, PR3).

        gateway: The :class:`Gateway` instance whose
            :attr:`Gateway.esp32` the audio frames are pushed through.
            Required for the pipeline; left optional in the signature
            so callers can inspect validation errors without setting
            up a gateway (e.g. argument-validation tests).

        registry: Engine registry to look up ``voice`` in. Defaults to
            the process-wide registry. Tests inject a fresh registry
            here to avoid leaking state across cases.

    Returns:
        Dict describing the synthesis: ``engine``, ``text``,
        ``speaker_id``, ``frame_count``, ``sample_rate``,
        ``frame_duration_ms``, ``duration_ms``, plus emoji-expression
        metadata such as ``face`` and ``text_stripped``.

    Raises:
        ValueError: if ``text`` is missing / empty / non-string.
        NotImplementedError: if no engine is registered under ``voice``.
            The message lists the registered engines so callers can
            tell whether they need to install an extra (e.g.
            ``pip install stackchan-mcp[tts]``) or pick a different
            ``voice``.
        RuntimeError: if ``gateway`` is omitted, or if no ESP32 device
            is connected when the orchestrator tries to push frames.
    """
    # Validation runs first so callers can probe argument shape without
    # a real gateway / engine.
    text = arguments.get("text", "")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("'text' is required and must be a non-empty string")

    face = detect_emoji_face(text)

    # An explicit, non-empty ``voice`` argument always wins. Otherwise the
    # default engine is resolved from STACKCHAN_TTS_ENGINE (falling back to
    # DEFAULT_VOICE), so an operator can switch the default without every
    # caller passing ``voice``.
    voice_raw = arguments.get("voice")
    voice = (
        voice_raw
        if isinstance(voice_raw, str) and voice_raw
        else _resolve_default_engine()
    )

    reg = registry if registry is not None else get_registry()
    engine = reg.get(voice)

    if engine is None:
        available = reg.names()
        raise NotImplementedError(
            f"TTS engine '{voice}' is not registered. "
            f"Available engines: {available or '(none)'}. "
            "Install the relevant extra (e.g. "
            "'pip install stackchan-mcp[tts]' for VOICEVOX) and ensure "
            "the corresponding service (e.g. the VOICEVOX HTTP engine) "
            "is reachable."
        )

    if gateway is None:
        raise RuntimeError(
            "synthesize_and_send requires a 'gateway' argument to push "
            "audio frames; this call appears to be a validation probe "
            "without one."
        )

    if not gateway.esp32.device_connected:
        raise RuntimeError(
            "No ESP32 device connected; cannot deliver synthesised audio."
        )

    speaker_id = arguments.get("speaker_id")
    speaker_name = arguments.get("speaker_name")
    reference_audio = arguments.get("reference_audio")

    plain_tts_text = strip_emoji_for_plain_tts(text)
    tts_text = text
    text_stripped = False
    if not getattr(engine, "supports_emoji_style", False):
        text_stripped = plain_tts_text != text
        tts_text = plain_tts_text

    face_dispatched = False
    face_error: str | None = None
    face_redispatched = False
    face_redispatch_error: str | None = None
    should_redispatch_face_after_speech = (
        face is not None and bool(tts_text.strip())
    )

    if not tts_text.strip():
        if face is not None:
            face_dispatched, face_error = await _try_set_avatar_face_with_tts_lock(
                gateway,
                face,
            )
        logger.info(
            "say(): engine=%s speaker=%s speech skipped: text empty after "
            "emoji strip",
            voice,
            speaker_id if speaker_id is not None else "default",
        )
        result = {
            "engine": voice,
            "text": text,
            "speaker_id": speaker_id,
            "frame_count": 0,
            "sample_rate": DEVICE_SAMPLE_RATE,
            "frame_duration_ms": DEVICE_FRAME_DURATION_MS,
            "duration_ms": 0,
            "face": face,
            "face_dispatched": face_dispatched,
            "face_error": face_error,
            "face_redispatched": face_redispatched,
            "face_redispatch_error": face_redispatch_error,
            "text_stripped": text_stripped,
            "spoke": False,
            "reason": "text empty after emoji strip",
        }
        if text_stripped:
            result["tts_text"] = tts_text
        return result

    # Engine failures (HTTP errors from VOICEVOX, malformed WAV from
    # the synthesiser, etc.) are translated to RuntimeError so the
    # MCP layer's narrow exception filter still produces clean error
    # JSON. Validation errors (ValueError) are kept distinct so bad
    # arguments stay separable from operational degradation.
    try:
        pcm = await engine.synthesize(
            tts_text,
            speaker_id=speaker_id,
            speaker_name=speaker_name,
            reference_audio=reference_audio,
        )
    except ValueError:
        raise
    except Exception as exc:
        raise RuntimeError(
            f"TTS engine '{voice}' failed: {exc}"
        ) from exc

    if not pcm:
        # An engine returning no PCM is a bug, not a runtime condition;
        # surface it to the caller rather than silently sending zero
        # frames (which would look like the device "ignored" the call).
        raise RuntimeError(
            f"Engine '{voice}' produced no PCM data for the given text."
        )

    async def dispatch_face_before_first_frame() -> None:
        nonlocal face_dispatched, face_error
        if face is not None:
            face_dispatched, face_error = await _try_set_avatar_face(
                gateway,
                face,
            )

    async def redispatch_face_after_playback() -> None:
        nonlocal face_redispatched, face_redispatch_error
        if face is None:
            return
        await asyncio.sleep(TTS_STOP_FACE_REDISPATCH_SETTLE_DELAY_S)
        face_redispatched, face_redispatch_error = await _try_set_avatar_face(
            gateway,
            face,
        )

    # Hand the PCM off to the shared encode-and-push path. Engines that
    # have already resampled to DEVICE_SAMPLE_RATE (the documented
    # TTSEngine contract) need no further conversion here.
    result = await send_pcm_audio(
        gateway,
        pcm,
        source_label=f"engine:{voice}",
        before_first_frame=(
            dispatch_face_before_first_frame if face is not None else None
        ),
        after_playback_complete=(
            redispatch_face_after_playback
            if should_redispatch_face_after_speech
            else None
        ),
    )

    logger.info(
        "say(): engine=%s speaker=%s frames=%d duration_ms=%d",
        voice,
        speaker_id if speaker_id is not None else "default",
        result["frame_count"],
        result["duration_ms"],
    )

    response = {
        "engine": voice,
        "text": text,
        "speaker_id": speaker_id,
        "frame_count": result["frame_count"],
        "sample_rate": result["sample_rate"],
        "frame_duration_ms": result["frame_duration_ms"],
        "duration_ms": result["duration_ms"],
        "face": face,
        "face_dispatched": face_dispatched,
        "face_error": face_error,
        "face_redispatched": face_redispatched,
        "face_redispatch_error": face_redispatch_error,
        "text_stripped": text_stripped,
        "spoke": True,
    }
    if text_stripped:
        response["tts_text"] = tts_text
    return response


async def send_pcm_audio(
    gateway: "Gateway",
    pcm: bytes,
    *,
    source_rate: int = DEVICE_SAMPLE_RATE,
    source_label: str = "external",
    before_first_frame: Callable[[], Awaitable[None]] | None = None,
    after_playback_complete: Callable[[], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    """Encode mono PCM and push as Opus frames to the connected device.

    This is the shared back-half of the TTS pipeline. ``synthesize_and_send``
    delegates here after running its engine; external producers (an HTTP
    PCM bridge, a sound-effect player, another voice stack like the SAIVerse
    voice-tts addon) can call this directly to push pre-synthesised audio
    without going through a registered :class:`TTSEngine`.

    Args:
        gateway: The :class:`Gateway` instance whose
            :attr:`Gateway.esp32` the audio frames are pushed through.
        pcm: Signed-16-bit little-endian mono PCM bytes. Must be
            non-empty.
        source_rate: Sample rate of ``pcm``. Defaults to
            :data:`DEVICE_SAMPLE_RATE` (16 kHz). When the source is at a
            different rate (e.g. voice-tts produces 32 kHz) the bytes
            are resampled linearly before Opus encoding; engines that
            already resample to the device rate internally should leave
            this at the default.
        source_label: Label that appears in the orchestrator log line so
            external callers can be traced separately from engine-driven
            synthesis (e.g. ``"voice-tts"``, ``"sfx:notification"``).
        before_first_frame: Internal hook for ``say()`` side effects that
            must be serialized with speech delivery.
        after_playback_complete: Internal hook for side effects that must
            run after the stop notification while the TTS lock is still
            held. Skipped if frame delivery is cancelled or interrupted.

    Returns:
        Dict describing the push: ``source``, ``frame_count``,
        ``sample_rate``, ``frame_duration_ms``, ``duration_ms``.
        ``sample_rate`` is always :data:`DEVICE_SAMPLE_RATE` because that
        is what the device actually decoded, regardless of the source
        rate.

    Raises:
        RuntimeError: if ``pcm`` is empty, ``gateway`` is missing, no
            device is connected, the negotiated protocol is not v1, Opus
            encoding fails, or the device disconnects mid-stream.
    """
    if not pcm:
        # Surface empty input as a clear bug rather than silently doing
        # nothing — same reasoning as the "engine produced no PCM" guard
        # in synthesize_and_send.
        raise RuntimeError(
            f"send_pcm_audio: PCM payload was empty (source={source_label!r})."
        )

    # Validate source_rate before it reaches resample_pcm16_linear.
    # The resampler computes ``n_dst = n_src * dst_rate // src_rate``,
    # which raises ZeroDivisionError on 0 and produces nonsense for
    # negatives — neither of which the caller's narrow ``RuntimeError``
    # filter translates cleanly to an MCP-facing error. Catch invalid
    # rates here so non-engine producers (HTTP /pcm bridges,
    # external voice stacks) that forward unvalidated request params
    # get a deterministic error instead of a raw stack trace.
    if not isinstance(source_rate, int) or source_rate <= 0:
        raise RuntimeError(
            f"send_pcm_audio: source_rate must be a positive integer, "
            f"got {source_rate!r}."
        )

    if gateway is None:
        raise RuntimeError(
            "send_pcm_audio requires a 'gateway' argument to push audio "
            "frames; this call appears to be a validation probe without one."
        )

    if not gateway.esp32.device_connected:
        raise RuntimeError(
            "No ESP32 device connected; cannot deliver audio."
        )

    # Resample to the device's rate before Opus encoding. ``encode_opus_frames``
    # expects samples at DEVICE_SAMPLE_RATE; passing a different rate would
    # produce frames that play back too fast / too slow on the device.
    if source_rate != DEVICE_SAMPLE_RATE:
        pcm = resample_pcm16_linear(pcm, source_rate, DEVICE_SAMPLE_RATE)

    # Encode -> push. Materialising the frame list before pushing keeps
    # the count reportable and makes it easy to short-circuit if Opus
    # encoding fails before any audio reaches the wire.
    try:
        opus_frames = list(encode_opus_frames(pcm))
    except Exception as exc:
        raise RuntimeError(f"Opus encoding failed: {exc}") from exc

    # Bracket the binary audio frames in TTS start/stop notifications.
    # The device firmware (Application::OnIncomingAudio) only accepts
    # binary audio frames while in kDeviceStateSpeaking, which is
    # entered on receipt of {"type":"tts","state":"start"} and exited
    # on "stop". Without these notifications the audio frames are
    # silently discarded.
    #
    # The whole start → frames → stop block runs under the device's
    # TTS lock so two concurrent pushes can't interleave their Opus
    # frames on the same WebSocket or overlap their state notifications.
    tts_lock = getattr(gateway.esp32, "tts_lock", None)
    lock_ctx = tts_lock if tts_lock is not None else nullcontext()

    sent = 0
    push_error: ConnectionError | None = None
    stop_error: Exception | None = None
    drain_metrics: dict[str, Any] | None = None
    verify_integrity = False
    async with lock_ctx:
        connection = gateway.esp32.connection
        if connection is None or not connection.connected:
            raise RuntimeError(
                "No ESP32 device connected; cannot deliver audio."
            )
        verify_integrity = getattr(connection, "direct_audio_metrics", False)
        try:
            await connection.send_tts_state("start")
        except ConnectionError as exc:
            raise RuntimeError(
                f"Device disconnected before TTS start notification: {exc}"
            ) from exc

        playback_complete = False
        try:
            await asyncio.sleep(TTS_START_TRANSITION_DELAY_S)
            if before_first_frame is not None:
                await before_first_frame()

            # Burst the firmware's four-frame reserve, then replenish it
            # at the device's consumption rate.
            frame_period_s = DEVICE_FRAME_DURATION_MS / 1000.0
            loop = asyncio.get_running_loop()
            next_send_time = 0.0
            for index, frame in enumerate(opus_frames):
                if index >= DIRECT_PCM_PREROLL_FRAMES:
                    delay = next_send_time - loop.time()
                    if delay > 0:
                        await asyncio.sleep(delay)
                send_started = loop.time()
                try:
                    await connection.send_audio_frame(frame)
                except ConnectionError as exc:
                    # Stop pushing on the first disconnect, but fall
                    # through to the stop notification (see finally) so
                    # that *if* the device is somehow still listening
                    # it returns to idle rather than staying in speaking
                    # forever.
                    push_error = exc
                    break
                sent += 1
                next_send_time = send_started + frame_period_s
            playback_complete = push_error is None
        finally:
            try:
                if connection is not None and connection.connected:
                    drain_metrics = await stop_tts_after_drain(connection)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                stop_error = exc

        if (
            playback_complete
            and push_error is None
            and stop_error is None
            and after_playback_complete is not None
        ):
            await after_playback_complete()

    if stop_error is not None:
        raise RuntimeError(f"TTS drain failed: {stop_error}") from stop_error
    if push_error is not None:
        raise RuntimeError(
            f"Device disconnected after sending "
            f"{sent}/{len(opus_frames)} frames: {push_error}"
        ) from push_error

    duration_ms = sent * DEVICE_FRAME_DURATION_MS
    metrics = {
        "frame_count": sent,
        "frame_duration_ms": DEVICE_FRAME_DURATION_MS,
        "duration_ms": duration_ms,
    }
    integrity_metrics = (
        _validate_direct_audio_integrity(drain_metrics, sent, metrics)
        if verify_integrity
        else {}
    )

    logger.info(
        "send_pcm_audio: source=%s frames=%d duration_ms=%d",
        source_label,
        sent,
        duration_ms,
    )

    return {
        "source": source_label,
        "sample_rate": DEVICE_SAMPLE_RATE,
        **metrics,
        **integrity_metrics,
    }


async def send_pcm_stream(
    gateway: "Gateway",
    pcm_chunks: AsyncIterator[bytes],
    *,
    source_rate: int = DEVICE_SAMPLE_RATE,
    source_label: str = "stream",
    expected_session_id: str | None = None,
) -> dict[str, Any]:
    """Encode one mono PCM stream into fixed 60 ms Opus frames."""

    if gateway is None:
        raise RuntimeError("send_pcm_stream requires a gateway")
    if not isinstance(source_rate, int) or source_rate <= 0:
        raise RuntimeError("send_pcm_stream requires a positive sample rate")
    try:
        import opuslib  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("opuslib is required for streamed audio") from exc

    samples_per_frame = (
        DEVICE_SAMPLE_RATE * DEVICE_FRAME_DURATION_MS // 1000
    )
    bytes_per_frame = samples_per_frame * 2
    source_samples_per_frame = (
        source_rate * DEVICE_FRAME_DURATION_MS // 1000
    )
    if source_samples_per_frame <= 0:
        raise RuntimeError("send_pcm_stream source rate is too low")
    source_bytes_per_frame = source_samples_per_frame * 2
    encoder = opuslib.Encoder(
        DEVICE_SAMPLE_RATE, DEVICE_CHANNELS, opuslib.APPLICATION_VOIP
    )
    sent = 0
    first_frame_ms: int | None = None
    buffer = bytearray()
    preroll: list[bytes] = []
    connection: Any = None
    next_send_time = 0.0
    started = False
    push_error: Exception | None = None
    stop_error: Exception | None = None
    drain_metrics: dict[str, Any] | None = None

    def partial_metrics() -> dict[str, int]:
        metrics = {
            "frame_count": sent,
            "frame_duration_ms": DEVICE_FRAME_DURATION_MS,
            "duration_ms": sent * DEVICE_FRAME_DURATION_MS,
        }
        if first_frame_ms is not None:
            metrics["gateway_first_audio_frame_sent_ms"] = first_frame_ms
        return metrics

    def convert_frame(source_frame: bytes) -> bytes:
        if source_rate != DEVICE_SAMPLE_RATE:
            source_frame = resample_pcm16_linear(
                source_frame,
                source_rate,
                DEVICE_SAMPLE_RATE,
            )
        return source_frame[:bytes_per_frame].ljust(bytes_per_frame, b"\x00")

    async def send_frame(opus_frame: bytes, *, paced: bool) -> bool:
        nonlocal first_frame_ms, next_send_time, sent, push_error
        loop = asyncio.get_running_loop()
        if paced:
            delay = next_send_time - loop.time()
            if delay > 0:
                await asyncio.sleep(delay)
        send_started = loop.time()
        try:
            await connection.send_audio_frame(opus_frame)
        except ConnectionError as exc:
            push_error = exc
            return False
        sent += 1
        if first_frame_ms is None:
            first_frame_ms = time.time_ns() // 1_000_000
        next_send_time = send_started + (
            DEVICE_FRAME_DURATION_MS / 1000.0
        )
        return True

    async def start_preroll() -> bool:
        nonlocal started
        await connection.send_tts_state("start")
        started = True
        await asyncio.sleep(TTS_START_TRANSITION_DELAY_S)
        for opus_frame in preroll:
            if not await send_frame(opus_frame, paced=False):
                return False
        preroll.clear()
        return True

    async def submit_frame(pcm_frame: bytes, *, final: bool = False) -> bool:
        try:
            opus_frame = encoder.encode(pcm_frame, samples_per_frame)
        except Exception as exc:
            raise RuntimeError(f"Opus encoding failed: {exc}") from exc
        if not started:
            preroll.append(opus_frame)
            if len(preroll) < DIRECT_PCM_PREROLL_FRAMES and not final:
                return True
            return await start_preroll()
        return await send_frame(opus_frame, paced=True)

    tts_lock = getattr(gateway.esp32, "tts_lock", None)
    lock_context = tts_lock if tts_lock is not None else nullcontext()
    async with lock_context:
        connection = gateway.esp32.connection
        if connection is None or not connection.connected:
            raise RuntimeError("No ESP32 device connected")
        if (
            expected_session_id is not None
            and connection.session_id != expected_session_id
        ):
            raise RuntimeError("ESP32 session changed before streamed playback")
        verify_integrity = getattr(connection, "direct_audio_metrics", False)
        try:
            async for chunk in pcm_chunks:
                buffer.extend(chunk)
                while len(buffer) >= source_bytes_per_frame:
                    source_frame = bytes(buffer[:source_bytes_per_frame])
                    del buffer[:source_bytes_per_frame]
                    if not await submit_frame(convert_frame(source_frame)):
                        break
                if push_error is not None:
                    break
            if push_error is None and buffer:
                if len(buffer) % 2:
                    buffer.pop()
                if buffer:
                    source_frame = bytes(buffer).ljust(
                        source_bytes_per_frame,
                        b"\x00",
                    )
                    await submit_frame(convert_frame(source_frame), final=True)
            if push_error is None and preroll:
                await start_preroll()
        except Exception as exc:
            push_error = exc
        finally:
            if started and connection.connected:
                try:
                    drain_metrics = await stop_tts_after_drain(connection)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    stop_error = exc

    if stop_error is not None:
        raise PcmStreamError(
            f"PCM drain failed after {sent} frames: {stop_error}",
            metrics=partial_metrics(),
        ) from stop_error
    if push_error is not None:
        raise PcmStreamError(
            f"PCM stream failed after {sent} frames: {push_error}",
            metrics=partial_metrics(),
        ) from push_error
    if not started:
        return {
            "source": source_label,
            "frame_count": 0,
            "sample_rate": DEVICE_SAMPLE_RATE,
            "frame_duration_ms": DEVICE_FRAME_DURATION_MS,
            "duration_ms": 0,
        }
    integrity_metrics = (
        _validate_direct_audio_integrity(
            drain_metrics,
            sent,
            partial_metrics(),
        )
        if verify_integrity
        else {}
    )

    result = {
        "source": source_label,
        "frame_count": sent,
        "sample_rate": DEVICE_SAMPLE_RATE,
        "frame_duration_ms": DEVICE_FRAME_DURATION_MS,
        "duration_ms": sent * DEVICE_FRAME_DURATION_MS,
        "gateway_playback_completed_ms": time.time_ns() // 1_000_000,
    }
    if first_frame_ms is not None:
        result["gateway_first_audio_frame_sent_ms"] = first_frame_ms
    result.update(integrity_metrics)
    return result
