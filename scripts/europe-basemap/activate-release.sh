#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=lib.sh
source "${SCRIPT_DIR}/lib.sh"

usage() {
  cat <<'EOF'
Usage:
  activate-release.sh --version europe-YYYYMMDD-z15
      [--mode preview|on|off]
      [--root PATH] [--tools-root PATH]
      [--api-url URL] [--public-url URL | --no-public-smoke]
      [--runtime docker|systemd]
      [--container NAME | --service UNIT]
      [--expect-active VERSION|none] [--expect-previous VERSION|none]
      [--expect-mode preview|on|off]
      [--dry-run]

Die Aktivierung:
  1. prüft Hash, PMTiles-Struktur und Protomaps-v4-Schema vollständig,
  2. setzt previous und active jeweils atomar,
  3. startet ausschließlich die Tiles-API kontrolliert neu,
  4. prüft intern und öffentlich Health, Config und einen echten Tile,
  5. prüft mit lsof +L1 auf gelöschte, noch offene Runtime-Dateien.

Bei jedem Fehler nach Beginn der Umschaltung werden Pointer und Modus
automatisch auf den vorherigen Stand zurückgesetzt und die Tiles-API erneut
gestartet. Standardmodus nach einer neuen Aktivierung ist preview.
EOF
}

ROOT=$OK_EUROPE_DEFAULT_ROOT
TOOLS_ROOT=$OK_EUROPE_DEFAULT_TOOLS_ROOT
API_URL=$OK_EUROPE_DEFAULT_API_URL
PUBLIC_URL=$OK_EUROPE_DEFAULT_PUBLIC_URL
RUNTIME="docker"
CONTAINER=$OK_EUROPE_DEFAULT_CONTAINER
SERVICE="openkataster-tiles-api.service"
VERSION=""
FINAL_MODE="preview"
EXPECT_ACTIVE=""
EXPECT_PREVIOUS=""
EXPECT_MODE=""
OK_DRY_RUN=0

TRANSACTION_STARTED=0
COMMITTED=0
OLD_ACTIVE=""
OLD_PREVIOUS=""
OLD_MODE="off"
OLD_MODE_FILE_PRESENT=0

restart_runtime() {
  local deadline
  local health_status
  case "$RUNTIME" in
    docker)
      ok_require_command docker
      ok_run docker inspect "$CONTAINER" >/dev/null
      ok_run docker restart --time 30 "$CONTAINER" >/dev/null
      if [[ $OK_DRY_RUN == "1" ]]; then
        return 0
      fi
      deadline=$((SECONDS + 120))
      while ((SECONDS < deadline)); do
        health_status=$(docker inspect \
          --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{if .State.Running}}running{{else}}stopped{{end}}{{end}}' \
          "$CONTAINER" 2>/dev/null || true)
        if [[ $health_status == "healthy" || $health_status == "running" ]]; then
          return 0
        fi
        [[ $health_status != "unhealthy" && $health_status != "stopped" ]] \
          || return 1
        sleep 2
      done
      return 1
      ;;
    systemd)
      ok_require_command systemctl
      ok_run systemctl restart "$SERVICE"
      if [[ $OK_DRY_RUN == "1" ]]; then
        return 0
      fi
      systemctl is-active --quiet "$SERVICE"
      ;;
    *)
      ok_die "Unbekannte Runtime: $RUNTIME"
      ;;
  esac
}

smoke_all() {
  local mode=$1
  local expected_version=${2:-$VERSION}
  local configured_mode=${3:-$mode}
  local -a args=(
    --base-url "$API_URL"
    --expected-mode "$mode"
    --expected-configured-mode "$configured_mode"
  )
  if [[ $mode != "off" ]]; then
    args+=(--expected-version "$expected_version")
  fi
  bash "${SCRIPT_DIR}/smoke.sh" "${args[@]}" || return 1
  if [[ -n $PUBLIC_URL && $PUBLIC_URL != "$API_URL" ]]; then
    args=(
      --base-url "$PUBLIC_URL"
      --expected-mode "$mode"
      --expected-configured-mode "$configured_mode"
    )
    if [[ $mode != "off" ]]; then
      args+=(--expected-version "$expected_version")
    fi
    bash "${SCRIPT_DIR}/smoke.sh" "${args[@]}" || return 1
  fi
}

