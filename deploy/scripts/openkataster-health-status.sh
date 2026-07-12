#!/usr/bin/env bash
set -u -o pipefail

issues=()

check_container() {
    local name="$1"
    local expected="$2"
    local value
    value="$(docker inspect -f "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}" "$name" 2>/dev/null || true)"
    [[ "$value" == "$expected" ]] || issues+=("container:$name=$value")
}

check_container openkataster-tiles-api healthy
check_container openkataster-tiles-caddy healthy
check_container openkataster-api running
check_container openkataster-api-worker running
check_container openkataster-api-redis running
check_container openkataster-pocketbase running

free_percent="$(df -P /srv | awk 'NR==2 {gsub(/%/, "", $5); print 100-$5}')"
[[ "${free_percent:-0}" -ge 15 ]] || issues+=("disk:/srv-free=${free_percent}%")

status_file=/var/lib/openkataster-backup/status.env
if [[ ! -f "$status_file" ]]; then
    issues+=("backup:missing-status")
else
    # shellcheck disable=SC1090
    source "$status_file"
    age_seconds=$(( $(date -u +%s) - $(date -u -d "${BACKUP_TIMESTAMP:-1970-01-01T00:00:00Z}" +%s 2>/dev/null || echo 0) ))
    [[ "${BACKUP_STATUS:-}" == "ok" && "$age_seconds" -le 93600 ]] || issues+=("backup:stale-or-failed")
fi

if (( ${#issues[@]} )); then
    printf 'failed %s\n' "${issues[*]}"
    exit 1
fi

printf 'ok tiles-api=healthy caddy=healthy disk-free=%s%%\n' "$free_percent"
