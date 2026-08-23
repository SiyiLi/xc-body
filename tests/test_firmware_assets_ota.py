from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
OTA = (ROOT / "firmware" / "main" / "ota.cc").read_text()
ASSETS = (ROOT / "firmware" / "main" / "assets.cc").read_text()
APPLICATION = (ROOT / "firmware" / "main" / "application.cc").read_text()
BOARD = (
    ROOT / "firmware" / "main" / "boards" / "common" / "board.h"
).read_text()
STACKCHAN = (
    ROOT / "firmware" / "main" / "boards" / "stackchan" / "stackchan.cc"
).read_text()


class FirmwareAssetsOtaTests(unittest.TestCase):
    def test_assets_failure_cannot_accept_or_reboot_app(self) -> None:
        self.assertNotIn("assets_verified_this_boot", OTA)
        self.assertIn("esp_ota_mark_app_valid_cancel_rollback()", OTA)
        self.assertNotIn("rebooting once for clean use", APPLICATION)


if __name__ == "__main__":
    unittest.main()