assert_no_deleted_runtime_files() {
  local deleted
  ok_require_command lsof
  deleted=$(lsof -nP +L1 2>/dev/null | grep -F -- "${ROOT}/" || true)
  if [[ -n $deleted ]]; then
    ok_warn "Gelöschte Runtime-Dateien sind noch geöffnet:"
    printf '%s\n' "$deleted" >&2
    return 1
  fi
}

restore_old_state() {
  local restore_failed=0
  set +e
  ok_warn "Aktivierung fehlgeschlagen; stelle den vorherigen Zustand wieder her."
  if [[ -n $OLD_ACTIVE ]]; then
    ok_atomic_symlink "versions/${OLD_ACTIVE}" "${ROOT}/active" || restore_failed=1
  else
    ok_remove_pointer "${ROOT}/active" || restore_failed=1
  fi
  if [[ -n $OLD_PREVIOUS ]]; then
    ok_atomic_symlink "versions/${OLD_PREVIOUS}" "${ROOT}/previous" || restore_failed=1
  else
    ok_remove_pointer "${ROOT}/previous" || restore_failed=1
  fi
  ok_restore_mode_state "$ROOT" "$OLD_MODE" "$OLD_MODE_FILE_PRESENT" \
    || restore_failed=1
  restart_runtime || restore_failed=1
  if [[ $restore_failed == "0" ]]; then
    if [[ $OLD_MODE == "off" || -z $OLD_ACTIVE ]]; then
      smoke_all off "" "$OLD_MODE" || restore_failed=1
    else
      smoke_all "$OLD_MODE" "$OLD_ACTIVE" "$OLD_MODE" || restore_failed=1
    fi
  fi
  if [[ $restore_failed == "1" ]]; then
    ok_warn "KRITISCH: Der automatische Rollback war nicht vollständig erfolgreich."
  else
    ok_log "Vorheriger Runtime-Zustand wurde wiederhergestellt."
  fi
  set -e
}

on_exit() {
  local exit_code=$?
  trap - EXIT
  if [[ $exit_code != "0" && $TRANSACTION_STARTED == "1" && $COMMITTED != "1" ]]; then
    restore_old_state
  fi
  exit "$exit_code"
}
trap on_exit EXIT

