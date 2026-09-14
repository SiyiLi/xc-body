import asyncio
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
        from gateway.semantic_e2e import TOKEN_ENV, URL_ENV, load_config

        config = load_config(
            url="http://gateway:8767/mcp",
            environ={TOKEN_ENV: "test-token"},
        )
        environment_config = load_config(
            environ={
                URL_ENV: "https://daemon.invalid/mcp",
                TOKEN_ENV: "environment-token",
            }
        )

        self.assertEqual(config.url, "http://gateway:8767/mcp")
        self.assertEqual(config.token, "test-token")
        self.assertNotIn("test-token", repr(config))
        self.assertEqual(environment_config.url, "https://daemon.invalid/mcp")

    def test_config_rejects_plaintext_non_loopback_url(self):
        from gateway.semantic_e2e import TOKEN_ENV, RunnerConfigError, load_config

        with self.assertRaisesRegex(RunnerConfigError, "must use HTTPS"):
            load_config(
                url="http://daemon.invalid/mcp",
                environ={TOKEN_ENV: "test-token"},
            )

    def test_readiness_requires_connected_initialized_device(self):
        from gateway import semantic_e2e

        class Session:
            def __init__(self, status):
                self.status = status
                self.calls = []

            async def call_tool(self, name, arguments):
                self.calls.append((name, arguments))
                return self.status

        ready = Session(
            {
                "connected": True,
                "initialized": True,
                "session_id": "device-session",
            }
        )
        self.assertEqual(
            asyncio.run(semantic_e2e._require_ready_device(ready)),
            "device-session",
        )
        self.assertEqual(ready.calls, [("get_status", {})])

        unavailable = Session({"connected": False})
        with self.assertRaises(semantic_e2e.RunnerExecutionError):
            asyncio.run(semantic_e2e._require_ready_device(unavailable))


if __name__ == "__main__":
    unittest.main()
