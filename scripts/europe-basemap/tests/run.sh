#!/usr/bin/env bash
# lib.sh is checked separately; the tests source it through a canonical runtime path.
# shellcheck disable=SC1091
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

grep -Fq 'OK_EUROPE_COVERAGE_PROFILE="de-at-buffer-v1"' "${SCRIPT_DIR}/constants.sh"
grep -Fq 'OK_EUROPE_BBOX="5,45.5,18,55.75"' "${SCRIPT_DIR}/constants.sh"
grep -Fq 'OK_EUROPE_LEGACY_BBOX="-25,34,45,72"' "${SCRIPT_DIR}/constants.sh"
grep -Fq 'OK_EUROPE_MAX_ZOOM="15"' "${SCRIPT_DIR}/constants.sh"
grep -Fq 'OK_PMTILES_VERSION="1.31.2"' "${SCRIPT_DIR}/constants.sh"
grep -Fq \
  'OK_PMTILES_BINARY_SHA256="a7e9ae10184d109c83f456ccdf6df4f3e2a64ba6cf69d9ed0f9f1840305055c1"' \
  "${SCRIPT_DIR}/constants.sh"
grep -Fq 'lsof -nP +L1' "${SCRIPT_DIR}/activate-release.sh"
grep -Fq 'OK_EUROPE_MAX_VERSIONS="2"' "${SCRIPT_DIR}/constants.sh"
grep -Fq -- "--header 'Accept-Encoding: gzip'" "${SCRIPT_DIR}/smoke.sh"
grep -Fq 'gzip.decompress(compressed)' "${SCRIPT_DIR}/smoke.sh"

TEMP_ROOT=$(mktemp -d)
cleanup() {
  rm -rf -- "$TEMP_ROOT"
}
trap cleanup EXIT
mkdir -p "${TEMP_ROOT}/runtime/.incoming"
FAILED_PART="${TEMP_ROOT}/runtime/.incoming/europe-de-at-20260723-z15.pmtiles.part"
: >"$FAILED_PART"
if ADOPT_OUTPUT=$(bash "${SCRIPT_DIR}/adopt-extract.sh" \
  --input "$FAILED_PART" \
  --build-date 20260723 \
  --confirm-version europe-de-at-20260723-z15 \
  --root "${TEMP_ROOT}/runtime" \
  --tools-root "${TEMP_ROOT}/tools" \
  --dry-run 2>&1); then
  printf 'Null-header .part was unexpectedly accepted.\n' >&2
  exit 1
fi
grep -Fq 'zu klein' <<<"$ADOPT_OUTPUT"

(
  # shellcheck source=../lib.sh
  source "${SCRIPT_DIR}/lib.sh"
  df() {
    printf '%s\n' \
      'Filesystem 1024-blocks Used Available Capacity Mounted on' \
      'mockfs 200000000 20000000 180000000 10% /tmp'
  }
  ok_preflight_disk "$TEMP_ROOT" >/dev/null
  [[ $(ok_version_for_build_date 20260723) == "europe-de-at-20260723-z15" ]]
  [[ $(ok_build_date_for_version europe-20260723-z15) == "20260723" ]]
  [[ $(ok_build_date_for_version europe-de-at-20260723-z15) == "20260723" ]]
  [[ $(ok_coverage_profile_for_version europe-20260723-z15) == "legacy-europe-v1" ]]
  [[ $(ok_coverage_profile_for_version europe-de-at-20260723-z15) == "de-at-buffer-v1" ]]
  [[ $(ok_bbox_for_version europe-20260723-z15) == "-25,34,45,72" ]]
  [[ $(ok_bbox_for_version europe-de-at-20260723-z15) == "5,45.5,18,55.75" ]]
  ok_validate_version europe-20260723-z15
  ok_validate_version europe-de-at-20260723-z15
)
if (
  # shellcheck source=../lib.sh
  source "${SCRIPT_DIR}/lib.sh"
  ok_validate_version europe-de-at-20260723-z15/../../outside
) 2>/dev/null; then
  printf 'Unsafe regional version was unexpectedly accepted.\n' >&2
  exit 1
fi

if [[ $(uname -s) == "Linux" ]]; then
  POINTER_ROOT="${TEMP_ROOT}/pointer-runtime"
  mkdir -p \
    "${POINTER_ROOT}/versions/europe-20260723-z15" \
    "${POINTER_ROOT}/versions/europe-de-at-20260723-z15"
  ln -s "versions/europe-20260723-z15" "${POINTER_ROOT}/active"
  ln -s "versions/europe-de-at-20260723-z15" "${POINTER_ROOT}/previous"
  (
    # shellcheck source=../lib.sh
    source "${SCRIPT_DIR}/lib.sh"
    [[ $(ok_link_version "$POINTER_ROOT" active) == "europe-20260723-z15" ]]
    [[ $(ok_link_version "$POINTER_ROOT" previous) == "europe-de-at-20260723-z15" ]]
    [[ $(ok_count_versions "$POINTER_ROOT") == "2" ]]
  )
  unlink "${POINTER_ROOT}/active"
  ln -s "versions/europe-de-at-20260723-z15/../../outside" "${POINTER_ROOT}/active"
  if (
    # shellcheck source=../lib.sh
    source "${SCRIPT_DIR}/lib.sh"
    ok_link_version "$POINTER_ROOT" active
  ) >/dev/null 2>&1; then
    printf 'Unsafe active pointer was unexpectedly accepted.\n' >&2
    exit 1
  fi
fi

python3 -m unittest -v "${TEST_DIR}/test_validate_release.py"
bash "${TEST_DIR}/test_smoke_health.sh"

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
    "${TEST_DIR}/test_smoke_health.sh" \
    "${TEST_DIR}/test_mode_state.sh"
fi

printf 'europe-basemap-ops-tests=ok\n'
