#!/bin/sh
set -eu

gateway_token=${STACKCHAN_TOKEN:-${BEARER_TOKEN:-}}
if [ -z "$gateway_token" ]; then
  echo "XC Body gateway token is missing" >&2
  exit 1
fi

playback_token=${STACKCHAN_PCM_TOKEN:-$gateway_token}
interaction_token=${XC_BODY_INTERACTION_HTTP_TOKEN:-$gateway_token}

export XC_BODY_STACKCHAN_MCP_URL=http://gateway:8767/mcp
export XC_BODY_STACKCHAN_MCP_TOKEN="$gateway_token"
export XC_BODY_PLAYBACK_URL=http://gateway:8766/opus
export XC_BODY_PCM_URL=http://gateway:8766/pcm
export XC_BODY_PLAYBACK_TOKEN="$playback_token"
export XC_BODY_INTERACTION_HTTP_TOKEN="$interaction_token"
export XC_BODY_VOICE=${XC_BODY_VOICE:-zh-CN-YunxiNeural}

exec python3 -m gateway.interaction_service \
  --host 0.0.0.0 --port 8770
