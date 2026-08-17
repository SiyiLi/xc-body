import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from gateway.pending_thought_http_service import (
    AUTH_FAILURE_MESSAGE,
    DOWNSTREAM_TOKEN_ENV,
    _BearerAuthApp,
    _readiness_payload,
    load_downstream_token,
    main,
    validate_bind_safety,
)
from gateway.pending_thought_service import (
    PlaybackConfig,
    PendingThoughtServiceError,
    validate_playback_url,
)
from gateway.semantic_e2e import TOKEN_ENV as UPSTREAM_TOKEN_ENV
from gateway.semantic_e2e import URL_ENV as UPSTREAM_URL_ENV


class RecordingApp:
    def __init__(self):
        self.calls = []

    async def __call__(self, scope, receive, send):
        del receive
        self.calls.append(scope)
        await send({"type": "http.response.start", "status": 204})
        await send({"type": "http.response.body", "body": b""})


async def call_app(app, path, authorization=None):
    headers = []
    if authorization is not None:
        headers.append((b"authorization", authorization.encode("ascii")))
    scope = {"type": "http", "path": path, "headers": headers}
    messages = []

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        messages.append(message)

    await app(scope, receive, send)
    return messages


class PendingThoughtHTTPServiceTests(unittest.TestCase):
    def test_mcp_requires_exact_downstream_bearer(self):
        inner = RecordingApp()
        app = _BearerAuthApp(inner, "downstream-secret")

        missing = asyncio.run(call_app(app, "/mcp"))
        wrong = asyncio.run(
            call_app(app, "/mcp", "Bearer upstream-secret")
        )
        accepted = asyncio.run(
            call_app(app, "/mcp", "Bearer downstream-secret")
        )

        self.assertEqual(missing[0]["status"], 401)
        self.assertEqual(wrong[0]["status"], 401)
        self.assertEqual(missing[1]["body"].decode(), AUTH_FAILURE_MESSAGE)
        self.assertEqual(accepted[0]["status"], 204)
        self.assertEqual(len(inner.calls), 1)

    def test_health_is_public_liveness_without_pending_metadata(self):
        inner = RecordingApp()
        app = _BearerAuthApp(inner, "")

        response = asyncio.run(call_app(app, "/healthz"))

        self.assertEqual(response[0]["status"], 204)
        self.assertEqual(len(inner.calls), 1)

    def test_readiness_state_requires_exact_downstream_bearer(self):
        inner = RecordingApp()
        app = _BearerAuthApp(inner, "downstream-secret")

        missing = asyncio.run(call_app(app, "/readyz"))
        accepted = asyncio.run(
            call_app(app, "/readyz", "Bearer downstream-secret")
        )

        self.assertEqual(missing[0]["status"], 401)
        self.assertEqual(accepted[0]["status"], 204)
        runtime = SimpleNamespace(
            machine=SimpleNamespace(pending_thought_id="eval:private"),
            is_ready=AsyncMock(return_value=True),
        )
        self.assertEqual(
            asyncio.run(_readiness_payload(runtime)),
            {"ok": True, "pending_thought_id": "eval:private"},
        )

    def test_only_network_visible_bind_requires_downstream_token(self):
        for host in ("127.0.0.1", "::1", "localhost"):
            with self.subTest(host=host):
                validate_bind_safety(host, "")
            validate_bind_safety(host, "downstream-secret")
        with self.assertRaisesRegex(
            PendingThoughtServiceError,
            DOWNSTREAM_TOKEN_ENV,
        ):
            validate_bind_safety("0.0.0.0", "")

    def test_playback_url_allows_plaintext_only_on_loopback(self):
        for url in (
            "http://localhost:8766/opus",
            "http://127.0.0.1:8766/opus",
            "http://[::1]:8766/opus",
            "https://playback.invalid/opus",
        ):
            validate_playback_url(url)

        with self.assertRaisesRegex(
            PendingThoughtServiceError,
            "must use HTTPS",
        ):
            validate_playback_url("http://playback.invalid/opus")
        with self.assertRaisesRegex(
            PendingThoughtServiceError,
            "absolute HTTP",
        ):
            validate_playback_url("file:///tmp/audio")

    def test_downstream_token_does_not_fall_back_to_upstream_token(self):
        self.assertEqual(
            load_downstream_token({UPSTREAM_TOKEN_ENV: "upstream-secret"}),
            "",
        )
        self.assertEqual(
            load_downstream_token(
                {
                    UPSTREAM_TOKEN_ENV: "upstream-secret",
                    DOWNSTREAM_TOKEN_ENV: " downstream-secret ",
                }
            ),
            "downstream-secret",
        )

    def test_main_loads_environment_and_passes_separate_boundary_tokens(self):
        service = AsyncMock()
        environment = {
            UPSTREAM_URL_ENV: "https://stackchan.invalid/mcp",
            UPSTREAM_TOKEN_ENV: "upstream-secret",
            DOWNSTREAM_TOKEN_ENV: "downstream-secret",
            "XC_BODY_AVATAR_ARCHIVE_PATH": "/srv/xc-body/avatar.rgb565le",
            "XC_BODY_PLAYBACK_URL": "http://127.0.0.1:8766/opus",
            "XC_BODY_PLAYBACK_TOKEN": "playback-secret",
        }

        with patch(
            "gateway.pending_thought_http_service.run_http_service",
            service,
        ):
            exit_code = main([], environ=environment)

        self.assertEqual(exit_code, 0)
        config = service.await_args.args[0]
        self.assertEqual(config.token, "upstream-secret")
        self.assertEqual(
            service.await_args.kwargs["downstream_token"],
            "downstream-secret",
        )
        self.assertEqual(
            service.await_args.args[1],
            PlaybackConfig(
                url="http://127.0.0.1:8766/opus",
                token="playback-secret",
            ),
        )


if __name__ == "__main__":
    unittest.main()
