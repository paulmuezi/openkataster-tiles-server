#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=lib.sh
source "${SCRIPT_DIR}/lib.sh"

usage() {
  cat <<'EOF'
Usage:
  build-release.sh [--build-date YYYYMMDD] [--root PATH]
                   [--tools-root PATH] [--dry-run]

Erstellt einen reproduzierbaren Protomaps-v4-Extract für Deutschland und
Österreich mit einem kontrollierten Randpuffer:
  bbox      5,45.5,18,55.75
  zoom      0-15
  pmtiles   CLI 1.31.2 (geprüfter Binary-SHA-256)

Die Quelldatei wird nie als "latest" aufgelöst. Aus YYYYMMDD entsteht fest:
  https://build.protomaps.com/YYYYMMDD.pmtiles
  europe-de-at-YYYYMMDD-z15

Der Build wird unter ROOT/.incoming erzeugt, vollständig geprüft und erst
danach atomar nach ROOT/versions verschoben. Er aktiviert die Version nicht.
EOF
}

ROOT=$OK_EUROPE_DEFAULT_ROOT
TOOLS_ROOT=$OK_EUROPE_DEFAULT_TOOLS_ROOT
BUILD_DATE=$OK_EUROPE_DEFAULT_BUILD_DATE
OK_DRY_RUN=0
STAGING_DIR=""
STAGING_PARENT=""
INSTALL_STAGING_DIR=""

cleanup() {
  local exit_code=$?
  if [[ $OK_DRY_RUN != "1" ]]; then
    if [[ -n $STAGING_PARENT && -d $STAGING_PARENT ]]; then
      rm -rf -- "$STAGING_PARENT"
    elif [[ -n $STAGING_DIR && -d $STAGING_DIR ]]; then
      rm -rf -- "$STAGING_DIR"
    fi
    if [[ -n $INSTALL_STAGING_DIR && -d $INSTALL_STAGING_DIR ]]; then
      rm -rf -- "$INSTALL_STAGING_DIR"
    fi
  fi
  exit "$exit_code"
}
trap cleanup EXIT

