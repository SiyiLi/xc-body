"""Serialized body operations for conversation, offers, and expressions."""

from __future__ import annotations

import asyncio
import base64
import http.client
import json
import logging
import time
import urllib.request
from collections.abc import Callable, Iterator, Mapping
from threading import RLock
from typing import Any
from urllib.parse import urlsplit

from gateway.pending_thought import OfferFlow
from gateway.stackchan_event_session import create_stackchan_client_session

ToolCaller = Callable[[str, Mapping[str, object]], object]
_MISSING = object()
logger = logging.getLogger(__name__)


class InteractionRuntimeError(RuntimeError):
    """The interaction runtime could not complete a body operation."""

    def __init__(
        self,
        message: str,
        *,
        metrics: Mapping[str, int] | None = None,
    ) -> None:
        super().__init__(message)
        self.metrics = dict(metrics or {})


def _stream_playback_metrics(playback: Mapping[str, object]) -> dict[str, int]:
    """Extract content-free PCM playback metrics from one gateway response."""

    metrics: dict[str, int] = {}
    for source, target in (
        ("duration_ms", "playback_audio_ms"),
        ("frame_count", "streamed_audio_frames"),
        (
            "gateway_first_audio_frame_sent_ms",
            "gateway_first_audio_frame_sent_ms",
        ),
        ("gateway_playback_completed_ms", "gateway_playback_completed_ms"),
        ("accepted_frames", "firmware_accepted_audio_frames"),
        ("rejected_frames", "firmware_rejected_audio_frames"),
        ("codec_output_frames", "firmware_codec_output_frames"),
        ("max_codec_write_gap_ms", "firmware_max_codec_write_gap_ms"),
    ):
        value = playback.get(source)
        if isinstance(value, int) and value >= 0:
            metrics[target] = value
    return metrics


