#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/.." && pwd)"
TARGET="${XC_BODY_DEPLOY_TARGET:-}"
IDENTITY="${XC_BODY_DEPLOY_IDENTITY:-$HOME/.ssh/id_ed25519}"
PUBLIC_URL="${XC_BODY_PUBLIC_URL:-}"
SUMMARY_URL=$PUBLIC_URL/xc-body/summary/v1
SESSION_KEY="${XC_BODY_OPENCLAW_SESSION_KEY:-}"
TELEGRAM_TARGET="${XC_BODY_TELEGRAM_TARGET:-}"
PROJECTION_API_KEY_FILE="${XC_BODY_PROJECTION_API_KEY_FILE:-}"
STATE_DIR="${XC_BODY_DEPLOY_STATE_DIR:-$REPO/build/deploy}"
SSH_OPTIONS=(
  -i "$IDENTITY"
  -o IdentitiesOnly=yes
  -o BatchMode=yes
  -o ConnectTimeout=10
)

die() {
  echo "[FATAL] $1" >&2
  exit "${2:-1}"
}

for command in npm openclaw python3 ssh; do
  command -v "$command" >/dev/null 2>&1 \
    || die "required command not found: $command" 64
done
[ -n "$TARGET" ] || die "XC_BODY_DEPLOY_TARGET is required" 64
[ -r "$IDENTITY" ] || die "SSH identity is missing: $IDENTITY" 64
[ -r "$PROJECTION_API_KEY_FILE" ] \
  || die "projection API key file is missing: $PROJECTION_API_KEY_FILE" 64
[[ "$PUBLIC_URL" =~ ^https://[A-Za-z0-9.-]+$ ]] \
  || die "XC_BODY_PUBLIC_URL must be an HTTPS origin without a port" 64
if [ -n "$SESSION_KEY" ] || [ -n "$TELEGRAM_TARGET" ]; then
  [ -n "$SESSION_KEY" ] && [ -n "$TELEGRAM_TARGET" ] \
    || die "session key and Telegram target must be set together" 64
  VOICE_URL=$PUBLIC_URL/xc-body/voice/v1/
else
  VOICE_URL=
fi

token=$(ssh "${SSH_OPTIONS[@]}" -T "$TARGET" bash -s <<'REMOTE'
set -eu
env_file=/data/xc-body/deploy/gateway.env
value() {
  awk -v key="$1" '
    index($0, key "=") == 1 { sub(/^[^=]*=/, ""); found = $0 }
    END { print found }
  ' "$env_file"
}
token=$(value XC_BODY_PENDING_HTTP_TOKEN)
[ -n "$token" ] || token=$(value STACKCHAN_TOKEN)
[ -n "$token" ] || token=$(value BEARER_TOKEN)
[ -n "$token" ]
printf '%s' "$token"
REMOTE
)

plugin_path=$REPO/openclaw-plugin
configured_paths=$(
  openclaw config get plugins.load.paths --json 2>/dev/null \
    || printf '[]'
)
configured_paths=$(python3 - "$configured_paths" <<'PY'
import json
from pathlib import Path
import sys

retained = []
for raw_path in json.loads(sys.argv[1]):
    candidate = Path(raw_path).expanduser().resolve()
    try:
        manifest = json.loads(
            (candidate / "openclaw.plugin.json").read_text()
        )
    except (OSError, json.JSONDecodeError):
        manifest = {}
    if manifest.get("id") != "xc-body-native":
        retained.append(raw_path)
print(json.dumps(retained, separators=(",", ":")))
PY
)
openclaw config set plugins.load.paths \
  "$configured_paths" --strict-json >/dev/null
npm run build --prefix "$plugin_path" >/dev/null
openclaw plugins install --force "$plugin_path" >/dev/null
unset configured_paths plugin_path
config=$(python3 - \
  "$token" "$SUMMARY_URL" "$VOICE_URL" "$SESSION_KEY" \
  "$TELEGRAM_TARGET" "$PROJECTION_API_KEY_FILE" <<'PY'
import json
import sys

token, summary_url, voice_url, session_key, telegram_target, api_key_file = (
    sys.argv[1:]
)
config = {
    "summaryUrl": summary_url,
    "token": token,
    "projectionApiKeyFile": api_key_file,
    "timeoutMs": 180000,
}
if voice_url:
    config.update({
        "voiceUrl": voice_url,
        "sessionKey": session_key,
        "telegramTarget": telegram_target,
    })
print(json.dumps(config, separators=(",", ":")))
PY
)
openclaw config set plugins.entries.xc-body-native.config \
  "$config" --strict-json >/dev/null
openclaw config set \
  plugins.entries.xc-body-native.hooks.allowConversationAccess \
  true --strict-json >/dev/null
openclaw config unset plugins.entries.xc-body-native.llm >/dev/null 2>&1 \
  || true
openclaw plugins enable xc-body-native >/dev/null
openclaw mcp unset xc-body >/dev/null 2>&1 || true
openclaw mcp unset xc-body-embodiment >/dev/null 2>&1 || true
unset token config

mkdir -p "$STATE_DIR"
gateway_probe=$STATE_DIR/openclaw-gateway.json
plugin_probe=$STATE_DIR/openclaw-xc-body-native.json
openclaw gateway restart --json \
  > "$STATE_DIR/openclaw-gateway-restart.json"
openclaw gateway status --json --require-rpc > "$gateway_probe"
openclaw plugins inspect xc-body-native --runtime --json > "$plugin_probe"
python3 - "$gateway_probe" "$plugin_probe" <<'PY'
import json
import sys

gateway_path, plugin_path = sys.argv[1:]
with open(gateway_path, encoding="utf-8") as source:
    gateway = json.load(source)
with open(plugin_path, encoding="utf-8") as source:
    result = json.load(source)

runtime = gateway.get("service", {}).get("runtime", {})
if (
    runtime.get("status") != "running"
    or gateway.get("rpc", {}).get("ok") is not True
):
    raise SystemExit("OpenClaw Gateway did not restart healthy")

plugin = result.get("plugin", {})
if plugin.get("status") != "loaded" or plugin.get("enabled") is not True:
    raise SystemExit("XC Body plugin is not loaded and enabled")
if result.get("diagnostics"):
    raise SystemExit("XC Body plugin has runtime diagnostics")
hooks = {
    hook.get("name")
    for hook in result.get("typedHooks", [])
    if isinstance(hook, dict)
}
required_hooks = {
    "agent_end",
    "cron_changed",
    "subagent_ended",
}
if not required_hooks.issubset(hooks):
    raise SystemExit("XC Body plugin hooks are incomplete")
PY
echo "openclaw_plugin=xc-body-native:deployed"
