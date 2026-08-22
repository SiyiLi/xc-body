from __future__ import annotations

import asyncio
import importlib
import sys
import types
import unittest
from unittest import mock


esp32_client = None
if sys.version_info >= (3, 10):
    try:
        esp32_client = importlib.import_module("stackchan_mcp.esp32_client")
    except ModuleNotFoundError as error:
        if error.name not in {"aiohttp", "websockets"}:
            raise
        fake_websockets = types.ModuleType("websockets")
        fake_websockets.__path__ = []
        fake_exceptions = types.ModuleType("websockets.exceptions")
        fake_asyncio = types.ModuleType("websockets.asyncio")
        fake_asyncio.__path__ = []
        fake_server = types.ModuleType("websockets.asyncio.server")
        fake_server.ServerConnection = object
        fake_aiohttp = types.ModuleType("aiohttp")
        fake_yaml = types.ModuleType("yaml")
        fake_yaml.YAMLError = ValueError
        fake_yaml.safe_load = lambda _stream: None
        fake_pydantic = types.ModuleType("pydantic")
        fake_pydantic.BaseModel = object
        fake_pydantic.Field = lambda **kwargs: kwargs.get("default_factory")
        with mock.patch.dict(
            sys.modules,
            {
                "aiohttp": fake_aiohttp,
                "yaml": fake_yaml,
                "pydantic": fake_pydantic,
                "websockets": fake_websockets,
                "websockets.exceptions": fake_exceptions,
                "websockets.asyncio": fake_asyncio,
                "websockets.asyncio.server": fake_server,
            },
        ):
            esp32_client = importlib.import_module(
                "stackchan_mcp.esp32_client"
            )

ESP32Manager = (
    esp32_client.ESP32Manager if esp32_client is not None else None
)


class FakeConnection:
    def __init__(self) -> None:
        self.connected = True
        self.initialized = True
        self.session_id = "device-session-1"
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def call_tool(
        self, name: str, arguments: dict[str, object]
    ) -> tuple[dict[str, object], None]:
        self.calls.append((name, arguments))
        return {"ok": True}, None


@unittest.skipIf(ESP32Manager is None, "requires Python 3.10+")
class XcBodyBehaviorTests(unittest.IsolatedAsyncioTestCase):
    async def test_behavior_failures_clean_the_correlated_waiter(self) -> None:
        async def run_failure(mode: str) -> str:
            manager = ESP32Manager()
            connection = FakeConnection()
            manager._connection = connection
            behavior_id = f"robot:{mode}"

            if mode == "timeout":
                async def immediate_timeout(_waiter, timeout):
                    self.assertEqual(
                        timeout,
                        esp32_client.XC_BODY_BEHAVIOR_TIMEOUT_S,
                    )
                    raise asyncio.TimeoutError

                with mock.patch.object(
                    esp32_client.asyncio,
                    "wait_for",
                    side_effect=immediate_timeout,
                ):
                    result, error = (
                        await manager.perform_xc_body_behavior(
                            behavior_id, "attention"
                        )
                    )
            else:
                task = asyncio.create_task(
                    manager.perform_xc_body_behavior(
                        behavior_id, "attention"
                    )
                )
                await self._wait_for_behavior(manager, behavior_id)
                if mode == "disconnect":
                    manager._fail_behavior_waiters(connection.session_id)
                else:
                    await manager._emit_stackchan_event(
                        {
                            "event_type": "behavior",
                            "subtype": "behavior_failed",
                            "behavior_id": behavior_id,
                            "duration_ms": 20000,
                            "ts": 1,
                            "session_id": connection.session_id,
                        }
                    )
                result, error = await task

            self.assertIsNone(result)
            self.assertEqual(manager._behavior_waiters, {})
            return error["message"]

        self.assertEqual(
            await run_failure("timeout"),
            "robot behavior did not complete",
        )
        self.assertEqual(
            await run_failure("disconnect"),
            "ESP32 disconnected",
        )
        self.assertEqual(
            await run_failure("firmware"),
            "robot behavior failed",
        )

    async def test_knock_keeps_pr2_device_tool_compatibility(self) -> None:
        manager = ESP32Manager()
        connection = FakeConnection()
        manager._connection = connection

        task = asyncio.create_task(manager.perform_xc_body_knock("offer:1"))
        await self._wait_for_behavior(manager, "offer:1")
        await manager._emit_stackchan_event(
            {
                "event_type": "behavior",
                "subtype": "knock_complete",
                "behavior_id": "offer:1",
                "duration_ms": 11000,
                "ts": 1,
                "session_id": connection.session_id,
            }
        )

        result, error = await task
        self.assertIsNone(error)
        self.assertEqual(result["behavior_id"], "offer:1")
        self.assertEqual(
            connection.calls,
            [
                (
                    "self.robot.xc_body_knock",
                    {"behavior_id": "offer:1"},
                )
            ],
        )

    @staticmethod
    async def _wait_for_behavior(
        manager: ESP32Manager, behavior_id: str
    ) -> None:
        for _ in range(100):
            if any(
                key[1] == behavior_id for key in manager._behavior_waiters
            ):
                return
            await asyncio.sleep(0)
        raise AssertionError("behavior waiter was not registered")


if __name__ == "__main__":
    unittest.main()
