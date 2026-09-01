import asyncio
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from stackchan_mcp.tts.orchestrator import PcmStreamError, send_pcm_stream


class _Encoder:
    def __init__(self, *args):
        del args

    def encode(self, pcm, samples_per_frame):
        del pcm, samples_per_frame
        return b"opus-frame"


class _Connection:
    def __init__(self, esp32, session_id="device-session-1"):
        self._esp32 = esp32
        self.session_id = session_id
        self.protocol_version = 1
        self.connected = True

    async def send_tts_state(self, state):
        self._esp32.states.append((self.session_id, state))

    async def send_audio_frame(self, frame):
        if not self.connected:
            raise ConnectionError("ESP32 not connected")
        self._esp32.frames.append((self.session_id, frame))


class _Esp32:
    def __init__(self):
        self.tts_lock = asyncio.Lock()
        self.states = []
        self.frames = []
        self.connection = _Connection(self)


class StreamingPcmTests(unittest.IsolatedAsyncioTestCase):
    async def test_producer_failure_after_first_frame_stops_tts(self):
        async def chunks():
            yield b"\x00" * 1920
            raise RuntimeError("producer failed")

        esp32 = _Esp32()
        gateway = SimpleNamespace(esp32=esp32)
        opuslib = types.SimpleNamespace(
            Encoder=_Encoder,
            APPLICATION_VOIP=object(),
        )
        with patch.dict(sys.modules, {"opuslib": opuslib}), patch(
            "stackchan_mcp.tts.orchestrator.asyncio.sleep",
            new=AsyncMock(),
        ):
            with self.assertRaisesRegex(RuntimeError, "producer failed"):
                await send_pcm_stream(gateway, chunks())

        self.assertEqual(
            esp32.frames,
            [("device-session-1", b"opus-frame")],
        )
        self.assertEqual(
            esp32.states,
            [
                ("device-session-1", "start"),
                ("device-session-1", "stop"),
            ],
        )

    async def test_device_failure_returns_partial_metrics_and_stops_tts(self):
        async def chunks():
            yield b"\x00" * 1920
            yield b"\x00" * 1920

        class FailingConnection(_Connection):
            async def send_audio_frame(self, frame):
                if self._esp32.frames:
                    raise ConnectionError("lost")
                await super().send_audio_frame(frame)

        esp32 = _Esp32()
        esp32.connection = FailingConnection(esp32)
        gateway = SimpleNamespace(esp32=esp32)
        opuslib = types.SimpleNamespace(
            Encoder=_Encoder,
            APPLICATION_VOIP=object(),
        )
        with patch.dict(sys.modules, {"opuslib": opuslib}), patch(
            "stackchan_mcp.tts.orchestrator.asyncio.sleep",
            new=AsyncMock(),
        ):
            with self.assertRaises(PcmStreamError) as raised:
                await send_pcm_stream(
                    gateway,
                    chunks(),
                    expected_session_id="device-session-1",
                )

        self.assertEqual(
            esp32.states,
            [
                ("device-session-1", "start"),
                ("device-session-1", "stop"),
            ],
        )
        self.assertEqual(raised.exception.metrics["frame_count"], 1)
        self.assertEqual(raised.exception.metrics["duration_ms"], 60)
        self.assertIn(
            "gateway_first_audio_frame_sent_ms",
            raised.exception.metrics,
        )
        self.assertNotIn(
            "gateway_playback_completed_ms",
            raised.exception.metrics,
        )

    async def test_session_replacement_never_receives_streamed_pcm(self):
        async def chunks():
            yield b"\x00" * 1920
            esp32.connection.connected = False
            esp32.connection = _Connection(esp32, "device-session-2")
            yield b"\x00" * 1920

        esp32 = _Esp32()
        gateway = SimpleNamespace(esp32=esp32)
        opuslib = types.SimpleNamespace(
            Encoder=_Encoder,
            APPLICATION_VOIP=object(),
        )
        with patch.dict(sys.modules, {"opuslib": opuslib}), patch(
            "stackchan_mcp.tts.orchestrator.asyncio.sleep",
            new=AsyncMock(),
        ):
            with self.assertRaises(PcmStreamError) as raised:
                await send_pcm_stream(
                    gateway,
                    chunks(),
                    expected_session_id="device-session-1",
                )

        self.assertEqual(
            esp32.frames,
            [("device-session-1", b"opus-frame")],
        )
        self.assertEqual(
            esp32.states,
            [("device-session-1", "start")],
        )
        self.assertEqual(raised.exception.metrics["frame_count"], 1)

    async def test_replaced_session_is_rejected_before_tts_starts(self):
        async def chunks():
            if False:
                yield b"pcm"

        esp32 = _Esp32()
        esp32.connection = _Connection(esp32, "device-session-2")
        gateway = SimpleNamespace(esp32=esp32)
        opuslib = types.SimpleNamespace(
            Encoder=_Encoder,
            APPLICATION_VOIP=object(),
        )
        with patch.dict(sys.modules, {"opuslib": opuslib}):
            with self.assertRaisesRegex(
                RuntimeError,
                "session changed before streamed playback",
            ):
                await send_pcm_stream(
                    gateway,
                    chunks(),
                    expected_session_id="device-session-1",
                )

        self.assertEqual(esp32.frames, [])
        self.assertEqual(esp32.states, [])


if __name__ == "__main__":
    unittest.main()
