import asyncio
import io
import sys
import types
import unittest
from contextlib import asynccontextmanager, redirect_stderr
from unittest.mock import AsyncMock, patch

from gateway.pending_thought_proxy import (
    TOKEN_ENV,
    URL_ENV,
    PendingThoughtProxyConfig,
    PendingThoughtProxyConfigError,
    load_config,
    main,
    run_proxy,
)


class PendingThoughtProxyTests(unittest.TestCase):
    def test_config_requires_url_and_token_without_leaking_token(self):
        with self.assertRaisesRegex(
            PendingThoughtProxyConfigError,
            URL_ENV,
        ):
            load_config({})
        with self.assertRaisesRegex(
            PendingThoughtProxyConfigError,
            TOKEN_ENV,
        ):
            load_config({URL_ENV: "http://127.0.0.1:8770/mcp"})

        config = load_config(
            {
                URL_ENV: "http://127.0.0.1:8770/mcp",
                TOKEN_ENV: "pending-secret",
            }
        )

        self.assertEqual(config.token, "pending-secret")
        self.assertNotIn("pending-secret", repr(config))

    def test_config_allows_plaintext_only_for_loopback(self):
        for url in (
            "http://localhost:8770/mcp",
            "http://127.0.0.1:8770/mcp",
            "http://[::1]:8770/mcp",
            "https://pending.invalid/mcp",
        ):
            config = load_config({URL_ENV: url, TOKEN_ENV: "secret"})
            self.assertEqual(config.url, url)

        with self.assertRaisesRegex(
            PendingThoughtProxyConfigError,
            "must use HTTPS",
        ):
            load_config(
                {
                    URL_ENV: "http://pending.invalid/mcp",
                    TOKEN_ENV: "secret",
                }
            )

    def test_proxy_adds_bearer_through_injected_http_client(self):
        records = {}

        class FakeAsyncClient:
            def __init__(self, **kwargs):
                records["http_client_kwargs"] = kwargs

            async def __aenter__(self):
                records["http_client"] = self
                return self

            async def __aexit__(self, *args):
                return None

        class FakeClientSession:
            def __init__(self, *streams):
                records["client_streams"] = streams

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            async def initialize(self):
                records["initialized"] = True

        class FakeServer:
            def __init__(self, name):
                records["server_name"] = name

            def list_tools(self):
                return lambda handler: handler

            def call_tool(self):
                return lambda handler: handler

            def create_initialization_options(self):
                return "options"

            async def run(self, *arguments):
                records["server_run"] = arguments

        @asynccontextmanager
        async def fake_streamable_http_client(url, *, http_client):
            records["upstream"] = (url, http_client)
            yield ("up-read", "up-write", None)

        @asynccontextmanager
        async def fake_stdio_server():
            yield ("down-read", "down-write")

        modules = _fake_proxy_modules(
            FakeAsyncClient,
            FakeClientSession,
            FakeServer,
            fake_streamable_http_client,
            fake_stdio_server,
        )
        config = PendingThoughtProxyConfig(
            url="https://pending.invalid/mcp",
            token="pending-secret",
        )

        with patch.dict(sys.modules, modules):
            asyncio.run(run_proxy(config))

        self.assertEqual(
            records["http_client_kwargs"]["headers"],
            {"Authorization": "Bearer pending-secret"},
        )
        self.assertIs(records["upstream"][1], records["http_client"])
        self.assertTrue(records["initialized"])
        self.assertEqual(
            records["server_run"],
            ("down-read", "down-write", "options"),
        )

    def test_main_validates_before_starting_proxy(self):
        proxy = AsyncMock()
        stderr = io.StringIO()

        with patch("gateway.pending_thought_proxy.run_proxy", proxy):
            with redirect_stderr(stderr):
                exit_code = main(environ={URL_ENV: "http://localhost/mcp"})

        self.assertEqual(exit_code, 1)
        self.assertIn(TOKEN_ENV, stderr.getvalue())
        proxy.assert_not_awaited()


def _fake_proxy_modules(
    async_client,
    client_session,
    server,
    streamable_client,
    stdio_server,
):
    httpx_module = types.ModuleType("httpx")
    httpx_module.AsyncClient = async_client
    mcp_module = types.ModuleType("mcp")
    mcp_module.ClientSession = client_session
    streamable_module = types.ModuleType("mcp.client.streamable_http")
    streamable_module.streamable_http_client = streamable_client
    server_module = types.ModuleType("mcp.server")
    server_module.Server = server
    stdio_module = types.ModuleType("mcp.server.stdio")
    stdio_module.stdio_server = stdio_server
    types_module = types.ModuleType("mcp.types")
    types_module.TextContent = type("TextContent", (), {})
    types_module.Tool = type("Tool", (), {})
    return {
        "httpx": httpx_module,
        "mcp": mcp_module,
        "mcp.client.streamable_http": streamable_module,
        "mcp.server": server_module,
        "mcp.server.stdio": stdio_module,
        "mcp.types": types_module,
    }


if __name__ == "__main__":
    unittest.main()
