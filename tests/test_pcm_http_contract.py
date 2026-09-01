import json
import unittest
from unittest.mock import AsyncMock, patch

from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from gateway.pending_thought_runtime import (
    PendingThoughtRuntimeError,
    StackChanThoughtBody,
)
from stackchan_mcp.capture_server import (
    GATEWAY_KEY,
    PCM_TOKEN_KEY,
    handle_pcm,
)
from stackchan_mcp.tts.orchestrator import PcmStreamError


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return self._payload


class _Connection:
    def __init__(self, payload):
        self._payload = payload
        self.sent = []
        self.headers = {}

    def putrequest(self, *args):
        del args

    def putheader(self, name, value):
        self.headers[name] = value

    def endheaders(self):
        pass

    def send(self, payload):
        self.sent.append(payload)

    def getresponse(self):
        return _Response(self._payload)

    def close(self):
        pass


class PcmHttpContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_handler_success_is_accepted_by_pending_client(self):
        result = {
            "source": "http_pcm:robot:1",
            "frame_count": 2,
            "duration_ms": 120,
            "gateway_first_audio_frame_sent_ms": 1000,
            "gateway_playback_completed_ms": 1120,
        }
        app = web.Application()
        app[PCM_TOKEN_KEY] = "pcm-token"
        app[GATEWAY_KEY] = object()
        request = make_mocked_request(
            "POST",
            "/pcm",
            headers={
                "Authorization": "Bearer pcm-token",
                "X-Sample-Rate": "16000",
                "X-StackChan-Session": "device-session-1",
            },
            app=app,
        )
        send_pcm_stream = AsyncMock(return_value=result)
        with patch(
            "stackchan_mcp.tts.send_pcm_stream",
            new=send_pcm_stream,
        ):
            response = await handle_pcm(request)

        payload = response.body
        self.assertEqual(json.loads(payload), {"ok": True, **result})
        self.assertEqual(
            send_pcm_stream.call_args.kwargs["expected_session_id"],
            "device-session-1",
        )
        body = StackChanThoughtBody(
            lambda _name, _arguments: {"ok": True},
            streaming_url="http://127.0.0.1:8766/pcm",
            playback_token="pcm-token",
        )
        connection = _Connection(payload)
        with patch(
            "gateway.pending_thought_runtime.http.client.HTTPConnection",
            return_value=connection,
        ):
            accepted = body._play_pcm_stream(
                iter((b"pcm",)),
                "robot:1",
                "device-session-1",
            )

        self.assertEqual(accepted["frame_count"], 2)
        self.assertEqual(connection.sent[-1], b"0\r\n\r\n")
        self.assertEqual(
            connection.headers["X-StackChan-Session"],
            "device-session-1",
        )

        legacy_request = make_mocked_request(
            "POST",
            "/pcm",
            headers={
                "Authorization": "Bearer pcm-token",
                "X-Sample-Rate": "16000",
            },
            app=app,
        )
        legacy_send = AsyncMock(return_value=result)
        with patch(
            "stackchan_mcp.tts.send_pcm_stream",
            new=legacy_send,
        ):
            legacy_response = await handle_pcm(legacy_request)

        self.assertEqual(legacy_response.status, 200)
        self.assertIsNone(
            legacy_send.call_args.kwargs["expected_session_id"],
        )

    async def test_handler_failure_metrics_reach_pending_client(self):
        metrics = {
            "frame_count": 1,
            "frame_duration_ms": 60,
            "duration_ms": 60,
            "gateway_first_audio_frame_sent_ms": 1000,
        }
        app = web.Application()
        app[PCM_TOKEN_KEY] = "pcm-token"
        app[GATEWAY_KEY] = object()
        request = make_mocked_request(
            "POST",
            "/pcm",
            headers={
                "Authorization": "Bearer pcm-token",
                "X-Sample-Rate": "16000",
                "X-StackChan-Session": "device-session-1",
            },
            app=app,
        )
        error = PcmStreamError("Device disconnected", metrics=metrics)
        with patch(
            "stackchan_mcp.tts.send_pcm_stream",
            new=AsyncMock(side_effect=error),
        ):
            response = await handle_pcm(request)

        payload = response.body
        self.assertEqual(response.status, 500)
        self.assertEqual(
            json.loads(payload),
            {"ok": False, "error": "Device disconnected", **metrics},
        )
        body = StackChanThoughtBody(
            lambda _name, _arguments: {"ok": True},
            streaming_url="http://127.0.0.1:8766/pcm",
            playback_token="pcm-token",
        )
        connection = _Connection(payload)
        with patch(
            "gateway.pending_thought_runtime.http.client.HTTPConnection",
            return_value=connection,
        ):
            with self.assertRaises(PendingThoughtRuntimeError) as raised:
                body._play_pcm_stream(
                    iter((b"pcm",)),
                    "robot:1",
                    "device-session-1",
                )

        self.assertEqual(
            raised.exception.metrics,
            {
                "streamed_audio_frames": 1,
                "playback_audio_ms": 60,
                "gateway_first_audio_frame_sent_ms": 1000,
            },
        )


if __name__ == "__main__":
    unittest.main()
