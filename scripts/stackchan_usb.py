#!/usr/bin/env python3
"""Control XC Body firmware over the CoreS3 USB serial connection."""

from __future__ import annotations

import argparse
import glob
import json
import os
import select
import sys
import termios
import time
import tty
from collections.abc import Sequence
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


REQUEST_PREFIX = b"XC_BODY_REQUEST "
RESPONSE_PREFIX = b"XC_BODY_RESPONSE "
DEFAULT_TOKEN_ENV = "XC_BODY_STACKCHAN_MCP_TOKEN"
PORT_PATTERNS = ("/dev/cu.usbmodem*", "/dev/ttyACM*")
MAX_MANIFEST_BYTES = 64 * 1024
EXPRESSION_NAMES = (
    "agree",
    "pleased",
    "curious",
    "concerned",
    "surprised",
    "embarrassed",
    "mischievous",
)


class UsbControlError(RuntimeError):
    """Raised when the USB maintenance channel cannot complete a command."""


def _discover_port(explicit_port: str | None) -> str:
    if explicit_port:
        return explicit_port
    ports = sorted(
        path
        for pattern in PORT_PATTERNS
        for path in glob.glob(pattern)
    )
    if not ports:
        raise UsbControlError("no CoreS3 USB serial port found")
    if len(ports) > 1:
        joined = ", ".join(ports)
        raise UsbControlError(f"multiple USB serial ports found: {joined}")
    return ports[0]


def _open_port(path: str) -> tuple[int, list[object]]:
    try:
        descriptor = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    except OSError as error:
        raise UsbControlError(f"cannot open {path}: {error}") from error

    previous = termios.tcgetattr(descriptor)
    tty.setraw(descriptor, termios.TCSANOW)
    attributes = termios.tcgetattr(descriptor)
    attributes[4] = termios.B115200
    attributes[5] = termios.B115200
    termios.tcsetattr(descriptor, termios.TCSANOW, attributes)
    termios.tcflush(descriptor, termios.TCIFLUSH)
    return descriptor, previous


def _close_port(descriptor: int, previous: list[object]) -> None:
    try:
        termios.tcsetattr(descriptor, termios.TCSANOW, previous)
    except (OSError, termios.error):
        pass
    try:
        os.close(descriptor)
    except OSError:
        pass


