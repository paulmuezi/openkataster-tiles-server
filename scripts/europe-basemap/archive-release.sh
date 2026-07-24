#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=lib.sh
source "${SCRIPT_DIR}/lib.sh"

usage() {
  cat <<'EOF'
Usage:
  archive-release.sh --version europe-YYYYMMDD-z15 --confirm VERSION
                     --archive-root ABSOLUTE_PATH
                     [--root PATH] [--tools-root PATH] [--dry-run]

Es werden nie Versionen automatisch gelöscht. Dieses ausdrücklich bestätigte
Kommando kopiert eine nicht aktive Version auf ein ANDERES Dateisystem, prüft
die Kopie vollständig und veröffentlicht sie dort atomar. Erst danach wird die
Quelle aus ROOT/versions entfernt. Ein passender previous-Pointer wird
kontrolliert entfernt.

Ein Archiv auf demselben Dateisystem ist absichtlich verboten: Zwei Releases
in versions plus ein gleich großes lokales Archiv würden die 2-Versionen-
Kapazitätsgrenze umgehen.
EOF
}

ROOT=$OK_EUROPE_DEFAULT_ROOT
TOOLS_ROOT=$OK_EUROPE_DEFAULT_TOOLS_ROOT
ARCHIVE_ROOT=""
VERSION=""
CONFIRM=""
OK_DRY_RUN=0
COPY_PARENT=""

cleanup() {
  local exit_code=$?
  trap - EXIT
  if [[ -n $COPY_PARENT && -d $COPY_PARENT ]]; then
    rm -rf -- "$COPY_PARENT"
  fi
  exit "$exit_code"
}
trap cleanup EXIT

while (($# > 0)); do
  case "$1" in
    --version)
      (($# >= 2)) || ok_die "--version benötigt einen Wert."
      VERSION=$2
      shift 2
      ;;
    --confirm)
      (($# >= 2)) || ok_die "--confirm benötigt einen Wert."
      CONFIRM=$2
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
    --archive-root)
      (($# >= 2)) || ok_die "--archive-root benötigt einen Wert."
      ARCHIVE_ROOT=$2
      shift 2
      ;;
    --dry-run)
      OK_DRY_RUN=1
      shift
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
[[ $CONFIRM == "$VERSION" ]] \
  || ok_die "--confirm muss die vollständige Version exakt wiederholen."
[[ -n $ARCHIVE_ROOT ]] \
  || ok_die "--archive-root auf einem anderen Dateisystem ist erforderlich."
ok_assert_safe_root "$ROOT"
ok_assert_safe_root "$TOOLS_ROOT"
ok_assert_safe_root "$ARCHIVE_ROOT"
ok_require_command cp
ok_require_command ionice
ok_require_command lsof
ok_require_command nice
ok_require_command stat
ok_require_command sync
ok_acquire_lock "$ROOT"

ACTIVE=""
PREVIOUS=""
ok_capture_link_version "$ROOT" active ACTIVE
ok_capture_link_version "$ROOT" previous PREVIOUS
[[ $ACTIVE != "$VERSION" ]] || ok_die "Die aktive Version darf nicht archiviert werden."
SOURCE="${ROOT}/versions/${VERSION}"
TARGET="${ARCHIVE_ROOT}/${VERSION}"
[[ -d $SOURCE && ! -L $SOURCE ]] || ok_die "Version fehlt: $SOURCE"
[[ ! -e $TARGET && ! -L $TARGET ]] || ok_die "Archivziel existiert bereits: $TARGET"

EXISTING_ARCHIVE_PATH=$ARCHIVE_ROOT
while [[ ! -e $EXISTING_ARCHIVE_PATH ]]; do
  EXISTING_ARCHIVE_PATH=$(dirname -- "$EXISTING_ARCHIVE_PATH")
done
SOURCE_DEVICE=$(stat -c '%d' -- "$SOURCE")
ARCHIVE_DEVICE=$(stat -c '%d' -- "$EXISTING_ARCHIVE_PATH")
[[ $SOURCE_DEVICE != "$ARCHIVE_DEVICE" ]] \
  || ok_die "--archive-root muss auf einem anderen Dateisystem liegen."

if [[ $OK_DRY_RUN == "1" ]]; then
  ok_log "Dry-run: würde $SOURCE auf das externe Volume $ARCHIVE_ROOT kopieren, vollständig prüfen und erst danach die Quelle entfernen."
  if [[ $PREVIOUS == "$VERSION" ]]; then
    ok_log "Dry-run: würde previous kontrolliert entfernen."
  fi
  exit 0
fi

bash "${SCRIPT_DIR}/verify-release.sh" \
  --release-dir "$SOURCE" \
  --tools-root "$TOOLS_ROOT"
if lsof -- "${SOURCE}/basemap.pmtiles" 2>/dev/null | grep -q .; then
  ok_die "Die zu archivierende PMTiles-Datei ist noch geöffnet. Tiles-API kontrolliert neu starten und erneut prüfen."
fi
mkdir -p -- "${ARCHIVE_ROOT}/.incoming"
COPY_PARENT=$(mktemp -d "${ARCHIVE_ROOT}/.incoming/${VERSION}.copy.XXXXXX")
COPY_RELEASE="${COPY_PARENT}/${VERSION}"
mkdir -- "$COPY_RELEASE"
ok_run_io_niced cp --reflink=auto --sparse=always --preserve=mode,timestamps \
  "${SOURCE}/basemap.pmtiles" \
  "${SOURCE}/manifest.json" \
  "$COPY_RELEASE/"
ok_run_io_niced sync -f "${COPY_RELEASE}/basemap.pmtiles"
ok_run_io_niced sync -f "${COPY_RELEASE}/manifest.json"

bash "${SCRIPT_DIR}/verify-release.sh" \
  --release-dir "$COPY_RELEASE" \
  --tools-root "$TOOLS_ROOT"
mv -T -- "$COPY_RELEASE" "$TARGET"
rmdir -- "$COPY_PARENT"
COPY_PARENT=""
ok_run_io_niced sync -f "$ARCHIVE_ROOT"

if [[ $PREVIOUS == "$VERSION" ]]; then
  ok_remove_pointer "${ROOT}/previous"
fi
rm -- "${SOURCE}/basemap.pmtiles" "${SOURCE}/manifest.json"
rmdir -- "$SOURCE"
ok_run_io_niced sync -f "${ROOT}/versions"
ok_log "Version extern archiviert und geprüft: $TARGET"
