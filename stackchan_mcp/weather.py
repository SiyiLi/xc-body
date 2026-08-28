"""Synchronize QWeather current conditions to the idle screen."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from aiohttp import ClientSession, ClientTimeout

logger = logging.getLogger(__name__)

_REFRESH_SECONDS = 60 * 60
_RETRY_SECONDS = 60
_DEVICE_CHECK_SECONDS = 10
_API_HOST_PATTERN = re.compile(r"^[A-Za-z0-9.-]+$")
_SHANGHAI_FALLBACK_LOCATION = (31.23, 121.47)


@dataclass(frozen=True)
class WeatherConfig:
    api_host: str
    api_key: str


@dataclass(frozen=True)
class WeatherSnapshot:
    icon_code: int
    temperature_c: int
    summary: str


def load_weather_config(environ: Mapping[str, str]) -> WeatherConfig | None:
    """Load private QWeather settings, or disable weather when all are absent."""

    values = {
        "api_host": environ.get("XC_BODY_QWEATHER_API_HOST", "").strip(),
        "api_key": environ.get("XC_BODY_QWEATHER_API_KEY", "").strip(),
    }
    if not any(values.values()):
        return None
    if not all(values.values()):
        raise ValueError("QWeather API host and key are required")

    api_host = values["api_host"]
    if not _API_HOST_PATTERN.fullmatch(api_host) or "." not in api_host:
        raise ValueError("QWeather API host must be a hostname without a scheme")
    return WeatherConfig(
        api_host=api_host,
        api_key=values["api_key"],
    )


def parse_public_ip_location(result: Any) -> tuple[float, float] | None:
    """Extract validated latitude and longitude from a device tool result."""

    if not isinstance(result, Mapping):
        return None
    content = result.get("content")
    if not isinstance(content, list) or not content:
        return None
    first = content[0]
    if not isinstance(first, Mapping) or not isinstance(first.get("text"), str):
        return None
    try:
        payload = json.loads(first["text"])
    except (TypeError, ValueError):
        return None

    try:
        latitude = float(payload["latitude"])
        longitude = float(payload["longitude"])
    except (KeyError, TypeError, ValueError):
        return None
    if not math.isfinite(latitude) or not -90 <= latitude <= 90:
        return None
    if not math.isfinite(longitude) or not -180 <= longitude <= 180:
        return None
    return latitude, longitude


def parse_weather_snapshot(payload: object) -> WeatherSnapshot:
    """Validate the QWeather fields used by the firmware."""

    if not isinstance(payload, Mapping):
        raise ValueError("weather response must be an object")
    if payload.get("code") != "200":
        raise ValueError(f"QWeather returned code {payload.get('code')!r}")
    current = payload.get("now")
    if not isinstance(current, Mapping):
        raise ValueError("weather response has no current conditions")

    raw_temperature = current.get("temp")
    raw_icon = current.get("icon")
    summary = current.get("text")
    try:
        temperature_c = int(raw_temperature)
        icon_code = int(raw_icon)
    except (TypeError, ValueError) as exc:
        raise ValueError("weather temperature or icon code is invalid") from exc
    if not -99 <= temperature_c <= 99:
        raise ValueError("weather temperature is outside display range")
    if not 100 <= icon_code <= 999:
        raise ValueError("weather icon code is invalid")
    if not isinstance(summary, str) or not summary:
        raise ValueError("weather summary is invalid")
    if len(summary.encode("utf-8")) > 48:
        raise ValueError("weather summary exceeds the firmware display limit")

    return WeatherSnapshot(
        icon_code=icon_code,
        temperature_c=temperature_c,
        summary=summary,
    )


class WeatherUpdater:
    """Fetch current conditions hourly and push changes to the device."""

    def __init__(self, esp32: Any, config: WeatherConfig):
        self._esp32 = esp32
        self._config = config

    async def run(self) -> None:
        snapshot: WeatherSnapshot | None = None
        fetched_session_id: str | None = None
        pushed_snapshot: WeatherSnapshot | None = None
        pushed_session_id: str | None = None
        next_fetch_at = 0.0
        timeout = ClientTimeout(total=10)

        async with ClientSession(timeout=timeout) as session:
            while True:
                now = time.monotonic()
                status = self._esp32.get_status()
                session_id = status.get("session_id")
                ready = (
                    status.get("connected") is True
                    and status.get("initialized") is True
                    and isinstance(session_id, str)
                    and bool(session_id)
                )
                if not ready:
                    await asyncio.sleep(_DEVICE_CHECK_SECONDS)
                    continue

                if session_id != fetched_session_id:
                    fetched_session_id = session_id
                    snapshot = None
                    next_fetch_at = 0.0
                    await asyncio.sleep(_DEVICE_CHECK_SECONDS)
                    continue

                if now >= next_fetch_at:
                    result, error = await self._esp32.call_tool(
                        "self.location.get", {}
                    )
                    if error:
                        logger.warning(
                            "location cache read failed; using central "
                            "Shanghai: %s",
                            error.get("message", str(error)),
                        )
                        location = _SHANGHAI_FALLBACK_LOCATION
                    else:
                        location = parse_public_ip_location(result)
                        if location is None:
                            logger.warning(
                                "location cache is empty; "
                                "using central Shanghai"
                            )
                            location = _SHANGHAI_FALLBACK_LOCATION
                    try:
                        snapshot = await self._fetch(session, location)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        logger.warning("weather refresh failed: %s", exc)
                        next_fetch_at = now + _RETRY_SECONDS
                    else:
                        next_fetch_at = now + _REFRESH_SECONDS

                if snapshot is not None and (
                    session_id != pushed_session_id
                    or snapshot != pushed_snapshot
                ):
                    _result, error = await self._esp32.call_tool(
                        "self.display.set_weather",
                        {
                            "icon_code": snapshot.icon_code,
                            "temperature_c": snapshot.temperature_c,
                            "summary": snapshot.summary,
                        },
                    )
                    if error:
                        logger.warning(
                            "weather push failed: %s",
                            error.get("message", str(error)),
                        )
                    else:
                        pushed_session_id = session_id
                        pushed_snapshot = snapshot

                await asyncio.sleep(_DEVICE_CHECK_SECONDS)

    async def _fetch(
        self,
        session: ClientSession,
        location: tuple[float, float],
    ) -> WeatherSnapshot:
        config = self._config
        latitude, longitude = location
        params = {
            "location": f"{longitude:.2f},{latitude:.2f}",
            "lang": "zh",
            "unit": "m",
        }
        headers = {"X-QW-Api-Key": config.api_key}
        async with session.get(
            f"https://{config.api_host}/v7/weather/now",
            params=params,
            headers=headers,
            allow_redirects=False,
        ) as response:
            response.raise_for_status()
            return parse_weather_snapshot(await response.json())


class ClockUpdater:
    """Set the device clock once for each authenticated device session."""

    def __init__(self, esp32: Any):
        self._esp32 = esp32

    async def run(self) -> None:
        pushed_session_id: str | None = None
        while True:
            status = self._esp32.get_status()
            session_id = status.get("session_id")
            ready = (
                status.get("connected") is True
                and status.get("initialized") is True
                and isinstance(session_id, str)
                and bool(session_id)
            )
            if ready and session_id != pushed_session_id:
                _result, error = await self._esp32.call_tool(
                    "self.display.set_clock",
                    {"epoch_seconds": int(time.time())},
                )
                if error:
                    logger.warning(
                        "clock push failed: %s",
                        error.get("message", str(error)),
                    )
                else:
                    pushed_session_id = session_id

            await asyncio.sleep(_DEVICE_CHECK_SECONDS)
