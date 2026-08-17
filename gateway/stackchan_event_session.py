"""Import-safe MCP client session support for StackChan event notifications."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import Any, Literal, Union

from gateway.pending_thought import KnockWaitTell, is_head_acknowledgment

STACKCHAN_EVENT_METHOD = "stackchan/event"
logger = logging.getLogger(__name__)


class StackChanEventSessionError(RuntimeError):
    """The deployment MCP SDK cannot route StackChan event notifications."""


class StackChanEventDispatcher:
    """Run at most one gesture worker and coalesce one pending gesture."""

    def __init__(self, machine: KnockWaitTell):
        self._machine = machine
        self._pending: Mapping[str, object] | None = None
        self._task: asyncio.Task[None] | None = None

    @property
    def active_task_count(self) -> int:
        return int(self._task is not None)

    def dispatch(self, event: Mapping[str, object]) -> None:
        self._pending = event
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def drain(self) -> None:
        while self._task is not None:
            await self._task

    async def _run(self) -> None:
        while self._pending is not None:
            event = self._pending
            self._pending = None
            try:
                await asyncio.to_thread(
                    self._machine.handle_stackchan_event,
                    event,
                )
            except Exception:
                logger.exception("StackChan event handler failed")
        self._task = None


async def handle_session_message(
    message: object,
    machine: KnockWaitTell,
    dispatcher: StackChanEventDispatcher | None = None,
) -> None:
    """Queue one event without blocking the MCP session receive loop."""

    notification = getattr(message, "root", None)
    if getattr(notification, "method", None) != STACKCHAN_EVENT_METHOD:
        return
    params = getattr(notification, "params", None)
    if not isinstance(params, Mapping):
        raise StackChanEventSessionError(
            "stackchan/event params must be an object"
        )
    if not is_head_acknowledgment(params):
        return
    if dispatcher is None:
        await asyncio.to_thread(machine.handle_stackchan_event, params)
        return
    dispatcher.dispatch(params)


async def wait_for_stackchan_event_tasks(session: object) -> None:
    """Finish tap-driven work before closing its upstream MCP session."""

    dispatcher = getattr(session, "_stackchan_event_dispatcher", None)
    if not isinstance(dispatcher, StackChanEventDispatcher):
        return
    await dispatcher.drain()


def create_stackchan_client_session(
    read_stream: Any,
    write_stream: Any,
    machine: KnockWaitTell,
) -> Any:
    """Create a pinned-SDK session that accepts ``stackchan/event``."""

    try:
        from mcp import ClientSession
        import mcp.types as types
        from pydantic import RootModel
    except ImportError as exc:
        raise StackChanEventSessionError(
            "the deployment environment must provide the MCP Python SDK"
        ) from exc

    try:
        server_notification_type = types.ServerNotificationType
        notification_base = types.Notification
    except AttributeError as exc:
        raise StackChanEventSessionError(
            "MCP Python SDK does not expose the verified notification API"
        ) from exc

    class StackChanEventNotification(
        notification_base[
            dict[str, Any], Literal["stackchan/event"]
        ]
    ):
        method: Literal["stackchan/event"] = STACKCHAN_EVENT_METHOD
        params: dict[str, Any]

    notification_union = Union[
        server_notification_type, StackChanEventNotification
    ]

    class StackChanServerNotification(RootModel[notification_union]):
        pass

    event_dispatcher = StackChanEventDispatcher(machine)

    async def message_handler(message: object) -> None:
        await handle_session_message(message, machine, event_dispatcher)

    session = ClientSession(
        read_stream,
        write_stream,
        message_handler=message_handler,
    )
    if not hasattr(session, "_receive_notification_type"):
        raise StackChanEventSessionError(
            "MCP ClientSession lacks the verified notification extension point"
        )
    session._receive_notification_type = StackChanServerNotification
    session._stackchan_event_dispatcher = event_dispatcher
    return session
