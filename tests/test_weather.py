import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, patch

from stackchan_mcp.weather import (
    WeatherConfig,
    WeatherSnapshot,
    WeatherUpdater,
)


class _StopUpdater(Exception):
    pass


class WeatherUpdaterTests(unittest.TestCase):
    def test_new_device_session_fetches_location_before_weather_push(self):
        esp32 = Mock()
        esp32.get_status.side_effect = [
            {
                "connected": True,
                "initialized": True,
                "session_id": session_id,
            }
            for session_id in (
                "session-1",
                "session-1",
                "session-2",
                "session-2",
            )
        ] + [_StopUpdater]
        sleep_count = 0
        location_read_after_sleep = []

        async def call_tool(name, _arguments):
            if name == "self.location.get":
                location_read_after_sleep.append(sleep_count)
            return {}, None

        esp32.call_tool = AsyncMock(side_effect=call_tool)
        updater = WeatherUpdater(
            esp32,
            WeatherConfig(api_host="weather.example.com", api_key="key"),
        )
        locations = [(31.23, 121.47), (22.54, 114.06)]
        snapshots = [
            WeatherSnapshot(101, 21, "weather-1"),
            WeatherSnapshot(102, 22, "weather-2"),
        ]

        async def no_sleep(_seconds):
            nonlocal sleep_count
            sleep_count += 1

        with (
            patch.object(updater, "_fetch", new_callable=AsyncMock) as fetch,
            patch(
                "stackchan_mcp.weather.parse_public_ip_location",
                side_effect=locations,
            ),
            patch("stackchan_mcp.weather.asyncio.sleep", no_sleep),
            patch("stackchan_mcp.weather.time.monotonic", return_value=100.0),
            self.assertRaises(_StopUpdater),
        ):
            fetch.side_effect = snapshots
            asyncio.run(updater.run())

        self.assertEqual(
            [call.args[1] for call in fetch.await_args_list],
            locations,
        )
        self.assertEqual(
            location_read_after_sleep,
            [1, 3],
        )
        weather_calls = [
            call
            for call in esp32.call_tool.await_args_list
            if call.args[0] == "self.display.set_weather"
        ]
        self.assertEqual(
            [call.args[1]["summary"] for call in weather_calls],
            ["weather-1", "weather-2"],
        )


if __name__ == "__main__":
    unittest.main()
