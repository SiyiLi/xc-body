#!/usr/bin/env python3
"""Run Milestone 2 as a long-lived Streamable HTTP MCP service."""

from __future__ import annotations

import argparse
import asyncio
import hmac
import ipaddress
import json
import logging
import os
import sys
import time
from collections.abc import Mapping
from contextlib import asynccontextmanager
from typing import Any

from gateway.direct_conversation import (
    DirectConversationError,
    VoiceMailbox,
    build_direct_turn_report,
    emit_direct_turn_metrics,
    parse_plugin_metrics,
    speak_direct_answer,
)
from gateway.pending_thought_runtime import PendingThoughtRuntime
from gateway.pending_thought_service import (
    PlaybackConfig,
    PendingThoughtServiceError,
    create_service_server,
    load_playback_config,
    prepare_pending_runtime,
)
from gateway.semantic_e2e import RunnerConfigError, load_config
from gateway.stackchan_event_session import wait_for_stackchan_event_tasks
from gateway.thought_summary_service import (
    handle_summary_request,
    load_summary_voice,
)

DOWNSTREAM_TOKEN_ENV = "XC_BODY_PENDING_HTTP_TOKEN"
AUTH_FAILURE_MESSAGE = "Unauthorized: missing or invalid bearer token"
TOKEN_REQUIRED_MESSAGE = (
    f"refusing pending-thought HTTP service without {DOWNSTREAM_TOKEN_ENV}"
)
_AUTHENTICATED_PATHS = frozenset(
    (
        "/mcp",
        "/readyz",
        "/summary/v1",
        "/voice/v1/capture",
        "/voice/v1/abandon",
        "/voice/v1/answer",
    )
)
_MAX_SUMMARY_REQUEST_BYTES = 4096
_MAX_ANSWER_REQUEST_BYTES = 64 * 1024
_RECOVERY_DELAY_SECONDS = 5
_CAPTURE_METRIC_HEADERS = {
    "capture_started_uptime_us": "X-XC-Device-Capture-Start-Us",
    "capture_stopped_uptime_us": "X-XC-Device-Capture-Stop-Us",
    "gateway_capture_started_ms": "X-XC-Gateway-Capture-Start-Ms",
    "gateway_capture_stopped_ms": "X-XC-Gateway-Capture-Stop-Ms",
    "gateway_upload_started_ms": "X-XC-Gateway-Upload-Started-Ms",
}
logger = logging.getLogger(__name__)


def is_loopback_bind_host(host: str) -> bool:
    """Return whether one bind host is local-only."""

    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def validate_bind_safety(host: str, downstream_token: str) -> None:
    """Require authentication when the HTTP service is network-visible."""

    if not downstream_token and not is_loopback_bind_host(host):
        raise PendingThoughtServiceError(TOKEN_REQUIRED_MESSAGE)


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

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if (
            scope.get("type") == "http"
            and scope.get("path") in _AUTHENTICATED_PATHS
        ):
            provided = _header_value(scope, b"authorization")
            expected = f"Bearer {self._downstream_token}"
            authorized = not self._downstream_token or hmac.compare_digest(
                provided, expected
            )
            if not authorized:
                await _send_auth_failure(send)
                return
        await self._app(scope, receive, send)

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


async def _readiness_payload(
    runtime: PendingThoughtRuntime,
) -> dict[str, object]:
    return {
        "ok": await runtime.is_ready(),
        "pending_thought_id": await runtime.pending_thought_id(),
    }


async def _restore_pending_runtime_if_needed(
    session: Any,
    runtime: PendingThoughtRuntime,
    avatar_path: str,
) -> bool:
    if await runtime.is_ready():
        return True
    try:
        await prepare_pending_runtime(session, runtime, avatar_path)
    except PendingThoughtServiceError:
        return False
    logger.info("StackChan session restored with the reviewed avatar")
    return True


