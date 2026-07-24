#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=lib.sh
source "${SCRIPT_DIR}/lib.sh"

usage() {
  cat <<'EOF'
Usage:
  rollback-release.sh [Optionen wie activate-release.sh]

Rollt sicher auf ROOT/previous zurück. active und previous werden dabei
getauscht, sodass der Rollback selbst wieder rückgängig gemacht werden kann.
Hash, PMTiles-Schema, kontrollierter Restart, interne/öffentliche Smokes,
lsof +L1 und automatische Wiederherstellung bei Fehlern entsprechen exakt
der normalen Aktivierung.

Optionen:
  --root PATH
  --tools-root PATH
  --api-url URL
  --public-url URL | --no-public-smoke
  --runtime docker|systemd
  --container NAME | --service UNIT
  --dry-run
EOF
}

ROOT=$OK_EUROPE_DEFAULT_ROOT
API_URL=$OK_EUROPE_DEFAULT_API_URL

PASSTHROUGH=()
while (($# > 0)); do
  case "$1" in
    --root)
      (($# >= 2)) || ok_die "--root benötigt einen Wert."
      ROOT=$2
      PASSTHROUGH+=(--root "$2")
      shift 2
      ;;
    --tools-root)
      (($# >= 2)) || ok_die "--tools-root benötigt einen Wert."
      PASSTHROUGH+=(--tools-root "$2")
      shift 2
      ;;
    --api-url)
      (($# >= 2)) || ok_die "--api-url benötigt einen Wert."
      API_URL=${2%/}
      PASSTHROUGH+=(--api-url "$2")
      shift 2
      ;;
    --public-url)
      (($# >= 2)) || ok_die "--public-url benötigt einen Wert."
      PASSTHROUGH+=(--public-url "$2")
      shift 2
      ;;
    --no-public-smoke)
      PASSTHROUGH+=(--no-public-smoke)
      shift
      ;;
    --runtime)
      (($# >= 2)) || ok_die "--runtime benötigt einen Wert."
      PASSTHROUGH+=(--runtime "$2")
      shift 2
      ;;
    --container)
      (($# >= 2)) || ok_die "--container benötigt einen Wert."
      PASSTHROUGH+=(--container "$2")
      shift 2
      ;;
    --service)
      (($# >= 2)) || ok_die "--service benötigt einen Wert."
      PASSTHROUGH+=(--service "$2")
      shift 2
      ;;
    --dry-run)
      PASSTHROUGH+=(--dry-run)
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

ok_assert_safe_root "$ROOT"
ACTIVE=""
PREVIOUS=""
ok_capture_link_version "$ROOT" active ACTIVE
ok_capture_link_version "$ROOT" previous PREVIOUS
MODE=""
MODE_FILE_PRESENT=0
MODE_SOURCE=""
ok_capture_runtime_mode_state \
  "$ROOT" "$API_URL" MODE MODE_FILE_PRESENT MODE_SOURCE
[[ -n $ACTIVE ]] || ok_die "Kein aktiver Europe-Basemap-Release vorhanden."
[[ -n $PREVIOUS ]] || ok_die "Kein previous-Release für einen Rollback vorhanden."
[[ $ACTIVE != "$PREVIOUS" ]] || ok_die "active und previous zeigen auf dieselbe Version."

ok_log "Rollback-Plan: active=$ACTIVE -> $PREVIOUS; previous wird $ACTIVE; mode bleibt $MODE."
exec bash "${SCRIPT_DIR}/activate-release.sh" \
  --version "$PREVIOUS" \
  --mode "$MODE" \
  --expect-active "$ACTIVE" \
  --expect-previous "$PREVIOUS" \
  --expect-mode "$MODE" \
  "${PASSTHROUGH[@]}"
