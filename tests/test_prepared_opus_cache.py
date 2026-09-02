from __future__ import annotations

import asyncio
import importlib
import json
import sys
import types
import unittest
from unittest import mock

from stackchan_mcp.esp32_client import ESP32Connection
from stackchan_mcp.tts.orchestrator import send_pcm_stream


try:
    capture_server = importlib.import_module("stackchan_mcp.capture_server")
except ModuleNotFoundError as error:
    if error.name != "aiohttp":
        raise
    fake_web = types.SimpleNamespace(
        AppKey=lambda *args: object(),
        Application=object,
        Request=object,
        Response=object,
        json_response=lambda *args, **kwargs: None,
    )
    with mock.patch.dict(
        sys.modules,
        {"aiohttp": types.SimpleNamespace(web=fake_web)},
    ):
        capture_server = importlib.import_module(
            "stackchan_mcp.capture_server"
        )


class FakeConnection:
    def __init__(self, fail: bool = False) -> None:
        self.events = []
        self.fail = fail
        self.connected = True

    async def send_tts_state(self, state: str, **metadata: object) -> None:
        self.events.append((state, metadata))

    async def send_audio_frame(self, packet: bytes) -> None:
        if self.fail:
            raise ConnectionError("interrupted")
        self.events.append(("packet", packet))


class FakeEsp32:
    def __init__(self, fail: bool = False) -> None:
        self.connection = FakeConnection(fail)
        self.tts_lock = asyncio.Lock()


class _Encoder:
    def __init__(self, *args) -> None:
        del args

    def encode(self, pcm, samples_per_frame) -> bytes:
        del pcm, samples_per_frame
        return b"opus-frame"


class PreparedOpusCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_play_sends_correlated_metrics_id(self) -> None:
        packets = [b"one", b"two"]
        for fail, expected_states in (
            (False, ["prepare", "play", "stop"]),
            (True, ["prepare", "stop"]),
        ):
            with self.subTest(fail=fail):
                esp32 = FakeEsp32(fail)
                gateway = types.SimpleNamespace(esp32=esp32)
                with mock.patch.object(
                    capture_server.asyncio,
                    "sleep",
                    mock.AsyncMock(),
                ):
                    if fail:
                        with self.assertRaises(ConnectionError):
                            await capture_server._play_prepared_opus(
                                gateway, packets, "robot:1"
                            )
                    else:
                        await capture_server._play_prepared_opus(
                            gateway, packets, "robot:1"
                        )
                states = [
                    event[0]
                    for event in esp32.connection.events
                    if event[0] != "packet"
                ]
                self.assertEqual(states, expected_states)
                for state, metadata in esp32.connection.events:
                    if state in ("prepare", "stop"):
                        self.assertEqual(metadata["transfer_id"], "robot:1")
                    elif state == "play":
                        self.assertNotIn("transfer_id", metadata)

    async def test_session_change_fails_playback(self) -> None:
        esp32 = FakeEsp32()
        original = esp32.connection
        gateway = types.SimpleNamespace(esp32=esp32)
        sleeps = 0

        async def replace_session(_delay: float) -> None:
            nonlocal sleeps
            sleeps += 1
            if sleeps == 2:
                original.connected = False
                esp32.connection = FakeConnection()

        with mock.patch.object(
            capture_server.asyncio,
            "sleep",
            side_effect=replace_session,
        ):
            with self.assertRaisesRegex(ConnectionError, "session changed"):
                await capture_server._play_prepared_opus(
                    gateway, [b"one"], "robot:1"
                )

        self.assertEqual(esp32.connection.events, [])

    async def test_prepared_opus_holds_tts_lane_before_direct_pcm(self) -> None:
        class WebSocket:
            def __init__(self) -> None:
                self.messages = []

            async def send(self, message) -> None:
                self.messages.append(message)

            async def close(self, **_kwargs) -> None:
                pass

            def drain_ids(self) -> list[str]:
                return [
                    payload["drain_id"]
                    for message in self.messages
                    if isinstance(message, str)
                    for payload in (json.loads(message),)
                    if payload.get("state") == "stop"
                    and payload.get("drain_id")
                ]

        async def one_frame():
            yield b"\x00" * 1920

        async def wait_for_drains(websocket, count: int) -> None:
            while len(websocket.drain_ids()) < count:
                await asyncio.sleep(0.001)

        websocket = WebSocket()
        connection = ESP32Connection(websocket, "device-session-1")
        connection.tts_drain_ack = True
        connection.direct_audio_metrics = True
        esp32 = types.SimpleNamespace(
            connection=connection,
            tts_lock=asyncio.Lock(),
        )
        gateway = types.SimpleNamespace(esp32=esp32)
        opuslib = types.SimpleNamespace(
            Encoder=_Encoder,
            APPLICATION_VOIP=object(),
        )
        with mock.patch.dict(sys.modules, {"opuslib": opuslib}):
            prepared = asyncio.create_task(
                capture_server._play_prepared_opus(gateway, [b"one"], "robot:1")
            )
            await asyncio.wait_for(wait_for_drains(websocket, 1), timeout=1)
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
            await prepared
            await asyncio.wait_for(wait_for_drains(websocket, 2), timeout=1)
            connection.handle_tts_drained(
                {
                    "type": "tts",
                    "state": "drained",
                    "drain_id": websocket.drain_ids()[1],
                    "ok": True,
                    "accepted_frames": 1,
                    "rejected_frames": 0,
                    "codec_output_frames": 1,
                    "max_codec_write_gap_ms": 0,
                }
            )
            await direct


if __name__ == "__main__":
    unittest.main()
