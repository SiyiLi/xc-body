import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from gateway.pending_thought_http_service import (
    AUTH_FAILURE_MESSAGE,
    DOWNSTREAM_TOKEN_ENV,
    _BearerAuthApp,
    _maintain_pending_runtime,
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
            is_ready=AsyncMock(return_value=True),
            pending_thought_id=AsyncMock(return_value="eval:private"),
        )
        self.assertEqual(
            asyncio.run(_readiness_payload(runtime)),
            {"ok": True, "pending_thought_id": "eval:private"},
        )
        runtime.pending_thought_id.assert_awaited_once_with()

    def test_control_routes_require_exact_downstream_bearer(self):
        inner = RecordingApp()
        app = _BearerAuthApp(inner, "downstream-secret")

        paths = (
            "/summary/v1",
            "/voice/v1/capture",
            "/voice/v1/abandon",
            "/voice/v1/answer",
        )
        for path in paths:
            with self.subTest(path=path):
                missing = asyncio.run(call_app(app, path))
                accepted = asyncio.run(
                    call_app(app, path, "Bearer downstream-secret")
                )

                self.assertEqual(missing[0]["status"], 401)
                self.assertEqual(accepted[0]["status"], 204)
        self.assertEqual(len(inner.calls), len(paths))

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

    def test_failed_restore_recreates_upstream_session(self):
        class AsyncContext:
            def __init__(self, value):
                self.value = value

            async def __aenter__(self):
                return self.value

            async def __aexit__(self, *args):
                del args

        class Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                del args

            async def initialize(self):
                pass

        config = SimpleNamespace(
            token="upstream-secret",
            url="https://stackchan.invalid/mcp",
            avatar_path="/srv/xc-body/avatar.rgb565le",
        )
        runtime = Mock()
        runtime.create_session.side_effect = [Session(), Session()]
        httpx = SimpleNamespace(
            Timeout=lambda *args, **kwargs: object(),
            AsyncClient=lambda **kwargs: AsyncContext(object()),
        )
        streamable_http_client = Mock(
            side_effect=lambda *args, **kwargs: AsyncContext(
                (object(), object())
            )
        )

        async def exercise():
            with patch(
                "gateway.pending_thought_http_service."
                "_restore_pending_runtime_if_needed",
                new=AsyncMock(side_effect=[False, asyncio.CancelledError]),
            ), patch(
                "gateway.pending_thought_http_service.asyncio.sleep",
                new=AsyncMock(),
            ):
                with self.assertRaises(asyncio.CancelledError):
                    await _maintain_pending_runtime(
                        config,
                        runtime,
                        httpx,
                        streamable_http_client,
                    )

        asyncio.run(exercise())

        self.assertEqual(streamable_http_client.call_count, 2)
        self.assertEqual(runtime.create_session.call_count, 2)

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
            "XC_BODY_VOICE": "zh-CN-XiaoxiaoNeural",
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
        self.assertEqual(
            service.await_args.kwargs["voice"],
            "zh-CN-XiaoxiaoNeural",
        )


if __name__ == "__main__":
    unittest.main()