class XcBodyInteractionBody:
    """Synchronous offer and conversation operations over one body lane."""

    def __init__(
        self,
        call_tool: ToolCaller,
        *,
        playback_url: str | None = None,
        streaming_url: str | None = None,
        playback_token: str = "",
    ):
        self._call_tool = call_tool
        self._playback_url = playback_url
        self._streaming_url = streaming_url
        self._playback_token = playback_token
        self._device_session_id: str | None = None
        self._operation_lock = RLock()

    def mark_device_ready(self, session_id: str) -> None:
        """Bind readiness to one initialized device session."""

        if not isinstance(session_id, str) or not session_id:
            raise InteractionRuntimeError(
                "device readiness requires a device session"
            )
        self._device_session_id = session_id

    def is_ready(self) -> bool:
        """Return whether the initialized device session is still active."""

        if self._device_session_id is None:
            return False
        try:
            status = self._call("get_status", {})
        except InteractionRuntimeError:
            return False
        return ready_device_session_id(status) == self._device_session_id

    def reconcile_offer_state(self, offer_pending: bool) -> None:
        """Align the firmware screensaver gate with pending state."""

        with self._operation_lock:
            self.set_offer_pending(offer_pending)

    def tell(self, thought_id: str, audio_base64: str) -> None:
        """Play prepared audio after firmware reports acknowledgment."""

        with self._operation_lock:
            self._require_ready()
            self._play_audio(audio_base64, thought_id)

    def perform_expression(self, expression: str) -> Mapping[str, object]:
        """Run one firmware-owned named expression in the body lane."""

        with self._operation_lock:
            return self._perform_expression_locked(expression)

    def tell_direct_stream(
        self,
        turn_id: str,
        expression: str,
        pcm: Any,
    ) -> dict[str, int]:
        """Run one expression, then hold the lane through PCM playback."""

        with self._operation_lock:
            started = time.monotonic()
            self._perform_expression_locked(expression)
            expression_ms = round((time.monotonic() - started) * 1000)
            expression_completed_ms = time.time_ns() // 1_000_000
            session_id = self._device_session_id
            if session_id is None:
                raise InteractionRuntimeError(
                    "device session is unavailable"
                )
            metrics = {
                "expression_ms": expression_ms,
                "expression_completed_ms": expression_completed_ms,
            }
            try:
                pcm.wait_for_playable()
            except Exception as exc:
                raise InteractionRuntimeError(
                    f"direct stream: {type(exc).__name__}",
                    metrics=metrics,
                ) from exc
            started = time.monotonic()
            try:
                playback = self._play_pcm_stream(
                    pcm.iter_pcm_chunks(),
                    turn_id,
                    session_id,
                )
            except InteractionRuntimeError as exc:
                metrics["playback_request_ms"] = round(
                    (time.monotonic() - started) * 1000
                )
                metrics.update(exc.metrics)
                raise InteractionRuntimeError(
                    str(exc),
                    metrics=metrics,
                ) from exc
            except Exception as exc:
                metrics["playback_request_ms"] = round(
                    (time.monotonic() - started) * 1000
                )
                raise InteractionRuntimeError(
                    f"stream audio: {type(exc).__name__}",
                    metrics=metrics,
                ) from exc
            metrics["playback_request_ms"] = round(
                (time.monotonic() - started) * 1000
            )
            metrics.update(_stream_playback_metrics(playback))
            return metrics

    def _perform_expression_locked(
        self,
        expression: str,
    ) -> Mapping[str, object]:
        self._require_ready()
        return self._call(
            "perform_expression",
            {"expression": expression},
        )

    def set_offer_pending(self, pending: bool) -> None:
        """Best-effort hint controlling the firmware idle screensaver."""

        with self._operation_lock:
            try:
                self._require_ready()
                self._call("set_offer_pending", {"pending": pending})
            except InteractionRuntimeError as exc:
                logger.warning("offer-state synchronization failed: %s", exc)

    def _play_audio(self, audio_base64: str, thought_id: str) -> None:
        self._play_audio_bytes(base64.b64decode(audio_base64), thought_id)

    def _play_audio_bytes(
        self,
        payload: bytes,
        thought_id: str,
    ) -> Mapping[str, object]:
        if not self._playback_url:
            raise InteractionRuntimeError("audio playback URL is not configured")
        request = urllib.request.Request(
            self._playback_url,
            data=payload,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._playback_token}",
                "Content-Type": "application/octet-stream",
                "X-Message-Id": thought_id,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                result = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            raise InteractionRuntimeError(f"play audio: {exc}") from exc
        if result.get("ok") is not True:
            raise InteractionRuntimeError(
                f"play audio: {result.get('error', 'playback failed')}"
            )
        return result

    def _play_pcm_stream(
        self,
        chunks: Iterator[bytes],
        turn_id: str,
        session_id: str,
    ) -> Mapping[str, object]:
        if not self._streaming_url:
            raise InteractionRuntimeError(
                "PCM playback URL is not configured"
            )
        parsed = urlsplit(self._streaming_url)
        if not parsed.hostname:
            raise InteractionRuntimeError("PCM playback URL is invalid")
        target = parsed.path or "/"
        if parsed.query:
            target = f"{target}?{parsed.query}"
        connection_type = (
            http.client.HTTPSConnection
            if parsed.scheme == "https"
            else http.client.HTTPConnection
        )
        connection = connection_type(
            parsed.hostname,
            parsed.port,
            timeout=300,
        )
        stream_error: Exception | None = None
        try:
            connection.putrequest("POST", target)
            connection.putheader(
                "Authorization",
                f"Bearer {self._playback_token}",
            )
            connection.putheader("Content-Type", "application/octet-stream")
            connection.putheader("Transfer-Encoding", "chunked")
            connection.putheader("X-Message-Id", turn_id)
            connection.putheader("X-StackChan-Session", session_id)
            connection.putheader("X-Sample-Rate", "16000")
            connection.putheader("X-Channels", "1")
            connection.endheaders()
            try:
                for chunk in chunks:
                    if not chunk:
                        continue
                    connection.send(f"{len(chunk):X}\r\n".encode("ascii"))
                    connection.send(chunk)
                    connection.send(b"\r\n")
            except Exception as exc:
                stream_error = exc
            connection.send(b"0\r\n\r\n")
            response = connection.getresponse()
            result = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            raise InteractionRuntimeError(
                f"stream audio: {type(exc).__name__}"
            ) from exc
        finally:
            connection.close()
        metrics = _stream_playback_metrics(result) if isinstance(
            result,
            Mapping,
        ) else {}
        if not isinstance(result, Mapping) or result.get("ok") is not True:
            detail = result.get("error") if isinstance(result, Mapping) else None
            raise InteractionRuntimeError(
                f"stream audio: {detail or 'playback failed'}",
                metrics=metrics,
            )
        if stream_error is not None:
            metrics.pop("gateway_playback_completed_ms", None)
            raise InteractionRuntimeError(
                f"stream audio: {type(stream_error).__name__}",
                metrics=metrics,
            ) from stream_error
        return result

    def _require_ready(self) -> None:
        if not self.is_ready():
            raise InteractionRuntimeError(
                "device is not ready for the current session"
            )

    def _call(
        self, name: str, arguments: Mapping[str, object]
    ) -> Mapping[str, object]:
        try:
            result = self._call_tool(name, arguments)
        except Exception as exc:
            raise InteractionRuntimeError(f"{name}: {exc}") from exc
        payload = _tool_payload(result)
        if payload.get("ok") is False or "error" in payload:
            message = payload.get("error") or payload.get("message")
            raise InteractionRuntimeError(
                f"{name}: {message or 'XC Body tool failed'}"
            )
        return payload


