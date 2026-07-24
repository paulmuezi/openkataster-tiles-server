#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
export PYTHONDONTWRITEBYTECODE=1

TEST_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
SCRIPT_DIR="$(cd -- "${TEST_DIR}/.." && pwd -P)"

for script in \
  activate-release.sh \
  adopt-extract.sh \
  archive-release.sh \
  build-release.sh \
  launch-build-systemd.sh \
  migrate-legacy-manifest.sh \
  rollback-release.sh \
  set-mode.sh \
  smoke.sh \
  verify-release.sh; do
  bash -n "${SCRIPT_DIR}/${script}"
  bash "${SCRIPT_DIR}/${script}" --help >/dev/null
done

grep -Fq 'OK_EUROPE_BBOX="-25,34,45,72"' "${SCRIPT_DIR}/constants.sh"
grep -Fq 'OK_EUROPE_MAX_ZOOM="15"' "${SCRIPT_DIR}/constants.sh"
grep -Fq 'OK_PMTILES_VERSION="1.31.2"' "${SCRIPT_DIR}/constants.sh"
grep -Fq \
  'OK_PMTILES_BINARY_SHA256="a7e9ae10184d109c83f456ccdf6df4f3e2a64ba6cf69d9ed0f9f1840305055c1"' \
  "${SCRIPT_DIR}/constants.sh"
grep -Fq 'lsof -nP +L1' "${SCRIPT_DIR}/activate-release.sh"
grep -Fq 'OK_EUROPE_MAX_VERSIONS="2"' "${SCRIPT_DIR}/constants.sh"

TEMP_ROOT=$(mktemp -d)
cleanup() {
  rm -rf -- "$TEMP_ROOT"
}
trap cleanup EXIT
mkdir -p "${TEMP_ROOT}/runtime/.incoming"
FAILED_PART="${TEMP_ROOT}/runtime/.incoming/europe-20260723-z15.pmtiles.part"
: >"$FAILED_PART"
if ADOPT_OUTPUT=$(bash "${SCRIPT_DIR}/adopt-extract.sh" \
  --input "$FAILED_PART" \
  --build-date 20260723 \
  --confirm-version europe-20260723-z15 \
  --root "${TEMP_ROOT}/runtime" \
  --tools-root "${TEMP_ROOT}/tools" \
  --dry-run 2>&1); then
  printf 'Null-header .part was unexpectedly accepted.\n' >&2
  exit 1
fi
grep -Fq 'zu klein' <<<"$ADOPT_OUTPUT"

python3 -m unittest -v "${TEST_DIR}/test_validate_release.py"

if [[ $(uname -s) == "Linux" ]]; then
  bash "${TEST_DIR}/test_mode_state.sh"
fi

if command -v shellcheck >/dev/null 2>&1; then
  shellcheck -x -P "${SCRIPT_DIR}" \
    "${SCRIPT_DIR}/constants.sh" \
    "${SCRIPT_DIR}/lib.sh" \
    "${SCRIPT_DIR}/activate-release.sh" \
    "${SCRIPT_DIR}/adopt-extract.sh" \
    "${SCRIPT_DIR}/archive-release.sh" \
    "${SCRIPT_DIR}/build-release.sh" \
    "${SCRIPT_DIR}/launch-build-systemd.sh" \
    "${SCRIPT_DIR}/migrate-legacy-manifest.sh" \
    "${SCRIPT_DIR}/rollback-release.sh" \
    "${SCRIPT_DIR}/set-mode.sh" \
    "${SCRIPT_DIR}/smoke.sh" \
    "${SCRIPT_DIR}/verify-release.sh" \
    "${TEST_DIR}/run.sh" \
    "${TEST_DIR}/test_mode_state.sh"
fi

printf 'europe-basemap-ops-tests=ok\n'
