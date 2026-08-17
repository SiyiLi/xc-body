import asyncio
import io
import json
import sys
import threading
import types
import unittest
from contextlib import contextmanager, redirect_stderr
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from gateway.embodiment import ExpressionAndIdleError, IntentRequestError
from stackchan.avatar_verification import REVIEWED_AVATAR_CHECKSUM
from gateway.semantic_service import (
    SemanticServiceError,
    create_service_server,
    main,
    run_service_streams,
)


VALID_CHECKSUM = REVIEWED_AVATAR_CHECKSUM
VALID_STATUS = {
    "connected": True,
    "initialized": True,
    "session_id": "device-session",
}


class SemanticServiceTests(unittest.TestCase):
    def test_import_and_server_creation_have_no_transport_side_effects(self):
        with fake_mcp_server() as server_class:
            server = create_service_server(Mock())

        self.assertIsInstance(server, server_class)
        self.assertEqual(server.name, "xc-body-semantic-embodiment")
        tools = asyncio.run(server.list_tools_handler())
        self.assertEqual([tool.name for tool in tools], ["embody"])
        self.assertEqual(
            tools[0].inputSchema["properties"]["intent"]["enum"],
            ["idle", "curious", "pleased", "concerned"],
        )

    def test_main_reports_missing_avatar_path(self):
        stderr = io.StringIO()
        environment = {
            "XC_BODY_STACKCHAN_MCP_URL": "https://daemon.invalid/mcp",
            "XC_BODY_STACKCHAN_MCP_TOKEN": "test-token",
        }

        with redirect_stderr(stderr):
            exit_code = main([], environ=environment)

        self.assertEqual(exit_code, 1)
        payload = json.loads(stderr.getvalue())
        self.assertEqual(payload["error"], "RunnerConfigError")
        self.assertIn("avatar archive path is required", payload["message"])

    def test_main_runs_import_safe_service_boundary(self):
        service = AsyncMock()
        environment = {
            "XC_BODY_STACKCHAN_MCP_URL": "https://daemon.invalid/mcp",
            "XC_BODY_STACKCHAN_MCP_TOKEN": "test-token",
            "XC_BODY_AVATAR_ARCHIVE_PATH": "/state/native.rgb565le",
        }

        with patch("gateway.semantic_service.run_stdio_service", service):
            exit_code = main([], environ=environment)

        self.assertEqual(exit_code, 0)
        (config,) = service.await_args.args
        self.assertEqual(config.url, "https://daemon.invalid/mcp")
        self.assertNotIn("test-token", repr(config))
        self.assertEqual(config.avatar_path, "/state/native.rgb565le")

    def test_stream_runner_loads_avatar_once_before_downstream(self):
        events = []

        class Session:
            def __init__(self, read, write):
                events.append(("session", read, write))

            async def __aenter__(self):
                events.append("upstream-enter")
                return self

            async def __aexit__(self, *args):
                events.append("upstream-exit")

            async def initialize(self):
                events.append("upstream-initialize")

            async def call_tool(self, name, arguments):
                events.append(("upstream-call", name, arguments))
                payload = (
                    VALID_STATUS
                    if name == "get_status"
                    else {"ok": True, "checksum": VALID_CHECKSUM}
                )
                return SimpleNamespace(
                    content=[
                        SimpleNamespace(
                            text=json.dumps(payload)
                        )
                    ],
                    is_error=False,
                )

        class Server:
            def create_initialization_options(self):
                return "options"

            async def run(self, read, write, options):
                events.append(("downstream-run", read, write, options))

        def create_server(caller, *, recipe_executor):
            del recipe_executor
            events.append(("server-create", caller))
            return Server()

        with (
            fake_mcp_client(Session),
            patch(
                "gateway.semantic_service.create_service_server",
                side_effect=create_server,
            ),
        ):
            asyncio.run(
                run_service_streams(
                    "up-read",
                    "up-write",
                    "down-read",
                    "down-write",
                    avatar_path="/state/native.rgb565le",
                )
            )

        calls = [event for event in events if event[0] == "upstream-call"]
        self.assertEqual(
            [call[1] for call in calls],
            ["get_status", "load_avatar_set", "get_status"],
        )
        load_call = calls[1]
        self.assertEqual(
            load_call[2],
            {
                "archive_path": "/state/native.rgb565le",
                "mode": "layered-320x240",
                "timeout": 120,
            },
        )
        self.assertLess(
            events.index(load_call),
            next(
                index
                for index, event in enumerate(events)
                if event[0] == "downstream-run"
            ),
        )
        self.assertEqual(events[-1], "upstream-exit")

    def test_invalid_avatar_load_fails_before_server_run(self):
        events = []

        class Session:
            def __init__(self, read, write):
                events.append(("session", read, write))

            async def __aenter__(self):
                events.append("upstream-enter")
                return self

            async def __aexit__(self, *args):
                events.append("upstream-exit")

            async def initialize(self):
                events.append("upstream-initialize")

            async def call_tool(self, name, arguments):
                events.append(("upstream-call", name, arguments))
                if name == "get_status":
                    return VALID_STATUS
                return {
                    "structuredContent": {
                        "ok": False,
                        "error": "device checksum mismatch",
                    }
                }

        server_factory = Mock()
        with (
            fake_mcp_client(Session),
            patch(
                "gateway.semantic_service.create_service_server",
                server_factory,
            ),
            self.assertRaisesRegex(
                SemanticServiceError,
                "device checksum mismatch",
            ),
        ):
            asyncio.run(
                run_service_streams(
                    "up-read",
                    "up-write",
                    "down-read",
                    "down-write",
                    avatar_path="/state/native.rgb565le",
                )
            )

        server_factory.assert_not_called()
        self.assertEqual(events[-1], "upstream-exit")

    def test_stream_shutdown_drains_active_recipe_before_upstream_close(self):
        events = []
        verified_sessions = []
        recipe_started = threading.Event()
        release_recipe = threading.Event()
        active_server = None

        class Session:
            def __init__(self, read, write):
                del read, write

            async def __aenter__(self):
                events.append("upstream-enter")
                return self

            async def __aexit__(self, *args):
                del args
                events.append("upstream-exit")

            async def initialize(self):
                events.append("upstream-initialize")

            async def call_tool(self, name, arguments):
                del arguments
                if name == "get_status":
                    return VALID_STATUS
                return {"ok": True, "checksum": VALID_CHECKSUM}

        class Server:
            def __init__(self, executor):
                self.executor = executor
                self.recipe = None

            def create_initialization_options(self):
                return "options"

            async def run(self, read, write, options):
                del read, write, options
                self.recipe = asyncio.create_task(
                    self.executor.execute(
                        {"version": "v1", "intent": "curious"}
                    )
                )
                if not await asyncio.to_thread(recipe_started.wait, 1):
                    raise AssertionError("recipe did not start")
                self.recipe.cancel()
                asyncio.get_running_loop().call_later(
                    0.02,
                    release_recipe.set,
                )

        def create_server(_caller, *, recipe_executor):
            nonlocal active_server
            active_server = Server(recipe_executor)
            return active_server

        def execute(_payload, _caller, *, verified_session_id=None):
            events.append("recipe-start")
            verified_sessions.append(verified_session_id)
            recipe_started.set()
            if not release_recipe.wait(2):
                raise RuntimeError("test did not release recipe")
            events.append("recipe-end")
            return {
                "ok": True,
                "intent": "curious",
                "returned_to_idle": True,
            }

        with (
            fake_mcp_client(Session),
            patch(
                "gateway.semantic_service.create_service_server",
                side_effect=create_server,
            ),
            patch("gateway.semantic_service._execute_sync", side_effect=execute),
        ):
            asyncio.run(
                run_service_streams(
                    "up-read",
                    "up-write",
                    "down-read",
                    "down-write",
                    avatar_path="/state/native.rgb565le",
                )
            )

        self.assertIsNotNone(active_server)
        self.assertTrue(active_server.recipe.cancelled())
        self.assertEqual(verified_sessions, ["device-session"])
        self.assertLess(events.index("recipe-end"), events.index("upstream-exit"))

    def test_concurrent_handlers_serialize_complete_recipes(self):
        events = []
        first_started = threading.Event()
        second_started = threading.Event()
        release_first = threading.Event()

        def execute(payload, _caller, *, verified_session_id=None):
            del verified_session_id
            intent = payload["intent"]
            events.append(f"{intent}-start")
            if intent == "curious":
                first_started.set()
                if not release_first.wait(2):
                    raise RuntimeError("test did not release first recipe")
            else:
                second_started.set()
            events.append(f"{intent}-end")
            return {
                "ok": True,
                "intent": intent,
                "returned_to_idle": True,
            }

        with fake_mcp_server():
            server = create_service_server(Mock())
        with patch("gateway.semantic_service._execute_sync", side_effect=execute):
            second_ran_early, results = asyncio.run(
                run_concurrent_recipes(
                    server.call_tool_handler,
                    first_started,
                    second_started,
                    release_first,
                )
            )

        self.assertFalse(second_ran_early)
        self.assertEqual(
            events,
            ["curious-start", "curious-end", "idle-start", "idle-end"],
        )
        self.assertTrue(
            all(json.loads(result[0].text)["ok"] for result in results)
        )

    def test_embodiment_handler_returns_contract_errors(self):
        with fake_mcp_server():
            server = create_service_server(Mock())
        errors = (
            IntentRequestError("bad intent"),
            ExpressionAndIdleError(
                RuntimeError("expression failed"),
                RuntimeError("idle failed"),
            ),
        )

        for error in errors:
            with self.subTest(error=error):
                with patch(
                    "gateway.semantic_service._execute_sync",
                    side_effect=error,
                ):
                    result = asyncio.run(
                        server.call_tool_handler(
                            "embody",
                            {"version": "v1", "intent": "unknown"},
                        )
                    )

                self.assertEqual(
                    json.loads(result[0].text),
                    {"ok": False, "error": str(error)},
                )

