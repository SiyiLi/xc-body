import asyncio
import sys
import threading
import types
import unittest
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from gateway.pending_thought import KnockWaitTell
from gateway.stackchan_event_session import (
    StackChanEventDispatcher,
    StackChanEventSessionError,
    create_stackchan_client_session,
    handle_session_message,
    wait_for_stackchan_event_tasks,
)


_PREPARED_AUDIO_BASE64 = "ABRYAvkwTbsN5eOSCYk468rhsdHdhQ=="


class RecordingBody:
    def __init__(self):
        self.knocks = []
        self.tells = []

    def knock(self, thought_id):
        self.knocks.append(thought_id)

    def tell(self, thought_id, audio_base64):
        self.tells.append((thought_id, audio_base64))


class StackChanEventSessionTests(unittest.TestCase):
    def test_session_message_routes_only_stackchan_event(self):
        body = RecordingBody()
        machine = self._waiting_machine(body)
        unrelated = SimpleNamespace(
            root=SimpleNamespace(
                method="notifications/tools/list_changed",
                params={
                    "event_type": "touch",
                    "subtype": "tap",
                    "action": "head_pat",
                },
            )
        )
        head_tap = SimpleNamespace(
            root=SimpleNamespace(
                method="stackchan/event",
                params={
                    "event_type": "touch",
                    "subtype": "tap",
                    "action": "head_pat",
                },
            )
        )
        dispatcher = StackChanEventDispatcher(machine)

        async def route_messages():
            await handle_session_message(unrelated, dispatcher)
            self.assertEqual(machine.pending_thought_id, "eval:42")
            await handle_session_message(head_tap, dispatcher)
            await dispatcher.drain()

        asyncio.run(route_messages())

        self.assertIsNone(machine.pending_thought_id)
        self.assertEqual(
            body.tells,
            [("eval:42", _PREPARED_AUDIO_BASE64)],
        )

    def test_matching_message_requires_object_params(self):
        body = RecordingBody()
        machine = KnockWaitTell(body, body)
        malformed = SimpleNamespace(
            root=SimpleNamespace(method="stackchan/event", params=None)
        )
        dispatcher = StackChanEventDispatcher(machine)

        with self.assertRaisesRegex(
            StackChanEventSessionError,
            "params must be an object",
        ):
            asyncio.run(handle_session_message(malformed, dispatcher))

    def test_factory_installs_custom_notification_model_and_handler(self):
        body = RecordingBody()
        machine = self._waiting_machine(body)

        with fake_mcp_sdk() as fake_session_class:
            session = create_stackchan_client_session(
                "read",
                "write",
                machine,
            )

        self.assertIsInstance(session, fake_session_class)
        self.assertEqual(session.read_timeout_seconds, timedelta(seconds=60))
        self.assertEqual(
            session._receive_notification_type.__name__,
            "StackChanServerNotification",
        )
        notification = SimpleNamespace(
            root=SimpleNamespace(
                method="stackchan/event",
                params={
                    "event_type": "touch",
                    "subtype": "tap",
                    "action": "head_pat",
                },
            )
        )
        async def route_and_drain():
            await session.message_handler(notification)
            await wait_for_stackchan_event_tasks(session)

        asyncio.run(route_and_drain())
        self.assertIsNone(machine.pending_thought_id)

    def test_event_burst_uses_one_worker_and_one_coalesced_dispatch(self):
        started = threading.Event()
        release = threading.Event()

        class BlockingMachine:
            def __init__(self):
                self.calls = 0
                self.active = 0
                self.max_active = 0

            def handle_stackchan_event(self, event):
                del event
                self.calls += 1
                self.active += 1
                self.max_active = max(self.max_active, self.active)
                try:
                    if self.calls == 1:
                        started.set()
                        release.wait(timeout=2)
                finally:
                    self.active -= 1

        machine = BlockingMachine()
        dispatcher = StackChanEventDispatcher(machine)
        notification = SimpleNamespace(
            root=SimpleNamespace(
                method="stackchan/event",
                params={
                    "event_type": "touch",
                    "subtype": "tap",
                    "action": "head_pat",
                },
            )
        )

        async def dispatch_burst():
            await handle_session_message(
                notification,
                dispatcher,
            )
            for _ in range(1000):
                if started.is_set():
                    break
                await asyncio.sleep(0.001)
            self.assertTrue(started.is_set())

            for _ in range(1000):
                await handle_session_message(
                    notification,
                    dispatcher,
                )

            release.set()
            await dispatcher.drain()

        asyncio.run(dispatch_burst())

        self.assertEqual(machine.calls, 2)
        self.assertEqual(machine.max_active, 1)

    @staticmethod
    def _waiting_machine(body):
        machine = KnockWaitTell(body, body)
        machine.submit(
            {
                "version": "v1",
                "thought_id": "eval:42",
                "decision": "offer",
                "audio_base64": _PREPARED_AUDIO_BASE64,
            }
        )
        return machine


@contextmanager
def fake_mcp_sdk():
    class GenericBase:
        @classmethod
        def __class_getitem__(cls, parameters):
            del parameters
            return cls

    class FakeNotification(GenericBase):
        pass

    class FakeStandardNotification:
        pass

    class FakeRootModel(GenericBase):
        pass

    class FakeClientSession:
        def __init__(
            self,
            read_stream,
            write_stream,
            read_timeout_seconds,
            message_handler,
        ):
            self.read_stream = read_stream
            self.write_stream = write_stream
            self.read_timeout_seconds = read_timeout_seconds
            self.message_handler = message_handler
            self._receive_notification_type = object

    mcp_module = types.ModuleType("mcp")
    mcp_module.__path__ = []
    mcp_module.ClientSession = FakeClientSession
    mcp_types = types.ModuleType("mcp.types")
    mcp_types.Notification = FakeNotification
    mcp_types.ServerNotificationType = FakeStandardNotification
    pydantic_module = types.ModuleType("pydantic")
    pydantic_module.RootModel = FakeRootModel

    modules = {
        "mcp": mcp_module,
        "mcp.types": mcp_types,
        "pydantic": pydantic_module,
    }
    with patch.dict(sys.modules, modules):
        yield FakeClientSession


if __name__ == "__main__":
    unittest.main()
