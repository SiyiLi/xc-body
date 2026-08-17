#!/usr/bin/env python3
"""Run Milestone 2 as a long-lived Streamable HTTP MCP service."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hmac
import ipaddress
import json
import os
import sys
from collections.abc import Mapping
from contextlib import asynccontextmanager
from typing import Any

from gateway.pending_thought_runtime import PendingThoughtRuntime
from gateway.pending_thought_service import (
    PendingThoughtServiceError,
    create_service_server,
    load_playback_config,
)
from gateway.semantic_e2e import RunnerConfigError, load_config
from gateway.stackchan_event_session import wait_for_stackchan_event_tasks

DOWNSTREAM_TOKEN_ENV = "XC_BODY_PENDING_HTTP_TOKEN"
AUTH_FAILURE_MESSAGE = "Unauthorized: missing or invalid bearer token"
NON_LOOPBACK_TOKEN_REQUIRED_MESSAGE = (
    "refusing non-loopback pending-thought HTTP bind without "
    f"{DOWNSTREAM_TOKEN_ENV}"
)
_AUTHENTICATED_PATHS = frozenset(("/mcp", "/readyz"))


def is_loopback_bind_host(host: str) -> bool:
    """Return whether a bind target is limited to the local machine."""

    normalized = host.strip().lower()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def validate_bind_safety(host: str, downstream_token: str) -> None:
    """Reject an exposed service that has no downstream credential."""

    if not downstream_token and not is_loopback_bind_host(host):
        raise PendingThoughtServiceError(NON_LOOPBACK_TOKEN_REQUIRED_MESSAGE)


def load_downstream_token(
    environ: Mapping[str, str] | None = None,
) -> str:
    """Load the downstream token independently of upstream credentials."""

    values = os.environ if environ is None else environ
    return values.get(DOWNSTREAM_TOKEN_ENV, "").strip()


class _StreamableHTTPApp:
    def __init__(self, manager: Any):
        self._manager = manager

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        await self._manager.handle_request(scope, receive, send)


class _BearerAuthApp:
    """Require one exact bearer credential for the MCP route."""

    def __init__(self, app: Any, downstream_token: str):
        self._app = app
        self._downstream_token = downstream_token
        self.state = app.state

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if (
            scope.get("type") == "http"
            and scope.get("path") in _AUTHENTICATED_PATHS
        ):
            provided = _header_value(scope, b"authorization")
            expected = f"Bearer {self._downstream_token}"
            authorized = bool(self._downstream_token) and hmac.compare_digest(
                provided,
                expected,
            )
            if not authorized:
                await _send_auth_failure(send)
                return
        await self._app(scope, receive, send)

    @property
    def router(self) -> Any:
        return self._app.router


def _header_value(scope: Any, name: bytes) -> str:
    for raw_name, raw_value in scope.get("headers", []):
        if raw_name.lower() == name:
            return raw_value.decode("latin-1")
    return ""


async def _send_auth_failure(send: Any) -> None:
    body = AUTH_FAILURE_MESSAGE.encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"text/plain; charset=utf-8"),
                (b"content-length", str(len(body)).encode("ascii")),
                (b"www-authenticate", b"Bearer"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


def _health_payload() -> dict[str, bool]:
    return {"ok": True}


def _readiness_payload(runtime: PendingThoughtRuntime) -> dict[str, object]:
    machine = runtime.machine
    return {
        "ok": machine is not None,
        "pending_thought_id": (
            machine.pending_thought_id if machine is not None else None
        ),
    }


def build_app(
    config: Any,
    *,
    host: str,
    port: int,
    downstream_token: str = "",
) -> Any:
    """Build one process-wide runtime and its guarded HTTP MCP surface."""

    validate_bind_safety(host, downstream_token)
    playback_config = load_playback_config()

    try:
        import httpx
        from mcp.client.streamable_http import streamable_http_client
        from mcp.server.streamable_http_manager import (
            StreamableHTTPSessionManager,
        )
        from starlette.applications import Starlette
        from starlette.responses import JSONResponse
        from starlette.routing import Route
    except ImportError as exc:
        raise PendingThoughtServiceError(
            "the deployment environment must provide MCP, HTTP, and ASGI clients"
        ) from exc

    runtime = PendingThoughtRuntime(
        playback_url=playback_config.url,
        playback_token=playback_config.token,
    )
    server = create_service_server(runtime)
    manager = StreamableHTTPSessionManager(
        app=server,
        json_response=True,
        stateless=False,
    )

    async def healthz(_request: Any) -> Any:
        return JSONResponse(_health_payload())

    async def readyz(_request: Any) -> Any:
        payload = _readiness_payload(runtime)
        return JSONResponse(payload, status_code=200 if payload["ok"] else 503)

    @asynccontextmanager
    async def lifespan(_app: Any):
        headers = {"Authorization": f"Bearer {config.token}"}
        async with httpx.AsyncClient(headers=headers, timeout=60.0) as client:
            async with streamable_http_client(
                config.url,
                http_client=client,
            ) as streams:
                session = runtime.create_session(
                    *streams[:2],
                    asyncio.get_running_loop(),
                )
                async with session:
                    await session.initialize()
                    async with manager.run():
                        try:
                            yield
                        finally:
                            await wait_for_stackchan_event_tasks(session)

    routes = [
        Route(
            "/mcp",
            endpoint=_StreamableHTTPApp(manager),
            methods=["GET", "POST", "DELETE"],
        ),
        Route("/healthz", endpoint=healthz, methods=["GET"]),
        Route("/readyz", endpoint=readyz, methods=["GET"]),
    ]
    app = Starlette(routes=routes, lifespan=lifespan)
    app.state.runtime = runtime
    app.state.host = host
    app.state.port = port
    return _BearerAuthApp(app, downstream_token)


async def run_http_service(
    config: Any,
    *,
    host: str,
    port: int,
    downstream_token: str,
) -> None:
    try:
        import uvicorn
    except ImportError as exc:
        raise PendingThoughtServiceError(
            "the deployment environment must provide an ASGI server"
        ) from exc

    app = build_app(
        config,
        host=host,
        port=port,
        downstream_token=downstream_token,
    )
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=host,
            port=port,
            log_level="info",
            lifespan="on",
        )
    )
    await server.serve()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run persistent XC Body knock-wait-tell over HTTP."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--url")
    return parser


def main(
    argv: list[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> int:
    args = _parser().parse_args(argv)
    try:
        config = load_config(url=args.url, environ=environ)
        downstream_token = load_downstream_token(environ)
        validate_bind_safety(args.host, downstream_token)
        asyncio.run(
            run_http_service(
                config,
                host=args.host,
                port=args.port,
                downstream_token=downstream_token,
            )
        )
    except (RunnerConfigError, PendingThoughtServiceError) as exc:
        print(
            json.dumps(
                {"ok": False, "error": type(exc).__name__, "message": str(exc)},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
