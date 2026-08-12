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
        from gateway.semantic_e2e import TOKEN_ENV, URL_ENV, load_config

        config = load_config(
            url="http://127.0.0.1:8767/mcp",
            environ={TOKEN_ENV: "test-token"},
        )
        environment_config = load_config(
            environ={
                URL_ENV: "https://daemon.invalid/mcp",
                TOKEN_ENV: "environment-token",
            }
        )

        self.assertEqual(config.url, "http://127.0.0.1:8767/mcp")
        self.assertEqual(config.token, "test-token")
        self.assertNotIn("test-token", repr(config))
        self.assertEqual(environment_config.url, "https://daemon.invalid/mcp")

    def test_config_rejects_plaintext_non_loopback_url(self):
        from gateway.semantic_e2e import (
            TOKEN_ENV,
            RunnerConfigError,
            load_config,
        )

        with self.assertRaisesRegex(RunnerConfigError, "must use HTTPS"):
            load_config(
                url="http://daemon.invalid/mcp",
                environ={TOKEN_ENV: "test-token"},
            )

    def test_production_preflight_precedes_config_and_mcp(self):
        from gateway import semantic_e2e
        from stackchan.adapter import VisibleFaceVerificationError

        with patch.object(semantic_e2e, "load_config") as load_config:
            with patch.object(semantic_e2e, "_run_with_mcp") as execute:
                with self.assertRaisesRegex(
                    VisibleFaceVerificationError,
                    "visible face verification",
                ):
                    semantic_e2e.run("curious", environ={})
        load_config.assert_not_called()
        execute.assert_not_called()

    def test_main_prints_only_failure_for_production_curious(self):
        from gateway import semantic_e2e

        stdout = io.StringIO()
        stderr = io.StringIO()

        with redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = semantic_e2e.main(["curious"], environ={})

        self.assertEqual(exit_code, 1)
        self.assertEqual(stdout.getvalue(), "")
        result = json.loads(stderr.getvalue())
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "VisibleFaceVerificationError")
        self.assertIn("visible face verification", result["message"])

    def test_production_execution_cannot_return_success(self):
        from gateway import semantic_e2e
        from stackchan.adapter import VisibleFaceVerificationError

        calls = []

        def call_tool(name, arguments):
            calls.append((name, arguments))
            return {"ok": True}

        with self.assertRaises(VisibleFaceVerificationError):
            semantic_e2e._execute_sync(
                {"version": "v1", "intent": "curious"},
                call_tool,
            )

        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
