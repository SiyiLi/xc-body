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


class FakeEsp32:
    def __init__(self, fail: bool = False) -> None:
        self.events = []
        self.fail = fail
        self.tts_lock = asyncio.Lock()

    async def send_tts_state(self, state: str, **metadata: int) -> None:
        self.events.append((state, metadata))

    async def send_audio_frame(self, packet: bytes) -> None:
        if self.fail:
            raise ConnectionError("interrupted")
        self.events.append(("packet", packet))


class PreparedOpusCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_play_requires_complete_transfer(self) -> None:
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
                                gateway, packets
                            )
                    else:
                        await capture_server._play_prepared_opus(
                            gateway, packets
                        )
                states = [
                    event[0] for event in esp32.events
                    if event[0] != "packet"
                ]
                self.assertEqual(states, expected_states)


if __name__ == "__main__":
    unittest.main()