class SessionToolCaller:
    """Bridge synchronous body ports to one persistent async MCP session."""

    def __init__(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop
        self._session: Any | None = None

    def bind(self, session: Any) -> None:
        self._session = session

    def unbind(self, session: Any) -> None:
        if self._session is session:
            self._session = None

    def __call__(
        self, name: str, arguments: Mapping[str, object]
    ) -> object:
        if self._session is None:
            raise InteractionRuntimeError("MCP session is not bound")
        future = asyncio.run_coroutine_threadsafe(
            self._session.call_tool(name, arguments=dict(arguments)),
            self._loop,
        )
        return future.result()


class InteractionRuntime:
    """Own one state machine for the lifetime of an upstream MCP session."""

    def __init__(
        self,
        *,
        playback_url: str | None = None,
        streaming_url: str | None = None,
        playback_token: str = "",
    ) -> None:
        self._playback_url = playback_url
        self._streaming_url = streaming_url
        self._playback_token = playback_token
        self.machine: OfferFlow | None = None
        self.body: XcBodyInteractionBody | None = None
        self._caller: SessionToolCaller | None = None

    def mark_device_ready(self, session_id: str) -> None:
        """Record the initialized device session."""

        if self.body is None:
            raise InteractionRuntimeError("runtime session is not initialized")
        self.body.mark_device_ready(session_id)

    async def is_ready(self) -> bool:
        """Check readiness without blocking the service event loop."""

        if self.machine is None or self.body is None:
            return False
        return await asyncio.to_thread(self.body.is_ready)

    async def pending_thought_id(self) -> str | None:
        """Return the current unexpired offer."""

        if self.machine is None:
            return None
        return await asyncio.to_thread(
            lambda: self.machine.pending_thought_id
        )

    async def reconcile_offer_state(self) -> str | None:
        """Expire stale offers and restore the firmware display hint."""

        pending_id = await self.pending_thought_id()
        if self.body is not None:
            await asyncio.to_thread(
                self.body.reconcile_offer_state,
                pending_id is not None,
            )
        return pending_id

    async def tell_direct_stream(
        self,
        turn_id: str,
        expression: str,
        pcm: Any,
    ) -> dict[str, int]:
        """Keep direct expression and streamed playback in one body lane."""

        if self.machine is None or self.body is None:
            raise InteractionRuntimeError("runtime session is not initialized")
        return await asyncio.to_thread(
            self.body.tell_direct_stream,
            turn_id,
            expression,
            pcm,
        )

    async def perform_expression(
        self,
        expression: str,
    ) -> Mapping[str, object]:
        """Run one named expression without blocking the service loop."""

        if self.machine is None or self.body is None:
            raise InteractionRuntimeError("runtime session is not initialized")
        return await asyncio.to_thread(
            self.body.perform_expression,
            expression,
        )

    async def consider_thought(
        self, payload: Mapping[str, object]
    ):
        """Submit without blocking the MCP session's receive loop."""

        if self.machine is None:
            raise InteractionRuntimeError("runtime session is not initialized")
        return await asyncio.to_thread(self.machine.submit, payload)

    def create_session(
        self,
        read_stream: Any,
        write_stream: Any,
        loop: asyncio.AbstractEventLoop,
    ) -> Any:
        """Create the event-aware session and bind its concrete body ports."""

        if self.machine is None:
            self._caller = SessionToolCaller(loop)
            self.body = XcBodyInteractionBody(
                self._caller,
                playback_url=self._playback_url,
                streaming_url=self._streaming_url,
                playback_token=self._playback_token,
            )
            self.machine = OfferFlow(
                self.body,
                self.body,
                offer_display_port=self.body,
            )
        session = create_stackchan_client_session(
            read_stream,
            write_stream,
            self.machine,
        )
        self._caller.bind(session)
        return session

    def unbind_session(self, session: Any) -> None:
        if self._caller is not None:
            self._caller.unbind(session)


def ready_device_session_id(status: object) -> str | None:
    """Return the session ID only for a connected, initialized device."""

    payload = _tool_payload(status)
    session_id = payload.get("session_id")
    if (
        payload.get("connected") is not True
        or payload.get("initialized") is not True
        or not isinstance(session_id, str)
        or not session_id
    ):
        return None
    return session_id


def _tool_payload(result: object) -> Mapping[str, object]:
    """Extract one mapping from plain or MCP SDK tool results."""

    structured = _field(result, "structuredContent", "structured_content")
    if isinstance(structured, Mapping):
        return structured
    content = _field(result, "content")
    if content is not _MISSING and isinstance(content, list):
        for block in content:
            text = _field(block, "text")
            if not isinstance(text, str):
                continue
            try:
                decoded = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(decoded, Mapping):
                return decoded
    if isinstance(result, Mapping):
        return result
    raise InteractionRuntimeError("tool returned no structured result")


def _field(value: object, *names: str) -> object:
    for name in names:
        if isinstance(value, Mapping) and name in value:
            return value[name]
        attribute = getattr(value, name, _MISSING)
        if attribute is not _MISSING:
            return attribute
    return _MISSING
