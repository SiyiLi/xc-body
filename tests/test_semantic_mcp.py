import importlib.util
import json
from pathlib import Path
import unittest

from gateway.embodiment import ExpressionAndIdleError
from stackchan.adapter import (
    DeviceUnavailableError,
    HeadMove,
    StackChanAdapter,
    StackChanCalibration,
)


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "xc_body_semantic_tool",
    ROOT / "mcp" / "semantic_tool.py",
)
semantic_tool = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(semantic_tool)


# Synthetic values are test-only and must never be used on hardware.
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
    def __init__(self, connected=True):
        self.connected = connected
        self.calls = []

    def get_status(self):
        self.calls.append(("get_status",))
        return {"connected": self.connected}

    def set_avatar(self, face):
        self.calls.append(("set_avatar", face))
        return {"ok": True}

    def move_head(self, yaw, pitch, speed):
        self.calls.append(("move_head", yaw, pitch, speed))
        return {"ok": True}


class SemanticMcpTests(unittest.TestCase):
    def make_device(self, client):
        return StackChanAdapter(client, synthetic_calibration())

    def test_descriptor_schema_exactly_matches_contract(self):
        contract_path = ROOT / "contracts" / "embodiment-intent.schema.json"
        contract = json.loads(contract_path.read_text())
        descriptor = semantic_tool.tool_descriptor()

        self.assertEqual(descriptor["name"], "embody")
        self.assertEqual(descriptor["inputSchema"], contract)

    def test_descriptor_exposes_no_low_level_controls(self):
        properties = semantic_tool.tool_descriptor()["inputSchema"]["properties"]

        self.assertEqual(set(properties), {"version", "intent", "speech"})
        forbidden_fields = (
            "face",
            "motion",
            "servo",
            "speed",
            "led",
            "endpoint",
            "token",
            "duration",
            "return_control",
        )
        for forbidden in forbidden_fields:
            self.assertNotIn(forbidden, properties)

    def test_curious_traverses_boundary_and_returns_to_idle(self):
        client = FakeClient()

        result = semantic_tool.handle_tool_call(
            "embody",
            {"version": "v1", "intent": "curious"},
            self.make_device(client),
        )

        self.assertEqual(
            result,
            {
                "ok": True,
                "intent": "curious",
                "returned_to_idle": True,
            },
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

    def test_unsupported_intent_causes_zero_calls(self):
        client = FakeClient()

        with self.assertRaises(semantic_tool.InvalidToolArgumentsError):
            semantic_tool.handle_tool_call(
                "embody",
                {"version": "v1", "intent": "surprised"},
                self.make_device(client),
            )

        self.assertEqual(client.calls, [])

    def test_unknown_tool_causes_zero_calls(self):
        client = FakeClient()

        with self.assertRaises(semantic_tool.UnknownToolError):
            semantic_tool.handle_tool_call(
                "move_head",
                {"version": "v1", "intent": "curious"},
                self.make_device(client),
            )

        self.assertEqual(client.calls, [])

    def test_disconnected_device_surfaces_failure(self):
        client = FakeClient(connected=False)

        with self.assertRaises(ExpressionAndIdleError) as raised:
            semantic_tool.handle_tool_call(
                "embody",
                {"version": "v1", "intent": "curious"},
                self.make_device(client),
            )

        self.assertEqual(client.calls, [("get_status",), ("get_status",)])
        self.assertIsInstance(
            raised.exception.expression_error, DeviceUnavailableError
        )
        self.assertIsInstance(raised.exception.idle_error, DeviceUnavailableError)

    def test_repeated_requests_have_identical_results_and_calls(self):
        clients = (FakeClient(), FakeClient())
        results = [
            semantic_tool.handle_tool_call(
                "embody",
                {"version": "v1", "intent": "curious"},
                self.make_device(client),
            )
            for client in clients
        ]

        self.assertEqual(results[0], results[1])
        self.assertEqual(clients[0].calls, clients[1].calls)

    def test_result_is_json_serializable_without_calibration_values(self):
        client = FakeClient()
        result = semantic_tool.handle_tool_call(
            "embody",
            {"version": "v1", "intent": "pleased"},
            self.make_device(client),
        )

        serialized = json.dumps(result, sort_keys=True)
        self.assertEqual(set(result), {"ok", "intent", "returned_to_idle"})
        for value in ("face-happy-test", "yaw", "pitch", "speed"):
            self.assertNotIn(value, serialized)


if __name__ == "__main__":
    unittest.main()
