from __future__ import annotations

import hashlib
import json
import struct
import tempfile
import unittest
from pathlib import Path

from scripts.package_firmware_release import (
    ASSETS_ASSET_NAME,
    MERGED_ASSET_NAME,
    OTA_ASSET_NAME,
    STACKCHAN_PROJECT_NAME,
    package_release,
)


def package_test_release(
    root: Path,
    embedded_version: str = "0.1.0",
    embedded_project: str = STACKCHAN_PROJECT_NAME,
) -> None:
    build_dir = root / "build"
    build_dir.mkdir()
    image = bytearray(0x100)
    image[0] = 0xE9
    struct.pack_into("<I", image, 0x20, 0xABCD5432)
    image[0x30 : 0x30 + len(embedded_version)] = embedded_version.encode()
    image[0x50 : 0x50 + len(embedded_project)] = embedded_project.encode()
    (build_dir / "xc_body.bin").write_bytes(image)
    (build_dir / "merged-binary.bin").write_bytes(b"usb image")
    (build_dir / "generated_assets.bin").write_bytes(b"assets image")
    package_release(
        build_dir,
        root / "release",
        "0.1.0",
        "https://example.test/xc-body-stackchan-ota.bin",
    )


class FirmwareReleaseTests(unittest.TestCase):
    def test_packages_hash_bound_ota_and_recovery_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output_dir = root / "release"
            package_test_release(root)

            manifest = json.loads(
                (output_dir / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["product"], "xc-body")
            self.assertEqual(manifest["hardware"], "stackchan")
            firmware = manifest["firmware"]
            self.assertEqual(firmware["version"], "0.1.0")
            self.assertEqual(firmware["size"], 0x100)
            self.assertEqual(
                firmware["sha256"],
                hashlib.sha256(
                    (root / "build" / "xc_body.bin").read_bytes()
                ).hexdigest(),
            )
            self.assertEqual(
                (output_dir / OTA_ASSET_NAME).read_bytes(),
                (root / "build" / "xc_body.bin").read_bytes(),
            )
            self.assertEqual(
                (output_dir / MERGED_ASSET_NAME).read_bytes(),
                b"usb image",
            )
            assets = manifest["assets"]
            self.assertEqual(assets["version"], "0.1.0")
            self.assertEqual(
                assets["url"],
                "https://example.test/xc-body-stackchan-assets.bin",
            )
            self.assertEqual(assets["size"], len(b"assets image"))
            self.assertEqual(
                assets["sha256"], hashlib.sha256(b"assets image").hexdigest()
            )
            self.assertEqual(
                (output_dir / ASSETS_ASSET_NAME).read_bytes(), b"assets image"
            )
            checksum = output_dir / f"{ASSETS_ASSET_NAME}.sha256"
            self.assertEqual(
                checksum.read_text(),
                f"{assets['sha256']}  {ASSETS_ASSET_NAME}\n",
            )

    def test_rejects_non_stackchan_app_image(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, "not an XC Body StackChan"):
                package_test_release(
                    Path(temporary),
                    embedded_project="xc_body",
                )

    def test_rejects_stale_embedded_version(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, "does not match"):
                package_test_release(
                    Path(temporary),
                    embedded_version="0.0.9",
                )


if __name__ == "__main__":
    unittest.main()
