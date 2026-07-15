#!/usr/bin/env bash
set -euo pipefail

umask 077

if [[ -f /etc/openkataster-backup.env ]]; then
    # shellcheck disable=SC1091
    source /etc/openkataster-backup.env
fi

BACKUP_ROOT="${BACKUP_ROOT:-/srv/openkataster-backups/daily}"
KEEP_DAYS="${BACKUP_KEEP_DAYS:-30}"
TILES_REPO="${TILES_REPO:-/opt/openkataster-tiles}"
TILES_DATA="${TILES_DATA:-/srv/openkataster-tiles/data}"
TILES_ACTIVE="${TILES_ACTIVE:-/srv/openkataster-tiles/active}"
PB_DATA="${PB_DATA:-/srv/openkataster-api/runtime/pocketbase/data.db}"
PB_AUXILIARY="${PB_AUXILIARY:-/srv/openkataster-api/runtime/pocketbase/auxiliary.db}"
PB_STORAGE="${PB_STORAGE:-/srv/openkataster-api/runtime/pocketbase/storage}"
API_UPLOAD_CONTROL="${API_UPLOAD_CONTROL:-/srv/openkataster-api/data/.admin-upload-control.sqlite3}"
API_REPO="${API_REPO:-/opt/openkataster-api}"
STATUS_DIR="${STATUS_DIR:-/var/lib/openkataster-backup}"

exec 9>/run/lock/openkataster-backup.lock
flock -n 9 || exit 0

timestamp="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
backup_dir="$BACKUP_ROOT/$timestamp"
work_dir="$(mktemp -d)"

cleanup() {
    rm -rf "$work_dir"
}
trap cleanup EXIT

install -d -m 0700 "$backup_dir"
install -d -m 0755 "$STATUS_DIR"

backup_sqlite() {
    local source="$1"
    local target="$2"
    [[ -f "$source" ]] || return 0
    sqlite3 -cmd '.timeout 10000' "$source" ".backup '$target'"
    [[ "$(sqlite3 "$target" 'PRAGMA integrity_check;')" == "ok" ]]
}

backup_sqlite "$PB_DATA" "$backup_dir/pocketbase-data.sqlite"
backup_sqlite "$PB_AUXILIARY" "$backup_dir/pocketbase-auxiliary.sqlite"
backup_sqlite "$TILES_DATA/api_usage.sqlite" "$backup_dir/api-usage.sqlite"
backup_sqlite "$API_UPLOAD_CONTROL" "$backup_dir/admin-upload-control.sqlite"

if [[ -d "$PB_STORAGE" ]]; then
    tar -C "$PB_STORAGE" -czf "$backup_dir/pocketbase-storage.tar.gz" .
fi

tar -C "$API_REPO" -czf "$backup_dir/api-recovery-code.tar.gz" \
    app \
    pb_hooks \
    pb_migrations

tar -C / -czf "$backup_dir/runtime-config.tar.gz" \
    "etc/openkataster-backup.env" \
    "opt/openkataster-tiles/Caddyfile" \
    "opt/openkataster-tiles/docker-compose.yml" \
    "opt/openkataster-tiles/.env" \
    "opt/openkataster-api/docker-compose.yml" \
    "opt/openkataster-api/.env" \
    "etc/openkataster-secrets/pocketbase_admin_password" \
    "srv/openkataster-tiles/active/active" \
    "srv/openkataster-tiles/data/api_keys.json" \
    "srv/openkataster-tiles/data/deutschland.style.json" \
    2>/dev/null

git -C "$TILES_REPO" rev-parse HEAD > "$backup_dir/tiles-server.git-revision"
git -C "$API_REPO" rev-parse HEAD > "$backup_dir/api-server.git-revision"
docker ps --format '{{.Names}} {{.Image}} {{.Status}}' > "$backup_dir/docker-containers.txt"
df -h / /srv > "$backup_dir/disk-usage.txt"

(
    cd "$backup_dir"
    sha256sum ./* > SHA256SUMS
)
tar -tzf "$backup_dir/runtime-config.tar.gz" >/dev/null
tar -tzf "$backup_dir/api-recovery-code.tar.gz" >/dev/null
if [[ -f "$backup_dir/pocketbase-storage.tar.gz" ]]; then
    tar -tzf "$backup_dir/pocketbase-storage.tar.gz" >/dev/null
fi

restore_dir="$work_dir/restore"
mkdir -p "$restore_dir"
tar -xzf "$backup_dir/runtime-config.tar.gz" -C "$restore_dir"
[[ -f "$restore_dir/etc/openkataster-backup.env" ]]
[[ -f "$restore_dir/srv/openkataster-tiles/active/active/niedersachsen.json" ]]

offsite_file=""
if [[ -n "${OFFSITE_SFTP_TARGET:-}" ]]; then
    : "${OFFSITE_GPG_HOME:?OFFSITE_GPG_HOME fehlt}"
    : "${OFFSITE_GPG_RECIPIENT:?OFFSITE_GPG_RECIPIENT fehlt}"
    : "${OFFSITE_SSH_KEY:?OFFSITE_SSH_KEY fehlt}"

    compact_timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
    bundle_name="openkataster-core-$compact_timestamp.tar.gz"
    encrypted_name="$bundle_name.gpg"
    checksum_name="$encrypted_name.sha256"
    bundle="$work_dir/$bundle_name"
    encrypted="$work_dir/$encrypted_name"
    checksum="$work_dir/$checksum_name"
    sftp_batch="$work_dir/sftp.batch"

    tar -C "$BACKUP_ROOT" -czf "$bundle" "$(basename "$backup_dir")"
    gpg --batch --yes \
        --homedir "$OFFSITE_GPG_HOME" \
        --trust-model always \
        --recipient "$OFFSITE_GPG_RECIPIENT" \
        --output "$encrypted" \
        --encrypt "$bundle"
    (
        cd "$work_dir"
        sha256sum "$encrypted_name" > "$checksum_name"
    )

    {
        printf 'put %s %s.part\n' "$encrypted" "$encrypted_name"
        printf 'rename %s.part %s\n' "$encrypted_name" "$encrypted_name"
        printf 'put %s %s.part\n' "$checksum" "$checksum_name"
        printf 'rename %s.part %s\n' "$checksum_name" "$checksum_name"
    } > "$sftp_batch"

    sftp -q -b "$sftp_batch" \
        -i "$OFFSITE_SSH_KEY" \
        -o BatchMode=yes \
        -o IdentitiesOnly=yes \
        -o StrictHostKeyChecking=yes \
        "$OFFSITE_SFTP_TARGET"
    offsite_status="encrypted-uploaded"
    offsite_file="$encrypted_name"
elif [[ -n "${OFFSITE_RSYNC_TARGET:-}" ]]; then
    rsync -a --partial "$backup_dir/" "$OFFSITE_RSYNC_TARGET/$(hostname)/$timestamp/"
    offsite_status="synced"
else
    offsite_status="not-configured"
fi

find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d -mtime "+$KEEP_DAYS" -exec rm -rf {} +

cat > "$STATUS_DIR/status.env" <<EOF
BACKUP_STATUS=ok
BACKUP_TIMESTAMP=$timestamp
BACKUP_PATH=$backup_dir
OFFSITE_STATUS=$offsite_status
OFFSITE_FILE=$offsite_file
EOF
chmod 644 "$STATUS_DIR/status.env"
