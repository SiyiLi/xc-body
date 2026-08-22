# XC Body Firmware

This directory contains the XC Body firmware for the M5Stack StackChan
K151/CoreS3. The ESP-IDF project is `xc_body`; its StackChan app identity is
`xc_body_stackchan`. The current firmware version is `0.1.3`, and the app image
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
- `releases/v0.1.3_stackchan.zip`.

## Flash Layout

The 16 MiB StackChan layout contains two `0x3f0000` app partitions and one
8 MiB assets partition. Routine app-only flashing uses offset `0x20000` and
preserves NVS. The merged recovery image starts at `0x0` and is not used for
routine iteration.

Firmware flashing always requires separate explicit permission. Detailed
project rules are in [`../AGENTS.md`](../AGENTS.md).

The no-USB dual-slot OTA path is physically accepted. The gateway bridge
updated `0.1.1` to `0.1.2`; a later physical reset made `0.1.2` discover and
install `0.1.3` from the stable manifest. The robot booted `ota_1`, reached the
authenticated gateway, and marked `0.1.3` valid. An incomplete first download
was never activated and the next boot retried successfully. That first attempt
crashed in the existing Si12T head-touch I2C poll, which remains an open
firmware defect. Forced unhealthy-boot rollback also remains untested. The
merged USB recovery path remains available.
