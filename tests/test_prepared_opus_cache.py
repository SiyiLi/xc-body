from __future__ import annotations

import asyncio
import importlib
import sys
import types
import unittest
from unittest import mock


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


if __name__ == "__main__":
    unittest.main()
