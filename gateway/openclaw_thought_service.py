#!/usr/bin/env python3
"""Prepare OpenClaw thoughts for the remote XC Body service."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from gateway.semantic_e2e import (
    TOKEN_ENV,
    URL_ENV,
    RunnerConfig,
    RunnerConfigError,
    load_config,
)
from gateway.speech_preparation import (
    DEFAULT_VOICE,
    VOICE_ENV,
    SpeechPreparationError,
    prepare_speech as prepare_vm_speech,
)

TOOL_NAME = "consider_thought"
_CONTRACT_PATH = (
    Path(__file__).resolve().parents[1]
    / "contracts"
    / "openclaw-thought.schema.json"
)
_MAX_MESSAGE_CHARS = 240
PENDING_URL_ENV = "XC_BODY_PENDING_MCP_URL"
PENDING_TOKEN_ENV = "XC_BODY_PENDING_MCP_TOKEN"


class OpenClawThoughtServiceError(RuntimeError):
    """The OpenClaw-side thought producer could not complete its work."""


def load_producer_config(
    url: str | None,
    environ: Mapping[str, str],
) -> RunnerConfig:
    """Load the remote pending service through its producer-side names."""

    values = dict(environ)
    values[URL_ENV] = values.get(PENDING_URL_ENV, "")
    values[TOKEN_ENV] = values.get(PENDING_TOKEN_ENV, "")
    return load_config(url=url, environ=values)


def _validate_arguments(arguments: Mapping[str, object]) -> str | None:
    """Validate the text boundary and return an offer message when present."""

    with _CONTRACT_PATH.open(encoding="utf-8") as contract_file:
        contract = json.load(contract_file)
    allowed = set(contract["properties"])
    fields = set(arguments)
    extra = fields - allowed
    if extra:
        raise OpenClawThoughtServiceError(
            "unexpected field(s): " + ", ".join(sorted(extra))
        )
    required = set(contract["required"])
    missing = required - fields
    if missing:
        raise OpenClawThoughtServiceError(
            "missing field(s): " + ", ".join(sorted(missing))
        )
    decision = arguments.get("decision")
    if decision not in {"ignore", "remember", "offer"}:
        raise OpenClawThoughtServiceError("unsupported decision")
    message = arguments.get("message")
    if decision != "offer":
        if "message" in arguments:
            raise OpenClawThoughtServiceError(
                "message is permitted only when decision is 'offer'"
            )
        return None
    if not isinstance(message, str):
        raise OpenClawThoughtServiceError("offer requires a message")
    message = message.strip()
    if not message or len(message) > _MAX_MESSAGE_CHARS:
        raise OpenClawThoughtServiceError(
            "offer message must contain 1 to 240 characters"
        )
    return message


async def prepare_speech(message: str, voice: str) -> str:
    """Retain the historical wrapper while sharing VM-side preparation."""
    try:
        return await prepare_vm_speech(message, voice)
    except SpeechPreparationError as exc:
        raise OpenClawThoughtServiceError(str(exc)) from exc


def create_service_server(downstream: Any, voice: str) -> Any:
    """Create the semantic OpenClaw tool over one downstream MCP session."""

    try:
        from mcp.server import Server
        from mcp.types import TextContent, Tool
    except ImportError as exc:
        raise OpenClawThoughtServiceError("the MCP SDK is required") from exc

    server = Server("xc-body-openclaw-producer")

    @server.list_tools()
    async def list_tools() -> list[Any]:
        with _CONTRACT_PATH.open(encoding="utf-8") as contract_file:
            schema = json.load(contract_file)
        return [
            Tool(
                name=TOOL_NAME,
                description=(
                    "Classify one meaningful background result as ignore, "
                    "remember, or offer to the user through XC Body. For an "
                    "offer, write the exact short, non-private sentence XC "
                    "should speak after the user touches its head."
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
        values = arguments or {}
        try:
            message = _validate_arguments(values)
            payload = {
                "version": values["version"],
                "thought_id": values["thought_id"],
                "decision": values["decision"],
            }
            if message is not None:
                payload["audio_base64"] = await prepare_speech(message, voice)
            result = await downstream.call_tool(TOOL_NAME, arguments=payload)
        except OpenClawThoughtServiceError as exc:
            return [_error_content(TextContent, str(exc))]
        return result.content

    return server


def _error_content(text_content: Any, message: str) -> Any:
    return text_content(
        type="text",
        text=json.dumps({"ok": False, "error": message}, sort_keys=True),
    )


async def run_stdio_service(config: RunnerConfig, voice: str) -> None:
    """Expose the producer over stdio and connect to XC Body over HTTPS."""

    try:
        import httpx
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client
        from mcp.server.stdio import stdio_server
    except ImportError as exc:
        raise OpenClawThoughtServiceError(
            "the MCP SDK and HTTP client are required"
        ) from exc
    headers = {"Authorization": f"Bearer {config.token}"}
    try:
        async with httpx.AsyncClient(headers=headers, timeout=150.0) as client:
            async with streamable_http_client(
                config.url,
                http_client=client,
            ) as streams:
                async with ClientSession(*streams[:2]) as downstream:
                    await downstream.initialize()
                    server = create_service_server(downstream, voice)
                    async with stdio_server() as stdio:
                        await server.run(
                            *stdio,
                            server.create_initialization_options(),
                        )
    except OpenClawThoughtServiceError:
        raise
    except Exception as exc:
        raise OpenClawThoughtServiceError(
            f"XC Body transport failed ({type(exc).__name__})"
        ) from exc


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the OpenClaw-side XC Body thought producer."
    )
    parser.add_argument("--url", help="remote pending-thought MCP URL")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _argument_parser().parse_args(argv)
    try:
        config = load_producer_config(args.url, os.environ)
        voice = os.environ.get(VOICE_ENV, DEFAULT_VOICE).strip()
        if not voice:
            raise OpenClawThoughtServiceError(f"{VOICE_ENV} must not be empty")
        asyncio.run(run_stdio_service(config, voice))
    except (RunnerConfigError, OpenClawThoughtServiceError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
