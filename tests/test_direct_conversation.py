import asyncio
import json
import unittest
from unittest.mock import patch

from gateway.direct_conversation import (
    DirectConversationError,
    VoiceMailbox,
    build_direct_turn_report,
)


class DirectConversationTests(unittest.IsolatedAsyncioTestCase):
    async def test_mailbox_allows_only_one_exactly_once_active_turn(self):
        mailbox = VoiceMailbox()

        first_id = await mailbox.submit(b"first")
        first = await mailbox.claim(timeout=0)
        self.assertIsNotNone(first)
        self.assertEqual(first.turn_id, first_id)
        self.assertEqual(first.audio, b"first")

        with self.assertRaisesRegex(
            DirectConversationError,
            "another robot turn is pending",
        ):
            await mailbox.submit(b"overlap")

        self.assertTrue(await mailbox.abandon(first_id))
        self.assertFalse(await mailbox.abandon(first_id))

        second_id = await mailbox.submit(b"second")
        second = await mailbox.claim(timeout=0)
        self.assertIsNotNone(second)
        self.assertEqual(second.turn_id, second_id)
        self.assertTrue(await mailbox.begin_answer(second_id))
        self.assertFalse(await mailbox.abandon(second_id))
        self.assertFalse(await mailbox.begin_answer(second_id))
        await mailbox.finish_answer(second_id)

        third_id = await mailbox.submit(b"third")
        self.assertNotEqual(third_id, first_id)
        self.assertNotEqual(third_id, second_id)

    def test_turn_report_is_content_free_and_derives_latency(self):
        metrics = {
            "capture_started_uptime_us": 1_000_000,
            "capture_stopped_uptime_us": 2_250_000,
            "gateway_capture_started_ms": 1000,
            "gateway_capture_stopped_ms": 2250,
            "gateway_playback_started_ms": 4500,
        }

        with patch(
            "gateway.direct_conversation._unix_ms",
            return_value=6000,
        ):
            report = build_direct_turn_report(
                "robot:1",
                "ok",
                metrics,
                None,
            )

        self.assertEqual(metrics["device_recording_ms"], 1250)
        self.assertEqual(metrics["submit_to_speech_start_ms"], 2250)
        self.assertEqual(metrics["end_to_end_ms"], 5000)
        encoded = json.dumps(report)
        self.assertNotIn("transcript", encoded)
        self.assertNotIn("answer", encoded)


if __name__ == "__main__":
    unittest.main()
