"""Status-only WebSocket relay for one XC Buddy sender and receiver."""

from __future__ import annotations

import argparse
import asyncio
import hmac
import json
import logging
import os
import sys
from collections.abc import Mapping
from typing import Any


SENDER_TOKEN_ENV = "XC_BUDDY_RELAY_SENDER_TOKEN"
RECEIVER_TOKEN_ENV = "XC_BUDDY_RELAY_RECEIVER_TOKEN"

_STATES = frozenset({"idle", "working", "approval_needed"})
_NOTICES = frozenset({"done", "error"})
_MAX_MESSAGE_BYTES = 256
_SEND_TIMEOUT_SECONDS = 2.0
_LOGGER = logging.getLogger("uvicorn.error")


class RelayServiceError(ValueError):
    """Raised when the relay cannot start with its supplied configuration."""


def _secrets_match(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def load_tokens(
    environ: Mapping[str, str] | None = None,
) -> tuple[str, str]:
    """Load the two independent relay role credentials."""

    source = os.environ if environ is None else environ
    sender = source.get(SENDER_TOKEN_ENV, "").strip()
    receiver = source.get(RECEIVER_TOKEN_ENV, "").strip()
    if not sender or not receiver:
        raise RelayServiceError("XC Buddy relay tokens are missing")
    if _secrets_match(sender, receiver):
        raise RelayServiceError("XC Buddy relay tokens must be different")
    return sender, receiver


def _parse_sender_message(text: str) -> dict[str, str]:
    if len(text.encode("utf-8")) > _MAX_MESSAGE_BYTES:
        raise ValueError("message is too large")
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("message must be an object")
    if set(payload) == {"kind", "state"}:
        state = payload.get("state")
        if (
            payload.get("kind") == "state"
            and isinstance(state, str)
            and state in _STATES
        ):
            return {"kind": "state", "state": state}
    if set(payload) == {"kind", "notice"}:
        notice = payload.get("notice")
        if (
            payload.get("kind") == "notice"
            and isinstance(notice, str)
            and notice in _NOTICES
        ):
            return {"kind": "notice", "notice": notice}
    raise ValueError("unsupported relay message")


class XcBuddyRelay:
    """Own the single live sender, receiver, and current relay state."""

    def __init__(self, sender_token: str, receiver_token: str) -> None:
        self._sender_token = sender_token
        self._receiver_token = receiver_token
        self._sender: Any | None = None
        self._receiver: Any | None = None
        self._state = "idle"
        self._lock = asyncio.Lock()

    def role_for_authorization(self, authorization: str) -> str | None:
        sender = f"Bearer {self._sender_token}"
        receiver = f"Bearer {self._receiver_token}"
        if _secrets_match(authorization, sender):
            return "sender"
        if _secrets_match(authorization, receiver):
            return "receiver"
        return None

    async def handle(self, websocket: Any) -> None:
        role = self.role_for_authorization(
            websocket.headers.get("authorization", "")
        )
        if role is None:
            await websocket.close(code=4401)
            return

        await websocket.accept()
        if role == "sender":
            await self._handle_sender(websocket)
        else:
            await self._handle_receiver(websocket)

    async def _handle_sender(self, websocket: Any) -> None:
        previous = await self._replace_sender(websocket)
        _LOGGER.info(
            "XC Buddy relay sender connected replaced=%s",
            previous is not None,
        )
        await self._close_quietly(previous, 4001)
        received_state = False
        try:
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    break
                text = message.get("text")
                if not isinstance(text, str):
                    await websocket.close(code=1003)
                    break
                try:
                    payload = _parse_sender_message(text)
                except (UnicodeError, ValueError, json.JSONDecodeError):
                    await websocket.close(code=1008)
                    break
                if not received_state and payload["kind"] != "state":
                    await websocket.close(code=1008)
                    break
                received_state = True
                if not await self._publish(websocket, payload):
                    await websocket.close(code=4001)
                    break
        finally:
            await self._remove_sender(websocket)

    async def _handle_receiver(self, websocket: Any) -> None:
        previous, active = await self._replace_receiver(websocket)
        await self._close_quietly(previous, 4001)
        if not active:
            return
        _LOGGER.info(
            "XC Buddy relay receiver connected replaced=%s",
            previous is not None,
        )
        try:
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    break
                await websocket.close(code=1008)
                break
        finally:
            await self._remove_receiver(websocket)

    async def _replace_sender(self, websocket: Any) -> Any | None:
        async with self._lock:
            previous = self._sender
            self._sender = websocket
            return previous

    async def _replace_receiver(
        self,
        websocket: Any,
    ) -> tuple[Any | None, bool]:
        failed = None
        async with self._lock:
            previous = self._receiver
            self._receiver = websocket
            if not await self._send_receiver_locked(
                {
                    "kind": "state",
                    "state": self._state,
                    "snapshot": True,
                }
            ):
                failed = websocket
                self._receiver = None
        await self._close_quietly(failed, 1011)
        return previous, failed is None

    async def _publish(
        self,
        websocket: Any,
        payload: dict[str, str],
    ) -> bool:
        failed = None
        async with self._lock:
            if self._sender is not websocket:
                return False
            receiver_present = self._receiver is not None
            if payload["kind"] == "state":
                self._state = payload["state"]
                outbound: dict[str, str | bool] = {
                    "kind": "state",
                    "state": self._state,
                    "snapshot": False,
                }
            else:
                self._state = "idle"
                outbound = {
                    "kind": "notice",
                    "notice": payload["notice"],
                }
            if not await self._send_receiver_locked(outbound):
                failed = self._receiver
                self._receiver = None
            if not receiver_present:
                receiver_status = "absent"
            elif failed is None:
                receiver_status = "forwarded"
            else:
                receiver_status = "failed"
            value = payload[payload["kind"]]
            _LOGGER.info(
                "XC Buddy relay sender event kind=%s value=%s receiver=%s",
                payload["kind"],
                value,
                receiver_status,
            )
        await self._close_quietly(failed, 1011)
        return True

    async def _remove_sender(self, websocket: Any) -> None:
        failed = None
        async with self._lock:
            if self._sender is not websocket:
                return
            self._sender = None
            self._state = "idle"
            if not await self._send_receiver_locked(
                {
                    "kind": "state",
                    "state": "idle",
                    "snapshot": False,
                }
            ):
                failed = self._receiver
                self._receiver = None
            _LOGGER.info("XC Buddy relay sender disconnected")
        await self._close_quietly(failed, 1011)

    async def _remove_receiver(self, websocket: Any) -> None:
        async with self._lock:
            if self._receiver is websocket:
                self._receiver = None
                _LOGGER.info("XC Buddy relay receiver disconnected")

    async def _send_receiver_locked(
        self,
        payload: Mapping[str, str | bool],
    ) -> bool:
        if self._receiver is None:
            return True
        try:
            await asyncio.wait_for(
                self._receiver.send_json(dict(payload)),
                timeout=_SEND_TIMEOUT_SECONDS,
            )
        except Exception:
            return False
        return True

    @staticmethod
    async def _close_quietly(websocket: Any | None, code: int) -> None:
        if websocket is None:
            return
        try:
            await asyncio.wait_for(
                websocket.close(code=code),
                timeout=_SEND_TIMEOUT_SECONDS,
            )
        except Exception:
            pass


def build_app(sender_token: str, receiver_token: str) -> Any:
    """Build the isolated relay HTTP and WebSocket surface."""

    try:
        from starlette.applications import Starlette
        from starlette.responses import JSONResponse
        from starlette.routing import Route, WebSocketRoute
    except ImportError as exc:
        raise RelayServiceError(
            "the deployment environment must provide an ASGI stack"
        ) from exc

    relay = XcBuddyRelay(sender_token, receiver_token)

    async def healthz(_request: Any) -> Any:
        return JSONResponse({"ok": True})

    return Starlette(
        routes=[
            Route("/healthz", endpoint=healthz, methods=["GET"]),
            WebSocketRoute("/v1/stream", endpoint=relay.handle),
        ]
    )


async def run_service(
    sender_token: str,
    receiver_token: str,
    *,
    host: str,
    port: int,
) -> None:
    try:
        import uvicorn
    except ImportError as exc:
        raise RelayServiceError(
            "the deployment environment must provide an ASGI server"
        ) from exc

    server = uvicorn.Server(
        uvicorn.Config(
            build_app(sender_token, receiver_token),
            host=host,
            port=port,
            log_level="info",
            lifespan="off",
            ws_max_size=_MAX_MESSAGE_BYTES,
            ws_ping_interval=20.0,
            ws_ping_timeout=20.0,
        )
    )
    await server.serve()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the XC Buddy status relay service."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8781)
    return parser


def main(
    argv: list[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> int:
    args = _parser().parse_args(argv)
    try:
        sender_token, receiver_token = load_tokens(environ)
        asyncio.run(
            run_service(
                sender_token,
                receiver_token,
                host=args.host,
                port=args.port,
            )
        )
    except RelayServiceError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
