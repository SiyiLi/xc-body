#!/usr/bin/env bash
set -Eeuo pipefail

[ "$#" -ge 2 ] || {
  echo "usage: $0 LOG_NAME COMMAND [ARG ...]" >&2
  exit 64
}

log_dir=${XC_BODY_LOG_DIR:-/logs}
log_path=$log_dir/$1
max_log_bytes=104857600
shift

: >> "$log_path"
log_pipe=/tmp/xc-body-log.$$
mkfifo "$log_pipe"
(
  LC_ALL=C
  log_size=$(wc -c < "$log_path")
  while IFS= read -r line || [ -n "$line" ]; do
    line_bytes=$((${#line} + 1))
    if ((log_size + line_bytes > max_log_bytes)); then
      mv -f "$log_path" "$log_path.1"
      : > "$log_path"
      log_size=0
    fi
    printf '%s\n' "$line"
    printf '%s\n' "$line" >> "$log_path"
    log_size=$((log_size + line_bytes))
  done
) < "$log_pipe" &
log_pid=$!
exec 3> "$log_pipe"
rm "$log_pipe"
"$@" >&3 2>&1 3>&- &
service_pid=$!
exec 3>&-

forward_signal() {
  trap '' TERM INT
  kill -s "$1" "$service_pid" 2>/dev/null || true
}
trap 'forward_signal TERM' TERM
trap 'forward_signal INT' INT

service_status=0
wait "$service_pid" || service_status=$?
wait "$log_pid" || true
set +e
wait "$service_pid" 2>/dev/null
drained_status=$?
set -e
if ((drained_status != 127)); then
  service_status=$drained_status
fi
exit "$service_status"
