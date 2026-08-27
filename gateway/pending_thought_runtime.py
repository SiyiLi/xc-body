"""Concrete Milestone 2 runtime adapters for the XC Body gateway."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
import urllib.request
from collections.abc import Callable, Mapping
from threading import RLock
from typing import Any

from gateway.pending_thought import KnockWaitTell
from gateway.stackchan_event_session import create_stackchan_client_session

ToolCaller = Callable[[str, Mapping[str, object]], object]
_MISSING = object()
logger = logging.getLogger(__name__)


class PendingThoughtRuntimeError(RuntimeError):
    """The persistent thought runtime could not complete a body operation."""


class StackChanThoughtBody:
    """Synchronous knock/tell ports backed by an injected MCP tool caller."""

    def __init__(
        self,
        call_tool: ToolCaller,
        *,
        playback_url: str | None = None,
        playback_token: str = "",
    ):
        self._call_tool = call_tool
        self._playback_url = playback_url
        self._playback_token = playback_token
        self._verified_session_id: str | None = None
        self._base_view: str | None = None
        self._operation_lock = RLock()
        self._synced_offer_pending: bool | None = None

    def mark_avatar_ready(self, session_id: str) -> None:
        """Bind reviewed-avatar verification to one device session."""

        if not isinstance(session_id, str) or not session_id:
            raise PendingThoughtRuntimeError(
                "reviewed avatar verification requires a device session"
            )
        if session_id != self._verified_session_id:
            self._base_view = None
            self._synced_offer_pending = None
        self._verified_session_id = session_id

    def is_ready(self) -> bool:
        """Return whether the verified avatar is active in this session."""

        if self._verified_session_id is None:
            return False
        try:
            status = self._call("get_status", {})
        except PendingThoughtRuntimeError:
            return False
        return ready_device_session_id(status) == self._verified_session_id

    def set_base_view(self) -> None:
        """Keep the idle avatar visible without redundant device calls."""

        with self._operation_lock:
            if self._base_view == "avatar":
                return
            self._call("set_avatar", {"face": "idle"})
            self._base_view = "avatar"

    def restore_base_view(self) -> None:
        """Force the base view after a transient interaction ends."""

        with self._operation_lock:
            self._base_view = None
            self.set_base_view()

    def reconcile_base_view(self, offer_pending: bool) -> None:
        """Align the avatar and pending-offer gate as one body operation."""

        with self._operation_lock:
            self.set_base_view()
            self.set_offer_pending(offer_pending)

    def knock(self, thought_id: str) -> None:
        """Run the firmware-owned silent knock through physical completion."""

        with self._operation_lock:
            self._require_ready()
            self._base_view = "avatar"
            self._call("perform_knock", {"behavior_id": thought_id})

    def tell(self, thought_id: str, audio_base64: str) -> None:
        """Play prepared audio after firmware reports acknowledgment."""

        with self._operation_lock:
            self._require_ready()
            self._play_audio(audio_base64, thought_id)
            self._base_view = None
            self.set_base_view()

    def tell_direct(
        self,
        turn_id: str,
        audio: bytes,
    ) -> dict[str, int]:
        """Run the firmware-owned attention behavior, then speak."""

        with self._operation_lock:
            self._require_ready()
            self._base_view = "avatar"
            started = time.monotonic()
            self._call(
                "perform_behavior",
                {"behavior_id": turn_id, "kind": "attention"},
            )
            attention_ms = round((time.monotonic() - started) * 1000)
            started = time.monotonic()
            playback = self._play_audio_bytes(audio, turn_id)
            playback_request_ms = round(
                (time.monotonic() - started) * 1000
            )
            self._base_view = None
            metrics = {
                "attention_ms": attention_ms,
                "playback_request_ms": playback_request_ms,
            }
            for source, target in (
                ("duration_ms", "playback_audio_ms"),
                ("packet_count", "prepared_audio_packets"),
            ):
                value = playback.get(source)
                if isinstance(value, int) and value >= 0:
                    metrics[target] = value
            for name in (
                "gateway_playback_started_ms",
                "gateway_playback_completed_ms",
            ):
                value = playback.get(name)
                if isinstance(value, int) and value >= 0:
                    metrics[name] = value
            return metrics

    def set_offer_pending(self, pending: bool) -> None:
        """Best-effort synchronization of the firmware screensaver gate."""

        with self._operation_lock:
            if self._synced_offer_pending == pending:
                return
            try:
                self._require_ready()
                self._call("set_offer_pending", {"pending": pending})
            except PendingThoughtRuntimeError as exc:
                logger.warning("offer-state synchronization failed: %s", exc)
                return
            self._synced_offer_pending = pending

    def _play_audio(self, audio_base64: str, thought_id: str) -> None:
        self._play_audio_bytes(base64.b64decode(audio_base64), thought_id)

    def _play_audio_bytes(
        self,
        payload: bytes,
        thought_id: str,
    ) -> Mapping[str, object]:
        if not self._playback_url:
            raise PendingThoughtRuntimeError("audio playback URL is not configured")
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
            raise PendingThoughtRuntimeError(f"play audio: {exc}") from exc
        if result.get("ok") is not True:
            raise PendingThoughtRuntimeError(
                f"play audio: {result.get('error', 'playback failed')}"
            )
        return result

    def _require_ready(self) -> None:
        if not self.is_ready():
            raise PendingThoughtRuntimeError(
                "reviewed avatar is not ready for the current device session"
            )

    def _call(
        self, name: str, arguments: Mapping[str, object]
    ) -> Mapping[str, object]:
        try:
            result = self._call_tool(name, arguments)
        except Exception as exc:
            raise PendingThoughtRuntimeError(f"{name}: {exc}") from exc
        payload = _tool_payload(result)
        if payload.get("ok") is False or "error" in payload:
            message = payload.get("error") or payload.get("message")
            raise PendingThoughtRuntimeError(
                f"{name}: {message or 'StackChan tool failed'}"
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
            raise PendingThoughtRuntimeError("MCP session is not bound")
        future = asyncio.run_coroutine_threadsafe(
            self._session.call_tool(name, arguments=dict(arguments)),
            self._loop,
        )
        return future.result()


class PendingThoughtRuntime:
    """Own one state machine for the lifetime of an upstream MCP session."""

    def __init__(
        self,
        *,
        playback_url: str | None = None,
        playback_token: str = "",
    ) -> None:
        self._playback_url = playback_url
        self._playback_token = playback_token
        self.machine: KnockWaitTell | None = None
        self.body: StackChanThoughtBody | None = None
        self._caller: SessionToolCaller | None = None

    def mark_avatar_ready(self, session_id: str) -> None:
        """Record that startup restored the reviewed avatar."""

        if self.body is None:
            raise PendingThoughtRuntimeError("runtime session is not initialized")
        self.body.mark_avatar_ready(session_id)

    async def is_ready(self) -> bool:
        """Check readiness without blocking the service event loop."""

        if self.machine is None or self.body is None:
            return False
        return await asyncio.to_thread(self.body.is_ready)

    async def pending_thought_id(self) -> str | None:
        """Return the current unexpired offer without device side effects."""

        if self.machine is None:
            return None
        return await asyncio.to_thread(
            lambda: self.machine.pending_thought_id
        )

    async def reconcile_base_view(self) -> str | None:
        """Expire stale offers and explicitly align the device base view."""

        pending_id = await self.pending_thought_id()
        if self.body is not None:
            await asyncio.to_thread(
                self.body.reconcile_base_view,
                pending_id is not None,
            )
        return pending_id

    async def tell_direct(
        self,
        turn_id: str,
        audio: bytes,
    ) -> dict[str, int]:
        """Serialize a direct answer against pending-offer body operations."""

        if self.machine is None or self.body is None:
            raise PendingThoughtRuntimeError("runtime session is not initialized")
        try:
            return await asyncio.to_thread(
                self.body.tell_direct,
                turn_id,
                audio,
            )
        finally:
            await asyncio.to_thread(self.body.restore_base_view)

    async def consider_thought(
        self, payload: Mapping[str, object]
    ):
        """Submit without blocking the MCP session's receive loop."""

        if self.machine is None:
            raise PendingThoughtRuntimeError("runtime session is not initialized")
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
            self.body = StackChanThoughtBody(
                self._caller,
                playback_url=self._playback_url,
                playback_token=self._playback_token,
            )
            self.machine = KnockWaitTell(
                self.body,
                self.body,
                offer_state_port=self.body,
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
    raise PendingThoughtRuntimeError("tool returned no structured result")


def _field(value: object, *names: str) -> object:
    for name in names:
        if isinstance(value, Mapping) and name in value:
            return value[name]
        attribute = getattr(value, name, _MISSING)
        if attribute is not _MISSING:
            return attribute
    return _MISSING
