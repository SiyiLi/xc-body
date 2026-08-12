import unittest

from gateway.embodiment import embody
from stackchan.adapter import (
    CalibrationError,
    HeadMove,
    StackChanAdapter,
    VisibleFaceVerificationError,
)
from stackchan.calibration import measured_k151_cores3_calibration


class FakeClient:
    def __init__(self):
        self.calls = []

    def get_status(self):
        self.calls.append(("get_status",))
        return {"connected": True}

    def set_avatar(self, face):
        self.calls.append(("set_avatar", face))
        return {"ok": True}

    def move_head(self, yaw, pitch, speed):
        self.calls.append(("move_head", yaw, pitch, speed))
        return {"ok": True}


class MeasuredCalibrationTests(unittest.TestCase):
    def test_reviewed_face_and_motion_mappings_are_exact(self):
        calibration = measured_k151_cores3_calibration()

        self.assertEqual(
            dict(calibration.faces),
            {
                "neutral": "idle",
                "attentive": "thinking",
                "happy": "happy",
                "concerned": "sad",
            },
        )
        self.assertEqual(
            dict(calibration.motions),
            {
                "relaxed_center": (HeadMove(0, 43, 30),),
                "restrained_side_glance": (HeadMove(12, 50, 30),),
            },
        )
        self.assertEqual(calibration.verified_faces, frozenset())

    def test_idle_and_curious_require_visible_face_verification(self):
        for intent in ("idle", "curious"):
            with self.subTest(intent=intent):
                client = FakeClient()
                device = StackChanAdapter(
                    client, measured_k151_cores3_calibration()
                )

                with self.assertRaisesRegex(
                    VisibleFaceVerificationError,
                    "visible face verification",
                ):
                    embody({"version": "v1", "intent": intent}, device)

                self.assertEqual(client.calls, [])

    def test_uncalibrated_intents_fail_before_client_calls(self):
        for intent in ("pleased", "concerned"):
            with self.subTest(intent=intent):
                client = FakeClient()
                device = StackChanAdapter(
                    client, measured_k151_cores3_calibration()
                )

                with self.assertRaises(CalibrationError):
                    embody({"version": "v1", "intent": intent}, device)

                self.assertEqual(client.calls, [])


if __name__ == "__main__":
    unittest.main()
