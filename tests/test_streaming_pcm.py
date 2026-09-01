import asyncio
import json
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from stackchan_mcp.esp32_client import ESP32Connection
from stackchan_mcp.tts.orchestrator import (
    PcmStreamError,
    send_pcm_audio,
    send_pcm_stream,
)


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

    @property
    def device_connected(self):
        return self.connection.connected

    async def send_tts_state(self, state):
        await self.connection.send_tts_state(state)

    async def send_audio_frame(self, frame):
        await self.connection.send_audio_frame(frame)


class _WebSocket:
    def __init__(self):
        self.messages = []
        self.drain_requested = asyncio.Event()
        self.closed = False

    async def send(self, message):
        self.messages.append(message)
        if not isinstance(message, str):
            return
        payload = json.loads(message)
        if payload.get("state") == "stop" and payload.get("drain_id"):
            self.drain_requested.set()

    async def close(self, **_kwargs):
        self.closed = True

    def drain_ids(self):
        return [
            payload["drain_id"]
            for message in self.messages
            if isinstance(message, str)
            for payload in (json.loads(message),)
            if payload.get("state") == "stop" and payload.get("drain_id")
        ]


async def _wait_for_drain_ids(websocket, count):
    while len(websocket.drain_ids()) < count:
        await asyncio.sleep(0.001)


class StreamingPcmTests(unittest.IsolatedAsyncioTestCase):
    async def test_direct_pcm_holds_tts_lane_until_firmware_drain(self):
        async def one_frame():
            yield b"\x00" * 1920

        websocket = _WebSocket()
        connection = ESP32Connection(websocket, "device-session-1")
        connection.tts_drain_ack = True
        esp32 = SimpleNamespace(
            tts_lock=asyncio.Lock(),
            connection=connection,
        )
        gateway = SimpleNamespace(esp32=esp32)
        opuslib = types.SimpleNamespace(
            Encoder=_Encoder,
            APPLICATION_VOIP=object(),
        )
        with patch.dict(sys.modules, {"opuslib": opuslib}):
            direct = asyncio.create_task(
                send_pcm_stream(gateway, one_frame())
            )
            await asyncio.wait_for(websocket.drain_requested.wait(), timeout=1)
            self.assertTrue(esp32.tts_lock.locked())
            first_drain_id = websocket.drain_ids()[0]

            next_playback = asyncio.create_task(
                send_pcm_stream(gateway, one_frame())
            )
            await asyncio.sleep(0.01)
            self.assertFalse(next_playback.done())

            connection.handle_tts_drained(
                {
                    "type": "tts",
                    "state": "drained",
                    "drain_id": first_drain_id,
                    "ok": True,
                }
            )
            await direct
            await _wait_for_drain_ids(websocket, 2)
            connection.handle_tts_drained(
                {
                    "type": "tts",
                    "state": "drained",
                    "drain_id": websocket.drain_ids()[1],
                    "ok": True,
                }
            )
            await next_playback

        self.assertEqual(
            [
                (payload["state"], bool(payload.get("drain_id")))
                for message in websocket.messages
                if isinstance(message, str)
                for payload in (json.loads(message),)
            ],
            [
                ("start", False),
                ("stop", True),
                ("start", False),
                ("stop", True),
            ],
        )

    async def test_ordinary_pcm_holds_tts_lane_before_direct_pcm(self):
        async def one_frame():
            yield b"\x00" * 1920

        websocket = _WebSocket()
        connection = ESP32Connection(websocket, "device-session-1")
        connection.tts_drain_ack = True
        esp32 = _Esp32()
        esp32.connection = connection
        gateway = SimpleNamespace(esp32=esp32)
        opuslib = types.SimpleNamespace(
            Encoder=_Encoder,
            APPLICATION_VOIP=object(),
        )
        with patch.dict(sys.modules, {"opuslib": opuslib}):
            ordinary = asyncio.create_task(
                send_pcm_audio(gateway, b"\x00" * 1920)
            )
            await asyncio.wait_for(websocket.drain_requested.wait(), timeout=1)
            direct = asyncio.create_task(send_pcm_stream(gateway, one_frame()))
            await asyncio.sleep(0.01)
            self.assertFalse(direct.done())

            connection.handle_tts_drained(
                {
                    "type": "tts",
                    "state": "drained",
                    "drain_id": websocket.drain_ids()[0],
                    "ok": True,
                }
            )
            await ordinary
            await _wait_for_drain_ids(websocket, 2)
            connection.handle_tts_drained(
                {
                    "type": "tts",
                    "state": "drained",
                    "drain_id": websocket.drain_ids()[1],
                    "ok": True,
                }
            )
            await direct

    async def test_ordinary_pcm_uses_replacement_session_after_waiting(self):
        esp32 = _Esp32()
        gateway = SimpleNamespace(esp32=esp32)
        old_connection = esp32.connection
        replacement = _Connection(esp32, "device-session-2")
        opuslib = types.SimpleNamespace(
            Encoder=_Encoder,
            APPLICATION_VOIP=object(),
        )
        await esp32.tts_lock.acquire()
        try:
            with patch.dict(sys.modules, {"opuslib": opuslib}):
                ordinary = asyncio.create_task(
                    send_pcm_audio(gateway, b"\x00" * 1920)
                )
                await asyncio.sleep(0)
                old_connection.connected = False
                esp32.connection = replacement
                esp32.tts_lock.release()
                result = await ordinary
        finally:
            if esp32.tts_lock.locked():
                esp32.tts_lock.release()

        self.assertEqual(result["frame_count"], 1)
        self.assertEqual(
            esp32.states,
            [
                ("device-session-2", "start"),
                ("device-session-2", "stop"),
            ],
        )
        self.assertEqual(
            esp32.frames,
            [("device-session-2", b"opus-frame")],
        )

    async def test_ordinary_pcm_fails_when_its_pinned_session_disconnects(self):
        esp32 = _Esp32()
        gateway = SimpleNamespace(esp32=esp32)
        pinned = esp32.connection
        replacement = _Connection(esp32, "device-session-2")
        opuslib = types.SimpleNamespace(
            Encoder=_Encoder,
            APPLICATION_VOIP=object(),
        )

        async def disconnect_pinned_session():
            pinned.connected = False
            esp32.connection = replacement

        with patch.dict(sys.modules, {"opuslib": opuslib}):
            with self.assertRaisesRegex(RuntimeError, "disconnected"):
                await send_pcm_audio(
                    gateway,
                    b"\x00" * 1920,
                    before_first_frame=disconnect_pinned_session,
                )

        self.assertEqual(
            esp32.states,
            [("device-session-1", "start")],
        )
        self.assertEqual(esp32.frames, [])

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
