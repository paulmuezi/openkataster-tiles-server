#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=lib.sh
source "${SCRIPT_DIR}/lib.sh"

usage() {
  cat <<'EOF'
Usage:
  migrate-legacy-manifest.sh
    --version europe[-de-at]-YYYYMMDD-z15
    --confirm-version europe[-de-at]-YYYYMMDD-z15
    [--root PATH] [--tools-root PATH] [--api-url URL]

Einmalige, kontrollierte Migration eines noch nie aktivierten schema-1-
Manifests: Das vorhandene PMTiles-Archiv wird mit gepinntem Werkzeug geprüft,
vollständig gehasht und aus echten Header-/Metadaten neu beschrieben. Das alte
Manifest wird außerhalb des Release-Verzeichnisses gesichert und atomar
ersetzt. Der Vorgang ist nur im live bestätigten Modus off und für eine weder
aktive noch als previous referenzierte Version zulässig.
EOF
}

ROOT=$OK_EUROPE_DEFAULT_ROOT
TOOLS_ROOT=$OK_EUROPE_DEFAULT_TOOLS_ROOT
API_URL=$OK_EUROPE_DEFAULT_API_URL
VERSION=""
CONFIRM_VERSION=""
TEMP_PARENT=""
BACKUP_PATH=""
MANIFEST_REPLACED=0

restore_on_error() {
  local exit_code=$?
  local restore_temp
  trap - EXIT
  if [[ $exit_code != "0" && $MANIFEST_REPLACED == "1" ]]; then
    set +e
    restore_temp="${RELEASE_DIR}/.manifest.json.restore.$$"
    install -m 0644 -- "$BACKUP_PATH" "$restore_temp"
    if ! mv -Tf -- "$restore_temp" "${RELEASE_DIR}/manifest.json"; then
      ok_warn "KRITISCH: Das alte Manifest konnte nicht automatisch wiederhergestellt werden."
      exit_code=2
    else
      ok_warn "Migration fehlgeschlagen; das alte Manifest wurde atomar wiederhergestellt."
    fi
  fi
  if [[ -n $TEMP_PARENT && -d $TEMP_PARENT ]]; then
    rm -rf -- "$TEMP_PARENT"
  fi
  exit "$exit_code"
}
trap restore_on_error EXIT

