# XC Body Firmware

This directory contains the XC Body firmware for the M5Stack StackChan
K151/CoreS3. The ESP-IDF project is `xc_body`; its StackChan app identity is
`xc_body_stackchan`. The current firmware version is `0.1.7`, and the app image
is `build/xc_body.bin`.

## Source Provenance

`firmware/` and `stackchan_mcp/` were imported from
<https://github.com/kisaragi-mochi/stackchan-mcp> at revision
`804af573ba8f577f63efbd39f6e8a9c7f57b4647`. The Smooth UI Toolkit revision is
`2a18ff5d3fd6b402339d0f4f3c2834f574e3cb05`.

XC Body maintains these sources directly. The upstream repository is
provenance, not a submodule, build input, or runtime dependency. The imported
code is MIT licensed; retained notices remain with the source trees.

## Build

Build the exact StackChan target from this directory:

```sh
docker run --rm --cpus=4 --ulimit nofile=65536:65536 \
  -v "$PWD":/project -w /project espressif/idf:v5.5.2 \
  python ./scripts/release.py stackchan
```

The `stackchan` argument is mandatory. A generic ESP32-S3 build selects the
wrong board configuration and can produce a PSRAM boot loop.

The build produces:

- `build/xc_body.bin` for the app partition;
- `build/merged-binary.bin` for full USB recovery; and
- `releases/v0.1.7_stackchan.zip`.

## Flash Layout

The 16 MiB StackChan layout contains two `0x3f0000` app partitions and one
8 MiB assets partition. Routine app-only flashing uses offset `0x20000` and
preserves NVS. The merged recovery image starts at `0x0` and is not used for
routine iteration.

Firmware flashing always requires separate explicit permission. Detailed
project rules are in [`../AGENTS.md`](../AGENTS.md).

The no-USB dual-slot OTA path is physically accepted through consecutive
updates from `0.1.4` to `0.1.5` and from `0.1.5` to `0.1.6`. Both images
authenticated with the gateway, restored the reviewed avatar, and survived a
validation reboot. The robot now runs `0.1.6` on `ota_0`; its app SHA-256 is
`3265a8e84bd306c7f705792ed1370e352fd6cca0f3da140f8765588fa9a5e2b9`.

After a bootloader rollback, firmware disables automatic boot OTA until it is
re-enabled through USB or the configuration screen. Configuration mode uses
SSID `XCBODY-3341`. Forced unhealthy-boot rollback remains untested, and the
intermittent Si12T head-touch I2C crash remains open. The merged USB recovery
path remains available.
