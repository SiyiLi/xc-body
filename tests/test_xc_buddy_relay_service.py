import unittest
from unittest.mock import AsyncMock, patch

from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from gateway.xc_buddy_relay_service import (
    RECEIVER_TOKEN_ENV,
    SENDER_TOKEN_ENV,
    RelayServiceError,
    build_app,
    load_tokens,
    main,
)


SENDER_HEADERS = {"Authorization": "Bearer sender-secret"}
RECEIVER_HEADERS = {"Authorization": "Bearer receiver-secret"}


class XcBuddyRelayServiceTests(unittest.TestCase):
    def build_client(self):
        return TestClient(build_app("sender-secret", "receiver-secret"))

    def test_health_is_public(self):
        with self.build_client() as client:
            response = client.get("/healthz")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True})

    def test_tokens_are_required_and_distinct(self):
        with self.assertRaises(RelayServiceError):
            load_tokens({})
        with self.assertRaises(RelayServiceError):
            load_tokens(
                {
                    SENDER_TOKEN_ENV: "same",
                    RECEIVER_TOKEN_ENV: "same",
                }
            )

    def test_unknown_credential_is_rejected(self):
        with self.build_client() as client:
            with self.assertRaises(WebSocketDisconnect) as raised:
                with client.websocket_connect(
                    "/v1/stream",
                    headers={"Authorization": "Bearer unknown"},
                ):
                    pass

        self.assertEqual(raised.exception.code, 4401)

    def test_sender_updates_receiver_and_disconnect_returns_idle(self):
        with self.assertLogs(
            "uvicorn.error", level="INFO"
        ) as captured, self.build_client() as client:
            with client.websocket_connect(
                "/v1/stream",
                headers=RECEIVER_HEADERS,
            ) as receiver:
                self.assertEqual(
                    receiver.receive_json(),
                    {"kind": "state", "state": "idle", "snapshot": True},
                )
                with client.websocket_connect(
                    "/v1/stream",
                    headers=SENDER_HEADERS,
                ) as sender:
                    sender.send_json({"kind": "state", "state": "working"})
                    self.assertEqual(
                        receiver.receive_json(),
                        {
                            "kind": "state",
                            "state": "working",
                            "snapshot": False,
                        },
                    )
                self.assertEqual(
                    receiver.receive_json(),
                    {"kind": "state", "state": "idle", "snapshot": False},
                )

        self.assertIn(
            "XC Buddy relay sender event kind=state value=working "
            "receiver=forwarded",
            "\n".join(captured.output),
        )

    def test_receiver_reconnect_gets_current_state_snapshot(self):
        with self.build_client() as client:
            with client.websocket_connect(
                "/v1/stream",
                headers=SENDER_HEADERS,
            ) as sender:
                sender.send_json({"kind": "state", "state": "working"})
                with client.websocket_connect(
                    "/v1/stream",
                    headers=RECEIVER_HEADERS,
                ) as receiver:
                    self.assertEqual(
                        receiver.receive_json(),
                        {
                            "kind": "state",
                            "state": "working",
                            "snapshot": True,
                        },
                    )

    def test_notice_is_live_only_and_reconnect_gets_idle(self):
        with self.build_client() as client:
            with client.websocket_connect(
                "/v1/stream",
                headers=SENDER_HEADERS,
            ) as sender:
                sender.send_json({"kind": "state", "state": "working"})
                sender.send_json({"kind": "notice", "notice": "done"})
                with client.websocket_connect(
                    "/v1/stream",
                    headers=RECEIVER_HEADERS,
                ) as receiver:
                    self.assertEqual(
                        receiver.receive_json(),
                        {
                            "kind": "state",
                            "state": "idle",
                            "snapshot": True,
                        },
                    )

    def test_live_notice_is_forwarded_without_snapshot_metadata(self):
        with self.build_client() as client:
            with client.websocket_connect(
                "/v1/stream",
                headers=RECEIVER_HEADERS,
            ) as receiver:
                receiver.receive_json()
                with client.websocket_connect(
                    "/v1/stream",
                    headers=SENDER_HEADERS,
                ) as sender:
                    sender.send_json({"kind": "state", "state": "working"})
                    receiver.receive_json()
                    sender.send_json({"kind": "notice", "notice": "done"})
                    self.assertEqual(
                        receiver.receive_json(),
                        {"kind": "notice", "notice": "done"},
                    )

    def test_receiver_cannot_publish(self):
        with self.build_client() as client:
            with client.websocket_connect(
                "/v1/stream",
                headers=RECEIVER_HEADERS,
            ) as receiver:
                receiver.receive_json()
                receiver.send_json({"kind": "state", "state": "working"})
                with self.assertRaises(WebSocketDisconnect) as raised:
                    receiver.receive_json()

        self.assertEqual(raised.exception.code, 1008)

    def test_sender_first_message_must_be_state(self):
        with self.build_client() as client:
            with client.websocket_connect(
                "/v1/stream",
                headers=SENDER_HEADERS,
            ) as sender:
                sender.send_json({"kind": "notice", "notice": "done"})
                with self.assertRaises(WebSocketDisconnect) as raised:
                    sender.receive_json()

        self.assertEqual(raised.exception.code, 1008)

    def test_sender_rejects_extra_or_unsupported_fields(self):
        invalid = (
            {"kind": "state", "state": "working", "path": "/private"},
            {"kind": "state", "state": "unknown"},
            {"kind": "state", "state": []},
        )
        for payload in invalid:
            with self.subTest(payload=payload):
                with self.build_client() as client:
                    with client.websocket_connect(
                        "/v1/stream",
                        headers=SENDER_HEADERS,
                    ) as sender:
                        sender.send_json(payload)
                        with self.assertRaises(WebSocketDisconnect):
                            sender.receive_json()

    def test_sender_message_size_is_bounded(self):
        with self.build_client() as client:
            with client.websocket_connect(
                "/v1/stream",
                headers=SENDER_HEADERS,
            ) as sender:
                sender.send_text(" " * 257)
                with self.assertRaises(WebSocketDisconnect) as raised:
                    sender.receive_json()

        self.assertEqual(raised.exception.code, 1008)

    def test_replacement_sender_late_close_does_not_reset_state(self):
        with self.build_client() as client:
            with client.websocket_connect(
                "/v1/stream",
                headers=RECEIVER_HEADERS,
            ) as receiver:
                receiver.receive_json()
                with client.websocket_connect(
                    "/v1/stream",
                    headers=SENDER_HEADERS,
                ) as first:
                    first.send_json({"kind": "state", "state": "working"})
                    receiver.receive_json()
                    with client.websocket_connect(
                        "/v1/stream",
                        headers=SENDER_HEADERS,
                    ) as replacement:
                        with self.assertRaises(WebSocketDisconnect):
                            first.receive_json()
                        replacement.send_json(
                            {"kind": "state", "state": "approval_needed"}
                        )
                        self.assertEqual(
                            receiver.receive_json(),
                            {
                                "kind": "state",
                                "state": "approval_needed",
                                "snapshot": False,
                            },
                        )

    def test_replacement_receiver_gets_snapshot_and_old_socket_closes(self):
        with self.build_client() as client:
            with client.websocket_connect(
                "/v1/stream",
                headers=SENDER_HEADERS,
            ) as sender:
                sender.send_json({"kind": "state", "state": "working"})
                with client.websocket_connect(
                    "/v1/stream",
                    headers=RECEIVER_HEADERS,
                ) as first:
                    first.receive_json()
                    with client.websocket_connect(
                        "/v1/stream",
                        headers=RECEIVER_HEADERS,
                    ) as replacement:
                        self.assertEqual(
                            replacement.receive_json(),
                            {
                                "kind": "state",
                                "state": "working",
                                "snapshot": True,
                            },
                        )
                        with self.assertRaises(WebSocketDisconnect):
                            first.receive_json()
                        sender.send_json(
                            {"kind": "state", "state": "approval_needed"}
                        )
                        self.assertEqual(
                            replacement.receive_json(),
                            {
                                "kind": "state",
                                "state": "approval_needed",
                                "snapshot": False,
                            },
                        )

    def test_main_uses_only_relay_tokens(self):
        service = AsyncMock()
        environment = {
            SENDER_TOKEN_ENV: "sender-secret",
            RECEIVER_TOKEN_ENV: "receiver-secret",
        }

        with patch(
            "gateway.xc_buddy_relay_service.run_service",
            service,
        ):
            exit_code = main([], environ=environment)

        self.assertEqual(exit_code, 0)
        service.assert_awaited_once_with(
            "sender-secret",
            "receiver-secret",
            host="127.0.0.1",
            port=8781,
        )


if __name__ == "__main__":
    unittest.main()
