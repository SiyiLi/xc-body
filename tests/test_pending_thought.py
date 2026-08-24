import base64
import unittest
from unittest.mock import patch

from gateway.pending_thought import (
    KnockWaitTell,
    PendingThoughtError,
    decode_prepared_audio,
    parse_pending_thought,
)


_OPUS_PACKET = bytes.fromhex(
    "5802f9304dbb0de5e392098938ebcae1b1d1dd85"
)
_FRAMED_OPUS = len(_OPUS_PACKET).to_bytes(2, "big") + _OPUS_PACKET
_AUDIO = base64.b64encode(_FRAMED_OPUS).decode("ascii")


class RecordingBody:
    def __init__(self, *, fail_knock=False, fail_tell=False):
        self.knocks = []
        self.tells = []
        self.offer_states = []
        self.fail_knock = fail_knock
        self.fail_tell = fail_tell

    def knock(self, thought_id):
        self.knocks.append(thought_id)
        if self.fail_knock:
            raise RuntimeError("synthetic knock failure")

    def tell(self, thought_id, audio_base64):
        self.tells.append((thought_id, audio_base64))
        if self.fail_tell:
            raise RuntimeError("synthetic tell failure")

    def set_offer_pending(self, pending):
        self.offer_states.append(pending)


def offer(machine, thought_id="run:42"):
    return machine.submit(
        {
            "version": "v1",
            "thought_id": thought_id,
            "decision": "offer",
            "audio_base64": _AUDIO,
        }
    )


def remember(machine, thought_id):
    return machine.submit(
        {
            "version": "v1",
            "thought_id": thought_id,
            "decision": "remember",
        }
    )