def _write_all(descriptor: int, payload: bytes, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    sent = 0
    while sent < len(payload):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise UsbControlError("timed out writing the USB command")
        _, writable, _ = select.select([], [descriptor], [], remaining)
        if not writable:
            continue
        sent += os.write(descriptor, payload[sent:])


def _decode_response(line: bytes) -> dict[str, object] | None:
    marker = line.find(RESPONSE_PREFIX)
    if marker < 0:
        return None
    encoded = line[marker + len(RESPONSE_PREFIX) :]
    try:
        response = json.loads(encoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise UsbControlError("firmware returned invalid JSON") from error
    if not isinstance(response, dict):
        raise UsbControlError("firmware returned a non-object response")
    return response


def _send_request(
    path: str,
    request: dict[str, object],
    timeout: float,
) -> dict[str, object]:
    descriptor, previous = _open_port(path)
    try:
        encoded = json.dumps(request, separators=(",", ":")).encode()
        _write_all(descriptor, REQUEST_PREFIX + encoded + b"\n", timeout)
        deadline = time.monotonic() + timeout
        buffered = b""
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise UsbControlError("timed out waiting for firmware")
            readable, _, _ = select.select([descriptor], [], [], remaining)
            if not readable:
                continue
            chunk = os.read(descriptor, 4096)
            if not chunk:
                continue
            buffered += chunk
            while b"\n" in buffered:
                line, buffered = buffered.split(b"\n", 1)
                response = _decode_response(line.rstrip(b"\r"))
                if response is not None:
                    return response
    finally:
        _close_port(descriptor, previous)


def _gateway_url(value: str) -> str:
    if not value:
        return value
    parsed = urlsplit(value)
    if parsed.scheme not in {"ws", "wss"} or not parsed.hostname:
        raise argparse.ArgumentTypeError(
            "gateway URL must use ws:// or wss:// and include a host"
        )
    if parsed.username or parsed.password:
        raise argparse.ArgumentTypeError(
            "gateway URL must not contain credentials"
        )
    return value


def _https_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname:
        raise argparse.ArgumentTypeError(
            "manifest URL must use https:// and include a host"
        )
    if parsed.username or parsed.password:
        raise argparse.ArgumentTypeError(
            "manifest URL must not contain credentials"
        )
    return value


def _firmware_from_manifest(url: str, timeout: float) -> dict[str, object]:
    request = Request(url, headers={"User-Agent": "XC-Body-USB"})
    try:
        with urlopen(request, timeout=timeout) as response:
            final_url = response.geturl()
            try:
                _https_url(final_url)
            except (TypeError, argparse.ArgumentTypeError) as error:
                raise UsbControlError(
                    "OTA manifest redirect must remain on HTTPS"
                ) from error
            encoded = response.read(MAX_MANIFEST_BYTES + 1)
    except OSError as error:
        raise UsbControlError(f"cannot download OTA manifest: {error}") from error
    if len(encoded) > MAX_MANIFEST_BYTES:
        raise UsbControlError("OTA manifest exceeds 65536 bytes")
    try:
        manifest = json.loads(encoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise UsbControlError("OTA manifest is not valid JSON") from error
    if not isinstance(manifest, dict):
        raise UsbControlError("OTA manifest must be an object")
    if (
        manifest.get("schema_version") != 1
        or manifest.get("product") != "xc-body"
        or manifest.get("hardware") != "stackchan"
    ):
        raise UsbControlError("OTA manifest is not for XC Body StackChan")
    firmware = manifest.get("firmware")
    if not isinstance(firmware, dict):
        raise UsbControlError("OTA manifest has no firmware object")
    version = firmware.get("version")
    ota_url = firmware.get("url")
    sha256 = firmware.get("sha256")
    size = firmware.get("size")
    if not isinstance(version, str) or not version:
        raise UsbControlError("OTA manifest firmware version is invalid")
    if not isinstance(ota_url, str):
        raise UsbControlError("OTA manifest firmware URL is invalid")
    try:
        _https_url(ota_url)
    except argparse.ArgumentTypeError as error:
        raise UsbControlError(str(error)) from error
    if (
        not isinstance(sha256, str)
        or len(sha256) != 64
        or any(character not in "0123456789abcdef" for character in sha256)
    ):
        raise UsbControlError("OTA manifest SHA-256 is invalid")
    if (
        not isinstance(size, int)
        or isinstance(size, bool)
        or size <= 0
        or size > 0x3F0000
    ):
        raise UsbControlError("OTA manifest firmware size is invalid")
    return {
        "command": "update",
        "url": ota_url,
        "sha256": sha256,
        "size": size,
        "version": version,
    }


def _configure_request(args: argparse.Namespace) -> dict[str, object]:
    request: dict[str, object] = {"command": "configure"}
    if args.url is not None:
        request["url"] = args.url
    if args.fallback_url is not None:
        request["fallback_url"] = args.fallback_url
    if args.clear_token:
        request["token"] = ""
    else:
        token_env = args.token_env or DEFAULT_TOKEN_ENV
        token = os.environ.get(token_env)
        if args.token_env and not token:
            raise UsbControlError(f"token environment variable is unset: {token_env}")
        if token:
            request["token"] = token
    if len(request) == 1:
        raise UsbControlError("configure requires a URL or token change")
    return request


def _load_expression_recipe(path: str) -> dict[str, object]:
    try:
        with open(path, encoding="utf-8") as source:
            recipe = json.load(source)
    except (OSError, json.JSONDecodeError) as error:
        raise UsbControlError(f"cannot read expression recipe: {error}") from error
    if not isinstance(recipe, dict):
        raise UsbControlError("expression recipe must be a JSON object")
    return recipe


def _expression_recipe_request(args: argparse.Namespace) -> dict[str, object]:
    return {
        "command": args.command.replace("-", "_"),
        "name": args.name,
        "recipe": _load_expression_recipe(args.recipe),
    }


def _monitor(path: str, seconds: float) -> None:
    descriptor, previous = _open_port(path)
    try:
        deadline = None if seconds == 0 else time.monotonic() + seconds
        while deadline is None or time.monotonic() < deadline:
            timeout = None
            if deadline is not None:
                timeout = max(0.0, deadline - time.monotonic())
            readable, _, _ = select.select([descriptor], [], [], timeout)
            if not readable:
                return
            chunk = os.read(descriptor, 4096)
            if chunk:
                sys.stdout.write(chunk.decode(errors="replace"))
                sys.stdout.flush()
    finally:
        _close_port(descriptor, previous)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Control XC Body firmware through CoreS3 USB serial."
    )
    parser.add_argument("--port", help="USB serial device; auto-detected by default")
    parser.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="command timeout in seconds (default: 5)",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="read Wi-Fi and gateway status")

    configure = commands.add_parser(
        "configure",
        help="persist gateway settings; takes effect after reboot",
    )
    configure.add_argument("--url", type=_gateway_url)
    configure.add_argument("--fallback-url", type=_gateway_url)
    configure.add_argument(
        "--token-env",
        help=(
            "read the token from this environment variable; defaults to "
            f"{DEFAULT_TOKEN_ENV} when it is set"
        ),
    )
    configure.add_argument(
        "--clear-token",
        action="store_true",
        help="clear the saved gateway token",
    )

    commands.add_parser("reboot", help="reboot through the application path")
    automatic_ota = commands.add_parser(
        "automatic-ota",
        help="enable or disable automatic boot OTA",
    )
    automatic_ota.add_argument("state", choices=("enable", "disable"))
    update = commands.add_parser(
        "update",
        help="queue a verified XC Body firmware release",
    )
    update.add_argument("--manifest", required=True, type=_https_url)
    for command in ("expression-preview", "expression-save"):
        expression = commands.add_parser(
            command,
            help=(
                "preview a transient expression recipe"
                if command.endswith("preview")
                else "persist an approved expression recipe"
            ),
        )
        expression.add_argument("name", choices=EXPRESSION_NAMES)
        expression.add_argument("recipe", help="curve/pause recipe JSON file")
    expression_show = commands.add_parser(
        "expression-show",
        help="show one stored expression recipe",
    )
    expression_show.add_argument("name", choices=EXPRESSION_NAMES)
    monitor = commands.add_parser("monitor", help="stream firmware logs")
    monitor.add_argument(
        "--seconds",
        type=float,
        default=0,
        help="stop after this many seconds; 0 runs until interrupted",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        path = _discover_port(args.port)
        if args.command == "monitor":
            _monitor(path, args.seconds)
            return 0
        if args.command == "configure":
            request = _configure_request(args)
        elif args.command == "automatic-ota":
            request = {
                "command": "automatic_ota",
                "enabled": args.state == "enable",
            }
        elif args.command == "update":
            request = _firmware_from_manifest(args.manifest, args.timeout)
        elif args.command in {"expression-preview", "expression-save"}:
            request = _expression_recipe_request(args)
        elif args.command == "expression-show":
            request = {
                "command": args.command.replace("-", "_"),
                "name": args.name,
            }
        else:
            request = {"command": args.command}
        response = _send_request(path, request, args.timeout)
        print(json.dumps(response, indent=2, sort_keys=True))
        return 0 if response.get("ok") is True else 1
    except (OSError, UsbControlError) as error:
        print(f"[FATAL] {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
