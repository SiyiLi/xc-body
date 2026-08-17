#!/usr/bin/env python3
"""Run one semantic intent against an upstream StackChan MCP daemon."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from ipaddress import ip_address
from typing import Any
from urllib.parse import urlparse

from gateway.embodiment import (
    IntentRequestError,
    embody,
    parse_intent_request,
    planned_steps_for,
)
from stackchan.adapter import StackChanAdapter, VisibleFaceVerificationError
from stackchan.avatar_verification import (
    AvatarVerificationError,
    require_reviewed_avatar_load,
)
from stackchan.calibration import measured_k151_cores3_calibration
from stackchan.upstream_client import UpstreamStackChanClient

URL_ENV = "XC_BODY_STACKCHAN_MCP_URL"
TOKEN_ENV = "XC_BODY_STACKCHAN_MCP_TOKEN"
AVATAR_PATH_ENV = "XC_BODY_AVATAR_ARCHIVE_PATH"


class RunnerInputError(ValueError):
    """The CLI input is not a valid semantic request."""


class RunnerConfigError(ValueError):
    """Required daemon configuration is absent or invalid."""


class RunnerExecutionError(RuntimeError):
    """The MCP SDK or daemon session could not execute the request."""


@dataclass(frozen=True)
class RunnerConfig:
    url: str
    token: str = field(repr=False)
    avatar_path: str = field(default="", repr=False)


def _is_loopback(hostname: str | None) -> bool:
    if hostname == "localhost":
        return True
    if hostname is None:
        return False
    try:
        return ip_address(hostname).is_loopback
    except ValueError:
        return False


def parse_request(raw_request: str) -> dict[str, object]:
    """Accept semantic JSON or the narrow `curious` shorthand."""

    if raw_request == "curious":
        payload: object = {"version": "v1", "intent": "curious"}
    else:
        try:
            payload = json.loads(raw_request)
        except json.JSONDecodeError as exc:
            raise RunnerInputError(
                "request must be semantic intent JSON or the word 'curious'"
            ) from exc
    if not isinstance(payload, Mapping):
        raise RunnerInputError("semantic intent JSON must be an object")
    try:
        request = parse_intent_request(payload)
    except IntentRequestError as exc:
        raise RunnerInputError(str(exc)) from exc
    return {
        "version": request.version,
        "intent": request.intent,
        "speech": request.speech,
    }


def load_config(
    *,
    url: str | None = None,
    environ: Mapping[str, str] | None = None,
    require_avatar: bool = False,
) -> RunnerConfig:
    """Load endpoint and token without providing deployment-specific defaults."""

    values = os.environ if environ is None else environ
    endpoint = values.get(URL_ENV, "") if url is None else url
    token = values.get(TOKEN_ENV, "")
    avatar_path = values.get(AVATAR_PATH_ENV, "")
    endpoint = endpoint.strip()
    token = token.strip()
    avatar_path = avatar_path.strip()
    if not endpoint:
        raise RunnerConfigError(f"daemon URL is required via --url or {URL_ENV}")
    if not token:
        raise RunnerConfigError(f"daemon token is required via {TOKEN_ENV}")
    if require_avatar and not avatar_path:
        raise RunnerConfigError(
            f"avatar archive path is required via {AVATAR_PATH_ENV}"
        )
    parsed = urlparse(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RunnerConfigError("daemon URL must be an absolute HTTP(S) URL")
    if parsed.scheme == "http" and not _is_loopback(parsed.hostname):
        raise RunnerConfigError("non-loopback daemon URLs must use HTTPS")
    return RunnerConfig(
        url=endpoint,
        token=token,
        avatar_path=avatar_path,
    )


class _SessionToolCaller:
    def __init__(self, session: Any, loop: asyncio.AbstractEventLoop):
        self._session = session
        self._loop = loop

    def __call__(
        self, name: str, arguments: Mapping[str, object]
    ) -> object:
        pending = asyncio.run_coroutine_threadsafe(
            self._session.call_tool(name, arguments=arguments),
            self._loop,
        )
        return pending.result()


def _execute_sync(
    payload: Mapping[str, object],
    call_tool: _SessionToolCaller,
    *,
    sleep=time.sleep,
) -> dict[str, object]:
    client = UpstreamStackChanClient(call_tool)
    device = StackChanAdapter(
        client,
        measured_k151_cores3_calibration(),
        sleep=sleep,
    )
    recipe = embody(payload, device)
    return {"ok": True, "intent": recipe.intent, "returned_to_idle": True}


def _preflight_production_calibration(payload: Mapping[str, object]) -> None:
    calibration = measured_k151_cores3_calibration()
    request = parse_intent_request(payload)
    StackChanAdapter.preflight_calibration(
        calibration,
        planned_steps_for(request),
    )


async def _restore_reviewed_avatar(session: Any, avatar_path: str) -> None:
    """Restore and verify the exact payload behind visible-face evidence."""

    try:
        loaded = await session.call_tool(
            "load_avatar_set",
            arguments={
                "archive_path": avatar_path,
                "mode": "layered-320x240",
                "timeout": 60,
            },
        )
        require_reviewed_avatar_load(loaded)
    except AvatarVerificationError as exc:
        raise RunnerExecutionError(str(exc)) from exc
    except Exception as exc:
        raise RunnerExecutionError(
            f"native avatar restore call failed ({type(exc).__name__})"
        ) from exc


async def _run_with_mcp(
    payload: Mapping[str, object], config: RunnerConfig
) -> dict[str, object]:
    try:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client
    except ImportError as exc:
        raise RunnerExecutionError(
            "the deployment environment must provide the MCP Python SDK"
        ) from exc

    try:
        import httpx

        headers = {"Authorization": f"Bearer {config.token}"}
        async with httpx.AsyncClient(
            headers=headers,
            timeout=20.0,
        ) as http_client:
            async with streamable_http_client(
                config.url,
                http_client=http_client,
            ) as streams:
                read_stream, write_stream = streams[:2]
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    await _restore_reviewed_avatar(
                        session,
                        config.avatar_path,
                    )
                    loop = asyncio.get_running_loop()
                    caller = _SessionToolCaller(session, loop)
                    return await asyncio.to_thread(
                        _execute_sync,
                        payload,
                        caller,
                    )
    except Exception as exc:
        if isinstance(exc, RunnerExecutionError):
            raise
        raise RunnerExecutionError(
            f"semantic daemon execution failed ({type(exc).__name__})"
        ) from exc


def run(
    raw_request: str,
    *,
    url: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Validate all local inputs before opening the daemon session."""

    payload = parse_request(raw_request)
    _preflight_production_calibration(payload)
    config = load_config(
        url=url,
        environ=environ,
        require_avatar=True,
    )
    return asyncio.run(_run_with_mcp(payload, config))


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one reviewed semantic StackChan intention."
    )
    parser.add_argument(
        "request",
        help="semantic intent JSON, or 'curious' as shorthand",
    )
    parser.add_argument(
        "--url",
        help=f"upstream MCP URL; defaults to {URL_ENV}",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> int:
    args = _argument_parser().parse_args(argv)
    try:
        result = run(args.request, url=args.url, environ=environ)
    except (
        RunnerInputError,
        RunnerConfigError,
        RunnerExecutionError,
        VisibleFaceVerificationError,
    ) as exc:
        error = {"ok": False, "error": type(exc).__name__, "message": str(exc)}
        print(json.dumps(error, sort_keys=True), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
