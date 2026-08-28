import json
from pathlib import Path
import struct
import unittest


ROOT = Path(__file__).resolve().parents[1]
STACKCHAN_DIR = ROOT / "firmware" / "main" / "boards" / "stackchan"
STACKCHAN = (STACKCHAN_DIR / "stackchan.cc").read_text(encoding="utf-8")
STACKCHAN_CONFIG = json.loads(
    (STACKCHAN_DIR / "config.json").read_text(encoding="utf-8")
)
FONT_ASSETS = (
    "screensaver-digits.cbin",
    "screensaver-weather.cbin",
    "screensaver-date.cbin",
)
WEATHER_ICON_ASSETS = (
    "w-clear-day.rgb565a8",
    "w-clear-night.rgb565a8",
    "w-wind.rgb565a8",
    "w-partly-day.rgb565a8",
    "w-partly-night.rgb565a8",
    "w-overcast.rgb565a8",
    "w-shower-day.rgb565a8",
    "w-shower-night.rgb565a8",
    "w-thunder-rain.rgb565a8",
    "w-hail.rgb565a8",
    "w-light-rain.rgb565a8",
    "w-heavy-rain.rgb565a8",
    "w-snow.rgb565a8",
    "w-sleet.rgb565a8",
    "w-fog.rgb565a8",
    "w-haze.rgb565a8",
    "w-dust.rgb565a8",
    "w-hot.rgb565a8",
    "w-cold.rgb565a8",
    "w-unknown.rgb565a8",
)


class StackChanScreenSaverTests(unittest.TestCase):
    def test_lvgl_binfont_assets_use_the_buffer_loader(self) -> None:
        sdkconfig = STACKCHAN_CONFIG["builds"][0]["sdkconfig_append"]
        self.assertIn("CONFIG_LV_FS_MEMFS_LETTER=77", sdkconfig)
        self.assertIn("CONFIG_LV_USE_BUILTIN_SPRINTF=y", sdkconfig)
        self.assertIn("CONFIG_LV_USE_FS_MEMFS=y", sdkconfig)
        self.assertIn("lv_binfont_create_from_buffer(", STACKCHAN)
        self.assertNotIn("std::make_unique<LvglCBinFont>(data)", STACKCHAN)

        for name in FONT_ASSETS:
            data = (STACKCHAN_DIR / "assets" / name).read_bytes()
            with self.subTest(name=name):
                self.assertEqual(data[4:8], b"head")

    def test_timer_display_update_never_loads_fonts(self) -> None:
        update_start = STACKCHAN.index("    void UpdateDisplayMode(")
        update_end = STACKCHAN.index("    void OpenVolumeSettings(")
        update_display = STACKCHAN[update_start:update_end]
        self.assertNotIn("EnsureScreenSaverLocked()", update_display)
        self.assertIn("PrepareScreenSaver();", STACKCHAN)

    def test_every_weather_icon_has_real_alpha(self) -> None:
        self.assertIn("kScreenSaverWeatherIconCount = 20", STACKCHAN)
        self.assertIn("kScreenSaverWeatherIconBytes = 80 * 64 * 3", STACKCHAN)
        self.assertIn("icon.header.cf = LV_COLOR_FORMAT_RGB565A8", STACKCHAN)
        self.assertIn("icon.header.w = 80", STACKCHAN)
        self.assertIn("icon.header.h = 64", STACKCHAN)
        self.assertIn("icon.header.stride = 160", STACKCHAN)

        seen_assets = set()
        for name in WEATHER_ICON_ASSETS:
            with self.subTest(name=name):
                self.assertIn(f'"{name}"', STACKCHAN)
                data = (STACKCHAN_DIR / "assets" / name).read_bytes()
                self.assertEqual(len(data), 80 * 64 * 3)
                self.assertNotIn(data, seen_assets)
                seen_assets.add(data)

                pixels = [
                    value[0]
                    for value in struct.iter_unpack("<H", data[: 80 * 64 * 2])
                ]
                alpha = data[80 * 64 * 2 :]
                self.assertGreater(len(set(pixels)), 1)
                self.assertIn(0, alpha)
                self.assertIn(255, alpha)
                self.assertTrue(any(0 < value < 255 for value in alpha))


if __name__ == "__main__":
    unittest.main()
