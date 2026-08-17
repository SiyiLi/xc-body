#!/usr/bin/env python3
"""Expose Milestone 1 embodiment over one persistent StackChan session."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from gateway.embodiment import ExpressionAndIdleError, IntentRequestError
from gateway.semantic_e2e import (
    RunnerConfig,
    RunnerConfigError,
    RunnerExecutionError,
    SessionToolCaller,
    _execute_sync,
    _restore_reviewed_avatar,
    load_config,
)
from stackchan.adapter import StackChanAdapterError

TOOL_NAME = "embody"
_CONTRACT_PATH = (
    Path(__file__).resolve().parents[1]
    / "contracts"
    / "embodiment-intent.schema.json"
)


class SemanticServiceError(RuntimeError):
    """The executable Milestone 1 service could not run safely."""


class _SerializedRecipeExecutor:
    """Run one complete recipe at a time and drain active work on shutdown."""

    def __init__(
        self,
        call_tool: SessionToolCaller,
        verified_session_id: str | None = None,
    ):
        self._call_tool = call_tool
        self._verified_session_id = verified_session_id
        self._lock: asyncio.Lock | None = None
        self._tasks: set[asyncio.Task[dict[str, object]]] = set()

    async def execute(
        self, payload: Mapping[str, object]
    ) -> dict[str, object]:
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            task = asyncio.create_task(
                asyncio.to_thread(
                    _execute_sync,
                    payload,
                    self._call_tool,
                    verified_session_id=self._verified_session_id,
                )
            )
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                await self._finish_after_cancellation(task)
                raise

    async def drain(self) -> None:
        while self._tasks:
            await asyncio.gather(
                *(asyncio.shield(task) for task in tuple(self._tasks)),
                return_exceptions=True,
            )

    @staticmethod
    async def _finish_after_cancellation(
        task: asyncio.Task[dict[str, object]],
    ) -> None:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if task.done() and not task.cancelled():
            task.exception()


def create_service_server(
    call_tool: SessionToolCaller,
    *,
    recipe_executor: _SerializedRecipeExecutor | None = None,
) -> Any:
    """Create the one-tool downstream MCP server without starting transport."""

    try:
        from mcp.server import Server
        from mcp.types import TextContent, Tool
    except ImportError as exc:
        raise SemanticServiceError(
            "the deployment environment must provide the MCP Python SDK"
        ) from exc

    server = Server("xc-body-semantic-embodiment")
    executor = recipe_executor or _SerializedRecipeExecutor(call_tool)

    @server.list_tools()
    async def list_tools() -> list[Any]:
        with _CONTRACT_PATH.open(encoding="utf-8") as contract_file:
            schema = json.load(contract_file)
        return [
            Tool(
                name=TOOL_NAME,
                description=(
                    "Present one reviewed semantic intention through the "
                    "physical body."
                ),
                inputSchema=schema,
            )
        ]

    @server.call_tool()
    async def call_tool_handler(
        name: str, arguments: dict[str, Any] | None
    ) -> list[Any]:
        if name != TOOL_NAME:
            return [_error_content(TextContent, f"unknown tool: {name!r}")]
        try:
            result = await executor.execute(arguments or {})
        except (
            ExpressionAndIdleError,
            IntentRequestError,
            StackChanAdapterError,
        ) as exc:
            return [_error_content(TextContent, str(exc))]
        return [
            TextContent(
                type="text",
                text=json.dumps(result, sort_keys=True),
            )
        ]

    return server


def _error_content(text_content: Any, message: str) -> Any:
    return text_content(
        type="text",
        text=json.dumps({"ok": False, "error": message}, sort_keys=True),
    )


async def run_service_streams(
    upstream_read: Any,
    upstream_write: Any,
    downstream_read: Any,
    downstream_write: Any,
    *,
    avatar_path: str,
) -> None:
    """Run the service over injected streams for production and tests."""

    try:
        from mcp import ClientSession
    except ImportError as exc:
        raise SemanticServiceError(
            "the deployment environment must provide the MCP Python SDK"
        ) from exc

    async with ClientSession(upstream_read, upstream_write) as session:
        await session.initialize()
        try:
            verified_session_id = await _restore_reviewed_avatar(
                session,
                avatar_path,
            )
        except RunnerExecutionError as exc:
            raise SemanticServiceError(str(exc)) from exc
        caller = SessionToolCaller(session, asyncio.get_running_loop())
        executor = _SerializedRecipeExecutor(caller, verified_session_id)
        server = create_service_server(caller, recipe_executor=executor)
        try:
            await server.run(
                downstream_read,
                downstream_write,
                server.create_initialization_options(),
            )
        finally:
            await executor.drain()


async def run_stdio_service(config: RunnerConfig) -> None:
    """Connect upstream over HTTP and expose embodiment over stdio."""

    try:
        import httpx
        from mcp.client.streamable_http import streamable_http_client
        from mcp.server.stdio import stdio_server
    except ImportError as exc:
        raise SemanticServiceError(
            "the deployment environment must provide MCP and HTTP clients"
        ) from exc

    try:
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
                    )
    except SemanticServiceError:
        raise
    except Exception as exc:
        raise SemanticServiceError(
            f"semantic service transport failed ({type(exc).__name__})"
        ) from exc


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the XC Body semantic embodiment MCP service."
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
        asyncio.run(run_stdio_service(config))
    except (RunnerConfigError, SemanticServiceError) as exc:
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
