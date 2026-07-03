#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$REPO_ROOT/viewer/deutschland"
DEST="/srv/openkataster-tiles/live-viewer/deutschland-v2"
OWNER="openkataster-tiles:openkataster-tiles"

if [[ ! -f "$SRC/index.html" || ! -f "$SRC/viewer.bundle.js" || ! -f "$SRC/bkg-style.json" ]]; then
  echo "viewer source is incomplete: $SRC" >&2
  exit 1
fi

install -d -m 0755 "$DEST"
rsync -a --delete --exclude '.DS_Store' "$SRC/" "$DEST/"
chown -R "$OWNER" "$DEST"
find "$DEST" -type d -exec chmod 0755 {} +
find "$DEST" -type f -exec chmod 0644 {} +

nginx -t
systemctl reload nginx

echo "deployed viewer to $DEST"
