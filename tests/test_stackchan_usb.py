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
    _firmware_from_manifest,
    main,
    _parser,
    _receive_response,
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
        admission = (
            b'XC_BODY_RESPONSE {"command":"expression_preview",'
            b'"ok":true,"outcome":"started"}\n'
        )
        with patch("scripts.stackchan_usb.select.select", return_value=([7], [], [])):
            with patch("scripts.stackchan_usb.os.read", return_value=abort + admission):
                response = _receive_response(
                    7,
                    "expression_preview",
                    float("inf"),
                    bytearray(),
                )

        self.assertEqual(response["outcome"], "started")

    def test_preview_uses_one_immediate_request(self) -> None:
        recipe = {"schema_version": 2, "animations": ["agree"], "steps": []}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "agree.json"
            path.write_text(json.dumps(recipe), encoding="utf-8")
            with patch(
                "scripts.stackchan_usb._discover_port",
                return_value="/dev/test",
            ), patch(
                "scripts.stackchan_usb._send_request",
                return_value={"command": "expression_preview", "ok": True},
            ) as send:
                with patch("sys.stdout", new_callable=io.StringIO):
                    self.assertEqual(
                        main(["expression-preview", "agree", str(path)]),
                        0,
                    )

        self.assertEqual(send.call_args.args[0], "/dev/test")
        self.assertEqual(send.call_args.args[1]["command"], "expression_preview")

if __name__ == "__main__":
    unittest.main()
