#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=lib.sh
source "${SCRIPT_DIR}/lib.sh"

usage() {
  cat <<'EOF'
Usage:
  adopt-extract.sh --input PATH --build-date YYYYMMDD
                   --confirm-version europe-de-at-YYYYMMDD-z15
                   [--root PATH] [--tools-root PATH] [--dry-run]

Einmaliger, strikter Übernahmepfad für einen bereits vollständig geladenen
manuellen Extract unter ROOT/.incoming:

  ROOT/.incoming/europe-de-at-YYYYMMDD-z15.pmtiles.part

Das Kommando lädt nichts erneut herunter und aktiviert nichts. Es verweigert
die Übernahme, solange ein Prozess die Datei geöffnet hat. Danach prüft es:
  * exakt abgeleitete Protomaps-Daily-URL und Version,
  * pmtiles CLI 1.31.2 samt Binary-SHA,
  * verify, MVT/gzip, bbox, Zoom 0-15,
  * Protomaps-v4-Layerschema und OSM-Replikationsdatum,
  * vollständigen Datei-SHA und Manifest-Schema 1.

Erst nach allen Prüfungen wird die Datei innerhalb desselben Dateisystems
atomar als neue Version veröffentlicht. Bei einem Fehler bleibt die .part-
Datei erhalten beziehungsweise wird an ihren Ursprungsort zurückgestellt.
EOF
}

ROOT=$OK_EUROPE_DEFAULT_ROOT
TOOLS_ROOT=$OK_EUROPE_DEFAULT_TOOLS_ROOT
INPUT=""
BUILD_DATE=""
CONFIRM_VERSION=""
OK_DRY_RUN=0
WORK_PARENT=""
MOVED_INPUT=0
PUBLISHED=0

cleanup() {
  local exit_code=$?
  trap - EXIT
  if [[ $PUBLISHED != "1" && $MOVED_INPUT == "1" && -n $WORK_PARENT ]]; then
    if [[ -f ${WORK_PARENT}/${VERSION}/basemap.pmtiles && ! -e $INPUT ]]; then
      mv -T -- "${WORK_PARENT}/${VERSION}/basemap.pmtiles" "$INPUT" || true
    fi
  fi
  if [[ -n $WORK_PARENT && -d $WORK_PARENT ]]; then
    rm -rf -- "$WORK_PARENT"
  fi
  exit "$exit_code"
}
trap cleanup EXIT

