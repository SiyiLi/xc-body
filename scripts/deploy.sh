#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/.." && pwd)"
TARGET="${XC_BODY_DEPLOY_TARGET:-medchain@43.143.37.91}"
IDENTITY="${XC_BODY_DEPLOY_IDENTITY:-$HOME/.ssh/id_ed25519_medchain}"
REGISTRY=docker.tc.nvda.ai
REGISTRY_REPO=$REGISTRY/nvcr.io/xc-body
RUNTIME_TAG=$REGISTRY_REPO:xc-body-0.1.0
CADDY_TAG=$REGISTRY_REPO:caddy-2.11.4
CADDY_SOURCE=caddy:2.11.4-alpine
PENDING_MCP_URL=https://43.143.37.91/xc-body/mcp
OPENCLAW_VENV=$REPO/.venv
OPENCLAW_PYTHON=$OPENCLAW_VENV/bin/python
EXPECTED_AVATAR_SHA256=daa35ed17a716860f0415c053dbf3d59e6e421ad50556de5ccc58cae28af36f7
STATE_DIR=$REPO/build/deploy
SSH_OPTIONS=(
  -i "$IDENTITY"
  -o IdentitiesOnly=yes
  -o BatchMode=yes
  -o ConnectTimeout=10
  -o ServerAliveInterval=15
  -o ServerAliveCountMax=3
)

candidate=0
status_only=0
build_root=""

die() {
  echo "[FATAL] $1" >&2
  exit "${2:-1}"
}

usage() {
  cat <<'EOF'
Usage: scripts/deploy.sh [--candidate] | --status

Build linux/amd64 production images, push them to TC Artifactory, and deploy
their exact digests to the XC Body rendezvous VM. Use --candidate only for an
explicitly authorized deployment from uncommitted runtime inputs.
EOF
}

require_command() {
  command -v "$1" >/dev/null 2>&1 \
    || die "required command not found: $1" 64
}

sha256_file() {
  shasum -a 256 "$1" | awk '{print $1}'
}

cleanup() {
  [ -z "$build_root" ] || rm -rf "$build_root"
}
trap cleanup EXIT

ssh_vm() {
  ssh "${SSH_OPTIONS[@]}" -T "$TARGET" "$@"
}

deployment_status() {
  require_command ssh
  [ -r "$IDENTITY" ] || die "SSH identity is missing: $IDENTITY" 64
  ssh_vm bash -s <<'REMOTE'
set -eu
echo "[status] containers"
docker ps -a --filter name=^/xc-body- \
  --format '{{.Names}}={{.Status}} image={{.Image}}'
echo "[status] deployment"
cat /data/xc-body/gateway-state/last-deploy-state.txt 2>/dev/null || true
echo "[status] gateway"
docker logs --tail 35 xc-body-gateway 2>&1 || true
echo "[status] pending"
docker logs --tail 50 xc-body-pending 2>&1 || true
REMOTE
}

copy_runtime_inputs() {
  local context=$1 file_list=$2

  if [ "$candidate" = "1" ]; then
    git -C "$REPO" ls-files -z --cached --others --exclude-standard -- \
      gateway stackchan stackchan_mcp contracts deploy scripts \
      pyproject.toml \
      | while IFS= read -r -d '' path; do
          [ -e "$REPO/$path" ] || [ -L "$REPO/$path" ] || continue
          printf '%s\0' "$path"
        done > "$file_list"
    COPYFILE_DISABLE=1 tar -cf - -C "$REPO" --null -T "$file_list" \
      | tar -xf - -C "$context/app"
  else
    git -C "$REPO" archive HEAD \
      gateway stackchan stackchan_mcp contracts deploy scripts \
      pyproject.toml \
      | tar -xf - -C "$context/app"
  fi
  find "$context/app" -type d -exec chmod 0755 {} +
  find "$context/app" -type f -exec chmod 0644 {} +
  chmod 0755 \
    "$context/app/deploy/install.sh" \
    "$context/app/deploy/run-pending-thought-service.sh"

}

prepare_context() {
  local context=$1 file_list=$2
  mkdir -p "$context/app" "$context/avatar"
  copy_runtime_inputs "$context" "$file_list"
  python3 "$context/app/scripts/build_avatar_assets.py" \
    --output-dir "$context/avatar" >/dev/null
}

build_and_push_runtime() {
  local context=$1 source_commit=$2 dirty=$3 built_at=$4
  local dockerfile=$context/app/deploy/Dockerfile.runtime
  local build_args=(
    --platform linux/amd64
    --provenance=false
    --sbom=false
    --build-arg "GIT_COMMIT=$source_commit"
    --build-arg "GIT_DIRTY=$dirty"
    --build-arg "BUILT_AT=$built_at"
    -f "$dockerfile"
  )

  echo "[build] XC Body runtime"
  docker buildx build "${build_args[@]}" \
    -t "$RUNTIME_TAG" --load "$context"
  echo "[push] XC Body runtime"
  docker buildx build "${build_args[@]}" \
    --output "type=image,name=$RUNTIME_TAG,oci-mediatypes=false,push=true" \
    "$context"
}

mirror_caddy() {
  echo "[push] Caddy runtime"
  docker pull --platform linux/amd64 "$CADDY_SOURCE"
  docker tag "$CADDY_SOURCE" "$CADDY_TAG"
  docker push "$CADDY_TAG"
}

manifest_digest() {
  docker buildx imagetools inspect "$1" \
    --format '{{.Manifest.Digest}}'
}

registry_credentials() {
  python3 - "$HOME/.docker/config.json" "$REGISTRY" <<'PY'
import base64
import json
import sys

path, registry = sys.argv[1:]
with open(path, encoding="utf-8") as config_file:
    config = json.load(config_file)
auth = config.get("auths", {}).get(registry, {}).get("auth", "")
if not auth:
    raise SystemExit("TC Artifactory Docker credential is missing")
username, password = base64.b64decode(auth).decode().split(":", 1)
print(username)
print(password)
PY
}

