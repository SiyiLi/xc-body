#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/.." && pwd)"
TARGET="${XC_BODY_DEPLOY_TARGET:-medchain@43.143.37.91}"
IDENTITY="${XC_BODY_DEPLOY_IDENTITY:-$HOME/.ssh/id_ed25519_medchain}"
SUMMARY_URL=https://43.143.37.91/xc-body/summary/v1
STATE_DIR=$REPO/build/deploy
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

for command in openclaw python3 ssh; do
  command -v "$command" >/dev/null 2>&1 \
    || die "required command not found: $command" 64
done
[ -r "$IDENTITY" ] || die "SSH identity is missing: $IDENTITY" 64

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

if ! openclaw plugins inspect xc-body-native --json >/dev/null 2>&1; then
  openclaw plugins install --link "$REPO/openclaw-plugin" >/dev/null
fi
config=$(python3 - "$token" "$SUMMARY_URL" <<'PY'
import json
import sys

token, url = sys.argv[1:]
print(json.dumps(
    {"summaryUrl": url, "token": token, "timeoutMs": 120000},
    separators=(",", ":"),
))
PY
)
openclaw config set plugins.entries.xc-body-native.config \
  "$config" --strict-json >/dev/null
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
required_hooks = {"cron_changed", "subagent_ended"}
if not required_hooks.issubset(hooks):
    raise SystemExit("XC Body plugin hooks are incomplete")
PY
echo "openclaw_plugin=xc-body-native:deployed"