async def run_concurrent_recipes(
    handler,
    first_started,
    second_started,
    release_first,
):
    first = asyncio.create_task(
        handler("embody", {"version": "v1", "intent": "curious"})
    )
    if not await asyncio.to_thread(first_started.wait, 1):
        release_first.set()
        await asyncio.gather(first, return_exceptions=True)
        raise AssertionError("first recipe did not start")
    second = asyncio.create_task(
        handler("embody", {"version": "v1", "intent": "idle"})
    )
    await asyncio.sleep(0.05)
    second_ran_early = second_started.is_set()
    release_first.set()
    return second_ran_early, await asyncio.gather(first, second)


@contextmanager
def fake_mcp_client(session_class):
    mcp_module = types.ModuleType("mcp")
    mcp_module.ClientSession = session_class
    with patch.dict(sys.modules, {"mcp": mcp_module}):
        yield


@contextmanager
def fake_mcp_server():
    class FakeServer:
        def __init__(self, name):
            self.name = name
            self.list_tools_handler = None
            self.call_tool_handler = None

        def list_tools(self):
            def register(handler):
                self.list_tools_handler = handler
                return handler

            return register

        def call_tool(self):
            def register(handler):
                self.call_tool_handler = handler
                return handler

            return register

    class FakeTool:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class FakeTextContent:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    mcp_module = types.ModuleType("mcp")
    mcp_module.__path__ = []
    server_module = types.ModuleType("mcp.server")
    server_module.Server = FakeServer
    types_module = types.ModuleType("mcp.types")
    types_module.Tool = FakeTool
    types_module.TextContent = FakeTextContent
    modules = {
        "mcp": mcp_module,
        "mcp.server": server_module,
        "mcp.types": types_module,
    }
    with patch.dict(sys.modules, modules):
        yield FakeServer


if __name__ == "__main__":
    unittest.main()
