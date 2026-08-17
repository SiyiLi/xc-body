import asyncio
import io
import json
import sys
import types
import unittest
from contextlib import asynccontextmanager, contextmanager, redirect_stderr
from unittest.mock import AsyncMock, Mock, patch

from gateway.pending_thought_runtime import PendingThoughtRuntime
from gateway.pending_thought_service import (
    PlaybackConfig,
    PendingThoughtServiceError,
    create_service_server,
    main,
    run_service_streams,
    run_stdio_service,
)
from gateway.semantic_e2e import RunnerConfig


_OPUS_PACKET = bytes.fromhex(
    "5802f9304dbb0de5e392098938ebcae1b1d1dd85"
)
_FRAMED_OPUS = len(_OPUS_PACKET).to_bytes(2, "big") + _OPUS_PACKET
_PREPARED_AUDIO_BASE64 = "ABRYAvkwTbsN5eOSCYk468rhsdHdhQ=="


class PendingThoughtServiceTests(unittest.TestCase):
    def test_import_and_server_creation_have_no_transport_side_effects(self):
        runtime = PendingThoughtRuntime()

        with fake_mcp_server() as server_class:
            server = create_service_server(runtime)

        self.assertIsInstance(server, server_class)
        self.assertEqual(server.name, "xc-body-knock-wait-tell")

    def test_main_reports_missing_configuration(self):
        stderr = io.StringIO()

        with redirect_stderr(stderr):
            exit_code = main([], environ={})

        self.assertEqual(exit_code, 1)
        payload = json.loads(stderr.getvalue())
        self.assertEqual(payload["error"], "RunnerConfigError")
        self.assertIn("daemon URL is required", payload["message"])

    def test_main_runs_import_safe_service_boundary(self):
        service = AsyncMock()
        environment = {
            "XC_BODY_STACKCHAN_MCP_URL": "https://daemon.invalid/mcp",
            "XC_BODY_STACKCHAN_MCP_TOKEN": "test-token",
            "XC_BODY_PLAYBACK_URL": "http://127.0.0.1:8766/opus",
            "XC_BODY_PLAYBACK_TOKEN": "playback-token",
        }

        with patch(
            "gateway.pending_thought_service.run_stdio_service", service
        ):
            exit_code = main([], environ=environment)

        self.assertEqual(exit_code, 0)
        config = service.await_args.args[0]
        self.assertEqual(config.url, "https://daemon.invalid/mcp")
        self.assertEqual(config.token, "test-token")
        self.assertNotIn("test-token", repr(config))
        playback_config = service.await_args.args[1]
        self.assertEqual(
            playback_config.url,
            "http://127.0.0.1:8766/opus",
        )
        self.assertEqual(playback_config.token, "playback-token")
        self.assertNotIn("playback-token", repr(playback_config))

    def test_main_reports_missing_playback_configuration(self):
        stderr = io.StringIO()
        environment = {
            "XC_BODY_STACKCHAN_MCP_URL": "https://daemon.invalid/mcp",
            "XC_BODY_STACKCHAN_MCP_TOKEN": "test-token",
        }

        with redirect_stderr(stderr):
            exit_code = main([], environ=environment)

        self.assertEqual(exit_code, 1)
        payload = json.loads(stderr.getvalue())
        self.assertEqual(payload["error"], "PendingThoughtServiceError")
        self.assertIn("XC_BODY_PLAYBACK_URL", payload["message"])

    def test_stream_runner_initializes_upstream_before_downstream(self):
        events = []

        class Session:
            async def __aenter__(self):
                events.append("upstream-enter")
                return self

            async def __aexit__(self, *args):
                events.append("upstream-exit")

            async def initialize(self):
                events.append("upstream-initialize")

        class Runtime:
            def create_session(self, read, write, loop):
                self.arguments = (read, write, loop)
                return Session()

        class Server:
            def create_initialization_options(self):
                return "options"

            async def run(self, read, write, options):
                events.append(("downstream-run", read, write, options))

        runtime = Runtime()
        with (
            patch(
                "gateway.pending_thought_service.create_service_server",
                return_value=Server(),
            ),
            patch(
                "gateway.pending_thought_service."
                "wait_for_stackchan_event_tasks",
                new=AsyncMock(),
            ) as wait_tasks,
        ):
            asyncio.run(
                run_service_streams(
                    "up-read",
                    "up-write",
                    "down-read",
                    "down-write",
                    runtime=runtime,
                )
            )

        self.assertEqual(events[0:2], ["upstream-enter", "upstream-initialize"])
        self.assertEqual(
            events[2],
            ("downstream-run", "down-read", "down-write", "options"),
        )
        self.assertEqual(events[3], "upstream-exit")
        wait_tasks.assert_awaited_once()

    @patch("gateway.pending_thought_runtime.urllib.request.urlopen")
    def test_stdio_offer_gesture_uses_configured_playback(self, urlopen):
        response = Mock()
        response.read.return_value = b'{"ok": true}'
        urlopen.return_value.__enter__.return_value = response
        upstream_calls = []
        active_runtime = None

        class HTTPClient:
            def __init__(self, **kwargs):
                self.headers = kwargs["headers"]

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                del args

        class Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                del args

            async def initialize(self):
                pass

            async def call_tool(self, name, arguments):
                upstream_calls.append((name, arguments))
                return {"ok": True}

        class Server:
            def create_initialization_options(self):
                return "options"

            async def run(self, read, write, options):
                del read, write, options
                offer = await active_runtime.consider_thought(
                    {
                        "version": "v1",
                        "thought_id": "eval:stdio",
                        "decision": "offer",
                        "audio_base64": _PREPARED_AUDIO_BASE64,
                    }
                )
                told = await asyncio.to_thread(
                    active_runtime.machine.handle_stackchan_event,
                    {
                        "event_type": "touch",
                        "subtype": "tap",
                        "action": "head_pat",
                    },
                )
                self_test.assertEqual(offer.state, "waiting")
                self_test.assertEqual(told.state, "told")

        def create_server(runtime):
            nonlocal active_runtime
            active_runtime = runtime
            return Server()

        @asynccontextmanager
        async def streamable_http_client(url, *, http_client):
            self.assertEqual(url, "https://daemon.invalid/mcp")
            self.assertEqual(
                http_client.headers["Authorization"],
                "Bearer upstream-secret",
            )
            yield ("up-read", "up-write")

        @asynccontextmanager
        async def stdio_server():
            yield ("down-read", "down-write")

        self_test = self
        httpx_module = types.ModuleType("httpx")
        httpx_module.AsyncClient = HTTPClient
        mcp_module = types.ModuleType("mcp")
        mcp_module.__path__ = []
        mcp_client_module = types.ModuleType("mcp.client")
        mcp_client_module.__path__ = []
        streamable_module = types.ModuleType("mcp.client.streamable_http")
        streamable_module.streamable_http_client = streamable_http_client
        mcp_server_module = types.ModuleType("mcp.server")
        mcp_server_module.__path__ = []
        stdio_module = types.ModuleType("mcp.server.stdio")
        stdio_module.stdio_server = stdio_server
        modules = {
            "httpx": httpx_module,
            "mcp": mcp_module,
            "mcp.client": mcp_client_module,
            "mcp.client.streamable_http": streamable_module,
            "mcp.server": mcp_server_module,
            "mcp.server.stdio": stdio_module,
        }
        with (
            patch.dict(sys.modules, modules),
            patch(
                "gateway.pending_thought_runtime."
                "create_stackchan_client_session",
                return_value=Session(),
            ),
            patch(
                "gateway.pending_thought_service.create_service_server",
                side_effect=create_server,
            ),
        ):
            asyncio.run(
                run_stdio_service(
                    RunnerConfig(
                        url="https://daemon.invalid/mcp",
                        token="upstream-secret",
                    ),
                    PlaybackConfig(
                        url="http://127.0.0.1:8766/opus",
                        token="playback-secret",
                    ),
                )
            )

        urlopen.assert_called_once()
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:8766/opus")
        self.assertEqual(
            request.get_header("Authorization"),
            "Bearer playback-secret",
        )
        self.assertEqual(request.get_header("X-message-id"), "eval:stdio")
        self.assertEqual(request.data, _FRAMED_OPUS)
        self.assertNotIn("say", [name for name, _ in upstream_calls])

    def test_server_creation_fails_clearly_without_sdk(self):
        empty_server = types.ModuleType("mcp.server")

        with patch.dict(sys.modules, {"mcp.server": empty_server}):
            with self.assertRaisesRegex(
                PendingThoughtServiceError,
                "must provide the MCP Python SDK",
            ):
                create_service_server(PendingThoughtRuntime())


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

    server_module = types.ModuleType("mcp.server")
    server_module.Server = FakeServer
    types_module = types.ModuleType("mcp.types")
    types_module.Tool = FakeTool
    types_module.TextContent = FakeTextContent
    modules = {
        "mcp.server": server_module,
        "mcp.types": types_module,
    }
    with patch.dict(sys.modules, modules):
        yield FakeServer


if __name__ == "__main__":
    unittest.main()