while (($# > 0)); do
  case "$1" in
    --version)
      (($# >= 2)) || ok_die "--version benötigt einen Wert."
      VERSION=$2
      shift 2
      ;;
    --confirm-version)
      (($# >= 2)) || ok_die "--confirm-version benötigt einen Wert."
      CONFIRM_VERSION=$2
      shift 2
      ;;
    --root)
      (($# >= 2)) || ok_die "--root benötigt einen Wert."
      ROOT=$2
      shift 2
      ;;
    --tools-root)
      (($# >= 2)) || ok_die "--tools-root benötigt einen Wert."
      TOOLS_ROOT=$2
      shift 2
      ;;
    --api-url)
      (($# >= 2)) || ok_die "--api-url benötigt einen Wert."
      API_URL=${2%/}
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      ok_die "Unbekannte Option: $1"
      ;;
  esac
done

[[ -n $VERSION ]] || ok_die "--version ist erforderlich."
ok_validate_version "$VERSION"
[[ $CONFIRM_VERSION == "$VERSION" ]] \
  || ok_die "--confirm-version muss die vollständige Version exakt wiederholen."
ok_assert_safe_root "$ROOT"
ok_assert_safe_root "$TOOLS_ROOT"
ok_require_command install
ok_require_command ionice
ok_require_command nice
ok_require_command python3
ok_require_command sha256sum
ok_require_command sync
ok_acquire_lock "$ROOT"

ACTIVE=""
PREVIOUS=""
MODE=""
ok_capture_link_version "$ROOT" active ACTIVE
ok_capture_link_version "$ROOT" previous PREVIOUS
ok_capture_runtime_mode_state "$ROOT" "$API_URL" MODE
[[ $MODE == "off" ]] || ok_die "Manifestmigration ist ausschließlich im live bestätigten Modus off erlaubt."
[[ $ACTIVE != "$VERSION" ]] || ok_die "Das Manifest einer aktiven Version darf nicht migriert werden."
[[ $PREVIOUS != "$VERSION" ]] || ok_die "Das Manifest einer previous-Version darf nicht migriert werden."

RELEASE_DIR="${ROOT}/versions/${VERSION}"
MANIFEST_PATH="${RELEASE_DIR}/manifest.json"
ARCHIVE_PATH="${RELEASE_DIR}/basemap.pmtiles"
[[ -d $RELEASE_DIR && ! -L $RELEASE_DIR ]] \
  || ok_die "Release-Verzeichnis fehlt oder ist unsicher: $RELEASE_DIR"
[[ -f $MANIFEST_PATH && ! -L $MANIFEST_PATH ]] \
  || ok_die "Legacy-Manifest fehlt oder ist unsicher: $MANIFEST_PATH"
[[ -f $ARCHIVE_PATH && ! -L $ARCHIVE_PATH ]] \
  || ok_die "PMTiles-Archiv fehlt oder ist unsicher: $ARCHIVE_PATH"

OLD_SHA=$(python3 - "$MANIFEST_PATH" "$VERSION" <<'PY'
import json
import re
import sys
from pathlib import Path

path, version = sys.argv[1:]
payload = json.loads(Path(path).read_text(encoding="utf-8"))
if payload.get("schema_version") != 1:
    raise SystemExit("Legacy-Manifest hat nicht schema_version=1")
if payload.get("version") != version or payload.get("pmtiles") != "basemap.pmtiles":
    raise SystemExit("Legacy-Manifest gehört nicht zur bestätigten Version")
if payload.get("attribution") != "© OpenStreetMap contributors":
    raise SystemExit("Manifest ist kein erwartetes Legacy-Manifest")
provenance = payload.get("provenance")
if not isinstance(provenance, dict):
    raise SystemExit("Legacy-Provenienz fehlt")
if "data_licenses" in provenance or "vector_layers" in provenance:
    raise SystemExit("Manifest ist bereits migriert oder hat einen unbekannten Zwischenstand")
digest = str(payload.get("sha256", "")).lower()
if re.fullmatch(r"[a-f0-9]{64}", digest) is None:
    raise SystemExit("Legacy-Manifest enthält keinen gültigen SHA-256")
if int(payload.get("size_bytes", 0)) != Path(path).with_name("basemap.pmtiles").stat().st_size:
    raise SystemExit("Legacy-Manifestgröße stimmt nicht mit dem Archiv überein")
print(digest)
PY
)

PMTILES="${TOOLS_ROOT}/${OK_PMTILES_VERSION}/pmtiles"
[[ -f $PMTILES && ! -L $PMTILES ]] \
  || ok_die "Gepinntes pmtiles-Binary fehlt: $PMTILES"
[[ $(ok_sha256_file "$PMTILES") == "$OK_PMTILES_BINARY_SHA256" ]] \
  || ok_die "pmtiles-Binary hat einen unerwarteten SHA-256."

mkdir -p -- "${ROOT}/.incoming" "${ROOT}/manifest-backups"
chmod 0700 "${ROOT}/manifest-backups"
TEMP_PARENT=$(mktemp -d "${ROOT}/.incoming/${VERSION}.manifest-migration.XXXXXX")
HEADER_PATH="${TEMP_PARENT}/header.json"
METADATA_PATH="${TEMP_PARENT}/metadata.json"
NEW_MANIFEST="${TEMP_PARENT}/manifest.json"

ok_run_io_niced "$PMTILES" verify "$ARCHIVE_PATH"
ok_run_io_niced "$PMTILES" show --header-json "$ARCHIVE_PATH" >"$HEADER_PATH"
ok_run_io_niced "$PMTILES" show --metadata "$ARCHIVE_PATH" >"$METADATA_PATH"
BUILD_DATE=$(ok_build_date_for_version "$VERSION")
SOURCE_URL=$(ok_source_url_for_build_date "$BUILD_DATE")
ok_run_io_niced python3 "${SCRIPT_DIR}/validate-release.py" create \
  --pmtiles "$ARCHIVE_PATH" \
  --header-json "$HEADER_PATH" \
  --metadata-json "$METADATA_PATH" \
  --manifest "$NEW_MANIFEST" \
  --version "$VERSION" \
  --build-date "$BUILD_DATE" \
  --source-url "$SOURCE_URL" \
  --pmtiles-cli-version "$OK_PMTILES_VERSION" \
  --pmtiles-cli-sha256 "$OK_PMTILES_BINARY_SHA256"

NEW_SHA=$(python3 - "$NEW_MANIFEST" <<'PY'
import json
import sys
from pathlib import Path
print(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))["sha256"])
PY
)
[[ $NEW_SHA == "$OLD_SHA" ]] \
  || ok_die "Neu berechneter Archiv-Hash stimmt nicht mit dem Legacy-Manifest überein."

TIMESTAMP=$(date -u +%Y%m%dT%H%M%SZ)
BACKUP_PATH="${ROOT}/manifest-backups/${VERSION}.pre-data-license-${TIMESTAMP}.json"
[[ ! -e $BACKUP_PATH && ! -L $BACKUP_PATH ]] \
  || ok_die "Manifest-Backup existiert bereits: $BACKUP_PATH"
install -m 0600 -- "$MANIFEST_PATH" "$BACKUP_PATH"
[[ $(ok_sha256_file "$BACKUP_PATH") == "$(ok_sha256_file "$MANIFEST_PATH")" ]] \
  || ok_die "Manifest-Backup konnte nicht verifiziert werden."

NEW_MANIFEST_IN_RELEASE="${RELEASE_DIR}/.manifest.json.new.$$"
install -m 0644 -- "$NEW_MANIFEST" "$NEW_MANIFEST_IN_RELEASE"
mv -Tf -- "$NEW_MANIFEST_IN_RELEASE" "$MANIFEST_PATH"
MANIFEST_REPLACED=1
ok_run_io_niced sync -f "$MANIFEST_PATH"
ok_run_io_niced sync -f "$RELEASE_DIR"

# create hat das Archiv bereits vollständig gehasht. Die Abschlussprüfung
# wiederholt Struktur, Header, Metadaten und Lizenzinventar ohne zweiten Hash.
bash "${SCRIPT_DIR}/verify-release.sh" \
  --release-dir "$RELEASE_DIR" \
  --tools-root "$TOOLS_ROOT" \
  --quick

MANIFEST_REPLACED=0
rm -rf -- "$TEMP_PARENT"
TEMP_PARENT=""
trap - EXIT
ok_log "Legacy-Manifest vollständig geprüft und migriert: $MANIFEST_PATH"
ok_log "Unverändertes Backup: $BACKUP_PATH"
