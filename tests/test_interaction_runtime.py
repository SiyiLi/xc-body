import asyncio
import base64
import threading
import unittest
from unittest.mock import AsyncMock, Mock, patch

from gateway.interaction_runtime import (
    InteractionRuntime,
    InteractionRuntimeError,
    XcBodyInteractionBody,
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
    body = XcBodyInteractionBody(caller, **kwargs)
    body.mark_device_ready(_READY_STATUS["session_id"])
    return body


class InteractionRuntimeTests(unittest.TestCase):
    def test_expression_failure_is_reported(self):
        caller = RecordingCaller([RuntimeError("expression failed")])
        body = ready_body(caller)

        with self.assertRaisesRegex(
            InteractionRuntimeError,
            "expression failed",
        ):
            body.perform_expression("pleased")

    def test_expression_uses_the_existing_body_lane(self):
        caller = RecordingCaller()
        body = ready_body(caller)

        body.perform_expression("curious")

        self.assertEqual(
            caller.calls,
            [
                ("get_status", {}),
                ("perform_expression", {"expression": "curious"}),
            ],
        )

    @patch("gateway.interaction_runtime.urllib.request.urlopen")
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
            [("get_status", {})],
        )

    @patch("gateway.interaction_runtime.urllib.request.urlopen")
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

        with self.assertRaisesRegex(InteractionRuntimeError, "unavailable"):
            body.tell("eval:42", _PREPARED_AUDIO_BASE64)
        body.tell("eval:42", _PREPARED_AUDIO_BASE64)

        self.assertEqual(urlopen.call_count, 2)

    def test_body_rejects_unverified_or_reconnected_device_session(self):
        caller = RecordingCaller()
        body = XcBodyInteractionBody(caller)

        with self.assertRaisesRegex(
            InteractionRuntimeError,
            "device is not ready",
        ):
            body.perform_expression("curious")
        self.assertEqual(caller.calls, [])

        body.mark_device_ready("device-session-1")
        caller.status["session_id"] = "device-session-2"
        with self.assertRaisesRegex(
            InteractionRuntimeError,
            "device is not ready",
        ):
            body.perform_expression("curious")
        self.assertEqual(caller.calls, [("get_status", {})])

    def test_runtime_owns_machine_for_session_lifetime(self):
        runtime = InteractionRuntime()
        first_session = object()
        second_session = object()

        with patch(
            "gateway.interaction_runtime.create_stackchan_client_session",
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
        runtime = InteractionRuntime()
        runtime.body = body
        runtime.machine = Mock(pending_thought_id=None)

        self.assertTrue(asyncio.run(runtime.is_ready()))
        caller.status["session_id"] = "device-session-2"
        self.assertFalse(asyncio.run(runtime.is_ready()))

    def test_direct_stream_waits_for_attention_before_opening_pcm(self):
        class Pcm:
            def __init__(self):
                self.calls = []

            def wait_for_playable(self):
                self.calls.append("wait")

            def iter_pcm_chunks(self):
                self.calls.append("iterate")
                return iter((b"pcm",))

        caller = RecordingCaller()
        body = ready_body(
            caller,
            streaming_url="http://127.0.0.1:8766/pcm",
        )
        pcm = Pcm()
        with patch.object(
            body,
            "_play_pcm_stream",
            return_value={
                "ok": True,
                "frame_count": 1,
                "duration_ms": 60,
                "gateway_first_audio_frame_sent_ms": 1000,
                "gateway_playback_completed_ms": 1060,
            },
        ) as play:
            metrics = body.tell_direct_stream("robot:1", "pleased", pcm)

        self.assertEqual(
            caller.calls,
            [
                ("get_status", {}),
                (
                    "perform_expression",
                    {"expression": "pleased"},
                ),
            ],
        )
        self.assertEqual(pcm.calls, ["wait", "iterate"])
        self.assertEqual(play.call_args.args[1], "robot:1")
        self.assertEqual(play.call_args.args[2], "device-session-1")
        self.assertEqual(metrics["streamed_audio_frames"], 1)
        self.assertEqual(metrics["gateway_first_audio_frame_sent_ms"], 1000)

    def test_direct_stream_never_opens_pcm_after_producer_failure(self):
        class Pcm:
            def wait_for_playable(self):
                raise RuntimeError("producer failed")

            def iter_pcm_chunks(self):
                raise AssertionError("PCM iterator must not be opened")

        body = ready_body(
            RecordingCaller(),
            streaming_url="http://127.0.0.1:8766/pcm",
        )
        with patch.object(body, "_play_pcm_stream") as play:
            with self.assertRaisesRegex(
                InteractionRuntimeError,
                "direct stream: RuntimeError",
            ):
                body.tell_direct_stream("robot:1", "concerned", Pcm())

        play.assert_not_called()

    def test_stream_failure_after_pcm_write_returns_partial_metrics(self):
        class Response:
            def read(self):
                return (
                    b'{"ok":true,"frame_count":1,"duration_ms":60,'
                    b'"gateway_first_audio_frame_sent_ms":1000,'
                    b'"gateway_playback_completed_ms":1060}'
                )

        class Connection:
            def __init__(self):
                self.sent = []
                self.closed = False

            def putrequest(self, *args):
                del args

            def putheader(self, *args):
                del args

            def endheaders(self):
                pass

            def send(self, payload):
                self.sent.append(payload)

            def getresponse(self):
                return Response()

            def close(self):
                self.closed = True

        def chunks():
            yield b"pcm"
            raise RuntimeError("producer failed")

        connection = Connection()
        body = ready_body(
            RecordingCaller(),
            streaming_url="http://127.0.0.1:8766/pcm",
        )
        with patch(
            "gateway.interaction_runtime.http.client.HTTPConnection",
            return_value=connection,
        ):
            with self.assertRaisesRegex(
                InteractionRuntimeError,
                "stream audio: RuntimeError",
            ) as raised:
                body._play_pcm_stream(
                    chunks(),
                    "robot:1",
                    "device-session-1",
                )

        self.assertEqual(
            connection.sent,
            [b"3\r\n", b"pcm", b"\r\n", b"0\r\n\r\n"],
        )
        self.assertTrue(connection.closed)
        self.assertEqual(raised.exception.metrics["streamed_audio_frames"], 1)
        self.assertNotIn(
            "gateway_playback_completed_ms",
            raised.exception.metrics,
        )

    def test_direct_stream_keeps_the_body_lane_until_playback_ends(self):
        class Pcm:
            def wait_for_playable(self):
                pass

            def iter_pcm_chunks(self):
                return iter((b"pcm",))

        playback_started = threading.Event()
        release_playback = threading.Event()
        presentation_finished = threading.Event()
        body = ready_body(
            RecordingCaller(),
            streaming_url="http://127.0.0.1:8766/pcm",
        )

        def play(*args):
            del args
            playback_started.set()
            release_playback.wait(timeout=1)
            return {"ok": True}

        def present():
            body.perform_expression("surprised")
            presentation_finished.set()

        with patch.object(body, "_play_pcm_stream", side_effect=play):
            direct = threading.Thread(
                target=body.tell_direct_stream,
                args=("robot:1", "curious", Pcm()),
            )
            direct.start()
            self.assertTrue(playback_started.wait(timeout=1))
            contender = threading.Thread(target=present)
            contender.start()
            self.assertFalse(presentation_finished.wait(timeout=0.02))
            release_playback.set()
            direct.join(timeout=1)
            contender.join(timeout=1)

        self.assertFalse(direct.is_alive())
        self.assertTrue(presentation_finished.is_set())

if __name__ == "__main__":
    unittest.main()
