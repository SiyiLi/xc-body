import asyncio
import base64
import unittest
from unittest.mock import Mock, patch

from gateway.pending_thought_runtime import (
    PendingThoughtRuntime,
    PendingThoughtRuntimeError,
    SessionToolCaller,
    StackChanThoughtBody,
)


_OPUS_PACKET = bytes.fromhex(
    "5802f9304dbb0de5e392098938ebcae1b1d1dd85"
)
_FRAMED_OPUS = len(_OPUS_PACKET).to_bytes(2, "big") + _OPUS_PACKET
_PREPARED_AUDIO_BASE64 = base64.b64encode(_FRAMED_OPUS).decode("ascii")


class RecordingCaller:
    def __init__(self, results=None):
        self.calls = []
        self.results = list(results or [])

    def __call__(self, name, arguments):
        self.calls.append((name, arguments))
        if self.results:
            result = self.results.pop(0)
            if isinstance(result, Exception):
                raise result
            return result
        return {"ok": True}


class PendingThoughtRuntimeTests(unittest.TestCase):
    def test_knock_is_silent_restrained_gesture_with_idle_return(self):
        caller = RecordingCaller()
        sleeps = []
        body = StackChanThoughtBody(caller, sleep=sleeps.append)

        body.knock("eval:42")

        self.assertEqual(
            caller.calls,
            [
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
        self.assertEqual(sleeps, [2.0])

    def test_knock_failure_still_attempts_idle_return(self):
        caller = RecordingCaller(
            [
                {"ok": True},
                RuntimeError("move failed"),
                {"ok": True},
                {"ok": True},
            ]
        )
        body = StackChanThoughtBody(caller, sleep=lambda seconds: None)

        with self.assertRaisesRegex(PendingThoughtRuntimeError, "move failed"):
            body.knock("eval:42")

        self.assertEqual(caller.calls[-1], ("set_avatar", {"face": "idle"}))

    @patch("gateway.pending_thought_runtime.urllib.request.urlopen")
    def test_tell_suppresses_duplicate_while_id_is_retained(self, urlopen):
        response = Mock()
        response.read.return_value = b'{"ok": true}'
        urlopen.return_value.__enter__.return_value = response
        caller = RecordingCaller()
        body = StackChanThoughtBody(
            caller,
            playback_url="http://127.0.0.1:8080/play",
            playback_token="secret",
        )

        body.tell("eval:42", _PREPARED_AUDIO_BASE64)
        body.tell("eval:42", _PREPARED_AUDIO_BASE64)

        urlopen.assert_called_once()
        request = urlopen.call_args.args[0]
        self.assertEqual(request.data, _FRAMED_OPUS)
        self.assertEqual(request.get_header("X-message-id"), "eval:42")
        self.assertNotIn("say", [name for name, _ in caller.calls])
        self.assertEqual(caller.calls[-1], ("set_avatar", {"face": "idle"}))

    @patch("gateway.pending_thought_runtime.urllib.request.urlopen")
    def test_tell_replays_after_id_eviction(self, urlopen):
        response = Mock()
        response.read.return_value = b'{"ok": true}'
        urlopen.return_value.__enter__.return_value = response
        body = StackChanThoughtBody(
            RecordingCaller(),
            playback_url="http://127.0.0.1:8080/play",
        )

        with patch("gateway.pending_thought_runtime._MAX_TOLD_IDS", 2):
            for thought_id in ("evicted", "recent:1", "recent:2", "evicted"):
                body.tell(thought_id, _PREPARED_AUDIO_BASE64)

        self.assertEqual(urlopen.call_count, 4)

    @patch("gateway.pending_thought_runtime.urllib.request.urlopen")
    def test_tell_error_is_not_marked_complete(self, urlopen):
        failure = Mock()
        failure.read.return_value = b'{"ok": false, "error": "unavailable"}'
        success = Mock()
        success.read.return_value = b'{"ok": true}'
        urlopen.return_value.__enter__.side_effect = [failure, success]
        body = StackChanThoughtBody(
            RecordingCaller(),
            playback_url="http://127.0.0.1:8080/play",
        )

        with self.assertRaisesRegex(PendingThoughtRuntimeError, "unavailable"):
            body.tell("eval:42", _PREPARED_AUDIO_BASE64)
        body.tell("eval:42", _PREPARED_AUDIO_BASE64)

        self.assertEqual(urlopen.call_count, 2)

    @patch("gateway.pending_thought_runtime.urllib.request.urlopen")
    def test_tell_rejects_invalid_framing_before_playback(self, urlopen):
        body = StackChanThoughtBody(
            RecordingCaller(),
            playback_url="http://127.0.0.1:8080/play",
        )
        oversized = b"\x04\xfc" + (b"x" * 1276)
        invalid = (
            ("", "no Opus packets"),
            ("AA==", "trailing bytes"),
            ("AAAA", "zero-length"),
            ("AAVhYmM=", "incomplete"),
            (base64.b64encode(oversized).decode("ascii"), "oversized"),
        )

        for audio_base64, message in invalid:
            with self.subTest(audio_base64=audio_base64):
                with self.assertRaisesRegex(PendingThoughtRuntimeError, message):
                    body.tell("eval:42", audio_base64)
        urlopen.assert_not_called()

    @patch("gateway.pending_thought_runtime.urllib.request.urlopen")
    def test_tell_rejects_excessive_packet_count_before_playback(self, urlopen):
        framed = _FRAMED_OPUS * 4097
        body = StackChanThoughtBody(
            RecordingCaller(),
            playback_url="http://127.0.0.1:8080/play",
        )

        with self.assertRaisesRegex(PendingThoughtRuntimeError, "too many"):
            body.tell("eval:42", base64.b64encode(framed).decode("ascii"))
        urlopen.assert_not_called()

    def test_session_caller_requires_one_bound_session(self):
        loop = asyncio.new_event_loop()
        caller = SessionToolCaller(loop)
        try:
            with self.assertRaisesRegex(
                PendingThoughtRuntimeError, "session is not bound"
            ):
                caller("get_status", {})
            caller.bind(object())
            with self.assertRaisesRegex(
                PendingThoughtRuntimeError, "already bound"
            ):
                caller.bind(object())
        finally:
            loop.close()

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

    def test_consider_thought_requires_initialized_session(self):
        runtime = PendingThoughtRuntime()

        with self.assertRaisesRegex(
            PendingThoughtRuntimeError, "not initialized"
        ):
            asyncio.run(
                runtime.consider_thought(
                    {
                        "version": "v1",
                        "thought_id": "eval:42",
                        "decision": "remember",
                    }
                )
            )

    def test_consider_thought_runs_machine_submission_off_loop(self):
        runtime = PendingThoughtRuntime()
        machine = Mock()
        expected = object()
        machine.submit.return_value = expected
        runtime.machine = machine
        payload = {
            "version": "v1",
            "thought_id": "eval:42",
            "decision": "remember",
        }

        result = asyncio.run(runtime.consider_thought(payload))

        self.assertIs(result, expected)
        machine.submit.assert_called_once_with(payload)


if __name__ == "__main__":
    unittest.main()
