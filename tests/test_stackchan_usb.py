from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.stackchan_usb import (
    UsbControlError,
    _expression_recipe_request,
    _expression_preview_timeout,
    _firmware_from_manifest,
    _parser,
    _receive_response,
    _send_expression_preview,
)


class ManifestResponse(io.BytesIO):
    def __init__(self, manifest: dict[str, object], url: str) -> None:
        super().__init__(json.dumps(manifest).encode())
        self.url = url

    def geturl(self) -> str:
        return self.url


class StackChanUsbManifestTests(unittest.TestCase):
    def test_builds_update_request_from_xc_body_manifest(self) -> None:
        manifest = {
            "schema_version": 1,
            "product": "xc-body",
            "hardware": "stackchan",
            "firmware": {
                "version": "0.1.1",
                "url": "https://example.test/xc-body-stackchan-ota.bin",
                "sha256": "a" * 64,
                "size": 1234,
            },
        }
        response = ManifestResponse(
            manifest,
            "https://example.test/manifest.json",
        )

        with patch("scripts.stackchan_usb.urlopen", return_value=response):
            request = _firmware_from_manifest(
                "https://example.test/manifest.json",
                5.0,
            )

        self.assertEqual(
            request,
            {
                "command": "update",
                "url": "https://example.test/xc-body-stackchan-ota.bin",
                "sha256": "a" * 64,
                "size": 1234,
                "version": "0.1.1",
            },
        )

    def test_rejects_https_to_http_manifest_redirect(self) -> None:
        response = ManifestResponse({}, "http://example.test/manifest.json")

        with patch("scripts.stackchan_usb.urlopen", return_value=response):
            with self.assertRaisesRegex(UsbControlError, "remain on HTTPS"):
                _firmware_from_manifest(
                    "https://example.test/manifest.json",
                    5.0,
                )


class StackChanUsbExpressionTests(unittest.TestCase):
    def test_builds_expression_recipe_request_from_json(self) -> None:
        recipe = {"schema_version": 1, "steps": []}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "agree.json"
            path.write_text(json.dumps(recipe), encoding="utf-8")
            args = _parser().parse_args(
                ["expression-preview", "agree", str(path)]
            )
            request = _expression_recipe_request(args)

        self.assertEqual(
            request,
            {
                "command": "expression_preview",
                "name": "agree",
                "recipe": recipe,
            },
        )

    def test_receive_ignores_other_valid_command_responses(self) -> None:
        abort = b'XC_BODY_RESPONSE {"command":"expression_abort","ok":true}\n'
        preview = (
            b'XC_BODY_RESPONSE {"command":"expression_preview",'
            b'"ok":false,"outcome":"interrupted"}\n'
        )
        with patch("scripts.stackchan_usb.select.select", return_value=([7], [], [])):
            with patch("scripts.stackchan_usb.os.read", return_value=abort + preview):
                response = _receive_response(
                    7,
                    "expression_preview",
                    float("inf"),
                    bytearray(),
                )

        self.assertEqual(response["outcome"], "interrupted")

    def test_preview_timeout_includes_execution_and_recovery(self) -> None:
        recipe = {
            "steps": [
                {"duration_ms": 5000.0},
                {"duration_ms": 5000.0},
                {"duration_ms": 5000.0},
            ]
        }

        self.assertEqual(_expression_preview_timeout(recipe, 5.0), 28.5)

    def test_preview_cancellation_reuses_descriptor_and_buffer(self) -> None:
        request = {
            "command": "expression_preview",
            "name": "agree",
            "recipe": {"steps": []},
        }
        terminal = {
            "command": "expression_preview",
            "ok": False,
            "outcome": "interrupted",
        }
        with patch(
            "scripts.stackchan_usb._open_port",
            return_value=(7, []),
        ), patch("scripts.stackchan_usb._close_port") as close, patch(
            "scripts.stackchan_usb._write_request"
        ) as write, patch(
            "scripts.stackchan_usb._receive_response",
            side_effect=(KeyboardInterrupt, terminal),
        ) as receive:
            with self.assertRaises(KeyboardInterrupt):
                _send_expression_preview("/dev/test", request, 20.0)

        self.assertEqual(write.call_args_list[0].args[:2], (7, request))
        self.assertEqual(
            write.call_args_list[1].args[:2],
            (7, {"command": "expression_abort"}),
        )
        self.assertIs(
            receive.call_args_list[0].args[3],
            receive.call_args_list[1].args[3],
        )
        close.assert_called_once_with(7, [])

if __name__ == "__main__":
    unittest.main()
