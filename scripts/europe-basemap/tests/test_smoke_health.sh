#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

TEST_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
SCRIPT_DIR="$(cd -- "${TEST_DIR}/.." && pwd -P)"
TEMP_ROOT=$(mktemp -d)
SERVER_PID=""

stop_server() {
  if [[ -n $SERVER_PID ]]; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
    SERVER_PID=""
  fi
}

cleanup() {
  stop_server
  rm -rf -- "$TEMP_ROOT"
}
trap cleanup EXIT

start_server() {
  local scenario=$1
  local port_file="${TEMP_ROOT}/${scenario}.port"
  local requests_file="${TEMP_ROOT}/${scenario}.requests"
  rm -f -- "$port_file" "$requests_file"
  python3 "${TEST_DIR}/mock_smoke_server.py" \
    --scenario "$scenario" \
    --port-file "$port_file" \
    --requests-file "$requests_file" &
  SERVER_PID=$!
  for _attempt in $(seq 1 100); do
    if [[ -s $port_file ]]; then
      SERVER_PORT=$(<"$port_file")
      SERVER_REQUESTS_FILE=$requests_file
      return
    fi
    kill -0 "$SERVER_PID" 2>/dev/null \
      || {
        printf 'Mock server exited before publishing its port.\n' >&2
        exit 1
      }
    sleep 0.05
  done
  printf 'Timed out waiting for mock server port.\n' >&2
  exit 1
}

run_failure_case() {
  local scenario=$1
  local expected_message=$2
  local output
  start_server "$scenario"
  if output=$(bash "${SCRIPT_DIR}/smoke.sh" \
    --base-url "http://127.0.0.1:${SERVER_PORT}" \
    --expected-mode off 2>&1); then
    printf 'Smoke unexpectedly accepted health scenario %s.\n' "$scenario" >&2
    exit 1
  fi
  stop_server
  grep -Fq "$expected_message" <<<"$output"
  [[ $(<"$SERVER_REQUESTS_FILE") == "/health" ]] \
    || {
      printf 'Smoke did not stop after the invalid health response (%s).\n' "$scenario" >&2
      exit 1
    }
}

start_server ok
bash "${SCRIPT_DIR}/smoke.sh" \
  --base-url "http://127.0.0.1:${SERVER_PORT}" \
  --expected-mode off >/dev/null
stop_server

run_failure_case redirect "Health-Endpunkt muss HTTP 200 liefern, erhielt 303."
run_failure_case wrong-json "health response must be a JSON object with status='ok'"

printf 'europe-basemap-smoke-health-tests=ok\n'
