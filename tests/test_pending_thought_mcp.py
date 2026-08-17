import json
import unittest

from gateway.pending_thought import KnockWaitTell
from mcp.pending_thought_tool import (
    InvalidPendingThoughtArgumentsError,
    UnknownPendingThoughtToolError,
    handle_mcp_notification,
    handle_stackchan_event,
    handle_tool_call,
    tool_descriptor,
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


class PendingThoughtMcpTests(unittest.TestCase):
    def test_descriptor_uses_tracked_contract(self):
        descriptor = tool_descriptor()

        self.assertEqual(descriptor["name"], "consider_thought")
        schema = descriptor["inputSchema"]
        self.assertEqual(schema["properties"]["version"]["const"], "v1")
        self.assertEqual(
            schema["properties"]["decision"]["enum"],
            ["ignore", "remember", "offer"],
        )
        json.dumps(descriptor)

    def test_handler_submits_offer_without_exposing_audio(self):
        body = RecordingBody()
        machine = KnockWaitTell(body, body)

        result = handle_tool_call(
            "consider_thought",
            {
                "version": "v1",
                "thought_id": "eval:42",
                "decision": "offer",
                "audio_base64": _PREPARED_AUDIO_BASE64,
            },
            machine,
        )

        self.assertEqual(
            result,
            {
                "ok": True,
                "thought_id": "eval:42",
                "decision": "offer",
                "state": "waiting",
                "pending_thought_id": "eval:42",
            },
        )
        self.assertEqual(body.knocks, ["eval:42"])
        self.assertEqual(body.tells, [])

    def test_unknown_tool_and_invalid_arguments_are_translated(self):
        body = RecordingBody()
        machine = KnockWaitTell(body, body)

        with self.assertRaises(UnknownPendingThoughtToolError):
            handle_tool_call("raw_move", {}, machine)
        with self.assertRaises(InvalidPendingThoughtArgumentsError):
            handle_tool_call(
                "consider_thought",
                {"version": "v1", "thought_id": "a", "decision": "offer"},
                machine,
            )
        with self.assertRaisesRegex(
            InvalidPendingThoughtArgumentsError,
            "unsupported decision",
        ):
            handle_tool_call(
                "consider_thought",
                {"version": "v1", "thought_id": "a", "decision": []},
                machine,
            )

    def test_exact_tap_event_tells_and_repeated_event_is_a_noop(self):
        body = RecordingBody()
        machine = KnockWaitTell(body, body)
        handle_tool_call(
            "consider_thought",
            {
                "version": "v1",
                "thought_id": "eval:42",
                "decision": "offer",
                "audio_base64": _PREPARED_AUDIO_BASE64,
            },
            machine,
        )
        event = {
            "event_type": "touch",
            "subtype": "tap",
            "action": "head_pat",
        }

        first = handle_stackchan_event(event, machine)
        second = handle_stackchan_event(event, machine)

        self.assertEqual(first["acknowledged"], True)
        self.assertEqual(first["state"], "told")
        self.assertEqual(second["acknowledged"], False)
        self.assertEqual(body.tells, [("eval:42", _PREPARED_AUDIO_BASE64)])

    def test_upstream_notification_envelope_routes_only_stackchan_event(self):
        body = RecordingBody()
        machine = KnockWaitTell(body, body)
        handle_tool_call(
            "consider_thought",
            {
                "version": "v1",
                "thought_id": "eval:42",
                "decision": "offer",
                "audio_base64": _PREPARED_AUDIO_BASE64,
            },
            machine,
        )
        unrelated = {
            "method": "notifications/tools/list_changed",
            "params": {
                "event_type": "touch",
                "subtype": "tap",
                "action": "head_pat",
            },
        }
        head_tap = {
            "method": "stackchan/event",
            "params": {
                "event_type": "touch",
                "subtype": "tap",
                "action": "head_pat",
                "duration_ms": 0,
                "ts": 42,
                "session_id": "device-session",
            },
        }

        ignored = handle_mcp_notification(unrelated, machine)
        acknowledged = handle_mcp_notification(head_tap, machine)

        self.assertEqual(ignored["acknowledged"], False)
        self.assertEqual(ignored["pending_thought_id"], "eval:42")
        self.assertEqual(acknowledged["acknowledged"], True)
        self.assertIsNone(acknowledged["pending_thought_id"])
        self.assertEqual(body.tells, [("eval:42", _PREPARED_AUDIO_BASE64)])

    def test_stackchan_notification_requires_object_params(self):
        body = RecordingBody()
        machine = KnockWaitTell(body, body)

        with self.assertRaisesRegex(
            InvalidPendingThoughtArgumentsError,
            "params must be an object",
        ):
            handle_mcp_notification(
                {"method": "stackchan/event", "params": None},
                machine,
            )


if __name__ == "__main__":
    unittest.main()
