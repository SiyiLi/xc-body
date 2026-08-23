from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
APPLICATION = ROOT / "firmware" / "main" / "application.cc"
BOARD = (
    ROOT
    / "firmware"
    / "main"
    / "boards"
    / "stackchan"
    / "stackchan.cc"
)


class StackChanTtsWatchdogTests(unittest.TestCase):
    def test_stalled_audio_stops_lipsync(self):
        source = BOARD.read_text()
        self.assertIn(
            "TTS_AUDIO_SILENCE_TIMEOUT_US = 3 * 1000 * 1000", source
        )
        self.assertIn(
            'ESP_LOGW(TAG, "TTS audio stalled; stopping lip-sync");\n'
            "            StopTtsLipSync();",
            source,
        )


if __name__ == "__main__":
    unittest.main()
