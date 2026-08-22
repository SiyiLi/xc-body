#!/usr/bin/env python3
"""Package verified XC Body StackChan OTA and USB recovery artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import struct
from pathlib import Path
from urllib.parse import urlsplit


PROJECT_NAME = "xc-body"
HARDWARE_NAME = "stackchan"
OTA_SOURCE_NAME = "xc_body.bin"
MERGED_SOURCE_NAME = "merged-binary.bin"
OTA_ASSET_NAME = "xc-body-stackchan-ota.bin"
MERGED_ASSET_NAME = "xc-body-stackchan-merged.bin"
OTA_SLOT_SIZE = 0x3F0000
APP_DESC_OFFSET = 0x20
APP_DESC_MAGIC = 0xABCD5432
APP_VERSION_OFFSET = 0x30
APP_PROJECT_OFFSET = 0x50
APP_FIELD_SIZE = 32
STACKCHAN_PROJECT_NAME = "xc_body_stackchan"


def read_project_version(cmake_path: Path) -> str:
    """Read the XC Body firmware version from its CMake project file."""
    pattern = re.compile(r'^set\(PROJECT_VER "([^"]+)"\)$')
    for line in cmake_path.read_text(encoding="utf-8").splitlines():
        match = pattern.fullmatch(line.strip())
        if match:
            return match.group(1)
    raise ValueError(f"PROJECT_VER is missing from {cmake_path}")


def sha256_file(path: Path) -> str:
    """Return a lowercase SHA-256 digest without loading the file at once."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_app_descriptor(path: Path) -> tuple[str, str]:
    """Return the embedded version and project name from an ESP app image."""
    with path.open("rb") as source:
        header = source.read(APP_PROJECT_OFFSET + APP_FIELD_SIZE)
    if len(header) < APP_PROJECT_OFFSET + APP_FIELD_SIZE or header[0] != 0xE9:
        raise ValueError(f"{path} is not an ESP application image")
    if struct.unpack_from("<I", header, APP_DESC_OFFSET)[0] != APP_DESC_MAGIC:
        raise ValueError(f"{path} has no valid ESP application descriptor")

    def decode_field(offset: int) -> str:
        encoded = header[offset : offset + APP_FIELD_SIZE].split(b"\0", 1)[0]
        try:
            return encoded.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError(f"{path} has an invalid application descriptor") from error

    return decode_field(APP_VERSION_OFFSET), decode_field(APP_PROJECT_OFFSET)


def validate_ota_url(value: str) -> str:
    """Require an absolute HTTPS artifact URL without embedded credentials."""
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("OTA URL must be an absolute HTTPS URL")
    if parsed.username or parsed.password:
        raise ValueError("OTA URL must not contain credentials")
    return value


def package_release(
    build_dir: Path,
    output_dir: Path,
    version: str,
    ota_url: str,
) -> dict[str, object]:
    """Copy artifacts and write their hash-bound XC Body manifest."""
    ota_url = validate_ota_url(ota_url)
    sources = {
        OTA_ASSET_NAME: build_dir / OTA_SOURCE_NAME,
        MERGED_ASSET_NAME: build_dir / MERGED_SOURCE_NAME,
    }
    missing = [str(path) for path in sources.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError("missing firmware artifact: " + ", ".join(missing))
    ota_size = sources[OTA_ASSET_NAME].stat().st_size
    if ota_size <= 0 or ota_size > OTA_SLOT_SIZE:
        raise ValueError(
            f"OTA image is {ota_size} bytes; slot limit is {OTA_SLOT_SIZE}"
        )
    embedded_version, embedded_project = read_app_descriptor(
        sources[OTA_ASSET_NAME]
    )
    if embedded_project != STACKCHAN_PROJECT_NAME:
        raise ValueError(
            "OTA image is not an XC Body StackChan firmware image"
        )
    if embedded_version != version:
        raise ValueError(
            f"OTA image version {embedded_version!r} does not match {version!r}"
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    packaged: dict[str, dict[str, object]] = {}
    for asset_name, source in sources.items():
        destination = output_dir / asset_name
        shutil.copyfile(source, destination)
        checksum = sha256_file(destination)
        (output_dir / f"{asset_name}.sha256").write_text(
            f"{checksum}  {asset_name}\n",
            encoding="utf-8",
        )
        packaged[asset_name] = {
            "asset": asset_name,
            "sha256": checksum,
            "size": destination.stat().st_size,
        }

    ota = packaged[OTA_ASSET_NAME]
    manifest: dict[str, object] = {
        "schema_version": 1,
        "product": PROJECT_NAME,
        "hardware": HARDWARE_NAME,
        "firmware": {
            "version": version,
            "url": ota_url,
            "sha256": ota["sha256"],
            "size": ota["size"],
        },
        "recovery": packaged[MERGED_ASSET_NAME],
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument(
        "--build-dir",
        type=Path,
        default=Path("firmware/build"),
    )
    result.add_argument(
        "--output-dir",
        type=Path,
        default=Path("build/firmware-release"),
    )
    result.add_argument("--ota-url", required=True)
    result.add_argument(
        "--cmake",
        type=Path,
        default=Path("firmware/CMakeLists.txt"),
    )
    return result


def main() -> int:
    args = parser().parse_args()
    version = read_project_version(args.cmake)
    manifest = package_release(
        args.build_dir,
        args.output_dir,
        version,
        args.ota_url,
    )
    firmware = manifest["firmware"]
    assert isinstance(firmware, dict)
    print(
        f"Packaged XC Body firmware {version}: "
        f"{firmware['size']} bytes, sha256={firmware['sha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
