import asyncio
import base64
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from gateway.speech_preparation import DEFAULT_VOICE, VOICE_ENV
from gateway.thought_summary_service import (
    handle_summary_request,
    load_summary_voice,
    parse_thought_summary,
)


_OPUS_PACKET = bytes.fromhex(
    "5802f9304dbb0de5e392098938ebcae1b1d1dd85"
)
_FRAMED_OPUS = len(_OPUS_PACKET).to_bytes(2, "big") + _OPUS_PACKET
_PREPARED_AUDIO_BASE64 = base64.b64encode(_FRAMED_OPUS).decode("ascii")


class ThoughtSummaryServiceTests(unittest.TestCase):
    def test_summary_contract_accepts_bounded_private_chinese_text(self):
        request = parse_thought_summary(
            {
                "version": "v1",
                "thought_id": "subagent:abc123",
                "summary": "  你的私人项目已经完成，可以查看结果。  ",
            }
        )

        self.assertEqual(request.thought_id, "subagent:abc123")
        self.assertEqual(
            request.summary,
            "你的私人项目已经完成，可以查看结果。",
        )

    def test_invalid_summary_fails_before_synthesis_or_runtime(self):
        runtime = SimpleNamespace(consider_thought=AsyncMock())
        prepare = AsyncMock(return_value=_PREPARED_AUDIO_BASE64)

        status, response = asyncio.run(
            handle_summary_request(
                runtime,
                {
                    "version": "v1",
                    "thought_id": "subagent:abc123",
                    "summary": "English only",
                },
                voice=DEFAULT_VOICE,
                speech_preparer=prepare,
            )
        )

        self.assertEqual(status, 400)
        self.assertEqual(response, {"ok": False, "error": "invalid_request"})
        prepare.assert_not_awaited()
        runtime.consider_thought.assert_not_awaited()

    def test_synthesis_and_audio_validation_precede_offer_transition(self):
        runtime = SimpleNamespace(
            consider_thought=AsyncMock(
                return_value=SimpleNamespace(
                    thought_id="cron:def456",
                    state="waiting",
                )
            )
        )
        prepare = AsyncMock(return_value=_PREPARED_AUDIO_BASE64)
        summary = "定时任务已经完成，结果可以查看。"

        status, response = asyncio.run(
            handle_summary_request(
                runtime,
                {
                    "version": "v1",
                    "thought_id": "cron:def456",
                    "summary": summary,
                },
                voice=DEFAULT_VOICE,
                speech_preparer=prepare,
            )
        )

        self.assertEqual(status, 200)
        self.assertEqual(
            response,
            {
                "ok": True,
                "thought_id": "cron:def456",
                "state": "waiting",
            },
        )
        prepare.assert_awaited_once_with(summary, DEFAULT_VOICE)
        payload = runtime.consider_thought.await_args.args[0]
        self.assertEqual(payload["decision"], "offer")
        self.assertEqual(payload["audio_base64"], _PREPARED_AUDIO_BASE64)
        self.assertNotIn("summary", payload)

    def test_synthesis_failure_causes_no_knock_or_plaintext_leakage(self):
        private_summary = "私人事项：体检报告已经整理完成。"
        runtime = SimpleNamespace(consider_thought=AsyncMock())
        prepare = AsyncMock(
            side_effect=RuntimeError(f"failed for {private_summary}")
        )

        status, response = asyncio.run(
            handle_summary_request(
                runtime,
                {
                    "version": "v1",
                    "thought_id": "cron:private",
                    "summary": private_summary,
                },
                voice=DEFAULT_VOICE,
                speech_preparer=prepare,
            )
        )

        self.assertEqual(status, 502)
        self.assertEqual(
            response,
            {"ok": False, "error": "speech_preparation_failed"},
        )
        self.assertNotIn(private_summary, json.dumps(response))
        runtime.consider_thought.assert_not_awaited()

    def test_invalid_prepared_audio_causes_no_knock(self):
        runtime = SimpleNamespace(consider_thought=AsyncMock())
        prepare = AsyncMock(return_value="not-valid-audio")

        status, response = asyncio.run(
            handle_summary_request(
                runtime,
                {
                    "version": "v1",
                    "thought_id": "cron:invalid-audio",
                    "summary": "任务已经完成，可以查看。",
                },
                voice=DEFAULT_VOICE,
                speech_preparer=prepare,
            )
        )

        self.assertEqual(status, 502)
        self.assertEqual(response["error"], "speech_preparation_failed")
        runtime.consider_thought.assert_not_awaited()

    def test_voice_uses_existing_default_and_environment_override(self):
        self.assertEqual(load_summary_voice({}), DEFAULT_VOICE)
        self.assertEqual(
            load_summary_voice({VOICE_ENV: " zh-CN-XiaoxiaoNeural "}),
            "zh-CN-XiaoxiaoNeural",
        )


if __name__ == "__main__":
    unittest.main()
