import asyncio
import base64
import unittest
from unittest.mock import AsyncMock, Mock, patch

from gateway.pending_thought_runtime import (
    PendingThoughtRuntime,
    PendingThoughtRuntimeError,
    StackChanThoughtBody,
)


_OPUS_PACKET = bytes.fromhex(
    "5802f9304dbb0de5e392098938ebcae1b1d1dd85"
)
_FRAMED_OPUS = len(_OPUS_PACKET).to_bytes(2, "big") + _OPUS_PACKET
_PREPARED_AUDIO_BASE64 = base64.b64encode(_FRAMED_OPUS).decode("ascii")
_READY_STATUS = {
    "connected": True,
    "initialized": True,
    "session_id": "device-session-1",
}


class RecordingCaller:
    def __init__(self, results=None, *, status=None):
        self.calls = []
        self.results = list(results or [])
        self.status = dict(status or _READY_STATUS)

    def __call__(self, name, arguments):
        self.calls.append((name, arguments))
        if name == "get_status":
            return self.status
        if self.results:
            result = self.results.pop(0)
            if isinstance(result, Exception):
                raise result
            return result
        return {"ok": True}


def ready_body(caller, **kwargs):
    body = StackChanThoughtBody(caller, **kwargs)
    body.mark_avatar_ready(_READY_STATUS["session_id"])
    return body


class PendingThoughtRuntimeTests(unittest.TestCase):
    def test_knock_delegates_complete_physical_behavior(self):
        caller = RecordingCaller()
        body = ready_body(caller)

        body.knock("eval:42")

        self.assertEqual(
            caller.calls,
            [
                ("get_status", {}),
                ("perform_knock", {"behavior_id": "eval:42"}),
            ],
        )
        self.assertNotIn("say", [name for name, _ in caller.calls])

    def test_knock_failure_is_reported(self):
        caller = RecordingCaller([RuntimeError("knock failed")])
        body = ready_body(caller)

        with self.assertRaisesRegex(PendingThoughtRuntimeError, "knock failed"):
            body.knock("eval:42")

    @patch("gateway.pending_thought_runtime.urllib.request.urlopen")
    def test_tell_posts_audio_without_cloud_motion_control(self, urlopen):
        response = Mock()
        response.read.return_value = b'{"ok": true}'
        urlopen.return_value.__enter__.return_value = response
        caller = RecordingCaller()
        body = ready_body(
            caller,
            playback_url="http://127.0.0.1:8080/play",
            playback_token="secret",
        )

        body.tell("eval:42", _PREPARED_AUDIO_BASE64)

        urlopen.assert_called_once()
        request = urlopen.call_args.args[0]
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 300)
        self.assertEqual(request.data, _FRAMED_OPUS)
        self.assertEqual(request.get_header("X-message-id"), "eval:42")
        self.assertEqual(
            caller.calls,
            [
                ("get_status", {}),
                ("set_avatar", {"face": "idle"}),
            ],
        )

    @patch("gateway.pending_thought_runtime.urllib.request.urlopen")
    def test_tell_error_is_not_marked_complete(self, urlopen):
        failure = Mock()
        failure.read.return_value = b'{"ok": false, "error": "unavailable"}'
        success = Mock()
        success.read.return_value = b'{"ok": true}'
        urlopen.return_value.__enter__.side_effect = [failure, success]
        body = ready_body(
            RecordingCaller(),
            playback_url="http://127.0.0.1:8080/play",
        )

        with self.assertRaisesRegex(PendingThoughtRuntimeError, "unavailable"):
            body.tell("eval:42", _PREPARED_AUDIO_BASE64)
        body.tell("eval:42", _PREPARED_AUDIO_BASE64)

        self.assertEqual(urlopen.call_count, 2)

    def test_body_rejects_unverified_or_reconnected_device_session(self):
        caller = RecordingCaller()
        body = StackChanThoughtBody(caller)

        with self.assertRaisesRegex(
            PendingThoughtRuntimeError,
            "reviewed avatar is not ready",
        ):
            body.knock("eval:unverified")
        self.assertEqual(caller.calls, [])

        body.mark_avatar_ready("device-session-1")
        caller.status["session_id"] = "device-session-2"
        with self.assertRaisesRegex(
            PendingThoughtRuntimeError,
            "reviewed avatar is not ready",
        ):
            body.knock("eval:reconnected")
        self.assertEqual(caller.calls, [("get_status", {})])

    def test_runtime_owns_machine_for_session_lifetime(self):
        runtime = PendingThoughtRuntime()
        first_session = object()
        second_session = object()

        with patch(
            "gateway.pending_thought_runtime.create_stackchan_client_session",
            side_effect=(first_session, second_session),
        ) as factory:
            first = runtime.create_session("read-1", "write-1", object())
            machine = runtime.machine
            body = runtime.body
            second = runtime.create_session("read-2", "write-2", object())

        self.assertIs(first, first_session)
        self.assertIs(second, second_session)
        self.assertIs(runtime.machine, machine)
        self.assertIs(runtime.body, body)
        self.assertEqual(factory.call_count, 2)
        self.assertIs(factory.call_args_list[0].args[2], machine)
        self.assertIs(factory.call_args_list[1].args[2], machine)

        runtime.unbind_session(first_session)
        self.assertIs(runtime._caller._session, second_session)
        runtime.unbind_session(second_session)
        self.assertIsNone(runtime._caller._session)

    def test_runtime_readiness_tracks_verified_device_session(self):
        caller = RecordingCaller()
        body = ready_body(caller)
        runtime = PendingThoughtRuntime()
        runtime.body = body
        runtime.machine = Mock(pending_thought_id=None)

        self.assertTrue(asyncio.run(runtime.is_ready()))
        caller.status["session_id"] = "device-session-2"
        self.assertFalse(asyncio.run(runtime.is_ready()))

    def test_direct_tell_restores_idle_base_view(self):
        runtime = PendingThoughtRuntime()
        runtime.machine = Mock(pending_thought_id="eval:waiting")
        runtime.body = Mock()

        asyncio.run(runtime.tell_direct("robot:1", b"audio"))

        runtime.body.restore_base_view.assert_called_once_with()

    @patch("gateway.pending_thought_runtime.urllib.request.urlopen")
    def test_direct_tell_reuses_firmware_attention_behavior(self, urlopen):
        response = Mock()
        response.read.return_value = b'{"ok": true}'
        urlopen.return_value.__enter__.return_value = response
        caller = RecordingCaller()
        body = ready_body(
            caller,
            playback_url="http://127.0.0.1:8080/play",
        )

        body.tell_direct("robot:1", b"audio")

        self.assertEqual(
            caller.calls,
            [
                ("get_status", {}),
                (
                    "perform_behavior",
                    {"behavior_id": "robot:1", "kind": "attention"},
                ),
            ],
        )
        self.assertEqual(urlopen.call_args.args[0].data, b"audio")

    def test_base_view_cache_is_invalidated_for_replacement_session(self):
        caller = RecordingCaller()
        body = ready_body(caller)
        body.set_base_view()
        caller.calls.clear()

        body.mark_avatar_ready("device-session-2")
        caller.status["session_id"] = "device-session-2"
        body.set_base_view()

        self.assertEqual(
            caller.calls,
            [("set_avatar", {"face": "idle"})],
        )



if __name__ == "__main__":
    unittest.main()
