import base64
import unittest
from unittest.mock import patch

from gateway.pending_thought import (
    KnockWaitTell,
    PendingOfferExistsError,
    PendingThoughtError,
    decode_prepared_audio,
    parse_pending_thought,
)


# A real 16 kHz mono, 60 ms Opus packet from the pinned upstream assets.
_OPUS_PACKET = bytes.fromhex(
    "5802f9304dbb0de5e392098938ebcae1b1d1dd85"
)
_FRAMED_OPUS = len(_OPUS_PACKET).to_bytes(2, "big") + _OPUS_PACKET
_PREPARED_AUDIO_BASE64 = base64.b64encode(_FRAMED_OPUS).decode("ascii")


class RecordingBody:
    def __init__(self, fail_tell=False):
        self.knocks = []
        self.tells = []
        self.fail_tell = fail_tell

    def knock(self, thought_id):
        self.knocks.append(thought_id)

    def tell(self, thought_id, audio_base64):
        call = (thought_id, audio_base64)
        if call not in self.tells:
            self.tells.append(call)
        if self.fail_tell:
            raise RuntimeError("synthetic tell failure")


class PendingThoughtTests(unittest.TestCase):
    def test_parser_accepts_each_decision(self):
        cases = (
            ({"version": "v1", "thought_id": "a", "decision": "ignore"}),
            ({"version": "v1", "thought_id": "b", "decision": "remember"}),
            (
                {
                    "version": "v1",
                    "thought_id": "run:42",
                    "decision": "offer",
                    "audio_base64": _PREPARED_AUDIO_BASE64,
                }
            ),
        )

        parsed = [parse_pending_thought(case) for case in cases]

        self.assertEqual(
            [thought.decision for thought in parsed],
            ["ignore", "remember", "offer"],
        )
        self.assertEqual(parsed[-1].audio_base64, _PREPARED_AUDIO_BASE64)

    def test_parser_rejects_unsafe_or_ambiguous_shapes(self):
        invalid = (
            None,
            {"version": "v2", "thought_id": "a", "decision": "ignore"},
            {"version": "v1", "thought_id": ".bad", "decision": "ignore"},
            {"version": "v1", "thought_id": "a b", "decision": "remember"},
            {"version": "v1", "thought_id": "a", "decision": "offer"},
            {
                "version": "v1",
                "thought_id": "a",
                "decision": "ignore",
                "audio_base64": _PREPARED_AUDIO_BASE64,
            },
            {
                "version": "v1",
                "thought_id": "a",
                "decision": "offer",
                "audio_base64": "x" * 1_048_577,
            },
            {
                "version": "v1",
                "thought_id": "a",
                "decision": [],
            },
        )

        for payload in invalid:
            with self.subTest(payload=payload):
                with self.assertRaises(PendingThoughtError):
                    parse_pending_thought(payload)

    def test_raw_audio_max_length_is_checked_before_whitespace_is_stripped(self):
        body = RecordingBody()
        machine = KnockWaitTell(body, body)
        padded = _PREPARED_AUDIO_BASE64 + " " * (
            1_048_577 - len(_PREPARED_AUDIO_BASE64)
        )

        with self.assertRaisesRegex(PendingThoughtError, "at most 1048576"):
            machine.submit(
                {
                    "version": "v1",
                    "thought_id": "overlong",
                    "decision": "offer",
                    "audio_base64": padded,
                }
            )

        self.assertEqual(body.knocks, [])

    def test_prepared_audio_checks_only_toc_visible_profile_facts(self):
        self.assertEqual(
            decode_prepared_audio(_PREPARED_AUDIO_BASE64),
            _FRAMED_OPUS,
        )
        stereo_packet = bytes((_OPUS_PACKET[0] | 0x04,)) + _OPUS_PACKET[1:]
        invalid = (
            (b"\x08x", "exactly 60 ms"),
            (stereo_packet, "declare mono"),
            (b"\x5b", "frame count"),
        )

        for packet, message in invalid:
            framed = len(packet).to_bytes(2, "big") + packet
            encoded = base64.b64encode(framed).decode("ascii")
            with self.subTest(message=message):
                with self.assertRaisesRegex(PendingThoughtError, message):
                    decode_prepared_audio(encoded)

    def test_ignore_and_remember_have_no_body_side_effects(self):
        body = RecordingBody()
        machine = KnockWaitTell(body, body)

        ignored = machine.submit(
            {"version": "v1", "thought_id": "a", "decision": "ignore"}
        )
        remembered = machine.submit(
            {"version": "v1", "thought_id": "b", "decision": "remember"}
        )

        self.assertEqual(ignored.state, "ignored")
        self.assertEqual(remembered.state, "remembered")
        self.assertEqual(body.knocks, [])
        self.assertEqual(body.tells, [])
        self.assertIsNone(machine.pending_thought_id)

    def test_offer_knocks_without_receiving_audio(self):
        body = RecordingBody()
        machine = KnockWaitTell(body, body)

        outcome = machine.submit(
            {
                "version": "v1",
                "thought_id": "run:42",
                "decision": "offer",
                "audio_base64": _PREPARED_AUDIO_BASE64,
            }
        )

        self.assertEqual(outcome.state, "waiting")
        self.assertEqual(body.knocks, ["run:42"])
        self.assertEqual(body.tells, [])
        self.assertEqual(machine.pending_thought_id, "run:42")

    def test_malformed_offer_is_rejected_before_recording_or_knock(self):
        body = RecordingBody()
        machine = KnockWaitTell(body, body)

        with self.assertRaisesRegex(PendingThoughtError, "incomplete"):
            machine.submit(
                {
                    "version": "v1",
                    "thought_id": "malformed",
                    "decision": "offer",
                    "audio_base64": "AAVhYmM=",
                }
            )

        self.assertEqual(body.knocks, [])
        self.assertNotIn("malformed", machine._outcomes)
        self.assertIsNone(machine.pending_thought_id)
        remembered = machine.submit(
            {
                "version": "v1",
                "thought_id": "after-malformed",
                "decision": "remember",
            }
        )
        self.assertEqual(remembered.state, "remembered")

    def test_head_tap_suppresses_duplicate_while_id_is_retained(self):
        body = RecordingBody()
        machine = KnockWaitTell(body, body)
        payload = {
            "version": "v1",
            "thought_id": "run:42",
            "decision": "offer",
            "audio_base64": _PREPARED_AUDIO_BASE64,
        }
        machine.submit(payload)

        told = machine.acknowledge_head_gesture()
        repeated_tap = machine.acknowledge_head_gesture()
        repeated_submit = machine.submit(payload)

        self.assertEqual(told.state, "told")
        self.assertIsNone(repeated_tap)
        self.assertEqual(repeated_submit.state, "told")
        self.assertEqual(body.knocks, ["run:42"])
        self.assertEqual(
            body.tells,
            [("run:42", _PREPARED_AUDIO_BASE64)],
        )
        self.assertIsNone(machine.pending_thought_id)

    def test_upstream_head_tap_event_acknowledges_offer(self):
        body = RecordingBody()
        machine = KnockWaitTell(body, body)
        machine.submit(
            {
                "version": "v1",
                "thought_id": "run:42",
                "decision": "offer",
                "audio_base64": _PREPARED_AUDIO_BASE64,
            }
        )

        outcome = machine.handle_stackchan_event(
            {
                "event_type": "touch",
                "subtype": "tap",
                "action": "head_pat",
                "duration_ms": 0,
            }
        )

        self.assertEqual(outcome.state, "told")
        self.assertEqual(
            body.tells,
            [("run:42", _PREPARED_AUDIO_BASE64)],
        )

    def test_upstream_head_stroke_event_acknowledges_offer(self):
        body = RecordingBody()
        machine = KnockWaitTell(body, body)
        machine.submit(
            {
                "version": "v1",
                "thought_id": "run:42",
                "decision": "offer",
                "audio_base64": _PREPARED_AUDIO_BASE64,
            }
        )

        outcome = machine.handle_stackchan_event(
            {
                "event_type": "touch",
                "subtype": "stroke",
                "action": "head_stroke",
                "duration_ms": 600,
            }
        )

        self.assertEqual(outcome.state, "told")
        self.assertEqual(
            body.tells,
            [("run:42", _PREPARED_AUDIO_BASE64)],
        )

    def test_unrelated_event_is_a_noop(self):
        body = RecordingBody()
        machine = KnockWaitTell(body, body)

        self.assertIsNone(
            machine.handle_stackchan_event(
                {
                    "event_type": "touch",
                    "subtype": "hold",
                    "action": "head_hold",
                }
            )
        )
        self.assertEqual(body.tells, [])

    def test_knock_failure_does_not_leave_offer_pending(self):
        class FailingBody(RecordingBody):
            def knock(self, thought_id):
                super().knock(thought_id)
                raise RuntimeError("synthetic knock failure")

        body = FailingBody()
        machine = KnockWaitTell(body, body)

        with self.assertRaisesRegex(RuntimeError, "synthetic knock failure"):
            machine.submit(
                {
                    "version": "v1",
                    "thought_id": "failed",
                    "decision": "offer",
                    "audio_base64": _PREPARED_AUDIO_BASE64,
                }
            )

        self.assertIsNone(machine.pending_thought_id)
        retry = machine.submit(
            {
                "version": "v1",
                "thought_id": "remembered",
                "decision": "remember",
            }
        )
        self.assertEqual(retry.state, "remembered")

    def test_second_offer_cannot_replace_pending_thought(self):
        body = RecordingBody()
        machine = KnockWaitTell(body, body)
        machine.submit(
            {
                "version": "v1",
                "thought_id": "first",
                "decision": "offer",
                "audio_base64": _PREPARED_AUDIO_BASE64,
            }
        )

        with self.assertRaises(PendingOfferExistsError):
            machine.submit(
                {
                    "version": "v1",
                    "thought_id": "second",
                    "decision": "offer",
                    "audio_base64": _PREPARED_AUDIO_BASE64,
                }
            )

        self.assertEqual(machine.pending_thought_id, "first")
        self.assertEqual(body.knocks, ["first"])

    def test_outcome_history_is_bounded_without_evicting_pending(self):
        body = RecordingBody()
        machine = KnockWaitTell(body, body)
        pending = {
            "version": "v1",
            "thought_id": "pending",
            "decision": "offer",
            "audio_base64": _PREPARED_AUDIO_BASE64,
        }
        machine.submit(pending)

        for index in range(1024):
            machine.submit(
                {
                    "version": "v1",
                    "thought_id": f"remember:{index}",
                    "decision": "remember",
                }
            )

        self.assertLessEqual(len(machine._outcomes), 1024)
        self.assertIn("pending", machine._outcomes)
        self.assertEqual(machine.submit(pending).state, "waiting")
        self.assertEqual(body.knocks, ["pending"])

    @patch("gateway.pending_thought._MAX_RECORDED_OUTCOMES", 2)
    def test_evicted_outcome_may_offer_again(self):
        body = RecordingBody()
        machine = KnockWaitTell(body, body)
        offer = {
            "version": "v1",
            "thought_id": "evicted",
            "decision": "offer",
            "audio_base64": _PREPARED_AUDIO_BASE64,
        }
        machine.submit(offer)
        machine.acknowledge_head_gesture()
        for thought_id in ("recent:1", "recent:2"):
            machine.submit(
                {
                    "version": "v1",
                    "thought_id": thought_id,
                    "decision": "remember",
                }
            )

        replay = machine.submit(offer)

        self.assertEqual(replay.state, "waiting")
        self.assertEqual(body.knocks, ["evicted", "evicted"])

    def test_tell_failure_keeps_offer_pending_for_honest_retry(self):
        body = RecordingBody(fail_tell=True)
        machine = KnockWaitTell(body, body)
        machine.submit(
            {
                "version": "v1",
                "thought_id": "run:42",
                "decision": "offer",
                "audio_base64": _PREPARED_AUDIO_BASE64,
            }
        )

        with self.assertRaisesRegex(RuntimeError, "synthetic tell failure"):
            machine.acknowledge_head_gesture()

        self.assertEqual(machine.pending_thought_id, "run:42")


if __name__ == "__main__":
    unittest.main()