class PendingThoughtTests(unittest.TestCase):
    def test_parser_accepts_decisions_and_rejects_unsafe_shapes(self):
        valid = (
            {"version": "v1", "thought_id": "a", "decision": "ignore"},
            {"version": "v1", "thought_id": "b", "decision": "remember"},
            {
                "version": "v1",
                "thought_id": "run:42",
                "decision": "offer",
                "audio_base64": _AUDIO,
            },
        )
        self.assertEqual(
            [parse_pending_thought(item).decision for item in valid],
            ["ignore", "remember", "offer"],
        )

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
                "audio_base64": _AUDIO,
            },
            {
                "version": "v1",
                "thought_id": "a",
                "decision": "offer",
                "audio_base64": "x" * 1_048_577,
            },
        )
        for payload in invalid:
            with self.subTest(payload=payload):
                with self.assertRaises(PendingThoughtError):
                    parse_pending_thought(payload)

    def test_prepared_audio_enforces_visible_opus_profile(self):
        self.assertEqual(decode_prepared_audio(_AUDIO), _FRAMED_OPUS)
        stereo = bytes((_OPUS_PACKET[0] | 0x04,)) + _OPUS_PACKET[1:]
        invalid = (
            (b"\x08x", "exactly 60 ms"),
            (stereo, "declare mono"),
            (b"\x5b", "frame count"),
        )
        for packet, message in invalid:
            framed = len(packet).to_bytes(2, "big") + packet
            with self.subTest(message=message):
                with self.assertRaisesRegex(PendingThoughtError, message):
                    decode_prepared_audio(base64.b64encode(framed).decode())

    def test_ignore_and_remember_have_no_body_side_effects(self):
        body = RecordingBody()
        machine = KnockWaitTell(body, body)
        ignored = machine.submit(
            {"version": "v1", "thought_id": "a", "decision": "ignore"}
        )
        remembered = remember(machine, "b")
        self.assertEqual((ignored.state, remembered.state), ("ignored", "remembered"))
        self.assertEqual((body.knocks, body.tells), ([], []))

    def test_offer_waits_for_one_acknowledgment(self):
        body = RecordingBody()
        machine = KnockWaitTell(body, body, offer_state_port=body)
        payload_outcome = offer(machine)
        self.assertEqual(payload_outcome.state, "waiting")
        self.assertEqual(body.knocks, ["run:42"])
        self.assertEqual(body.tells, [])
        self.assertEqual(body.offer_states, [True])

        told = machine.acknowledge_head_gesture()
        self.assertEqual(told.state, "told")
        self.assertIsNone(machine.acknowledge_head_gesture())
        self.assertEqual(offer(machine).state, "told")
        self.assertEqual(body.knocks, ["run:42"])
        self.assertEqual(body.tells, [("run:42", _AUDIO)])
        self.assertEqual(body.offer_states, [True, False])

    def test_head_pat_and_stroke_events_acknowledge_offer(self):
        for subtype, action in (("tap", "head_pat"), ("stroke", "head_stroke")):
            with self.subTest(action=action):
                body = RecordingBody()
                machine = KnockWaitTell(body, body)
                offer(machine)
                outcome = machine.handle_stackchan_event(
                    {
                        "event_type": "touch",
                        "subtype": subtype,
                        "action": action,
                    }
                )
                self.assertEqual(outcome.state, "told")
                self.assertEqual(body.tells, [("run:42", _AUDIO)])

    def test_unrelated_event_is_a_noop(self):
        machine = KnockWaitTell(RecordingBody(), RecordingBody())
        self.assertIsNone(
            machine.handle_stackchan_event(
                {
                    "event_type": "touch",
                    "subtype": "hold",
                    "action": "head_hold",
                }
            )
        )

    def test_knock_failure_clears_offer_and_suppresses_retry(self):
        body = RecordingBody(fail_knock=True)
        machine = KnockWaitTell(body, body)
        with self.assertRaisesRegex(RuntimeError, "knock failure"):
            offer(machine, "failed")
        self.assertIsNone(machine.pending_thought_id)
        self.assertEqual(offer(machine, "failed").state, "ignored")
        self.assertEqual(body.knocks, ["failed"])
        self.assertEqual(remember(machine, "retry").state, "remembered")

    def test_second_offer_is_ignored_without_replacing_pending_thought(self):
        body = RecordingBody()
        machine = KnockWaitTell(body, body)
        offer(machine, "first")

        second = offer(machine, "second")

        self.assertEqual(second.state, "ignored")
        self.assertEqual(machine.pending_thought_id, "first")
        self.assertEqual(body.knocks, ["first"])

    def test_offer_expires_after_thirty_minutes(self):
        now = [0.0]
        body = RecordingBody()
        machine = KnockWaitTell(body, body, clock=lambda: now[0])
        offer(machine, "expired")

        now[0] = 30 * 60

        self.assertIsNone(machine.acknowledge_head_gesture())
        self.assertIsNone(machine.pending_thought_id)
        self.assertEqual(offer(machine, "expired").state, "expired")
        self.assertEqual(offer(machine, "fresh").state, "waiting")
        self.assertEqual(body.knocks, ["expired", "fresh"])
        self.assertEqual(body.tells, [])

    @patch("gateway.pending_thought._MAX_RECORDED_OUTCOMES", 2)
    def test_eviction_preserves_pending_but_allows_old_completed_id(self):
        body = RecordingBody()
        machine = KnockWaitTell(body, body)
        offer(machine, "completed")
        machine.acknowledge_head_gesture()
        remember(machine, "recent:1")
        remember(machine, "recent:2")
        self.assertEqual(offer(machine, "completed").state, "waiting")

        pending_body = RecordingBody()
        pending_machine = KnockWaitTell(pending_body, pending_body)
        offer(pending_machine, "pending")
        remember(pending_machine, "new:1")
        remember(pending_machine, "new:2")
        self.assertEqual(offer(pending_machine, "pending").state, "waiting")
        self.assertEqual(pending_body.knocks, ["pending"])

    def test_tell_failure_keeps_offer_pending_for_retry(self):
        body = RecordingBody(fail_tell=True)
        machine = KnockWaitTell(body, body)
        offer(machine)
        with self.assertRaisesRegex(RuntimeError, "tell failure"):
            machine.acknowledge_head_gesture()
        self.assertEqual(machine.pending_thought_id, "run:42")


if __name__ == "__main__":
    unittest.main()
