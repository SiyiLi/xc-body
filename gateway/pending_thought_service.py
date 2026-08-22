#!/usr/bin/env python3
"""Run the Milestone 2 tool over one persistent StackChan MCP session."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from ipaddress import ip_address
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from gateway.pending_thought import PendingThoughtError
from gateway.pending_thought_runtime import (
    PendingThoughtRuntime,
    PendingThoughtRuntimeError,
    ready_device_session_id,
)
from gateway.semantic_e2e import RunnerConfig, RunnerConfigError, load_config
from gateway.stackchan_event_session import wait_for_stackchan_event_tasks
from stackchan.avatar_verification import (
    AvatarVerificationError,
    require_reviewed_avatar_load,
)

_CONTRACT_PATH = (
    Path(__file__).resolve().parents[1]
    / "contracts"
    / "pending-thought.schema.json"
)
TOOL_NAME = "consider_thought"
PLAYBACK_URL_ENV = "XC_BODY_PLAYBACK_URL"
PLAYBACK_TOKEN_ENV = "XC_BODY_PLAYBACK_TOKEN"


class PendingThoughtServiceError(RuntimeError):
    """The executable Milestone 2 service could not run safely."""


@dataclass(frozen=True)
class PlaybackConfig:
    url: str
    token: str = field(repr=False)


def _is_loopback_url_host(hostname: str | None) -> bool:
    if hostname == "localhost":
        return True
    if hostname is None:
        return False
    try:
        return ip_address(hostname).is_loopback
    except ValueError:
        return False


def validate_playback_url(url: str) -> None:
    """Require an absolute HTTP(S) URL and TLS outside loopback."""

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise PendingThoughtServiceError(
            f"{PLAYBACK_URL_ENV} must be an absolute HTTP(S) URL"
        )
    if parsed.scheme == "http" and not _is_loopback_url_host(parsed.hostname):
        raise PendingThoughtServiceError(
            f"non-loopback {PLAYBACK_URL_ENV} values must use HTTPS"
        )


def load_playback_config(
    environ: Mapping[str, str] | None = None,
) -> PlaybackConfig:
    """Load the required prepared-audio endpoint and credential."""

    values = os.environ if environ is None else environ
    url = values.get(PLAYBACK_URL_ENV, "").strip()
    token = values.get(PLAYBACK_TOKEN_ENV, "").strip()
    if not url:
        raise PendingThoughtServiceError(
            f"{PLAYBACK_URL_ENV} is required for prepared-audio playback"
        )
    if not token:
        raise PendingThoughtServiceError(
            f"{PLAYBACK_TOKEN_ENV} is required for prepared-audio playback"
        )
    validate_playback_url(url)
    return PlaybackConfig(url=url, token=token)


def create_service_server(runtime: PendingThoughtRuntime) -> Any:
    """Create the one-tool downstream MCP server without starting transport."""

    try:
        from mcp.server import Server
        from mcp.types import TextContent, Tool
    except ImportError as exc:
        raise PendingThoughtServiceError(
            "the deployment environment must provide the MCP Python SDK"
        ) from exc

    server = Server("xc-body-knock-wait-tell")

    @server.list_tools()
    async def list_tools() -> list[Any]:
        with _CONTRACT_PATH.open(encoding="utf-8") as contract_file:
            schema = json.load(contract_file)
        return [
            Tool(
                name=TOOL_NAME,
                description=(
                    "Classify one background result as ignore, remember, "
                    "or offer."
                ),
                inputSchema=schema,
            )
        ]

    @server.call_tool()
    async def call_tool(
        name: str, arguments: dict[str, Any] | None
    ) -> list[Any]:
        if name != TOOL_NAME:
            return [_error_content(TextContent, f"unknown tool: {name!r}")]
        try:
            outcome = await runtime.consider_thought(arguments or {})
        except (PendingThoughtError, PendingThoughtRuntimeError) as exc:
            return [_error_content(TextContent, str(exc))]
        payload = {
            "ok": True,
            "thought_id": outcome.thought_id,
            "decision": outcome.decision,
            "state": outcome.state,
            "pending_thought_id": runtime.machine.pending_thought_id,
        }
        return [
            TextContent(
                type="text",
                text=json.dumps(payload, sort_keys=True),
            )
        ]

    return server


def _error_content(text_content: Any, message: str) -> Any:
    return text_content(
        type="text",
        text=json.dumps({"ok": False, "error": message}, sort_keys=True),
    )


async def prepare_pending_runtime(
    session: Any,
    runtime: PendingThoughtRuntime,
    avatar_path: str,
) -> None:
    """Restore the reviewed avatar and bind it to the active device session."""

    session_id = await _ready_session_id(session)
    try:
        loaded = await session.call_tool(
            "load_avatar_set",
            arguments={
                "archive_path": avatar_path,
                "mode": "layered-320x240",
                "timeout": 120,
            },
            read_timeout_seconds=timedelta(seconds=150),
        )
        require_reviewed_avatar_load(loaded)
    except AvatarVerificationError as exc:
        raise PendingThoughtServiceError(str(exc)) from exc
    except Exception as exc:
        raise PendingThoughtServiceError(
            "native avatar restore call failed "
            f"({type(exc).__name__})"
        ) from exc

    verified_session_id = await _ready_session_id(session)
    if verified_session_id != session_id:
        raise PendingThoughtServiceError(
            "StackChan device session changed during avatar restore"
        )
    runtime.mark_avatar_ready(verified_session_id)


async def _ready_session_id(session: Any) -> str:
    try:
        status = await session.call_tool("get_status", arguments={})
        session_id = ready_device_session_id(status)
    except Exception as exc:
        raise PendingThoughtServiceError(
            f"device readiness check failed ({type(exc).__name__})"
        ) from exc
    if session_id is None:
        raise PendingThoughtServiceError(
            "StackChan device is not connected and initialized"
        )
    return session_id


async def run_service_streams(
    upstream_read: Any,
    upstream_write: Any,
    downstream_read: Any,
    downstream_write: Any,
    *,
    avatar_path: str,
    runtime: PendingThoughtRuntime | None = None,
) -> None:
    """Run the service over injected streams for production and tests."""

    active_runtime = runtime or PendingThoughtRuntime()
    loop = asyncio.get_running_loop()
    upstream_session = active_runtime.create_session(
        upstream_read,
        upstream_write,
        loop,
    )
    server = create_service_server(active_runtime)
    async with upstream_session:
        await upstream_session.initialize()
        await prepare_pending_runtime(
            upstream_session,
            active_runtime,
            avatar_path,
        )
        try:
            await server.run(
                downstream_read,
                downstream_write,
                server.create_initialization_options(),
            )
        finally:
            await wait_for_stackchan_event_tasks(upstream_session)


async def run_stdio_service(
    config: RunnerConfig,
    playback_config: PlaybackConfig,
) -> None:
    """Connect upstream over HTTP and expose the service over stdio."""

    try:
        import httpx
        from mcp.client.streamable_http import streamable_http_client
        from mcp.server.stdio import stdio_server
    except ImportError as exc:
        raise PendingThoughtServiceError(
            "the deployment environment must provide MCP and HTTP clients"
        ) from exc

    headers = {"Authorization": f"Bearer {config.token}"}
    async with httpx.AsyncClient(headers=headers, timeout=150.0) as client:
        async with streamable_http_client(
            config.url,
            http_client=client,
        ) as upstream_streams:
            async with stdio_server() as downstream_streams:
                await run_service_streams(
                    *upstream_streams[:2],
                    *downstream_streams,
                    avatar_path=config.avatar_path,
                    runtime=PendingThoughtRuntime(
                        playback_url=playback_config.url,
                        playback_token=playback_config.token,
                    ),
                )


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the XC Body knock-wait-tell MCP service."
    )
    parser.add_argument(
        "--url",
        help="upstream StackChan MCP URL; defaults to the configured env var",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> int:
    args = _argument_parser().parse_args(argv)
    try:
        config = load_config(
            url=args.url,
            environ=environ,
            require_avatar=True,
        )
        playback_config = load_playback_config(environ)
        asyncio.run(run_stdio_service(config, playback_config))
    except (
        RunnerConfigError,
        PendingThoughtServiceError,
        PendingThoughtRuntimeError,
    ) as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": type(exc).__name__,
                    "message": str(exc),
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