while (($# > 0)); do
  case "$1" in
    --input)
      (($# >= 2)) || ok_die "--input benötigt einen Wert."
      INPUT=$2
      shift 2
      ;;
    --build-date)
      (($# >= 2)) || ok_die "--build-date benötigt einen Wert."
      BUILD_DATE=$2
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

[[ -n $INPUT ]] || ok_die "--input ist erforderlich."
[[ -n $BUILD_DATE ]] || ok_die "--build-date ist erforderlich."
ok_validate_build_date "$BUILD_DATE"
ok_assert_safe_root "$ROOT"
ok_assert_safe_root "$TOOLS_ROOT"
VERSION=$(ok_version_for_build_date "$BUILD_DATE")
SOURCE_URL=$(ok_source_url_for_build_date "$BUILD_DATE")
readonly VERSION SOURCE_URL
[[ $CONFIRM_VERSION == "$VERSION" ]] \
  || ok_die "--confirm-version muss exakt $VERSION lauten."
EXPECTED_INPUT="${ROOT}/.incoming/${VERSION}.pmtiles.part"
[[ $INPUT == "$EXPECTED_INPUT" ]] \
  || ok_die "--input muss exakt $EXPECTED_INPUT sein."

ok_require_command lsof
ok_require_command dd
ok_require_command python3
ok_require_command sha256sum
ok_require_command sync
ok_require_command wc
ok_acquire_lock "$ROOT"
ok_assert_version_capacity "$ROOT" "$VERSION" "$INPUT"

FINAL_RELEASE="${ROOT}/versions/${VERSION}"
if [[ -d $FINAL_RELEASE && ! -L $FINAL_RELEASE ]]; then
  bash "${SCRIPT_DIR}/verify-release.sh" \
    --release-dir "$FINAL_RELEASE" \
    --tools-root "$TOOLS_ROOT"
  ok_log "$VERSION wurde bereits erfolgreich übernommen."
  exit 0
fi

[[ -f $INPUT && ! -L $INPUT ]] || ok_die "Extract-Datei fehlt oder ist unsicher: $INPUT"
if lsof -- "$INPUT" 2>/dev/null | grep -q .; then
  ok_die "Extract-Datei ist noch geöffnet. Erst nach abgeschlossenem Download übernehmen."
fi
[[ $(wc -c <"$INPUT" | tr -d ' ') -ge 127 ]] \
  || ok_die "Extract-Datei ist zu klein und enthält keinen vollständigen PMTiles-Header."
[[ $(dd if="$INPUT" bs=7 count=1 status=none) == "PMTiles" ]] \
  || ok_die "Extract-Datei hat keinen gültigen PMTiles-Header. Eine fehlgeschlagene .part-Datei ist nicht übernehmbar und darf nicht repariert werden."

PMTILES="${TOOLS_ROOT}/${OK_PMTILES_VERSION}/pmtiles"
[[ -f $PMTILES && ! -L $PMTILES ]] \
  || ok_die "Gepinntes pmtiles-Binary fehlt: $PMTILES"
[[ $(ok_sha256_file "$PMTILES") == "$OK_PMTILES_BINARY_SHA256" ]] \
  || ok_die "pmtiles-Binary hat einen unerwarteten SHA-256."

if [[ $OK_DRY_RUN == "1" ]]; then
  ok_log "Dry-run: würde $INPUT strikt als $VERSION prüfen und ohne Aktivierung nach versions übernehmen."
  exit 0
fi

ok_require_command ionice
ok_require_command nice
mkdir -p -- "${ROOT}/.incoming" "${ROOT}/versions"
WORK_PARENT=$(mktemp -d "${ROOT}/.incoming/${VERSION}.adopt.XXXXXX")
WORK_RELEASE="${WORK_PARENT}/${VERSION}"
mkdir -- "$WORK_RELEASE"
HEADER_PATH="${WORK_PARENT}/header.json"
METADATA_PATH="${WORK_PARENT}/metadata.json"

ok_run_io_niced "$PMTILES" verify "$INPUT"
ok_run_io_niced "$PMTILES" show --header-json "$INPUT" >"$HEADER_PATH"
ok_run_io_niced "$PMTILES" show --metadata "$INPUT" >"$METADATA_PATH"
ok_run_io_niced python3 "${SCRIPT_DIR}/validate-release.py" create \
  --pmtiles "$INPUT" \
  --header-json "$HEADER_PATH" \
  --metadata-json "$METADATA_PATH" \
  --manifest "${WORK_RELEASE}/manifest.json" \
  --version "$VERSION" \
  --build-date "$BUILD_DATE" \
  --source-url "$SOURCE_URL" \
  --pmtiles-cli-version "$OK_PMTILES_VERSION" \
  --pmtiles-cli-sha256 "$OK_PMTILES_BINARY_SHA256"

rm -- "$HEADER_PATH" "$METADATA_PATH"
mv -T -- "$INPUT" "${WORK_RELEASE}/basemap.pmtiles"
MOVED_INPUT=1
bash "${SCRIPT_DIR}/verify-release.sh" \
  --release-dir "$WORK_RELEASE" \
  --tools-root "$TOOLS_ROOT" \
  --quick
ok_run_io_niced sync -f "${WORK_RELEASE}/basemap.pmtiles"
ok_run_io_niced sync -f "${WORK_RELEASE}/manifest.json"
ok_run_io_niced sync -f "$WORK_RELEASE"

[[ ! -e $FINAL_RELEASE && ! -L $FINAL_RELEASE ]] \
  || ok_die "Zielversion wurde während der Übernahme angelegt: $FINAL_RELEASE"
mv -T -- "$WORK_RELEASE" "$FINAL_RELEASE"
PUBLISHED=1
rmdir -- "$WORK_PARENT"
WORK_PARENT=""
ok_run_io_niced sync -f "${ROOT}/versions"

bash "${SCRIPT_DIR}/verify-release.sh" \
  --release-dir "$FINAL_RELEASE" \
  --tools-root "$TOOLS_ROOT" \
  --quick
ok_log "Extract übernommen und nicht aktiviert: $FINAL_RELEASE"
