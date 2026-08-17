import asyncio
import base64
import unittest
from unittest.mock import Mock, patch

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
    def test_knock_is_silent_restrained_gesture_with_idle_return(self):
        caller = RecordingCaller()
        sleeps = []
        body = ready_body(caller, sleep=sleeps.append)

        body.knock("eval:42")

        self.assertEqual(
            caller.calls,
            [
                ("get_status", {}),
                ("set_avatar", {"face": "thinking"}),
                (
                    "move_head",
                    {"yaw": 12, "pitch": 50, "speed": "low"},
                ),
                (
                    "move_head",
                    {"yaw": 0, "pitch": 43, "speed": "low"},
                ),
                ("set_avatar", {"face": "idle"}),
            ],
        )
        self.assertNotIn("say", [name for name, _ in caller.calls])
        self.assertEqual(sleeps, [10.0])

    def test_knock_failure_still_attempts_idle_return(self):
        caller = RecordingCaller(
            [
                {"ok": True},
                RuntimeError("move failed"),
                {"ok": True},
                {"ok": True},
            ]
        )
        body = ready_body(caller, sleep=lambda seconds: None)

        with self.assertRaisesRegex(PendingThoughtRuntimeError, "move failed"):
            body.knock("eval:42")

        self.assertEqual(caller.calls[-1], ("set_avatar", {"face": "idle"}))

    @patch("gateway.pending_thought_runtime.urllib.request.urlopen")
    def test_tell_posts_prepared_audio_and_returns_to_idle(self, urlopen):
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
        self.assertEqual(request.data, _FRAMED_OPUS)
        self.assertEqual(request.get_header("X-message-id"), "eval:42")
        self.assertNotIn("say", [name for name, _ in caller.calls])
        self.assertEqual(caller.calls[-1], ("set_avatar", {"face": "idle"}))

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
        body = StackChanThoughtBody(caller, sleep=lambda seconds: None)

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
        fake_session = object()

        with patch(
            "gateway.pending_thought_runtime.create_stackchan_client_session",
            return_value=fake_session,
        ) as factory:
            session = runtime.create_session("read", "write", object())

        self.assertIs(session, fake_session)
        self.assertIsNotNone(runtime.machine)
        self.assertIsNotNone(runtime.body)
        factory.assert_called_once()
        self.assertIs(factory.call_args.args[2], runtime.machine)

    def test_runtime_readiness_tracks_verified_device_session(self):
        caller = RecordingCaller()
        body = ready_body(caller)
        runtime = PendingThoughtRuntime()
        runtime.body = body
        runtime.machine = Mock(pending_thought_id=None)

        self.assertTrue(asyncio.run(runtime.is_ready()))
        caller.status["session_id"] = "device-session-2"
        self.assertFalse(asyncio.run(runtime.is_ready()))


if __name__ == "__main__":
    unittest.main()
