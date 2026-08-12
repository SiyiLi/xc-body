import unittest

from gateway.embodiment import embody
from stackchan.adapter import (
    CalibrationError,
    ClientOperationError,
    DeviceUnavailableError,
    HeadMove,
    StackChanAdapter,
    StackChanCalibration,
)


# Synthetic, non-hardware calibration. Never use these values on a real device.
def synthetic_calibration():
    return StackChanCalibration(
        faces={
            "neutral": "face-neutral-test",
            "attentive": "face-attentive-test",
            "happy": "face-happy-test",
            "concerned": "face-concerned-test",
        },
        motions={
            "relaxed_center": (HeadMove(0, 43, 1),),
            "restrained_side_glance": (HeadMove(2, 44, 3),),
            "single_small_nod": (HeadMove(0, 45, 3),),
            "restrained_head_tilt": (HeadMove(-2, 44, 3),),
        },
        verified_faces=frozenset(
            {
                "face-neutral-test",
                "face-attentive-test",
                "face-happy-test",
                "face-concerned-test",
            }
        ),
    )


class FakeClient:
    def __init__(self, connected=True, fail_operation=None):
        self.connected = connected
        self.fail_operation = fail_operation
        self.failure_raised = False
        self.calls = []

    def _record(self, operation, *args):
        self.calls.append((operation, *args))
        if self.fail_operation == operation and not self.failure_raised:
            self.failure_raised = True
            raise RuntimeError("synthetic client failure")
        return {"ok": True}

    def get_status(self):
        self.calls.append(("get_status",))
        if self.fail_operation == "get_status" and not self.failure_raised:
            self.failure_raised = True
            raise RuntimeError("synthetic client failure")
        return {"connected": self.connected}

    def set_avatar(self, face):
        return self._record("set_avatar", face)

    def move_head(self, yaw, pitch, speed):
        return self._record("move_head", yaw, pitch, speed)


class StackChanAdapterTests(unittest.TestCase):
    def test_no_calibration_fails_before_client_calls(self):
        client = FakeClient()
        with self.assertRaises(CalibrationError):
            StackChanAdapter(client, None).present(
                face="happy",
                motion="single_small_nod",
            )
        self.assertEqual(client.calls, [])

    def test_invalid_calibration_value_fails_at_construction(self):
        client = FakeClient()
        with self.assertRaises(CalibrationError):
            StackChanCalibration(
                faces={},
                motions={
                    "relaxed_center": (HeadMove(0, 43, 0),),
                },
            )
        self.assertEqual(client.calls, [])

    def test_upstream_servo_limits_are_enforced(self):
        for move in (HeadMove(-91, 43, 1), HeadMove(0, 86, 1)):
            with self.subTest(move=move):
                with self.assertRaises(CalibrationError):
                    StackChanCalibration(
                        faces={},
                        motions={"relaxed_center": (move,)},
                    )

    def test_disconnected_status_prevents_face_and_motion_calls(self):
        client = FakeClient(connected=False)
        with self.assertRaises(DeviceUnavailableError):
            StackChanAdapter(client, synthetic_calibration()).present(
                face="attentive",
                motion="restrained_side_glance",
            )
        self.assertEqual(client.calls, [("get_status",)])

    def test_curious_maps_deterministically_and_returns_to_idle(self):
        client = FakeClient()
        embody(
            {"version": "v1", "intent": "curious"},
            StackChanAdapter(client, synthetic_calibration()),
        )
        self.assertEqual(
            client.calls,
            [
                ("get_status",),
                ("set_avatar", "face-attentive-test"),
                ("move_head", 2, 44, 3),
                ("get_status",),
                ("set_avatar", "face-neutral-test"),
                ("move_head", 0, 43, 1),
            ],
        )

    def test_complete_preflight_checks_idle_face_before_expression_calls(self):
        calibration = synthetic_calibration()
        expression_only = StackChanCalibration(
            faces=calibration.faces,
            motions=calibration.motions,
            verified_faces=frozenset({"face-attentive-test"}),
        )
        client = FakeClient()

        with self.assertRaisesRegex(CalibrationError, "visible face verification"):
            embody(
                {"version": "v1", "intent": "curious"},
                StackChanAdapter(client, expression_only),
            )

        self.assertEqual(client.calls, [])

    def test_pleased_expresses_exactly_one_nod_then_idle(self):
        client = FakeClient()
        embody(
            {"version": "v1", "intent": "pleased"},
            StackChanAdapter(client, synthetic_calibration()),
        )
        moves = [call for call in client.calls if call[0] == "move_head"]
        self.assertEqual(
            moves,
            [
                ("move_head", 0, 45, 3),
                ("move_head", 0, 43, 1),
            ],
        )

    def test_client_error_has_context_and_safe_return_is_attempted(self):
        client = FakeClient(fail_operation="move_head")
        with self.assertRaisesRegex(ClientOperationError, "move_head"):
            embody(
                {"version": "v1", "intent": "curious"},
                StackChanAdapter(client, synthetic_calibration()),
            )
        get_status_calls = sum(
            call[0] == "get_status" for call in client.calls
        )
        self.assertEqual(get_status_calls, 2)
        self.assertIn(("set_avatar", "face-neutral-test"), client.calls)

    def test_unsupported_payload_makes_zero_calls(self):
        client = FakeClient()
        with self.assertRaises(Exception):
            embody(
                {"version": "v1", "intent": "surprised"},
                StackChanAdapter(client, synthetic_calibration()),
            )
        self.assertEqual(client.calls, [])

    def test_repeated_request_produces_identical_calls(self):
        first = FakeClient()
        second = FakeClient()
        embody(
            {"version": "v1", "intent": "curious"},
            StackChanAdapter(first, synthetic_calibration()),
        )
        embody(
            {"version": "v1", "intent": "curious"},
            StackChanAdapter(second, synthetic_calibration()),
        )
        self.assertEqual(first.calls, second.calls)


if __name__ == "__main__":
    unittest.main()
