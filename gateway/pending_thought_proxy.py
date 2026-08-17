#!/usr/bin/env python3
"""Bridge OpenClaw stdio calls to the persistent Milestone 2 HTTP service."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from ipaddress import ip_address
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

TOOL_NAME = "consider_thought"
URL_ENV = "XC_BODY_PENDING_HTTP_URL"
TOKEN_ENV = "XC_BODY_PENDING_HTTP_TOKEN"
_CONTRACT_PATH = (
    Path(__file__).resolve().parents[1]
    / "contracts"
    / "pending-thought.schema.json"
)


class PendingThoughtProxyConfigError(ValueError):
    """The persistent pending-thought HTTP endpoint is unsafe or absent."""


@dataclass(frozen=True)
class PendingThoughtProxyConfig:
    url: str
    token: str = field(repr=False)


def _is_loopback(hostname: str | None) -> bool:
    if hostname == "localhost":
        return True
    if hostname is None:
        return False
    try:
        return ip_address(hostname).is_loopback
    except ValueError:
        return False


def load_config(
    environ: Mapping[str, str] | None = None,
) -> PendingThoughtProxyConfig:
    """Load a protected endpoint and reject remote plaintext transport."""

    values = os.environ if environ is None else environ
    url = values.get(URL_ENV, "").strip()
    token = values.get(TOKEN_ENV, "").strip()
    if not url:
        raise PendingThoughtProxyConfigError(f"{URL_ENV} is required")
    if not token:
        raise PendingThoughtProxyConfigError(f"{TOKEN_ENV} is required")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise PendingThoughtProxyConfigError(
            "pending HTTP URL must be an absolute HTTP(S) URL"
        )
    if parsed.scheme == "http" and not _is_loopback(parsed.hostname):
        raise PendingThoughtProxyConfigError(
            "non-loopback pending HTTP URLs must use HTTPS"
        )
    return PendingThoughtProxyConfig(url=url, token=token)


async def run_proxy(config: PendingThoughtProxyConfig) -> None:
    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import TextContent, Tool

    server = Server("xc-body-pending-thought-proxy")
    headers = {"Authorization": f"Bearer {config.token}"}

    async with httpx.AsyncClient(headers=headers, timeout=60.0) as http_client:
        async with streamable_http_client(
            config.url,
            http_client=http_client,
        ) as upstream:
            async with ClientSession(*upstream[:2]) as client:
                await client.initialize()

                @server.list_tools()
                async def list_tools() -> list[Any]:
                    schema = json.loads(
                        _CONTRACT_PATH.read_text(encoding="utf-8")
                    )
                    return [
                        Tool(
                            name=TOOL_NAME,
                            description=(
                                "Classify one background result as ignore, "
                                "remember, or offer."
                            ),
                            inputSchema=schema,
                        )
                    ]

                @server.call_tool()
                async def call_tool(
                    name: str, arguments: dict[str, Any] | None
                ) -> list[Any]:
                    if name != TOOL_NAME:
                        return [
                            TextContent(
                                type="text",
                                text=json.dumps(
                                    {
                                        "ok": False,
                                        "error": f"unknown tool: {name!r}",
                                    }
                                ),
                            )
                        ]
                    result = await client.call_tool(
                        name,
                        arguments=arguments or {},
                    )
                    return list(result.content)

                async with stdio_server() as downstream:
                    await server.run(
                        *downstream,
                        server.create_initialization_options(),
                    )


def main(*, environ: Mapping[str, str] | None = None) -> int:
    try:
        config = load_config(environ)
    except PendingThoughtProxyConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    asyncio.run(run_proxy(config))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