login_vm_to_registry() {
  local credentials username password
  credentials=$(registry_credentials)
  username=${credentials%%$'\n'*}
  password=${credentials#*$'\n'}
  printf '%s' "$password" \
    | ssh "${SSH_OPTIONS[@]}" -T "$TARGET" \
      docker login "$REGISTRY" --username "$username" --password-stdin \
      >/dev/null
  unset credentials password
}

remote_pending_token() {
  ssh_vm bash -s <<'REMOTE'
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
}

prepare_openclaw_producer() {
  if ! "$OPENCLAW_PYTHON" -c \
    'import edge_tts,importlib.metadata,mcp.server; assert edge_tts.__version__ == "7.2.3" and importlib.metadata.version("mcp") == "1.29.0"' \
    >/dev/null 2>&1; then
    uv venv --python 3.11 "$OPENCLAW_VENV"
    uv pip install --python "$OPENCLAW_PYTHON" \
      -r "$REPO/deploy/openclaw-requirements.txt"
  fi
}

configure_openclaw() {
  local token config
  prepare_openclaw_producer
  token=$(remote_pending_token)
  config=$(python3 - \
    "$token" "$PENDING_MCP_URL" "$OPENCLAW_PYTHON" "$REPO" <<'PY'
import json
import sys

token, url, python, repo = sys.argv[1:]
print(json.dumps({
    "enabled": True,
    "command": python,
    "args": ["-m", "gateway.openclaw_thought_service"],
    "cwd": repo,
    "env": {
        "XC_BODY_PENDING_MCP_URL": url,
        "XC_BODY_PENDING_MCP_TOKEN": token,
        "XC_BODY_VOICE": "zh-CN-YunxiNeural",
    },
    "connectTimeout": 90,
    "timeout": 180,
    "supportsParallelToolCalls": False,
    "toolFilter": {"include": ["consider_thought"]},
}, separators=(",", ":")))
PY
)
  openclaw mcp set xc-body "$config" >/dev/null
  openclaw mcp unset xc-body-embodiment >/dev/null 2>&1 || true
  openclaw mcp reload >/dev/null
  unset token config
}

probe_openclaw() {
  local probe=$STATE_DIR/openclaw-xc-body.json
  openclaw mcp probe xc-body --json > "$probe"
  python3 - "$probe" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as probe_file:
    probe = json.load(probe_file)
if probe.get("diagnostics"):
    raise SystemExit(f"OpenClaw MCP diagnostics: {probe['diagnostics']}")
tools = probe.get("tools", [])
if not any(name.endswith("consider_thought") for name in tools):
    raise SystemExit(f"consider_thought is missing: {tools}")
print("openclaw_probe=xc-body:consider_thought:ok")
PY
}

deploy_images() {
  local runtime_ref=$1 caddy_ref=$2 source_commit=$3
  local avatar_sha256=$4 deployment_kind=$5
  login_vm_to_registry
  ssh_vm bash -s -- \
    "$runtime_ref" "$caddy_ref" "$source_commit" "$avatar_sha256" \
    "$deployment_kind" < "$REPO/deploy/install.sh"
  configure_openclaw
  probe_openclaw
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --candidate) candidate=1 ;;
    --status) status_only=1 ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      exit 64
      ;;
  esac
  shift
done

if [ "$status_only" = "1" ]; then
  [ "$candidate" = "0" ] || die "--status and --candidate conflict" 64
  deployment_status
  exit 0
fi

for command in docker ffmpeg git openclaw python3 shasum ssh tar uv; do
  require_command "$command"
done
[ -r "$IDENTITY" ] || die "SSH identity is missing: $IDENTITY" 64

source_commit=$(git -C "$REPO" rev-parse HEAD)

dirty_paths=$(git -C "$REPO" status --porcelain --untracked-files=all -- \
  gateway stackchan stackchan_mcp contracts deploy scripts pyproject.toml)
dirty=false
if [ -n "$dirty_paths" ]; then
  dirty=true
  [ "$candidate" = "1" ] \
    || die "runtime inputs are dirty; use --candidate only when authorized" 65
fi
deployment_kind=committed
[ "$candidate" = "0" ] || deployment_kind=candidate

mkdir -p "$STATE_DIR"
build_root=$(mktemp -d /tmp/xc-body-image.XXXXXX)
context=$build_root/context
file_list=$build_root/files
prepare_context "$context" "$file_list"
avatar_sha256=$(sha256_file \
  "$context/avatar/xc-body-layered.rgb565le")
[ "$avatar_sha256" = "$EXPECTED_AVATAR_SHA256" ] \
  || die "generated avatar does not match the reviewed payload" 65

built_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
build_and_push_runtime \
  "$context" "$source_commit" "$dirty" "$built_at"
mirror_caddy
runtime_digest=$(manifest_digest "$RUNTIME_TAG")
caddy_digest=$(manifest_digest "$CADDY_TAG")
runtime_ref=$RUNTIME_TAG@$runtime_digest
caddy_ref=$CADDY_TAG@$caddy_digest

deploy_images \
  "$runtime_ref" "$caddy_ref" "$source_commit" "$avatar_sha256" \
  "$deployment_kind"

cat > "$STATE_DIR/last-deploy-state.txt" <<EOF
status=$deployment_kind
source_commit=$source_commit
avatar_sha256=$avatar_sha256
runtime_image=$runtime_ref
caddy_image=$caddy_ref
deployed_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
EOF
echo "deployed=$source_commit"
echo "runtime_image=$runtime_ref"
echo "caddy_image=$caddy_ref"
