import importlib
import json
import unittest


class SemanticE2eRunnerTests(unittest.TestCase):
    def test_request_accepts_json_or_only_curious_shortcut(self):
        from gateway.semantic_e2e import RunnerInputError, parse_request

        self.assertEqual(parse_request("curious")["intent"], "curious")
        payload = json.dumps({"version": "v1", "intent": "idle"})
        self.assertEqual(parse_request(payload)["intent"], "idle")
        with self.assertRaises(RunnerInputError):
            parse_request("idle")

    def test_config_uses_only_explicit_url_and_environment_token(self):
        from gateway.semantic_e2e import (
            AVATAR_PATH_ENV,
            TOKEN_ENV,
            URL_ENV,
            load_config,
        )

        config = load_config(
            url="http://127.0.0.1:8767/mcp",
            environ={
                TOKEN_ENV: "test-token",
                AVATAR_PATH_ENV: "/state/native.rgb565le",
            },
        )
        environment_config = load_config(
            environ={
                URL_ENV: "https://daemon.invalid/mcp",
                TOKEN_ENV: "environment-token",
                AVATAR_PATH_ENV: "/state/native.rgb565le",
            }
        )

        self.assertEqual(config.url, "http://127.0.0.1:8767/mcp")
        self.assertEqual(config.token, "test-token")
        self.assertNotIn("test-token", repr(config))
        self.assertEqual(config.avatar_path, "/state/native.rgb565le")
        self.assertNotIn("native.rgb565le", repr(config))
        self.assertEqual(environment_config.url, "https://daemon.invalid/mcp")

    def test_config_rejects_plaintext_non_loopback_url(self):
        from gateway.semantic_e2e import (
            AVATAR_PATH_ENV,
            TOKEN_ENV,
            RunnerConfigError,
            load_config,
        )

        with self.assertRaisesRegex(RunnerConfigError, "must use HTTPS"):
            load_config(
                url="http://daemon.invalid/mcp",
                environ={
                    TOKEN_ENV: "test-token",
                    AVATAR_PATH_ENV: "/state/native.rgb565le",
                },
            )

    def test_avatar_restore_requires_exact_reviewed_digest(self):
        from gateway import semantic_e2e
        from stackchan.avatar_verification import REVIEWED_AVATAR_CHECKSUM

        class Session:
            def __init__(self, checksum, session_ids=None):
                self.checksum = checksum
                self.session_ids = iter(
                    session_ids or ("device-session", "device-session")
                )
                self.calls = []

            async def call_tool(self, name, arguments):
                self.calls.append((name, arguments))
                if name == "get_status":
                    return {
                        "connected": True,
                        "initialized": True,
                        "session_id": next(self.session_ids),
                    }
                payload = {"ok": True, "checksum": self.checksum}
                return {
                    "content": [
                        {"type": "text", "text": json.dumps(payload)}
                    ],
                    "isError": False,
                }

        reviewed = Session(REVIEWED_AVATAR_CHECKSUM)
        asyncio = importlib.import_module("asyncio")
        verified_session_id = asyncio.run(
            semantic_e2e._restore_reviewed_avatar(
                reviewed,
                "/state/native.rgb565le",
            )
        )
        self.assertEqual(verified_session_id, "device-session")
        self.assertEqual(
            [call[0] for call in reviewed.calls],
            ["get_status", "load_avatar_set", "get_status"],
        )

        wrong = Session("sha256:" + ("a" * 64))
        with self.assertRaisesRegex(
            semantic_e2e.RunnerExecutionError,
            "does not match",
        ):
            asyncio.run(
                semantic_e2e._restore_reviewed_avatar(
                    wrong,
                    "/state/native.rgb565le",
                )
            )
        changed = Session(
            REVIEWED_AVATAR_CHECKSUM,
            session_ids=("session-1", "session-2"),
        )
        with self.assertRaisesRegex(
            semantic_e2e.RunnerExecutionError,
            "session changed",
        ):
            asyncio.run(
                semantic_e2e._restore_reviewed_avatar(
                    changed,
                    "/state/native.rgb565le",
                )
            )

if __name__ == "__main__":
    unittest.main()
