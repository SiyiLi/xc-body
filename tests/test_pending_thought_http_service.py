import asyncio
import io
import json
import unittest
from contextlib import redirect_stderr
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from gateway.pending_thought_http_service import (
    AUTH_FAILURE_MESSAGE,
    DOWNSTREAM_TOKEN_ENV,
    _BearerAuthApp,
    _health_payload,
    _readiness_payload,
    build_app,
    load_downstream_token,
    main,
    validate_bind_safety,
)
from gateway.pending_thought_service import (
    PendingThoughtServiceError,
    validate_playback_url,
)
from gateway.semantic_e2e import TOKEN_ENV as UPSTREAM_TOKEN_ENV
from gateway.semantic_e2e import URL_ENV as UPSTREAM_URL_ENV


class RecordingApp:
    def __init__(self):
        self.calls = []
        self.state = SimpleNamespace()
        self.router = object()

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
        self.assertEqual(_health_payload(), {"ok": True})

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
            machine=SimpleNamespace(pending_thought_id="eval:private")
        )
        self.assertEqual(
            _readiness_payload(runtime),
            {"ok": True, "pending_thought_id": "eval:private"},
        )

    def test_non_loopback_bind_requires_downstream_token(self):
        for host in ("127.0.0.1", "::1", "localhost"):
            validate_bind_safety(host, "")

        with self.assertRaisesRegex(
            PendingThoughtServiceError,
            DOWNSTREAM_TOKEN_ENV,
        ):
            validate_bind_safety("0.0.0.0", "")
        validate_bind_safety("0.0.0.0", "downstream-secret")

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

    def test_main_rejects_exposed_bind_before_starting_server(self):
        stderr = io.StringIO()
        environment = {
            UPSTREAM_URL_ENV: "https://stackchan.invalid/mcp",
            UPSTREAM_TOKEN_ENV: "upstream-secret",
        }

        with redirect_stderr(stderr):
            exit_code = main(
                ["--host", "0.0.0.0"],
                environ=environment,
            )

        self.assertEqual(exit_code, 1)
        result = json.loads(stderr.getvalue())
        self.assertIn(DOWNSTREAM_TOKEN_ENV, result["message"])

    def test_build_app_requires_prepared_audio_configuration(self):
        config = SimpleNamespace(token="upstream-secret")
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(
                PendingThoughtServiceError,
                "XC_BODY_PLAYBACK_URL",
            ):
                build_app(
                    config,
                    host="127.0.0.1",
                    port=8770,
                    downstream_token="downstream-secret",
                )
        with patch.dict(
            "os.environ",
            {"XC_BODY_PLAYBACK_URL": "http://127.0.0.1:8766/opus"},
            clear=True,
        ):
            with self.assertRaisesRegex(
                PendingThoughtServiceError,
                "XC_BODY_PLAYBACK_TOKEN",
            ):
                build_app(
                    config,
                    host="127.0.0.1",
                    port=8770,
                    downstream_token="downstream-secret",
                )

    def test_main_loads_environment_and_passes_separate_boundary_tokens(self):
        service = AsyncMock()
        environment = {
            UPSTREAM_URL_ENV: "https://stackchan.invalid/mcp",
            UPSTREAM_TOKEN_ENV: "upstream-secret",
            DOWNSTREAM_TOKEN_ENV: "downstream-secret",
        }

        with (
            patch.dict("os.environ", environment, clear=True),
            patch(
                "gateway.pending_thought_http_service.run_http_service",
                service,
            ),
        ):
            exit_code = main([])

        self.assertEqual(exit_code, 0)
        config = service.await_args.args[0]
        self.assertEqual(config.token, "upstream-secret")
        self.assertEqual(
            service.await_args.kwargs["downstream_token"],
            "downstream-secret",
        )


if __name__ == "__main__":
    unittest.main()
