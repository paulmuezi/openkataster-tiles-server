#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=lib.sh
source "${SCRIPT_DIR}/lib.sh"

usage() {
  cat <<'EOF'
Usage:
  set-mode.sh off|preview|on [--root PATH] [--tools-root PATH]
              [--api-url URL] [--public-url URL | --no-public-smoke]
              [--dry-run]

Mode:
  off      Nationale Grundkarten; Europe-Tiles serverseitig deaktiviert.
  preview  Nationale Grundkarten standardmäßig; ?basemap=europe für QA.
  on       Europe-Grundkarte standardmäßig; ?basemap=national als Fallback.

Die Datei ROOT/mode wird atomar ersetzt. Bei fehlgeschlagenen Config-/Tile-
Smokes wird der vorherige Modus automatisch wiederhergestellt.
EOF
}

ROOT=$OK_EUROPE_DEFAULT_ROOT
TOOLS_ROOT=$OK_EUROPE_DEFAULT_TOOLS_ROOT
API_URL=$OK_EUROPE_DEFAULT_API_URL
PUBLIC_URL=$OK_EUROPE_DEFAULT_PUBLIC_URL
OK_DRY_RUN=0
MODE=""

while (($# > 0)); do
  case "$1" in
    off|preview|on)
      [[ -z $MODE ]] || ok_die "Mehr als ein Modus angegeben."
      MODE=$1
      shift
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
    --public-url)
      (($# >= 2)) || ok_die "--public-url benötigt einen Wert."
      PUBLIC_URL=${2%/}
      shift 2
      ;;
    --no-public-smoke)
      PUBLIC_URL=""
      shift
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

[[ -n $MODE ]] || ok_die "Modus off|preview|on ist erforderlich."
ok_assert_safe_root "$ROOT"
ok_acquire_lock "$ROOT"
OLD_MODE=""
OLD_MODE_FILE_PRESENT=0
OLD_MODE_SOURCE=""
ok_capture_runtime_mode_state \
  "$ROOT" "$API_URL" OLD_MODE OLD_MODE_FILE_PRESENT OLD_MODE_SOURCE
ACTIVE=""
ok_capture_link_version "$ROOT" active ACTIVE

if [[ $MODE != "off" ]]; then
  [[ -n $ACTIVE ]] || ok_die "preview/on erfordert einen aktiven Release."
  bash "${SCRIPT_DIR}/verify-release.sh" \
    --release-dir "${ROOT}/versions/${ACTIVE}" \
    --tools-root "$TOOLS_ROOT" \
    --quick
fi

if [[ $OK_DRY_RUN == "1" ]]; then
  ok_atomic_mode "$ROOT" "$MODE"
  ok_log "Dry-run: würde interne und öffentliche Config-/Tile-Smokes für $MODE ausführen."
  exit 0
fi

smoke_mode() {
  local configured_mode=$1
  local active_version=$2
  local expected_mode=$configured_mode
  local -a smoke_args
  if [[ $configured_mode != "off" && -z $active_version ]]; then
    expected_mode="off"
  fi
  smoke_args=(
    --base-url "$API_URL"
    --expected-mode "$expected_mode"
    --expected-configured-mode "$configured_mode"
  )
  if [[ $expected_mode != "off" ]]; then
    smoke_args+=(--expected-version "$active_version")
  fi
  bash "${SCRIPT_DIR}/smoke.sh" "${smoke_args[@]}" || return 1
  if [[ -n $PUBLIC_URL && $PUBLIC_URL != "$API_URL" ]]; then
    smoke_args=(
      --base-url "$PUBLIC_URL"
      --expected-mode "$expected_mode"
      --expected-configured-mode "$configured_mode"
    )
    if [[ $expected_mode != "off" ]]; then
      smoke_args+=(--expected-version "$active_version")
    fi
    bash "${SCRIPT_DIR}/smoke.sh" "${smoke_args[@]}" || return 1
  fi
}

restore_mode() {
  local exit_code=$?
  local restore_failed=0
  trap - EXIT
  if [[ $exit_code == "0" ]]; then
    exit 0
  fi
  set +e
  ok_warn "Mode-Smoke fehlgeschlagen; stelle $OLD_MODE wieder her."
  ok_restore_mode_state "$ROOT" "$OLD_MODE" "$OLD_MODE_FILE_PRESENT" \
    || restore_failed=1
  if [[ $restore_failed == "0" ]]; then
    smoke_mode "$OLD_MODE" "$ACTIVE" || restore_failed=1
  fi
  if [[ $restore_failed == "1" ]]; then
    ok_warn "KRITISCH: Der vorherige Europe-Basemap-Modus konnte nicht bestätigt werden."
    exit 2
  fi
  ok_log "Vorheriger Europe-Basemap-Modus wurde wiederhergestellt und geprüft."
  exit "$exit_code"
}
trap restore_mode EXIT

ok_atomic_mode "$ROOT" "$MODE"
smoke_mode "$MODE" "$ACTIVE"

trap - EXIT
ok_log "Europe-Basemap-Modus ist jetzt $MODE."
