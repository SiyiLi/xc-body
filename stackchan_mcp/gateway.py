"""Two-faced gateway: bridges MCP client (stdio MCP) and ESP32 (WebSocket MCP).

MCP client sees a standard MCP server via stdio.
ESP32 sees a WebSocket server that sends MCP client requests.
This module orchestrates both sides.
"""

from __future__ import annotations

import asyncio
import logging
import os

from aiohttp import web

from .capture_server import create_capture_app
from .esp32_client import ESP32Manager
from .mdns_advertiser import MdnsAdvertiser
from .weather import ClockUpdater, WeatherUpdater, load_weather_config

logger = logging.getLogger(__name__)

BEAT_MODE_LISTEN_STOP_TIMEOUT_S = 3.0


class Gateway:
    """Main gateway orchestrator.

    Holds the ESP32 manager and provides the bridge between
    the stdio MCP server (MCP client side) and the ESP32 device.

    Also runs an HTTP capture server for receiving photos from ESP32.
    """

    def __init__(self):
        self.esp32 = ESP32Manager()
        self._running = False
        self._http_runner: web.AppRunner | None = None
        self._mdns_advertiser: MdnsAdvertiser | None = None
        self._clock_task: asyncio.Task[None] | None = None
        self._weather_task: asyncio.Task[None] | None = None

    @property
    def vision_url(self) -> str:
        """URL for ESP32 to POST captured photos to.

        VISION_URL can be set to a complete public capture URL for remote
        access setups such as Tailscale Funnel. Otherwise VISION_HOST should
        be the LAN IP of the host running this gateway, as seen from the ESP32
        (e.g. something like 192.168.x.y on a typical home network). Falls
        back to "127.0.0.1" with a warning if unset; in that case the ESP32
        will not be able to reach the capture endpoint over the network.
        """
        explicit_url = os.getenv("VISION_URL")
        if explicit_url:
            return explicit_url

        host = os.getenv("VISION_HOST")
        if not host:
            logger.warning(
                "VISION_URL/VISION_HOST not set; defaulting to 127.0.0.1. "
                "ESP32 will not reach the capture endpoint unless "
                "VISION_HOST is set to this host's LAN IP or VISION_URL is "
                "set to a full capture URL."
            )
            host = "127.0.0.1"
        port = int(os.getenv("CAPTURE_PORT", "8766"))
        return f"http://{host}:{port}/capture"

    @property
    def vision_token(self) -> str:
        """Bearer token expected by the capture endpoint.

        VISION_TOKEN can be set separately. By default, reuse the ESP32
        WebSocket token so remote capture uploads are protected whenever the
        gateway itself is protected.
        """
        return (
            os.getenv("VISION_TOKEN")
            or os.getenv("STACKCHAN_TOKEN")
            or os.getenv("BEARER_TOKEN")
            or ""
        )

    @property
    def audio_hook_url(self) -> str:
        """URL receiving device-driven listen captures as Ogg/Opus.

        STACKCHAN_AUDIO_HOOK_URL enables the device-driven listen
        capture path (wake word / button / LCD touch): the gateway
        packs inbound Opus frames into an Ogg container and POSTs to
        this URL on ``listen.stop``. The capture path is **disabled**
        when this is unset — stackchan-mcp's primary listen model
        remains MCP-client-driven (the ``listen()`` tool), and
        device-driven capture only makes sense when an external
        service is set up to receive the audio.
        """
        return os.getenv("STACKCHAN_AUDIO_HOOK_URL", "")

    @property
    def audio_hook_token(self) -> str:
        """Bearer token expected by the audio hook endpoint.

        XC_BODY_INTERACTION_HTTP_TOKEN owns the private Interaction capture
        route. A single-token deployment falls back to the gateway token.
        """
        return (
            os.getenv("XC_BODY_INTERACTION_HTTP_TOKEN")
            or os.getenv("STACKCHAN_TOKEN")
            or os.getenv("BEARER_TOKEN")
            or ""
        )

    @property
    def pcm_token(self) -> str:
        """Bearer token expected by the /pcm HTTP endpoint.

        Separate token from the ESP32 WebSocket / capture upload because
        the /pcm endpoint authorises external PCM producers (e.g. the
        SAIVerse voice-tts addon) — a different trust boundary from the
        device-to-gateway authentication. Falls back to STACKCHAN_TOKEN
        / BEARER_TOKEN when STACKCHAN_PCM_TOKEN is not configured so
        single-token local development keeps working.
        """
        return (
            os.getenv("STACKCHAN_PCM_TOKEN")
            or os.getenv("STACKCHAN_TOKEN")
            or os.getenv("BEARER_TOKEN")
            or ""
        )

    async def start(self, *, advertise_mdns: bool = True) -> None:
        """Start the ESP32 WebSocket server and HTTP capture server."""
        host = os.getenv("HOST", "0.0.0.0")
        ws_port = int(os.getenv("WS_PORT", os.getenv("PORT", "8765")))
        capture_port = int(os.getenv("CAPTURE_PORT", "8766"))

        # Start WebSocket server for ESP32
        await self.esp32.start(
            host,
            ws_port,
            vision_url=self.vision_url,
            vision_token=self.vision_token,
            audio_hook_url=self.audio_hook_url,
            audio_hook_token=self.audio_hook_token,
        )
        self._clock_task = asyncio.create_task(
            ClockUpdater(self.esp32).run()
        )

        try:
            weather_config = load_weather_config(os.environ)
        except ValueError as exc:
            logger.warning("Weather screensaver disabled: %s", exc)
        else:
            if weather_config is None:
                logger.warning(
                    "Weather screensaver disabled: QWeather configuration "
                    "is absent"
                )
            else:
                self._weather_task = asyncio.create_task(
                    WeatherUpdater(self.esp32, weather_config).run()
                )

        # Start the HTTP capture server. The PCM endpoint forwards into
        # send_pcm_stream, so we hand it the active Gateway instance so
        # it can reach esp32 + tts_lock.
        app = create_capture_app(
            capture_token=self.vision_token,
            pcm_token=self.pcm_token,
            gateway=self,
        )
        self._http_runner = web.AppRunner(app)
        await self._http_runner.setup()
        site = web.TCPSite(self._http_runner, host, capture_port)
        await site.start()

        if advertise_mdns:
            self._mdns_advertiser = MdnsAdvertiser()
            try:
                await self._mdns_advertiser.start(host=host, port=ws_port, path="/")
            except Exception as exc:  # pragma: no cover - host-specific
                logger.warning("mDNS advertisement failed: %s", exc)
                self._mdns_advertiser = None
        else:
            self._mdns_advertiser = None

        self._running = True
        logger.info(
            "Gateway started: WS on %s:%d, capture on %s:%d, vision_url=%s",
            host, ws_port, host, capture_port, self.vision_url,
        )

    async def stop(self) -> None:
        """Stop the gateway."""
        # Cancel any active pose-stream follower before the rest of the
        # shutdown sequence closes gateway-side services.
        try:
            from .follow_pose_stream import stop_follow

            await stop_follow()
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("follow_pose_stream shutdown failed: %s", exc)

        # Likewise cancel any active LED-stream follower so its WiFi
        # power-save lease is released before services close.
        try:
            from .follow_led_stream import stop_follow as stop_led_follow

            await stop_led_follow()
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("follow_led_stream shutdown failed: %s", exc)

        try:
            from .beat import stop_beat_mode

            await stop_beat_mode(
                listen_stop_timeout_s=BEAT_MODE_LISTEN_STOP_TIMEOUT_S,
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("beat mode shutdown failed: %s", exc)

        self._running = False
        if self._clock_task is not None:
            self._clock_task.cancel()
            await asyncio.gather(
                self._clock_task,
                return_exceptions=True,
            )
            self._clock_task = None
        if self._weather_task is not None:
            self._weather_task.cancel()
            await asyncio.gather(
                self._weather_task,
                return_exceptions=True,
            )
            self._weather_task = None
        if self._mdns_advertiser:
            try:
                await self._mdns_advertiser.stop()
            except Exception as exc:  # pragma: no cover - host-specific
                logger.warning("mDNS advertisement shutdown failed: %s", exc)
            finally:
                self._mdns_advertiser = None
        if self._http_runner:
            await self._http_runner.cleanup()
            self._http_runner = None
        await self.esp32.stop()
        logger.info("Gateway stopped")

# Singleton gateway instance, shared between stdio server and ESP32 manager
_gateway: Gateway | None = None


def get_gateway() -> Gateway:
    """Get or create the singleton gateway."""
    global _gateway
    if _gateway is None:
        _gateway = Gateway()
    return _gateway
