import asyncio
import json
import unittest
from unittest.mock import patch

from stackchan_mcp import esp32_client
from stackchan_mcp.esp32_client import (
    ESP32Connection,
    stop_tts_after_drain,
)


class _WebSocket:
    def __init__(self) -> None:
        self.messages = []
        self.stop_sent = asyncio.Event()
        self.closed = False

    async def send(self, message) -> None:
        self.messages.append(message)
        if isinstance(message, str) and json.loads(message).get("state") == "stop":
            self.stop_sent.set()

    async def close(self, **_kwargs) -> None:
        self.closed = True

    def drain_id(self) -> str:
        for message in reversed(self.messages):
            if not isinstance(message, str):
                continue
            drain_id = json.loads(message).get("drain_id")
            if drain_id:
                return drain_id
        raise AssertionError("Expected a drain request")


class TtsDrainTests(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_firmware_uses_stop_without_drain_wait(self) -> None:
        websocket = _WebSocket()
        connection = ESP32Connection(websocket, "device-session-1")

        result = await stop_tts_after_drain(connection)

        self.assertIsNone(result)
        self.assertEqual(
            json.loads(websocket.messages[0]),
            {
                "session_id": "device-session-1",
                "type": "tts",
                "state": "stop",
            },
        )

    async def test_timeout_after_invalid_ids_fences_the_session(self) -> None:
        websocket = _WebSocket()
        connection = ESP32Connection(websocket, "device-session-1")
        connection.tts_drain_ack = True

        with patch.object(esp32_client, "RESPONSE_TIMEOUT", 0.01):
            waiting = asyncio.create_task(stop_tts_after_drain(connection))
            await asyncio.wait_for(websocket.stop_sent.wait(), timeout=1)
            connection.handle_tts_drained({"drain_id": []})
            connection.handle_tts_drained(
                {"drain_id": "wrong", "ok": True}
            )
            with self.assertRaises(TimeoutError):
                await waiting

        self.assertFalse(connection.connected)
        self.assertTrue(websocket.closed)

    async def test_malformed_matching_drain_reply_fences_the_session(self) -> None:
        websocket = _WebSocket()
        connection = ESP32Connection(websocket, "device-session-1")
        connection.tts_drain_ack = True

        waiting = asyncio.create_task(stop_tts_after_drain(connection))
        await asyncio.wait_for(websocket.stop_sent.wait(), timeout=1)
        connection.handle_tts_drained({"drain_id": websocket.drain_id()})
        with self.assertRaisesRegex(RuntimeError, "Malformed TTS drain"):
            await waiting

        self.assertFalse(connection.connected)
        self.assertTrue(websocket.closed)

    async def test_disconnect_while_waiting_for_drain_fails_promptly(self) -> None:
        websocket = _WebSocket()
        connection = ESP32Connection(websocket, "device-session-1")
        connection.tts_drain_ack = True

        waiting = asyncio.create_task(stop_tts_after_drain(connection))
        await asyncio.wait_for(websocket.stop_sent.wait(), timeout=1)
        connection.disconnect()
        with self.assertRaises(ConnectionError):
            await asyncio.wait_for(waiting, timeout=1)

        self.assertFalse(connection.connected)

    async def test_negative_drain_reply_fences_the_session(self) -> None:
        websocket = _WebSocket()
        connection = ESP32Connection(websocket, "device-session-1")
        connection.tts_drain_ack = True

        waiting = asyncio.create_task(stop_tts_after_drain(connection))
        await asyncio.wait_for(websocket.stop_sent.wait(), timeout=1)
        self.assertFalse(waiting.done())
        connection.handle_tts_drained(
            {"drain_id": websocket.drain_id(), "ok": False}
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "Firmware TTS drain did not complete",
        ):
            await waiting

        self.assertFalse(connection.connected)
        self.assertTrue(websocket.closed)
        with self.assertRaises(ConnectionError):
            await connection.send_tts_state("start")


if __name__ == "__main__":
    unittest.main()
