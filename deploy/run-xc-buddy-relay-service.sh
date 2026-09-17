#!/bin/sh
set -eu

exec python3 -m gateway.xc_buddy_relay_service \
  --host 0.0.0.0 --port 8781
