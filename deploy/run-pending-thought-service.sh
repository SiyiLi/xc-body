#!/bin/sh
set -eu

gateway_token=${STACKCHAN_TOKEN:-${BEARER_TOKEN:-}}
if [ -z "$gateway_token" ]; then
  echo "XC Body gateway token is missing" >&2
  exit 1
fi

playback_token=${STACKCHAN_PCM_TOKEN:-$gateway_token}
pending_token=${XC_BODY_PENDING_HTTP_TOKEN:-$gateway_token}

export XC_BODY_STACKCHAN_MCP_URL=https://43.143.37.91/gateway-mcp/mcp
export XC_BODY_STACKCHAN_MCP_TOKEN="$gateway_token"
export XC_BODY_AVATAR_ARCHIVE_PATH=/opt/xc-body/xc-body-native-320.rgb565le
export XC_BODY_PLAYBACK_URL=https://43.143.37.91/opus
export XC_BODY_PLAYBACK_TOKEN="$playback_token"
export XC_BODY_PENDING_HTTP_TOKEN="$pending_token"

exec python3 -m gateway.pending_thought_http_service \
  --host 0.0.0.0 --port 8770
