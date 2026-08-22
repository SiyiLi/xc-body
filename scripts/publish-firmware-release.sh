#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/.." && pwd)"
TARGET="${XC_BODY_DEPLOY_TARGET:-medchain@43.143.37.91}"
IDENTITY="${XC_BODY_DEPLOY_IDENTITY:-$HOME/.ssh/id_ed25519}"
PUBLIC_ROOT="${XC_BODY_FIRMWARE_URL:-https://43.143.37.91/firmware}"
REMOTE_ROOT=/data/xc-body/firmware
OTA_ASSET=xc-body-stackchan-ota.bin
SSH_OPTIONS=(
  -i "$IDENTITY"
  -o IdentitiesOnly=yes
  -o BatchMode=yes
  -o ConnectTimeout=10
)
replace=false

die() {
  echo "[FATAL] $1" >&2
  exit "${2:-1}"
}

if [ "${1:-}" = "--replace" ]; then
  replace=true
  shift
fi
[ "$#" -eq 0 ] || die "usage: $0 [--replace]" 64

for command in docker python3 scp sed ssh; do
  command -v "$command" >/dev/null 2>&1 \
    || die "required command not found: $command" 64
done
[ -r "$IDENTITY" ] || die "SSH identity is missing: $IDENTITY" 64

version=$(sed -nE \
  's/^set\(PROJECT_VER "([^"]+)"\)$/\1/p' \
  "$REPO/firmware/CMakeLists.txt")
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] \
  || die "invalid firmware PROJECT_VER: $version" 65

release=v$version
output_dir=$REPO/build/firmware-release/$release
ota_url=$PUBLIC_ROOT/releases/$release/$OTA_ASSET

"$REPO/scripts/build_firmware.sh"

python3 "$REPO/scripts/package_firmware_release.py" \
  --build-dir "$REPO/firmware/build" \
  --output-dir "$output_dir" \
  --cmake "$REPO/firmware/CMakeLists.txt" \
  --ota-url "$ota_url"

stage=$(ssh "${SSH_OPTIONS[@]}" -T "$TARGET" \
  mktemp -d "$REMOTE_ROOT/.upload-$release.XXXXXX")
scp "${SSH_OPTIONS[@]}" \
  "$output_dir/manifest.json" \
  "$output_dir/$OTA_ASSET" \
  "$output_dir/$OTA_ASSET.sha256" \
  "$TARGET:$stage/"

ssh "${SSH_OPTIONS[@]}" -T "$TARGET" bash -s -- \
  "$stage" "$REMOTE_ROOT/releases/$release" "$REMOTE_ROOT" \
  "$OTA_ASSET" "$replace" <<'REMOTE'
set -eu
stage=$1
release_dir=$2
root=$3
ota_asset=$4
replace=$5

cd "$stage"
sha256sum -c "$ota_asset.sha256"
if [ -e "$release_dir" ]; then
  [ "$replace" = true ] || {
    echo "[FATAL] firmware release already exists: $release_dir" >&2
    exit 65
  }
  rm -rf "$release_dir"
fi
chmod 0755 "$stage"
chmod 0644 manifest.json "$ota_asset" "$ota_asset.sha256"
mv "$stage" "$release_dir"
manifest_tmp=$root/.manifest.json.$$
install -m 0644 "$release_dir/manifest.json" "$manifest_tmp"
mv "$manifest_tmp" "$root/manifest.json"
REMOTE

echo "published=$release"
echo "manifest=$PUBLIC_ROOT/manifest.json"
echo "firmware=$ota_url"
