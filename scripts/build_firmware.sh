#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/.." && pwd)"

exec docker run --rm --cpus=4 --ulimit nofile=65536:65536 \
  -v "$REPO/firmware:/project" \
  -w /project \
  espressif/idf:v5.5.2 \
  python ./scripts/release.py stackchan
