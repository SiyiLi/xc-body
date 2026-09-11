import unittest

from gateway.embodiment import embody
from stackchan.adapter import (
    CalibrationError,
    ClientOperationError,
    DeviceUnavailableError,
    StackChanAdapter,
)


class FakeClient:
    def __init__(self, connected=True, fail=False, session_id=None):
        self.connected = connected
        self.fail = fail
        self.session_id = session_id
        self.calls = []

    def get_status(self):
        self.calls.append(("get_status",))
        status = {"connected": self.connected}
        if self.session_id is not None:
            status.update({"initialized": True, "session_id": self.session_id})
        return status

    def perform_expression(self, expression):
        self.calls.append(("perform_expression", expression))
        if self.fail:
            raise RuntimeError("synthetic client failure")
        return {"ok": True}


class StackChanAdapterTests(unittest.TestCase):
    def test_unknown_recipe_step_fails_before_device_calls(self):
        client = FakeClient()
        with self.assertRaises(CalibrationError):
            StackChanAdapter(client).present(face="unknown", motion="unknown")
        self.assertEqual(client.calls, [])

    def test_disconnected_status_prevents_expression(self):
        client = FakeClient(connected=False)
        with self.assertRaises(DeviceUnavailableError):
            StackChanAdapter(client).present(
                face="attentive",
                motion="restrained_side_glance",
            )
        self.assertEqual(client.calls, [("get_status",)])

    def test_changed_session_prevents_expression(self):
        client = FakeClient(session_id="reconnected-session")
        with self.assertRaisesRegex(DeviceUnavailableError, "current session"):
            StackChanAdapter(
                client,
                verified_session_id="ready-session",
            ).present(
                face="attentive",
                motion="restrained_side_glance",
            )
        self.assertEqual(client.calls, [("get_status",)])

    def test_curious_uses_saved_expression_and_returns_to_idle(self):
        client = FakeClient()
        embody(
            {"version": "v1", "intent": "curious"},
            StackChanAdapter(client),
        )
        self.assertEqual(
            client.calls,
            [
                ("get_status",),
                ("perform_expression", "curious"),
                ("get_status",),
            ],
        )

    def test_expression_failure_still_attempts_idle_boundary(self):
        client = FakeClient(fail=True)
        with self.assertRaisesRegex(ClientOperationError, "perform_expression"):
            embody(
                {"version": "v1", "intent": "curious"},
                StackChanAdapter(client),
            )
        self.assertEqual(
            client.calls,
            [
                ("get_status",),
                ("perform_expression", "curious"),
                ("get_status",),
            ],
        )


if __name__ == "__main__":
    unittest.main()
