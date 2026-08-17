"""Concrete Milestone 2 runtime adapters for the pinned StackChan gateway."""

from __future__ import annotations

import asyncio
import base64
import json
import time
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any

from gateway.pending_thought import KnockWaitTell
from gateway.stackchan_event_session import create_stackchan_client_session

ToolCaller = Callable[[str, Mapping[str, object]], object]
_MISSING = object()


class PendingThoughtRuntimeError(RuntimeError):
    """The persistent thought runtime could not complete a body operation."""


class StackChanThoughtBody:
    """Synchronous knock/tell ports backed by an injected MCP tool caller."""

    def __init__(
        self,
        call_tool: ToolCaller,
        *,
        knock_hold_seconds: float = 10.0,
        sleep: Callable[[float], None] = time.sleep,
        playback_url: str | None = None,
        playback_token: str = "",
    ):
        self._call_tool = call_tool
        self._knock_hold_seconds = knock_hold_seconds
        self._sleep = sleep
        self._playback_url = playback_url
        self._playback_token = playback_token
        self._verified_session_id: str | None = None

    def mark_avatar_ready(self, session_id: str) -> None:
        """Bind reviewed-avatar verification to one device session."""

        if not isinstance(session_id, str) or not session_id:
            raise PendingThoughtRuntimeError(
                "reviewed avatar verification requires a device session"
            )
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

    def knock(self, thought_id: str) -> None:
        """Give one restrained silent gesture, then return to idle."""

        del thought_id
        self._require_ready()
        operation_error: Exception | None = None
        try:
            self._call("set_avatar", {"face": "thinking"})
            self._call(
                "move_head",
                {"yaw": 12, "pitch": 50, "speed": "low"},
            )
            self._sleep(self._knock_hold_seconds)
        except Exception as exc:
            operation_error = exc
        self._finish_with_idle(operation_error)

    def tell(self, thought_id: str, audio_base64: str) -> None:
        """Play prepared audio, then restore the reviewed idle pose."""

        self._require_ready()
        operation_error: Exception | None = None
        try:
            self._play_audio(audio_base64, thought_id)
        except Exception as exc:
            operation_error = exc
        self._finish_with_idle(operation_error)

    def _play_audio(self, audio_base64: str, thought_id: str) -> None:
        if not self._playback_url:
            raise PendingThoughtRuntimeError("audio playback URL is not configured")
        payload = base64.b64decode(audio_base64)
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
            with urllib.request.urlopen(request, timeout=60) as response:
                result = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            raise PendingThoughtRuntimeError(f"play audio: {exc}") from exc
        if result.get("ok") is not True:
            raise PendingThoughtRuntimeError(
                f"play audio: {result.get('error', 'playback failed')}"
            )

    def _finish_with_idle(self, operation_error: Exception | None) -> None:
        try:
            self._return_to_idle()
        except Exception as idle_error:
            if operation_error is not None:
                raise PendingThoughtRuntimeError(
                    f"operation failed ({operation_error}); "
                    f"idle return also failed ({idle_error})"
                ) from operation_error
            raise
        if operation_error is not None:
            raise operation_error

    def _require_ready(self) -> None:
        if not self.is_ready():
            raise PendingThoughtRuntimeError(
                "reviewed avatar is not ready for the current device session"
            )

    def _return_to_idle(self) -> None:
        move_error: Exception | None = None
        try:
            self._call(
                "move_head",
                {"yaw": 0, "pitch": 43, "speed": "low"},
            )
        except Exception as exc:
            move_error = exc
        try:
            self._call("set_avatar", {"face": "idle"})
        except Exception as avatar_error:
            if move_error is not None:
                raise PendingThoughtRuntimeError(
                    f"idle motion failed ({move_error}); "
                    f"idle face also failed ({avatar_error})"
                ) from move_error
            raise
        if move_error is not None:
            raise move_error

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
        if self._session is not None:
            raise PendingThoughtRuntimeError("MCP session is already bound")
        self._session = session

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
        knock_hold_seconds: float = 10.0,
        sleep: Callable[[float], None] = time.sleep,
        playback_url: str | None = None,
        playback_token: str = "",
    ) -> None:
        self._knock_hold_seconds = knock_hold_seconds
        self._sleep = sleep
        self._playback_url = playback_url
        self._playback_token = playback_token
        self.machine: KnockWaitTell | None = None
        self.body: StackChanThoughtBody | None = None

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

        caller = SessionToolCaller(loop)
        body = StackChanThoughtBody(
            caller,
            knock_hold_seconds=self._knock_hold_seconds,
            sleep=self._sleep,
            playback_url=self._playback_url,
            playback_token=self._playback_token,
        )
        machine = KnockWaitTell(body, body)
        session = create_stackchan_client_session(
            read_stream,
            write_stream,
            machine,
        )
        caller.bind(session)
        self.body = body
        self.machine = machine
        return session


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
