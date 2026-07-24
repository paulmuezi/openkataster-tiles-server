#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=lib.sh
source "${SCRIPT_DIR}/lib.sh"

usage() {
  cat <<'EOF'
Usage:
  smoke.sh --base-url URL --expected-mode off|preview|on
           [--expected-configured-mode off|preview|on]
           [--expected-version europe-YYYYMMDD-z15]

Read-only Produktions-Smoke:
  * GET /health
  * GET /api/v1/basemap/config
  * Style, Fonts und Sprites samt Content-Type und Immutable-Cache
  * versionierter z0/0/0-Vektortile samt Content-Type, gzip und Cache-Header

Im konfigurierten Modus off wird stattdessen ein 404 geprüft. Ist der Modus
preview/on konfiguriert, aber das Runtime-Archiv nicht verfügbar, wird 503
erwartet.
EOF
}

BASE_URL=$OK_EUROPE_DEFAULT_API_URL
EXPECTED_MODE=""
EXPECTED_CONFIGURED_MODE=""
EXPECTED_VERSION=""
TEMP_DIR=""

cleanup() {
  local exit_code=$?
  if [[ -n $TEMP_DIR && -d $TEMP_DIR ]]; then
    rm -rf -- "$TEMP_DIR"
  fi
  exit "$exit_code"
}
trap cleanup EXIT

fetch_immutable_asset() {
  local relative_path=$1
  local expected_content_type=$2
  local output_name=$3
  local output_path="${TEMP_DIR}/${output_name}"
  local headers_path="${TEMP_DIR}/${output_name}.headers"
  local normalized_headers="${headers_path}.normalized"
  curl \
    --fail \
    --silent \
    --show-error \
    --retry 3 \
    --retry-all-errors \
    --retry-delay 1 \
    --connect-timeout 5 \
    --max-time 30 \
    --dump-header "$headers_path" \
    --output "$output_path" \
    "${BASE_URL}${relative_path}"
  [[ -s $output_path ]] || ok_die "Viewer-Asset ist leer: $relative_path"
  tr -d '\r' <"$headers_path" >"$normalized_headers"
  grep -Eiq \
    "^content-type: ${expected_content_type}([;[:space:]]|$)" \
    "$normalized_headers" \
    || ok_die "Viewer-Asset hat einen unerwarteten Content-Type: $relative_path"
  grep -Eiq \
    '^cache-control: public, max-age=31536000, immutable[[:space:]]*$' \
    "$normalized_headers" \
    || ok_die "Viewer-Asset ist nicht immutable gecacht: $relative_path"
}