while (($# > 0)); do
  case "$1" in
    --version)
      (($# >= 2)) || ok_die "--version benötigt einen Wert."
      VERSION=$2
      shift 2
      ;;
    --mode)
      (($# >= 2)) || ok_die "--mode benötigt einen Wert."
      FINAL_MODE=$2
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
    --public-url)
      (($# >= 2)) || ok_die "--public-url benötigt einen Wert."
      PUBLIC_URL=${2%/}
      shift 2
      ;;
    --no-public-smoke)
      PUBLIC_URL=""
      shift
      ;;
    --runtime)
      (($# >= 2)) || ok_die "--runtime benötigt einen Wert."
      RUNTIME=$2
      shift 2
      ;;
    --container)
      (($# >= 2)) || ok_die "--container benötigt einen Wert."
      CONTAINER=$2
      shift 2
      ;;
    --service)
      (($# >= 2)) || ok_die "--service benötigt einen Wert."
      SERVICE=$2
      shift 2
      ;;
    --expect-active)
      (($# >= 2)) || ok_die "--expect-active benötigt einen Wert."
      EXPECT_ACTIVE=$2
      shift 2
      ;;
    --expect-previous)
      (($# >= 2)) || ok_die "--expect-previous benötigt einen Wert."
      EXPECT_PREVIOUS=$2
      shift 2
      ;;
    --expect-mode)
      (($# >= 2)) || ok_die "--expect-mode benötigt einen Wert."
      EXPECT_MODE=$2
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
[[ $FINAL_MODE == "off" || $FINAL_MODE == "preview" || $FINAL_MODE == "on" ]] \
  || ok_die "--mode muss off, preview oder on sein."
[[ $RUNTIME == "docker" || $RUNTIME == "systemd" ]] \
  || ok_die "--runtime muss docker oder systemd sein."
ok_assert_safe_root "$ROOT"
ok_assert_safe_root "$TOOLS_ROOT"
ok_acquire_lock "$ROOT"

VERSION_COUNT=$(ok_count_versions "$ROOT")
((VERSION_COUNT <= OK_EUROPE_MAX_VERSIONS)) \
  || ok_die "Es sind $VERSION_COUNT Versionen vorhanden; maximal $OK_EUROPE_MAX_VERSIONS sind zulässig."
RELEASE_DIR="${ROOT}/versions/${VERSION}"
[[ -d $RELEASE_DIR && ! -L $RELEASE_DIR ]] \
  || ok_die "Release-Verzeichnis fehlt oder ist unsicher: $RELEASE_DIR"

ok_capture_link_version "$ROOT" active OLD_ACTIVE
ok_capture_link_version "$ROOT" previous OLD_PREVIOUS
ok_capture_runtime_mode_state \
  "$ROOT" "$API_URL" OLD_MODE OLD_MODE_FILE_PRESENT

if [[ -n $EXPECT_ACTIVE ]]; then
  if [[ $EXPECT_ACTIVE == "none" ]]; then
    [[ -z $OLD_ACTIVE ]] || ok_die "active hat sich geändert; erwartet war kein Pointer."
  else
    ok_validate_version "$EXPECT_ACTIVE"
    [[ $OLD_ACTIVE == "$EXPECT_ACTIVE" ]] \
      || ok_die "active hat sich geändert: $OLD_ACTIVE (erwartet $EXPECT_ACTIVE)."
  fi
fi
if [[ -n $EXPECT_PREVIOUS ]]; then
  if [[ $EXPECT_PREVIOUS == "none" ]]; then
    [[ -z $OLD_PREVIOUS ]] || ok_die "previous hat sich geändert; erwartet war kein Pointer."
  else
    ok_validate_version "$EXPECT_PREVIOUS"
    [[ $OLD_PREVIOUS == "$EXPECT_PREVIOUS" ]] \
      || ok_die "previous hat sich geändert: $OLD_PREVIOUS (erwartet $EXPECT_PREVIOUS)."
  fi
fi
if [[ -n $EXPECT_MODE ]]; then
  [[ $EXPECT_MODE == "off" || $EXPECT_MODE == "preview" || $EXPECT_MODE == "on" ]] \
    || ok_die "--expect-mode muss off, preview oder on sein."
  [[ $OLD_MODE == "$EXPECT_MODE" ]] \
    || ok_die "mode hat sich geändert: $OLD_MODE (erwartet $EXPECT_MODE)."
fi

if [[ $OK_DRY_RUN == "1" ]]; then
  bash "${SCRIPT_DIR}/verify-release.sh" \
    --release-dir "$RELEASE_DIR" \
    --tools-root "$TOOLS_ROOT" \
    --quick
  ok_log "Dry-run Aktivierung: ${OLD_ACTIVE:-none} -> $VERSION, previous -> ${OLD_ACTIVE:-none}, Modus -> $FINAL_MODE."
  ok_log "Dry-run: würde Tiles-API neu starten sowie interne/öffentliche Smokes und lsof +L1 prüfen."
  exit 0
fi

bash "${SCRIPT_DIR}/verify-release.sh" \
  --release-dir "$RELEASE_DIR" \
  --tools-root "$TOOLS_ROOT"

TRANSACTION_STARTED=1
ok_atomic_mode "$ROOT" preview
if [[ $OLD_ACTIVE != "$VERSION" ]]; then
  if [[ -n $OLD_ACTIVE ]]; then
    ok_atomic_symlink "versions/${OLD_ACTIVE}" "${ROOT}/previous"
  else
    ok_remove_pointer "${ROOT}/previous"
  fi
  ok_atomic_symlink "versions/${VERSION}" "${ROOT}/active"
fi

restart_runtime || ok_die "Tiles-API wurde nach der Aktivierung nicht gesund."
smoke_all preview
assert_no_deleted_runtime_files \
  || ok_die "Tiles-API hält gelöschte Runtime-Dateien offen."

ok_atomic_mode "$ROOT" "$FINAL_MODE"
smoke_all "$FINAL_MODE"

COMMITTED=1
ok_log "Aktivierung abgeschlossen: active=$VERSION, previous=${OLD_ACTIVE:-none}, mode=$FINAL_MODE."