while (($# > 0)); do
  case "$1" in
    --build-date)
      (($# >= 2)) || ok_die "--build-date benötigt einen Wert."
      BUILD_DATE=$2
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

ok_validate_build_date "$BUILD_DATE"
ok_assert_safe_root "$ROOT"
ok_assert_safe_root "$TOOLS_ROOT"
VERSION=$(ok_version_for_build_date "$BUILD_DATE")
SOURCE_URL=$(ok_source_url_for_build_date "$BUILD_DATE")
readonly VERSION SOURCE_URL

ok_require_command awk
ok_require_command curl
ok_require_command df
ok_require_command find
ok_require_command flock
ok_require_command ionice
ok_require_command nice
ok_require_command python3
ok_require_command sha256sum
ok_require_command sync
ok_require_command tar
ok_require_command uname

if [[ $(uname -s) != "Linux" || $(uname -m) != "x86_64" ]]; then
  ok_die "Der gepinnte pmtiles-Build ist ausschließlich für Linux x86_64."
fi

ok_preflight_disk "$ROOT"
ok_acquire_lock "$ROOT"
ok_assert_version_capacity "$ROOT" "$VERSION"

install_pmtiles() {
  local version_dir="${TOOLS_ROOT}/${OK_PMTILES_VERSION}"
  local binary="${version_dir}/pmtiles"
  local binary_hash
  local archive
  local extracted_binary

  if [[ -f $binary && ! -L $binary ]]; then
    binary_hash=$(ok_sha256_file "$binary")
    [[ $binary_hash == "$OK_PMTILES_BINARY_SHA256" ]] \
      || ok_die "Vorhandenes pmtiles-Binary hat einen unerwarteten SHA-256: $binary_hash"
    ok_log "Verwende geprüftes pmtiles ${OK_PMTILES_VERSION}: $binary"
  else
    if [[ -e $version_dir || -L $version_dir ]]; then
      ok_die "Unvollständige pmtiles-Installation vorhanden: $version_dir"
    fi
    if [[ $OK_DRY_RUN == "1" ]]; then
      ok_log "Dry-run: würde pmtiles ${OK_PMTILES_VERSION} von ${OK_PMTILES_RELEASE_URL} installieren."
      PMTILES=$binary
      return 0
    fi
    mkdir -p -- "${TOOLS_ROOT}/.incoming"
    INSTALL_STAGING_DIR=$(mktemp -d "${TOOLS_ROOT}/.incoming/pmtiles-${OK_PMTILES_VERSION}.XXXXXX")
    archive="${INSTALL_STAGING_DIR}/release.tar.gz"
    curl \
      --fail \
      --location \
      --retry 3 \
      --retry-all-errors \
      --connect-timeout 15 \
      --output "$archive" \
      "$OK_PMTILES_RELEASE_URL"
    [[ $(ok_sha256_file "$archive") == "$OK_PMTILES_RELEASE_SHA256" ]] \
      || ok_die "pmtiles-Releasearchiv hat einen unerwarteten SHA-256."
    tar -xzf "$archive" -C "$INSTALL_STAGING_DIR"
    extracted_binary="${INSTALL_STAGING_DIR}/pmtiles"
    [[ -f $extracted_binary && ! -L $extracted_binary ]] \
      || ok_die "pmtiles-Binary fehlt im Releasearchiv."
    [[ $(ok_sha256_file "$extracted_binary") == "$OK_PMTILES_BINARY_SHA256" ]] \
      || ok_die "Entpacktes pmtiles-Binary hat einen unerwarteten SHA-256."
    chmod 0755 "$extracted_binary"
    mv -T -- "$INSTALL_STAGING_DIR" "$version_dir"
    INSTALL_STAGING_DIR=""
    ok_log "pmtiles ${OK_PMTILES_VERSION} geprüft und installiert."
  fi

  ok_atomic_symlink "$OK_PMTILES_VERSION" "${TOOLS_ROOT}/current"
  PMTILES=$binary
}

PMTILES=""
install_pmtiles
readonly PMTILES

if [[ $OK_DRY_RUN != "1" ]]; then
  [[ $("$PMTILES" version 2>&1) == *"pmtiles ${OK_PMTILES_VERSION}"* ]] \
    || ok_die "Installiertes pmtiles meldet nicht Version ${OK_PMTILES_VERSION}."
fi

if [[ -d ${ROOT}/versions/${VERSION} && ! -L ${ROOT}/versions/${VERSION} ]]; then
  ok_log "Version $VERSION existiert bereits; prüfe idempotent."
  if [[ $OK_DRY_RUN == "1" ]]; then
    ok_format_command python3 "${SCRIPT_DIR}/validate-release.py" check \
      --release-dir "${ROOT}/versions/${VERSION}"
    exit 0
  fi
  bash "${SCRIPT_DIR}/verify-release.sh" \
    --release-dir "${ROOT}/versions/${VERSION}" \
    --tools-root "$TOOLS_ROOT"
  ok_log "Version $VERSION ist bereits vollständig und unverändert vorhanden."
  exit 0
fi

if [[ $OK_DRY_RUN == "1" ]]; then
  ok_log "Build-Plan für $VERSION:"
  ok_format_command nice -n 10 ionice -c 2 -n 7 "$PMTILES" extract \
    "$SOURCE_URL" "${ROOT}/.incoming/${VERSION}.build.<pid>/basemap.pmtiles" \
    "--bbox=${OK_EUROPE_BBOX}" \
    "--minzoom=${OK_EUROPE_MIN_ZOOM}" \
    "--maxzoom=${OK_EUROPE_MAX_ZOOM}" \
    "--download-threads=${OK_EUROPE_DOWNLOAD_THREADS}" \
    "--overfetch=${OK_EUROPE_OVERFETCH}"
  ok_log "Danach: verify, show --header-json, show --metadata, Schema-/Hashprüfung und atomare Veröffentlichung."
  exit 0
fi

mkdir -p -- "${ROOT}/.incoming" "${ROOT}/versions"
STAGING_PARENT=$(mktemp -d "${ROOT}/.incoming/${VERSION}.build.XXXXXX")
STAGING_DIR="${STAGING_PARENT}/${VERSION}"
mkdir -- "$STAGING_DIR"
PMTILES_PATH="${STAGING_DIR}/basemap.pmtiles"
HEADER_PATH="${STAGING_PARENT}/pmtiles-header.json"
METADATA_PATH="${STAGING_PARENT}/pmtiles-metadata.json"
MANIFEST_PATH="${STAGING_DIR}/manifest.json"
readonly PMTILES_PATH HEADER_PATH METADATA_PATH MANIFEST_PATH

ok_log "Extrahiere $VERSION aus der fest gepinnten Quelle $SOURCE_URL."
nice -n 10 ionice -c 2 -n 7 "$PMTILES" extract \
  "$SOURCE_URL" \
  "$PMTILES_PATH" \
  "--bbox=${OK_EUROPE_BBOX}" \
  "--minzoom=${OK_EUROPE_MIN_ZOOM}" \
  "--maxzoom=${OK_EUROPE_MAX_ZOOM}" \
  "--download-threads=${OK_EUROPE_DOWNLOAD_THREADS}" \
  "--overfetch=${OK_EUROPE_OVERFETCH}"

[[ -s $PMTILES_PATH && ! -L $PMTILES_PATH ]] \
  || ok_die "PMTiles-Extract fehlt oder ist leer."
ok_run_io_niced "$PMTILES" verify "$PMTILES_PATH"
ok_run_io_niced "$PMTILES" show --header-json "$PMTILES_PATH" >"$HEADER_PATH"
ok_run_io_niced "$PMTILES" show --metadata "$PMTILES_PATH" >"$METADATA_PATH"

ok_run_io_niced python3 "${SCRIPT_DIR}/validate-release.py" create \
  --pmtiles "$PMTILES_PATH" \
  --header-json "$HEADER_PATH" \
  --metadata-json "$METADATA_PATH" \
  --manifest "$MANIFEST_PATH" \
  --version "$VERSION" \
  --build-date "$BUILD_DATE" \
  --source-url "$SOURCE_URL" \
  --pmtiles-cli-version "$OK_PMTILES_VERSION" \
  --pmtiles-cli-sha256 "$OK_PMTILES_BINARY_SHA256"

ok_run_io_niced python3 "${SCRIPT_DIR}/validate-release.py" inspect \
  --release-dir "$STAGING_DIR" \
  --header-json "$HEADER_PATH" \
  --metadata-json "$METADATA_PATH" \
  --skip-hash

# Header and full metadata are build-time validation inputs. The immutable
# runtime stays deliberately small and contains only archive plus manifest.
rm -- "$HEADER_PATH" "$METADATA_PATH"
ok_run_io_niced sync -f "$PMTILES_PATH"
ok_run_io_niced sync -f "$MANIFEST_PATH"
ok_run_io_niced sync -f "$STAGING_DIR"

if [[ -e ${ROOT}/versions/${VERSION} || -L ${ROOT}/versions/${VERSION} ]]; then
  ok_die "Zielversion wurde während des Builds angelegt: ${ROOT}/versions/${VERSION}"
fi
mv -T -- "$STAGING_DIR" "${ROOT}/versions/${VERSION}"
STAGING_DIR=""
rmdir -- "$STAGING_PARENT"
STAGING_PARENT=""
ok_run_io_niced sync -f "${ROOT}/versions"

ok_run_io_niced python3 "${SCRIPT_DIR}/validate-release.py" check \
  --release-dir "${ROOT}/versions/${VERSION}" \
  --skip-hash
ok_log "Build $VERSION ist geprüft und bereit, aber noch nicht aktiviert."
