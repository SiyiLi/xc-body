import unittest


from gateway.embodiment import (
    ExpressionAndIdleError,
    IntentRequestError,
    embody,
    parse_intent_request,
    recipe_for,
)


class RecordingDevice:
    def __init__(self, fail_on_calls=()):
        self.calls = []
        self.fail_on_calls = set(fail_on_calls)
        self.prepared = []

    def prepare(self, steps):
        self.prepared.append(steps)

    def present(self, *, face, motion):
        self.calls.append((face, motion))
        if len(self.calls) in self.fail_on_calls:
            raise RuntimeError("device step failed")


class SemanticCoreTests(unittest.TestCase):
    def test_valid_request_is_accepted(self):
        request = parse_intent_request(
            {"version": "v1", "intent": "curious", "speech": None}
        )

        self.assertEqual(request.version, "v1")
        self.assertEqual(request.intent, "curious")
        self.assertIsNone(request.speech)

    def test_invalid_requests_are_rejected_before_device_calls(self):
        invalid_requests = (
            {"version": "v2", "intent": "curious"},
            {"version": "v1", "intent": "surprised"},
            {"version": "v1", "intent": "curious", "extra": True},
            {"version": "v1", "intent": "curious", "return_to_idle": False},
            {"intent": "curious"},
            None,
        )

        for payload in invalid_requests:
            with self.subTest(payload=payload):
                device = RecordingDevice()
                with self.assertRaises(IntentRequestError):
                    embody(payload, device)
                self.assertEqual(device.calls, [])

    def test_same_intent_yields_identical_recipe(self):
        first = recipe_for("pleased")
        second = recipe_for("pleased")

        self.assertEqual(first, second)

    def test_each_expressive_intent_ends_in_idle(self):
        expected_calls = {
            "curious": [
                ("attentive", "restrained_side_glance"),
                ("neutral", "relaxed_center"),
            ],
            "pleased": [
                ("happy", "single_small_nod"),
                ("neutral", "relaxed_center"),
            ],
            "concerned": [
                ("concerned", "restrained_head_tilt"),
                ("neutral", "relaxed_center"),
            ],
        }

        for intent, calls in expected_calls.items():
            with self.subTest(intent=intent):
                device = RecordingDevice()
                embody({"version": "v1", "intent": intent}, device)
                self.assertEqual(device.calls, calls)

    def test_device_step_failure_still_attempts_idle_and_propagates(self):
        device = RecordingDevice(fail_on_calls=(1,))

        with self.assertRaisesRegex(RuntimeError, "device step failed"):
            embody({"version": "v1", "intent": "curious"}, device)

        self.assertEqual(
            device.calls,
            [
                ("attentive", "restrained_side_glance"),
                ("neutral", "relaxed_center"),
            ],
        )

    def test_expression_and_idle_failures_are_both_preserved(self):
        device = RecordingDevice(fail_on_calls=(1, 2))

        with self.assertRaises(ExpressionAndIdleError) as raised:
            embody({"version": "v1", "intent": "curious"}, device)

        self.assertEqual(device.calls, [
            ("attentive", "restrained_side_glance"),
            ("neutral", "relaxed_center"),
        ])
        self.assertRegex(str(raised.exception.expression_error), "device step")
        self.assertRegex(str(raised.exception.idle_error), "device step")

    def test_explicit_idle_performs_only_idle_behavior(self):
        device = RecordingDevice()

        embody({"version": "v1", "intent": "idle"}, device)

        self.assertEqual(device.calls, [("neutral", "relaxed_center")])

    def test_non_null_speech_is_rejected_before_device_calls(self):
        device = RecordingDevice()

        with self.assertRaisesRegex(IntentRequestError, "speech must be null"):
            embody(
                {"version": "v1", "intent": "pleased", "speech": "hello"},
                device,
            )

        self.assertEqual(device.calls, [])


if __name__ == "__main__":
    unittest.main()
