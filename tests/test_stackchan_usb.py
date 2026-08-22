from __future__ import annotations

import io
import json
import unittest
from unittest.mock import patch

from scripts.stackchan_usb import UsbControlError, _firmware_from_manifest


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


if __name__ == "__main__":
    unittest.main()
