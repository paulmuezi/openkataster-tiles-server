#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=lib.sh
source "${SCRIPT_DIR}/lib.sh"

usage() {
  cat <<'EOF'
Usage:
  verify-release.sh --release-dir PATH [--tools-root PATH] [--quick]

Prüft Manifest, Dateigröße, PMTiles-Struktur, Header und Protomaps-v4-Schema.
Standardmäßig wird zusätzlich der vollständige SHA-256 des Archivs geprüft.
--quick überspringt nur den vollständigen Datei-Hash und ist für häufige,
read-only Statusprüfungen gedacht, nicht für Build, Aktivierung oder Rollback.
EOF
}

RELEASE_DIR=""
TOOLS_ROOT=$OK_EUROPE_DEFAULT_TOOLS_ROOT
QUICK=0
TEMP_DIR=""

cleanup() {
  local exit_code=$?
  if [[ -n $TEMP_DIR && -d $TEMP_DIR ]]; then
    rm -rf -- "$TEMP_DIR"
  fi
  exit "$exit_code"
}
trap cleanup EXIT

while (($# > 0)); do
  case "$1" in
    --release-dir)
      (($# >= 2)) || ok_die "--release-dir benötigt einen Wert."
      RELEASE_DIR=$2
      shift 2
      ;;
    --tools-root)
      (($# >= 2)) || ok_die "--tools-root benötigt einen Wert."
      TOOLS_ROOT=$2
      shift 2
      ;;
    --quick)
      QUICK=1
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

[[ -n $RELEASE_DIR ]] || ok_die "--release-dir ist erforderlich."
ok_assert_safe_root "$RELEASE_DIR"
ok_assert_safe_root "$TOOLS_ROOT"
ok_require_command python3
ok_require_command sha256sum
ok_require_command nice
ok_require_command ionice

PMTILES="${TOOLS_ROOT}/${OK_PMTILES_VERSION}/pmtiles"
[[ -f $PMTILES && ! -L $PMTILES ]] \
  || ok_die "Gepinntes pmtiles-Binary fehlt: $PMTILES"
[[ $(ok_sha256_file "$PMTILES") == "$OK_PMTILES_BINARY_SHA256" ]] \
  || ok_die "pmtiles-Binary hat einen unerwarteten SHA-256."

CHECK_ARGS=(check --release-dir "$RELEASE_DIR")
if [[ $QUICK == "1" ]]; then
  CHECK_ARGS+=(--skip-hash)
fi
ok_run_io_niced python3 "${SCRIPT_DIR}/validate-release.py" "${CHECK_ARGS[@]}"

ARCHIVE="${RELEASE_DIR}/basemap.pmtiles"
ok_run_io_niced "$PMTILES" verify "$ARCHIVE"
TEMP_DIR=$(mktemp -d)
ok_run_io_niced "$PMTILES" show --header-json "$ARCHIVE" >"${TEMP_DIR}/header.json"
ok_run_io_niced "$PMTILES" show --metadata "$ARCHIVE" >"${TEMP_DIR}/metadata.json"

INSPECT_ARGS=(
  inspect
  --release-dir "$RELEASE_DIR"
  --header-json "${TEMP_DIR}/header.json"
  --metadata-json "${TEMP_DIR}/metadata.json"
)
if [[ $QUICK == "1" ]]; then
  INSPECT_ARGS+=(--skip-hash)
else
  # check already completed the expensive hash; inspect may safely reuse its
  # result and focus on structure/schema.
  INSPECT_ARGS+=(--skip-hash)
fi
ok_run_io_niced python3 "${SCRIPT_DIR}/validate-release.py" "${INSPECT_ARGS[@]}"
ok_log "Release-Prüfung bestanden: $RELEASE_DIR"
