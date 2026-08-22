import asyncio
import unittest

from gateway.direct_conversation import DirectConversationError, VoiceMailbox


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


if __name__ == "__main__":
    unittest.main()
