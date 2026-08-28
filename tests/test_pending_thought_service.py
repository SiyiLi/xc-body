import asyncio
import io
import json
import sys
import types
import unittest
from contextlib import contextmanager, redirect_stderr
from datetime import timedelta
from unittest.mock import AsyncMock, Mock, patch

from gateway.pending_thought_runtime import PendingThoughtRuntime
from gateway.pending_thought_http_service import (
    _restore_pending_runtime_if_needed,
)
from gateway.pending_thought_service import (
    PendingThoughtServiceError,
    create_service_server,
    main,
    prepare_pending_runtime,
    run_service_streams,
)
from stackchan.avatar_verification import REVIEWED_AVATAR_CHECKSUM


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
        tools = asyncio.run(server.list_tools_handler())
        self.assertEqual([tool.name for tool in tools], ["consider_thought"])
        self.assertEqual(
            tools[0].inputSchema["properties"]["decision"]["enum"],
            ["ignore", "remember", "offer"],
        )

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
            "XC_BODY_AVATAR_ARCHIVE_PATH": "/srv/xc-body/avatar.rgb565le",
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
        self.assertEqual(
            config.avatar_path,
            "/srv/xc-body/avatar.rgb565le",
        )
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
            "XC_BODY_AVATAR_ARCHIVE_PATH": "/srv/xc-body/avatar.rgb565le",
        }

        with redirect_stderr(stderr):
            exit_code = main([], environ=environment)

        self.assertEqual(exit_code, 1)
        payload = json.loads(stderr.getvalue())
        self.assertEqual(payload["error"], "PendingThoughtServiceError")
        self.assertIn("XC_BODY_PLAYBACK_URL", payload["message"])

    def test_runtime_preparation_rejects_disconnected_device(self):
        class Session:
            async def call_tool(self, name, arguments, **kwargs):
                del arguments
                del kwargs
                if name == "load_avatar_set":
                    return {
                        "ok": True,
                        "checksum": REVIEWED_AVATAR_CHECKSUM,
                    }
                return {
                    "connected": False,
                    "initialized": True,
                    "session_id": "device-session-1",
                }

        runtime = Mock()
        with self.assertRaisesRegex(
            PendingThoughtServiceError,
            "not connected and initialized",
        ):
            asyncio.run(
                prepare_pending_runtime(
                    Session(),
                    runtime,
                    "/srv/xc-body/avatar.rgb565le",
                )
            )
        runtime.mark_avatar_ready.assert_not_called()

    def test_runtime_preparation_rejects_reconnect_during_avatar_load(self):
        class Session:
            def __init__(self):
                self.session_ids = iter(("device-session-1", "device-session-2"))

            async def call_tool(self, name, arguments, **kwargs):
                del arguments
                del kwargs
                if name == "load_avatar_set":
                    return {
                        "ok": True,
                        "checksum": REVIEWED_AVATAR_CHECKSUM,
                    }
                return {
                    "connected": True,
                    "initialized": True,
                    "session_id": next(self.session_ids),
                }

        runtime = Mock()
        with self.assertRaisesRegex(
            PendingThoughtServiceError,
            "session changed during avatar restore",
        ):
            asyncio.run(
                prepare_pending_runtime(
                    Session(),
                    runtime,
                    "/srv/xc-body/avatar.rgb565le",
                )
            )
        runtime.mark_avatar_ready.assert_not_called()

    def test_reconnect_restores_avatar_without_replacing_pending_offer(self):
        status = {
            "connected": True,
            "initialized": True,
            "session_id": "device-session-2",
        }
        session = AsyncMock()
        session.call_tool.side_effect = [
            status,
            {"ok": True, "checksum": REVIEWED_AVATAR_CHECKSUM},
            status,
        ]
        runtime = Mock()
        runtime.is_ready = AsyncMock(return_value=False)
        runtime.reconcile_base_view = AsyncMock()
        machine = types.SimpleNamespace(pending_thought_id="cron:waiting")
        runtime.machine = machine

        restored = asyncio.run(
            _restore_pending_runtime_if_needed(
                session,
                runtime,
                "/srv/xc-body/avatar.rgb565le",
            )
        )

        self.assertTrue(restored)
        self.assertIs(runtime.machine, machine)
        self.assertEqual(machine.pending_thought_id, "cron:waiting")
        runtime.mark_avatar_ready.assert_called_once_with("device-session-2")
        self.assertEqual(
            session.call_tool.call_args_list[1].kwargs[
                "read_timeout_seconds"
            ],
            timedelta(seconds=150),
        )

    @patch("gateway.pending_thought_runtime.urllib.request.urlopen")
    def test_offer_gesture_posts_prepared_audio(self, urlopen):
        response = Mock()
        response.read.return_value = b'{"ok": true}'
        urlopen.return_value.__enter__.return_value = response
        upstream_calls = []
        runtime = PendingThoughtRuntime(
            playback_url="http://127.0.0.1:8766/opus",
            playback_token="playback-secret",
        )

        class Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                del args

            async def initialize(self):
                pass

            async def call_tool(self, name, arguments, **kwargs):
                del kwargs
                upstream_calls.append((name, arguments))
                if name == "load_avatar_set":
                    return {
                        "ok": True,
                        "checksum": REVIEWED_AVATAR_CHECKSUM,
                    }
                if name == "get_status":
                    return {
                        "connected": True,
                        "initialized": True,
                        "session_id": "device-session-1",
                    }
                return {"ok": True}

        class Server:
            def create_initialization_options(self):
                return "options"

            async def run(self, read, write, options):
                del read, write, options
                offer = await runtime.consider_thought(
                    {
                        "version": "v1",
                        "thought_id": "eval:stdio",
                        "decision": "offer",
                        "audio_base64": _PREPARED_AUDIO_BASE64,
                    }
                )
                told = await asyncio.to_thread(
                    runtime.machine.handle_stackchan_event,
                    {
                        "event_type": "touch",
                        "subtype": "tap",
                        "action": "head_pat",
                    },
                )
                self_test.assertEqual(offer.state, "waiting")
                self_test.assertEqual(told.state, "told")

        self_test = self
        with (
            patch(
                "gateway.pending_thought_runtime.create_stackchan_client_session",
                return_value=Session(),
            ),
            patch(
                "gateway.pending_thought_service.create_service_server",
                return_value=Server(),
            ),
        ):
            asyncio.run(
                run_service_streams(
                    "up-read",
                    "up-write",
                    "down-read",
                    "down-write",
                    runtime=runtime,
                    avatar_path="/srv/xc-body/avatar.rgb565le",
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