while (($# > 0)); do
  case "$1" in
    --base-url)
      (($# >= 2)) || ok_die "--base-url benötigt einen Wert."
      BASE_URL=${2%/}
      shift 2
      ;;
    --expected-mode)
      (($# >= 2)) || ok_die "--expected-mode benötigt einen Wert."
      EXPECTED_MODE=$2
      shift 2
      ;;
    --expected-configured-mode)
      (($# >= 2)) || ok_die "--expected-configured-mode benötigt einen Wert."
      EXPECTED_CONFIGURED_MODE=$2
      shift 2
      ;;
    --expected-version)
      (($# >= 2)) || ok_die "--expected-version benötigt einen Wert."
      EXPECTED_VERSION=$2
      shift 2
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

[[ $BASE_URL == http://* || $BASE_URL == https://* ]] \
  || ok_die "--base-url muss mit http:// oder https:// beginnen."
[[ $EXPECTED_MODE == "off" || $EXPECTED_MODE == "preview" || $EXPECTED_MODE == "on" ]] \
  || ok_die "--expected-mode off|preview|on ist erforderlich."
if [[ -z $EXPECTED_CONFIGURED_MODE ]]; then
  EXPECTED_CONFIGURED_MODE=$EXPECTED_MODE
fi
[[ $EXPECTED_CONFIGURED_MODE == "off" || $EXPECTED_CONFIGURED_MODE == "preview" || $EXPECTED_CONFIGURED_MODE == "on" ]] \
  || ok_die "--expected-configured-mode muss off|preview|on sein."
if [[ $EXPECTED_MODE != "off" ]]; then
  [[ -n $EXPECTED_VERSION ]] || ok_die "--expected-version ist für preview/on erforderlich."
  ok_validate_version "$EXPECTED_VERSION"
fi

ok_require_command curl
ok_require_command python3
TEMP_DIR=$(mktemp -d)

curl \
  --fail \
  --silent \
  --show-error \
  --retry 5 \
  --retry-all-errors \
  --retry-delay 1 \
  --connect-timeout 5 \
  --max-time 20 \
  --output "${TEMP_DIR}/health.json" \
  "${BASE_URL}/health"

curl \
  --fail \
  --silent \
  --show-error \
  --retry 5 \
  --retry-all-errors \
  --retry-delay 1 \
  --connect-timeout 5 \
  --max-time 20 \
  --output "${TEMP_DIR}/config.json" \
  "${BASE_URL}/api/v1/basemap/config"

python3 - \
  "$EXPECTED_MODE" \
  "$EXPECTED_CONFIGURED_MODE" \
  "$EXPECTED_VERSION" \
  "${TEMP_DIR}/config.json" \
  "${TEMP_DIR}/style-url.txt" <<'PY'
import json
import sys
from pathlib import Path

(
    expected_mode,
    expected_configured_mode,
    expected_version,
    path,
    style_url_path,
) = sys.argv[1:]
payload = json.loads(Path(path).read_text(encoding="utf-8"))
if payload.get("schema_version") != 1:
    raise SystemExit("config schema_version is not 1")
if payload.get("configured_mode") != expected_configured_mode:
    raise SystemExit(
        "configured mode mismatch: "
        f"{payload.get('configured_mode')!r} != {expected_configured_mode!r}"
    )
if payload.get("mode") != expected_mode:
    raise SystemExit(f"effective mode mismatch: {payload.get('mode')!r} != {expected_mode!r}")
if payload.get("fallback") != "national":
    raise SystemExit("national fallback is missing")
europe = payload.get("europe")
if not isinstance(europe, dict):
    raise SystemExit("Europe config object is missing")
if expected_mode == "off":
    expected_status = "disabled" if expected_configured_mode == "off" else "unavailable"
    if payload.get("status") != expected_status or europe.get("available") is not False:
        raise SystemExit("off mode is not reported as disabled/unavailable")
else:
    if payload.get("status") != "ready" or europe.get("available") is not True:
        raise SystemExit("Europe basemap is not ready")
    if europe.get("version") != expected_version:
        raise SystemExit(
            f"version mismatch: {europe.get('version')!r} != {expected_version!r}"
        )
    if europe.get("minzoom") != 0 or europe.get("maxzoom") != 15:
        raise SystemExit("unexpected Europe basemap zoom range")
    if europe.get("bounds") != [-25.0, 34.0, 45.0, 72.0]:
        raise SystemExit(f"unexpected Europe basemap bounds: {europe.get('bounds')!r}")
    attribution = europe.get("attribution")
    if (
        not isinstance(attribution, str)
        or "OpenStreetMap contributors" not in attribution
        or "ESA WorldCover project 2020" not in attribution
        or "modified Copernicus Sentinel data (2020)" not in attribution
    ):
        raise SystemExit("Europe basemap attribution is incomplete")
    licenses = europe.get("licenses")
    if not isinstance(licenses, list):
        raise SystemExit("Europe basemap license inventory is missing")
    license_by_id = {
        item.get("id"): item
        for item in licenses
        if isinstance(item, dict) and item.get("id")
    }
    if license_by_id.get("openstreetmap", {}).get("license") != "ODbL-1.0":
        raise SystemExit("OpenStreetMap license inventory is incomplete")
    if license_by_id.get("esa-worldcover-2020", {}).get("license") != "CC-BY-4.0":
        raise SystemExit("ESA WorldCover license inventory is incomplete")
    style_url = europe.get("style_url")
    if (
        not isinstance(style_url, str)
        or not style_url.startswith("/viewer-assets/")
        or style_url.startswith("//")
        or "?" in style_url
        or "#" in style_url
    ):
        raise SystemExit(f"unsafe Europe style URL: {style_url!r}")
    Path(style_url_path).write_text(style_url, encoding="utf-8")
PY

if [[ $EXPECTED_MODE == "off" ]]; then
  EXPECTED_TILE_STATUS=404
  if [[ $EXPECTED_CONFIGURED_MODE != "off" ]]; then
    EXPECTED_TILE_STATUS=503
  fi
  TILE_STATUS=$(curl \
    --silent \
    --show-error \
    --output "${TEMP_DIR}/tile-response" \
    --write-out '%{http_code}' \
    --connect-timeout 5 \
    --max-time 20 \
    "${BASE_URL}/api/v1/basemap/europe/0/0/0.mvt")
  [[ $TILE_STATUS == "$EXPECTED_TILE_STATUS" ]] \
    || ok_die "Tile-Endpunkt muss im effektiven Modus off $EXPECTED_TILE_STATUS liefern, erhielt $TILE_STATUS."
else
  STYLE_URL=$(<"${TEMP_DIR}/style-url.txt")
  fetch_immutable_asset "$STYLE_URL" 'application/json' 'europe-style.json'
  python3 - "${TEMP_DIR}/europe-style.json" <<'PY'
import json
import sys
from pathlib import Path

style = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if style.get("version") != 8:
    raise SystemExit("Europe style version is not 8")
source = style.get("sources", {}).get("openkataster_europe")
if not isinstance(source, dict) or source.get("type") != "vector":
    raise SystemExit("Europe style source is missing")
source_attribution = source.get("attribution")
if (
    not isinstance(source_attribution, str)
    or "OpenStreetMap contributors" not in source_attribution
    or "ESA WorldCover project 2020" not in source_attribution
    or "CC BY 4.0" not in source_attribution
):
    raise SystemExit("Europe style source attribution is incomplete")
if source.get("tiles") != [
    "/api/v1/basemap/europe/{z}/{x}/{y}.mvt"
    "?v=__OPENKATASTER_BASEMAP_VERSION__"
]:
    raise SystemExit("Europe style tile template is unexpected")
if style.get("glyphs") != (
    "/viewer-assets/europe-basemap-assets-protomaps-028c18f7/"
    "fonts/{fontstack}/{range}.pbf"
):
    raise SystemExit("Europe style glyph template is unexpected")
if style.get("sprite") != (
    "/viewer-assets/europe-basemap-assets-protomaps-028c18f7/sprites/v4/light"
):
    raise SystemExit("Europe style sprite template is unexpected")
PY
  fetch_immutable_asset \
    '/viewer-assets/europe-basemap-assets-protomaps-028c18f7/fonts/Noto%20Sans%20Regular/0-255.pbf' \
    'application/x-protobuf' \
    'font-regular.pbf'
  fetch_immutable_asset \
    '/viewer-assets/europe-basemap-assets-protomaps-028c18f7/fonts/Noto%20Sans%20Medium/0-255.pbf' \
    'application/x-protobuf' \
    'font-medium.pbf'
  fetch_immutable_asset \
    '/viewer-assets/europe-basemap-assets-protomaps-028c18f7/sprites/v4/light.json' \
    'application/json' \
    'sprite.json'
  fetch_immutable_asset \
    '/viewer-assets/europe-basemap-assets-protomaps-028c18f7/sprites/v4/light.png' \
    'image/png' \
    'sprite.png'
  fetch_immutable_asset \
    '/viewer-assets/europe-basemap-assets-protomaps-028c18f7/sprites/v4/light@2x.json' \
    'application/json' \
    'sprite@2x.json'
  fetch_immutable_asset \
    '/viewer-assets/europe-basemap-assets-protomaps-028c18f7/sprites/v4/light@2x.png' \
    'image/png' \
    'sprite@2x.png'
  TILE_STATUS=$(curl \
    --silent \
    --show-error \
    --header 'Accept-Encoding: gzip' \
    --dump-header "${TEMP_DIR}/tile-headers" \
    --output "${TEMP_DIR}/tile.mvt.gz" \
    --write-out '%{http_code}' \
    --connect-timeout 5 \
    --max-time 30 \
    "${BASE_URL}/api/v1/basemap/europe/0/0/0.mvt?v=${EXPECTED_VERSION}")
  [[ $TILE_STATUS == "200" ]] \
    || ok_die "Versionierter Europe-Tile lieferte HTTP $TILE_STATUS statt 200."
  [[ -s ${TEMP_DIR}/tile.mvt.gz ]] || ok_die "Versionierter Europe-Tile ist leer."
  tr -d '\r' <"${TEMP_DIR}/tile-headers" >"${TEMP_DIR}/tile-headers.normalized"
  grep -Eiq '^content-type: application/vnd\.mapbox-vector-tile([;[:space:]]|$)' \
    "${TEMP_DIR}/tile-headers.normalized" \
    || ok_die "Europe-Tile hat einen unerwarteten Content-Type."
  grep -Eiq '^content-encoding: gzip[[:space:]]*$' \
    "${TEMP_DIR}/tile-headers.normalized" \
    || ok_die "Europe-Tile ist nicht als gzip gekennzeichnet."
  python3 - "${TEMP_DIR}/tile.mvt.gz" <<'PY'
import gzip
import sys
from pathlib import Path

compressed = Path(sys.argv[1]).read_bytes()
if compressed[:2] != b"\x1f\x8b":
    raise SystemExit("Europe tile body is not gzip data")
if not gzip.decompress(compressed):
    raise SystemExit("Europe tile decompresses to an empty MVT payload")
PY
  grep -Eiq '^cache-control: public, max-age=31536000, immutable[[:space:]]*$' \
    "${TEMP_DIR}/tile-headers.normalized" \
    || ok_die "Versionierter Europe-Tile hat keinen immutable Cache-Header."
  grep -Eiq '^etag: ' "${TEMP_DIR}/tile-headers.normalized" \
    || ok_die "Europe-Tile hat keinen ETag."
  grep -Eiq '^link: .*openstreetmap\.org/copyright.*rel="license"' \
    "${TEMP_DIR}/tile-headers.normalized" \
    || ok_die "Europe-Tile verweist nicht auf die OpenStreetMap-Lizenz."
  grep -Eiq '^link: .*creativecommons\.org/licenses/by/4\.0/.*rel="license"' \
    "${TEMP_DIR}/tile-headers.normalized" \
    || ok_die "Europe-Tile verweist nicht auf die ESA/CC-BY-Lizenz."
fi

ok_log "Health-, Config- und Tile-Smokes bestanden: $BASE_URL ($EXPECTED_MODE)."
