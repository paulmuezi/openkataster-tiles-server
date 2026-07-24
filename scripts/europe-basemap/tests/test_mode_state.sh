#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

TEST_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
SCRIPT_DIR="$(cd -- "${TEST_DIR}/.." && pwd -P)"
# shellcheck source=lib.sh
source "${SCRIPT_DIR}/lib.sh"

TEMP_ROOT=$(mktemp -d)
cleanup() {
  rm -rf -- "$TEMP_ROOT"
}
trap cleanup EXIT

RUNTIME="${TEMP_ROOT}/runtime"
mkdir -p -- "$RUNTIME"

export OPENKATASTER_EUROPE_BASEMAP_MODE=" On "
MODE=""
FILE_PRESENT=""
ok_capture_mode_state "$RUNTIME" MODE FILE_PRESENT
[[ $MODE == "on" && $FILE_PRESENT == "0" ]]

ok_atomic_mode "$RUNTIME" preview
[[ $(<"${RUNTIME}/mode") == "preview" ]]
ok_restore_mode_state "$RUNTIME" "$MODE" "$FILE_PRESENT"
[[ ! -e ${RUNTIME}/mode && ! -L ${RUNTIME}/mode ]]
[[ $(ok_read_mode "$RUNTIME") == "on" ]]

unset OPENKATASTER_EUROPE_BASEMAP_MODE
ok_atomic_mode "$RUNTIME" off
ok_capture_mode_state "$RUNTIME" MODE FILE_PRESENT
[[ $MODE == "off" && $FILE_PRESENT == "1" ]]
ok_atomic_mode "$RUNTIME" on
ok_restore_mode_state "$RUNTIME" "$MODE" "$FILE_PRESENT"
[[ $(<"${RUNTIME}/mode") == "off" ]]

unlink -- "${RUNTIME}/mode"
export OPENKATASTER_EUROPE_BASEMAP_MODE=off
curl() {
  printf '%s\n' \
    '{"configured_mode":"on","mode_source":"environment","mode":"off"}'
}
RUNTIME_MODE=""
RUNTIME_FILE_PRESENT=""
RUNTIME_MODE_SOURCE=""
ok_capture_runtime_mode_state \
  "$RUNTIME" "http://runtime.test" \
  RUNTIME_MODE RUNTIME_FILE_PRESENT RUNTIME_MODE_SOURCE
[[ $RUNTIME_MODE == "on" ]]
[[ $RUNTIME_FILE_PRESENT == "0" ]]
[[ $RUNTIME_MODE_SOURCE == "environment" ]]

grep -Fq -- "--expect-mode \"\$MODE\"" "${SCRIPT_DIR}/rollback-release.sh"
grep -Fq "smoke_mode \"\$OLD_MODE\" \"\$ACTIVE\"" "${SCRIPT_DIR}/set-mode.sh"
grep -Fq "smoke_all off \"\" \"\$OLD_MODE\" || restore_failed=1" \
  "${SCRIPT_DIR}/activate-release.sh"

printf 'europe-basemap-mode-state-tests=ok\n'
