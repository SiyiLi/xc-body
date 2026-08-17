import importlib
import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch


class SemanticE2eRunnerTests(unittest.TestCase):
    def test_import_has_no_sdk_or_execution_side_effects(self):
        module = importlib.import_module("gateway.semantic_e2e")

        self.assertTrue(callable(module.main))

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

    def test_production_preflight_precedes_config_and_mcp(self):
        from gateway import semantic_e2e

        with patch.object(
            semantic_e2e,
            "load_config",
            side_effect=semantic_e2e.RunnerConfigError("missing"),
        ) as load_config:
            with patch.object(semantic_e2e, "_run_with_mcp") as execute:
                with self.assertRaises(semantic_e2e.RunnerConfigError):
                    semantic_e2e.run("curious", environ={})
        load_config.assert_called_once()
        execute.assert_not_called()

    def test_main_prints_only_failure_when_configuration_is_missing(self):
        from gateway import semantic_e2e

        stdout = io.StringIO()
        stderr = io.StringIO()

        with redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = semantic_e2e.main(["curious"], environ={})

        self.assertEqual(exit_code, 1)
        self.assertEqual(stdout.getvalue(), "")
        result = json.loads(stderr.getvalue())
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "RunnerConfigError")
        self.assertIn("daemon URL is required", result["message"])

    def test_avatar_restore_requires_exact_reviewed_digest(self):
        from gateway import semantic_e2e
        from stackchan.avatar_verification import REVIEWED_AVATAR_CHECKSUM

        class Session:
            def __init__(self, checksum):
                self.checksum = checksum
                self.calls = []

            async def call_tool(self, name, arguments):
                self.calls.append((name, arguments))
                return {"ok": True, "checksum": self.checksum}

        reviewed = Session(REVIEWED_AVATAR_CHECKSUM)
        asyncio = importlib.import_module("asyncio")
        asyncio.run(
            semantic_e2e._restore_reviewed_avatar(
                reviewed,
                "/state/native.rgb565le",
            )
        )
        self.assertEqual(reviewed.calls[0][0], "load_avatar_set")

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

    def test_production_execution_uses_verified_curious_recipe(self):
        from gateway import semantic_e2e

        calls = []

        def call_tool(name, arguments):
            calls.append((name, arguments))
            if name == "get_status":
                return {"connected": True}
            return {"ok": True}

        result = semantic_e2e._execute_sync(
            {"version": "v1", "intent": "curious"},
            call_tool,
            sleep=lambda seconds: None,
        )

        self.assertTrue(result["ok"])
        self.assertTrue(result["returned_to_idle"])
        self.assertTrue(calls)


if __name__ == "__main__":
    unittest.main()
