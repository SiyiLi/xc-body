import unittest

from stackchan.upstream_client import (
    UpstreamClientError,
    UpstreamStackChanClient,
)


class RecordingCaller:
    def __init__(self, results):
        self.results = iter(results)
        self.calls = []

    def __call__(self, name, arguments):
        self.calls.append((name, arguments))
        result = next(self.results)
        if isinstance(result, Exception):
            raise result
        return result


class TextBlock:
    def __init__(self, text):
        self.text = text


class SdkResult:
    def __init__(self, *, content=(), structured_content=None, is_error=False):
        self.content = content
        self.structured_content = structured_content
        self.is_error = is_error


class UpstreamClientTests(unittest.TestCase):
    def test_status_translates_structured_sdk_result(self):
        caller = RecordingCaller(
            [SdkResult(structured_content={"connected": True})]
        )

        result = UpstreamStackChanClient(caller).get_status()

        self.assertTrue(result["connected"])
        self.assertEqual(caller.calls, [("get_status", {})])

    def test_expression_translates_text_result_and_arguments(self):
        caller = RecordingCaller(
            [{"content": [{"type": "text", "text": "expression played"}]}]
        )

        result = UpstreamStackChanClient(caller).perform_expression("curious")

        self.assertTrue(result["ok"])
        self.assertEqual(
            caller.calls,
            [("perform_expression", {"expression": "curious"})],
        )

    def test_transport_and_tool_errors_include_operation_context(self):
        callers = (
            RecordingCaller([RuntimeError("session closed")]),
            RecordingCaller(
                [SdkResult(content=[TextBlock("servo rejected")], is_error=True)]
            ),
        )

        with self.assertRaisesRegex(
            UpstreamClientError, "perform_expression: session closed"
        ):
            UpstreamStackChanClient(callers[0]).perform_expression("curious")
        with self.assertRaisesRegex(
            UpstreamClientError, "perform_expression: servo rejected"
        ):
            UpstreamStackChanClient(callers[1]).perform_expression("curious")

    def test_status_without_connection_state_fails_closed(self):
        caller = RecordingCaller([{"content": []}])

        with self.assertRaisesRegex(UpstreamClientError, "get_status"):
            UpstreamStackChanClient(caller).get_status()


if __name__ == "__main__":
    unittest.main()
