#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=lib.sh
source "${SCRIPT_DIR}/lib.sh"

usage() {
  cat <<'EOF'
Usage:
  launch-build-systemd.sh [--build-date YYYYMMDD] [--root PATH]
                          [--tools-root PATH] [--dry-run]

Startet build-release.sh als transiente systemd-Unit und kehrt sofort zurück.
Die Unit ist absichtlich gedrosselt:
  Nice=10, CPUWeight=20, IOWeight=20, MemoryMax=8G
Netzwerkzugriffe sind auf IPv4 beschränkt. Bei einem transienten Fehler sind
höchstens drei vollständige Versuche erlaubt (kein Resume einer .part-Datei).

Status und Log:
  systemctl status UNIT
  journalctl -fu UNIT

Die eigentliche Build-Logik, der exklusive Lock und sämtliche Validierungen
bleiben in build-release.sh. Die SanDisk wird nicht verwendet.
EOF
}

ROOT=$OK_EUROPE_DEFAULT_ROOT
TOOLS_ROOT=$OK_EUROPE_DEFAULT_TOOLS_ROOT
BUILD_DATE=$OK_EUROPE_DEFAULT_BUILD_DATE
OK_DRY_RUN=0

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
ok_require_command date
ok_require_command systemd-run

UNIT="openkataster-europe-basemap-build-${BUILD_DATE}-$(date -u +%Y%m%d%H%M%S)"
COMMAND=(
  /usr/bin/env bash
  "${SCRIPT_DIR}/build-release.sh"
  --build-date "$BUILD_DATE"
  --root "$ROOT"
  --tools-root "$TOOLS_ROOT"
)

if [[ $OK_DRY_RUN == "1" ]]; then
  ok_format_command systemd-run \
    "--unit=${UNIT}" \
    "--description=OpenKataster Europe basemap ${BUILD_DATE}" \
    "--property=Type=exec" \
    "--property=Nice=10" \
    "--property=CPUWeight=20" \
    "--property=IOWeight=20" \
    "--property=MemoryMax=8G" \
    "--property=RestrictAddressFamilies=AF_INET AF_UNIX" \
    "--property=Restart=on-failure" \
    "--property=RestartSec=30s" \
    "--property=StartLimitIntervalSec=86400s" \
    "--property=StartLimitBurst=3" \
    "--property=TimeoutStartSec=infinity" \
    --collect \
    --no-block \
    "${COMMAND[@]}"
  exit 0
fi

systemd-run \
  "--unit=${UNIT}" \
  "--description=OpenKataster Europe basemap ${BUILD_DATE}" \
  "--property=Type=exec" \
  "--property=Nice=10" \
  "--property=CPUWeight=20" \
  "--property=IOWeight=20" \
  "--property=MemoryMax=8G" \
  "--property=RestrictAddressFamilies=AF_INET AF_UNIX" \
  "--property=Restart=on-failure" \
  "--property=RestartSec=30s" \
  "--property=StartLimitIntervalSec=86400s" \
  "--property=StartLimitBurst=3" \
  "--property=TimeoutStartSec=infinity" \
  --collect \
  --no-block \
  "${COMMAND[@]}"
ok_log "Build gestartet: $UNIT"
ok_log "Fortschritt: journalctl -fu $UNIT"