async def _maintain_pending_runtime(
    config: Any,
    runtime: PendingThoughtRuntime,
    httpx: Any,
    streamable_http_client: Any,
) -> None:
    while True:
        try:
            headers = {"Authorization": f"Bearer {config.token}"}
            timeout = httpx.Timeout(60.0, read=None)
            async with httpx.AsyncClient(
                headers=headers,
                timeout=timeout,
            ) as client:
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
                        try:
                            while True:
                                restored = (
                                    await _restore_pending_runtime_if_needed(
                                        session,
                                        runtime,
                                        config.avatar_path,
                                    )
                                )
                                if not restored:
                                    break
                                await runtime.reconcile_base_view()
                                await asyncio.sleep(_RECOVERY_DELAY_SECONDS)
                        finally:
                            await wait_for_stackchan_event_tasks(session)
                            runtime.unbind_session(session)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("StackChan upstream reconnect: %s", exc)
        await asyncio.sleep(_RECOVERY_DELAY_SECONDS)


def build_app(
    config: Any,
    playback_config: PlaybackConfig,
    *,
    host: str,
    downstream_token: str = "",
    voice: str,
) -> Any:
    """Build one process-wide runtime and its guarded HTTP MCP surface."""

    validate_bind_safety(host, downstream_token)
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
        streaming_url=playback_config.streaming_url,
        playback_token=playback_config.token,
    )
    voice_mailbox = VoiceMailbox()
    server = create_service_server(runtime)
    manager = StreamableHTTPSessionManager(
        app=server,
        json_response=True,
        stateless=False,
    )

    async def healthz(_request: Any) -> Any:
        return JSONResponse({"ok": True})

    async def readyz(_request: Any) -> Any:
        payload = await _readiness_payload(runtime)
        return JSONResponse(payload, status_code=200 if payload["ok"] else 503)

    async def voice_capture(request: Any) -> Any:
        request_started_ms = time.time_ns() // 1_000_000
        try:
            audio = await request.body()
            capture_metrics = {
                name: int(value)
                for name, header in _CAPTURE_METRIC_HEADERS.items()
                if (value := request.headers.get(header)) is not None
                and value.isascii()
                and value.isdigit()
                and len(value) <= 16
            }
            capture_metrics["server_capture_request_started_ms"] = (
                request_started_ms
            )
            capture_metrics["server_capture_received_ms"] = (
                time.time_ns() // 1_000_000
            )
            turn_id = await voice_mailbox.submit(audio, capture_metrics)
        except DirectConversationError:
            return JSONResponse(
                {"ok": False, "error": "capture_rejected"},
                status_code=409,
            )
        return JSONResponse({"ok": True, "turn_id": turn_id}, status_code=202)

    async def voice_claim(_request: Any) -> Any:
        capture = await voice_mailbox.claim()
        if capture is None:
            return JSONResponse({"ok": True, "capture": None})
        import base64

        return JSONResponse(
            {
                "ok": True,
                "capture": {
                    "turn_id": capture.turn_id,
                    "audio_base64": base64.b64encode(capture.audio).decode(
                        "ascii"
                    ),
                    "metrics": capture.metrics,
                },
            }
        )

    async def voice_abandon(request: Any) -> Any:
        try:
            raw_body = await request.body()
            if len(raw_body) > _MAX_ANSWER_REQUEST_BYTES:
                raise ValueError
            payload = json.loads(raw_body.decode("utf-8"))
            turn_id = payload["turn_id"]
            if not isinstance(turn_id, str):
                raise ValueError
            turn_metrics, failed_stage = parse_plugin_metrics(
                payload.get("metrics")
            )
        except (
            DirectConversationError,
            KeyError,
            UnicodeDecodeError,
            ValueError,
            json.JSONDecodeError,
        ):
            return JSONResponse(
                {"ok": False, "error": "invalid_request"},
                status_code=400,
            )
        abandoned = await voice_mailbox.abandon(turn_id)
        if abandoned:
            emit_direct_turn_metrics(
                build_direct_turn_report(
                    turn_id,
                    "abandoned",
                    turn_metrics,
                    failed_stage,
                )
            )
        return JSONResponse({"ok": True, "abandoned": abandoned})

    async def voice_answer(request: Any) -> Any:
        try:
            raw_body = await request.body()
            if len(raw_body) > _MAX_ANSWER_REQUEST_BYTES:
                raise ValueError
            payload = json.loads(raw_body.decode("utf-8"))
            turn_id = payload["turn_id"]
            answer = payload["answer"]
            if not isinstance(turn_id, str) or not isinstance(answer, str):
                raise ValueError
            turn_metrics, failed_stage = parse_plugin_metrics(
                payload.get("metrics")
            )
        except (
            DirectConversationError,
            KeyError,
            UnicodeDecodeError,
            ValueError,
            json.JSONDecodeError,
        ):
            return JSONResponse(
                {"ok": False, "error": "invalid_request"},
                status_code=400,
            )
        if not await voice_mailbox.begin_answer(turn_id):
            return JSONResponse(
                {"ok": False, "error": "turn_not_claimed"},
                status_code=409,
            )
        body_metrics = None
        status = "incomplete"
        turn_metrics["server_answer_received_ms"] = (
            time.time_ns() // 1_000_000
        )
        try:
            body_metrics = await speak_direct_answer(
                runtime,
                turn_id,
                answer,
                voice,
            )
        except Exception as exc:
            status = "body_unavailable"
            if isinstance(exc, DirectConversationError):
                body_metrics = exc.metrics
            logger.warning("direct answer failed (%s)", type(exc).__name__)
            response = JSONResponse(
                {"ok": False, "error": "body_unavailable"},
                status_code=503,
            )
        else:
            status = "ok"
            response = JSONResponse({"ok": True, "turn_id": turn_id})
        finally:
            await voice_mailbox.finish_answer(turn_id)
            turn_metrics.update(body_metrics or {})
            emit_direct_turn_metrics(
                build_direct_turn_report(
                    turn_id,
                    status,
                    turn_metrics,
                    failed_stage,
                )
            )
        return response

    async def summary_v1(request: Any) -> Any:
        try:
            raw_body = await request.body()
            if len(raw_body) > _MAX_SUMMARY_REQUEST_BYTES:
                raise ValueError
            payload = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
            return JSONResponse(
                {"ok": False, "error": "invalid_request"},
                status_code=400,
            )
        status, response = await handle_summary_request(
            runtime,
            payload,
            voice=voice,
        )
        return JSONResponse(response, status_code=status)

    @asynccontextmanager
    async def lifespan(_app: Any):
        async with manager.run():
            recovery_task = asyncio.create_task(
                _maintain_pending_runtime(
                    config,
                    runtime,
                    httpx,
                    streamable_http_client,
                )
            )
            try:
                yield
            finally:
                recovery_task.cancel()
                await asyncio.gather(
                    recovery_task,
                    return_exceptions=True,
                )

    routes = [
        Route(
            "/mcp",
            endpoint=_StreamableHTTPApp(manager),
            methods=["GET", "POST", "DELETE"],
        ),
        Route("/healthz", endpoint=healthz, methods=["GET"]),
        Route("/readyz", endpoint=readyz, methods=["GET"]),
        Route("/summary/v1", endpoint=summary_v1, methods=["POST"]),
        Route(
            "/voice/v1/capture",
            endpoint=voice_capture,
            methods=["POST"],
        ),
        Route(
            "/voice/v1/capture",
            endpoint=voice_claim,
            methods=["GET"],
        ),
        Route(
            "/voice/v1/abandon",
            endpoint=voice_abandon,
            methods=["POST"],
        ),
        Route(
            "/voice/v1/answer",
            endpoint=voice_answer,
            methods=["POST"],
        ),
    ]
    app = Starlette(routes=routes, lifespan=lifespan)
    return _BearerAuthApp(app, downstream_token)


async def run_http_service(
    config: Any,
    playback_config: PlaybackConfig,
    *,
    host: str,
    port: int,
    downstream_token: str,
    voice: str,
) -> None:
    try:
        import uvicorn
    except ImportError as exc:
        raise PendingThoughtServiceError(
            "the deployment environment must provide an ASGI server"
        ) from exc

    app = build_app(
        config,
        playback_config,
        host=host,
        downstream_token=downstream_token,
        voice=voice,
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
        config = load_config(
            url=args.url,
            environ=environ,
            require_avatar=True,
        )
        downstream_token = load_downstream_token(environ)
        validate_bind_safety(args.host, downstream_token)
        playback_config = load_playback_config(environ)
        voice = load_summary_voice(environ)
        asyncio.run(
            run_http_service(
                config,
                playback_config,
                host=args.host,
                port=args.port,
                downstream_token=downstream_token,
                voice=voice,
            )
        )
    except (
        RunnerConfigError,
        PendingThoughtServiceError,
        ValueError,
    ) as exc:
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
