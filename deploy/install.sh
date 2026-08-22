#!/usr/bin/env bash
set -Eeuo pipefail

die() {
  echo "[FATAL] $1" >&2
  exit "${2:-1}"
}

runtime_image=${1:-}
caddy_image=${2:-}
source_commit=${3:-}
avatar_sha256=${4:-}
deployment_kind=${5:-}
root=/data/xc-body
deploy_dir=$root/deploy
runtime_tag=${runtime_image%@*}
caddy_tag=${caddy_image%@*}
runtime_repository=${runtime_tag%:*}
caddy_repository=${caddy_tag%:*}

[[ "$runtime_image" =~ ^docker\.tc\.nvda\.ai/.+@sha256:[0-9a-f]{64}$ ]] \
  || die "invalid runtime image reference" 64
[[ "$caddy_image" =~ ^docker\.tc\.nvda\.ai/.+@sha256:[0-9a-f]{64}$ ]] \
  || die "invalid Caddy image reference" 64
[[ "$source_commit" =~ ^[0-9a-f]{40}$ ]] \
  || die "invalid source commit" 64
[[ "$avatar_sha256" =~ ^[0-9a-f]{64}$ ]] \
  || die "invalid avatar digest" 64
case "$deployment_kind" in
  candidate|committed) ;;
  *) die "invalid deployment kind" 64 ;;
esac

exec 9>/tmp/xc-body-deploy.lock
flock -n 9 || die "another XC Body deployment is running" 75
test -r "$deploy_dir/gateway.env" \
  || die "missing private gateway environment" 66
install -d -m 0755 "$root/firmware" "$root/firmware/releases"

echo "[deploy] pulling TC Artifactory images"
docker pull "$runtime_image"
docker pull "$caddy_image"
docker tag "$runtime_image" "${runtime_image%@*}"
docker tag "$caddy_image" "${caddy_image%@*}"

stage=$(mktemp -d /tmp/xc-body-config.XXXXXX)
container_id=$(docker create "$runtime_image")
cleanup() {
  docker rm -f "$container_id" >/dev/null 2>&1 || true
  rm -rf "$stage"
}
trap cleanup EXIT
docker cp "$container_id:/opt/xc-body/deploy/compose.yaml" \
  "$stage/compose.yaml"
docker cp "$container_id:/opt/xc-body/deploy/Caddyfile" \
  "$stage/Caddyfile"
docker rm "$container_id" >/dev/null
container_id=

install -m 0644 "$stage/compose.yaml" "$deploy_dir/compose.yaml"
install -m 0644 "$stage/Caddyfile" "$deploy_dir/Caddyfile"
images_tmp=$deploy_dir/.images.env.$$
{
  printf 'XC_BODY_RUNTIME_IMAGE=%s\n' "$runtime_image"
  printf 'XC_BODY_CADDY_IMAGE=%s\n' "$caddy_image"
} > "$images_tmp"
chmod 0600 "$images_tmp"
mv "$images_tmp" "$deploy_dir/images.env"

compose=(
  docker compose
  --env-file "$deploy_dir/images.env"
  -f "$deploy_dir/compose.yaml"
)
"${compose[@]}" config >/dev/null

echo "[deploy] replacing legacy XC Body containers"
for container in \
  xc-body-tunnel xc-body-pending xc-body-proxy xc-body-gateway; do
  if docker inspect "$container" >/dev/null 2>&1; then
    docker rm -f "$container" >/dev/null
  fi
done

echo "[deploy] starting gateway and proxy"
gateway_start=$(date -u +%Y-%m-%dT%H:%M:%SZ)
"${compose[@]}" up -d --force-recreate gateway proxy

attempt=0
while [ "$attempt" -lt 90 ]; do
  if docker logs --since "$gateway_start" xc-body-gateway 2>&1 \
    | grep -q 'ESP32 ready'; then
    break
  fi
  attempt=$((attempt + 1))
  sleep 2
done
[ "$attempt" -lt 90 ] \
  || die "StackChan did not become ready after gateway restart" 68

echo "[deploy] starting pending-thought service"
pending_start=$(date -u +%Y-%m-%dT%H:%M:%SZ)
"${compose[@]}" up -d --force-recreate pending
attempt=0
while [ "$attempt" -lt 90 ]; do
  if docker exec xc-body-pending python3 -c \
    'import os,urllib.request as u;t=os.getenv("XC_BODY_PENDING_HTTP_TOKEN") or os.getenv("STACKCHAN_TOKEN") or os.getenv("BEARER_TOKEN");r=u.Request("http://127.0.0.1:8770/readyz",headers={"Authorization":"Bearer "+t});raise SystemExit(0 if u.urlopen(r,timeout=5).status==200 else 1)' \
    >/dev/null 2>&1; then
    break
  fi
  attempt=$((attempt + 1))
  sleep 2
done
[ "$attempt" -lt 90 ] || {
  docker logs --since "$pending_start" xc-body-pending 2>&1 | tail -80
  die "pending-thought service did not become ready" 68
}

unexpected=$(
  docker ps -a --format '{{.Names}}' \
    | awk '/^xc-body-/ && $0 != "xc-body-gateway" && \
      $0 != "xc-body-pending" && $0 != "xc-body-proxy"'
)
[ -z "$unexpected" ] \
  || die "unexpected XC Body containers remain: $unexpected" 68

state_tmp=$root/gateway-state/.last-deploy-state.$$
{
  printf 'status=%s\n' "$deployment_kind"
  printf 'source_commit=%s\n' "$source_commit"
  printf 'avatar_sha256=%s\n' "$avatar_sha256"
  printf 'runtime_image=%s\n' "$runtime_image"
  printf 'caddy_image=%s\n' "$caddy_image"
  printf 'deployed_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} > "$state_tmp"
mv "$state_tmp" "$root/gateway-state/last-deploy-state.txt"

cleanup_dangling_images() {
  local repository=$1 image_ids
  image_ids=$(docker image ls "$repository" --no-trunc \
    --format '{{if eq .Tag "<none>"}}{{.ID}}{{end}}' \
    | sed '/^$/d' | sort -u)
  [ -z "$image_ids" ] || docker image rm $image_ids
}

cleanup_dangling_images "$runtime_repository"
[ "$caddy_repository" = "$runtime_repository" ] \
  || cleanup_dangling_images "$caddy_repository"

docker ps --filter name=^/xc-body- \
  --format '{{.Names}}={{.Status}} image={{.Image}}'
echo "source_commit=$source_commit"
