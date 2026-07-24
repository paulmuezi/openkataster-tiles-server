#!/usr/bin/env bash

if [[ -n ${OK_EUROPE_BASEMAP_LIB_LOADED:-} ]]; then
  return 0
fi
readonly OK_EUROPE_BASEMAP_LIB_LOADED=1

OK_EUROPE_SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly OK_EUROPE_SCRIPT_DIR
# shellcheck source=constants.sh
source "${OK_EUROPE_SCRIPT_DIR}/constants.sh"

ok_log() {
  printf '[europe-basemap] %s\n' "$*" >&2
}

ok_warn() {
  printf '[europe-basemap] WARN: %s\n' "$*" >&2
}

ok_die() {
  printf '[europe-basemap] ERROR: %s\n' "$*" >&2
  exit 1
}

ok_require_bash() {
  if ((BASH_VERSINFO[0] < 4)); then
    ok_die "Bash 4 oder neuer ist erforderlich."
  fi
}

ok_require_command() {
  command -v "$1" >/dev/null 2>&1 || ok_die "Fehlendes Kommando: $1"
}

ok_validate_build_date() {
  [[ $1 =~ ^20[0-9]{6}$ ]] || ok_die "Ungültiges Build-Datum '$1' (erwartet: YYYYMMDD)."
}

ok_validate_version() {
  [[ $1 =~ ^europe(-de-at)?-20[0-9]{6}-z15$ ]] \
    || ok_die "Ungültige Europe-Basemap-Version '$1'."
}

ok_version_for_build_date() {
  ok_validate_build_date "$1"
  printf 'europe-de-at-%s-z15\n' "$1"
}

ok_build_date_for_version() {
  local version=$1
  ok_validate_version "$version"
  if [[ $version =~ ^europe(-de-at)?-(20[0-9]{6})-z15$ ]]; then
    printf '%s\n' "${BASH_REMATCH[2]}"
    return 0
  fi
  ok_die "Build-Datum konnte nicht aus der Version gelesen werden: $version"
}

ok_coverage_profile_for_version() {
  local version=$1
  ok_validate_version "$version"
  if [[ $version =~ ^europe-de-at-20[0-9]{6}-z15$ ]]; then
    printf '%s\n' "$OK_EUROPE_COVERAGE_PROFILE"
  else
    printf '%s\n' "$OK_EUROPE_LEGACY_COVERAGE_PROFILE"
  fi
}

ok_bbox_for_version() {
  local version=$1
  ok_validate_version "$version"
  if [[ $version =~ ^europe-de-at-20[0-9]{6}-z15$ ]]; then
    printf '%s\n' "$OK_EUROPE_BBOX"
  else
    printf '%s\n' "$OK_EUROPE_LEGACY_BBOX"
  fi
}

ok_source_url_for_build_date() {
  ok_validate_build_date "$1"
  printf 'https://build.protomaps.com/%s.pmtiles\n' "$1"
}

ok_assert_safe_root() {
  local root=$1
  [[ $root == /* ]] || ok_die "Runtime-Root muss absolut sein: $root"
  [[ $root != "/" ]] || ok_die "Runtime-Root darf nicht / sein."
  [[ $root != *$'\n'* ]] || ok_die "Runtime-Root enthält einen Zeilenumbruch."
}

ok_sha256_file() {
  sha256sum -- "$1" | awk '{print $1}'
}

ok_format_command() {
  local item
  printf '+'
  for item in "$@"; do
    printf ' %q' "$item"
  done
  printf '\n'
}

ok_run() {
  if [[ ${OK_DRY_RUN:-0} == "1" ]]; then
    ok_format_command "$@" >&2
    return 0
  fi
  "$@"
}

ok_run_io_niced() {
  nice -n 10 ionice -c 2 -n 7 "$@"
}

ok_acquire_lock() {
  local root=$1
  ok_require_command flock
  if [[ ${OK_DRY_RUN:-0} == "1" ]]; then
    ok_log "Dry-run: würde exklusiv ${root}/.operations.lock sperren."
    return 0
  fi
  mkdir -p -- "$root"
  exec 9>"${root}/.operations.lock"
  flock -n 9 || ok_die "Eine andere Europe-Basemap-Operation läuft bereits."
}

ok_atomic_symlink() {
  local target=$1
  local link_path=$2
  local link_parent
  local temp_link
  link_parent=$(dirname -- "$link_path")
  temp_link="${link_parent}/.$(basename -- "$link_path").new.$$"
  if [[ ${OK_DRY_RUN:-0} == "1" ]]; then
    ok_log "Dry-run: würde $link_path atomar auf $target setzen."
    return 0
  fi
  [[ ! -e $temp_link && ! -L $temp_link ]] \
    || ok_die "Temporärer Link existiert bereits: $temp_link"
  ln -s -- "$target" "$temp_link"
  mv -Tf -- "$temp_link" "$link_path"
}

ok_atomic_mode() {
  local root=$1
  local mode=$2
  local temp_mode="${root}/.mode.new.$$"
  [[ $mode == "off" || $mode == "preview" || $mode == "on" ]] \
    || ok_die "Ungültiger Modus '$mode' (off|preview|on)."
  if [[ ${OK_DRY_RUN:-0} == "1" ]]; then
    ok_log "Dry-run: würde ${root}/mode atomar auf '$mode' setzen."
    return 0
  fi
  umask 022
  printf '%s\n' "$mode" >"$temp_mode"
  chmod 0644 "$temp_mode"
  mv -Tf -- "$temp_mode" "${root}/mode"
}

ok_read_mode() {
  local root=$1
  local mode_path="${root}/mode"
  local mode="${OPENKATASTER_EUROPE_BASEMAP_MODE:-off}"
  if [[ -L $mode_path ]]; then
    ok_die "Mode-Datei darf kein Symlink sein: $mode_path"
  fi
  if [[ -f $mode_path ]]; then
    [[ $(wc -c <"$mode_path" | tr -d ' ') -le 32 ]] \
      || ok_die "Mode-Datei ist unerwartet groß: $mode_path"
    mode=$(<"$mode_path")
  elif [[ -e $mode_path ]]; then
    ok_die "Mode-Pfad ist keine reguläre Datei: $mode_path"
  fi
  mode="${mode#"${mode%%[![:space:]]*}"}"
  mode="${mode%"${mode##*[![:space:]]}"}"
  mode=${mode,,}
  [[ $mode == "off" || $mode == "preview" || $mode == "on" ]] \
    || ok_die "Ungültiger Europe-Basemap-Modus aus Datei oder Umgebung."
  printf '%s\n' "$mode"
}

ok_capture_mode_state() {
  local root=$1
  local mode_variable=$2
  local file_present_variable=$3
  local mode
  local file_present=0
  mode=$(ok_read_mode "$root")
  if [[ -f ${root}/mode && ! -L ${root}/mode ]]; then
    file_present=1
  fi
  printf -v "$mode_variable" '%s' "$mode"
  printf -v "$file_present_variable" '%s' "$file_present"
}

ok_read_runtime_mode() {
  local base_url=${1%/}
  local mode_variable=$2
  local source_variable=$3
  local payload
  local parsed
  local mode
  local source
  ok_require_command curl
  ok_require_command python3
  payload=$(curl \
    --fail \
    --silent \
    --show-error \
    --retry 2 \
    --retry-all-errors \
    --retry-delay 1 \
    --connect-timeout 5 \
    --max-time 15 \
    "${base_url}/api/v1/basemap/config")
  parsed=$(python3 -c '
import json
import sys
payload = json.load(sys.stdin)
mode = payload.get("configured_mode")
source = payload.get("mode_source")
if mode not in {"off", "preview", "on"}:
    raise SystemExit("invalid configured_mode")
if source not in {"mode-file", "environment", "environment-invalid"}:
    raise SystemExit("invalid mode_source")
print(f"{mode}\t{source}")
' <<<"$payload")
  IFS=$'\t' read -r mode source <<<"$parsed"
  [[ -n $mode && -n $source ]] \
    || ok_die "Runtime-Modus konnte nicht aus der Config gelesen werden."
  printf -v "$mode_variable" '%s' "$mode"
  printf -v "$source_variable" '%s' "$source"
}

ok_capture_runtime_mode_state() {
  local root=$1
  local base_url=$2
  local mode_variable=$3
  local file_present_variable=${4:-}
  local source_variable=${5:-}
  local local_mode=""
  local local_file_present=0
  local runtime_mode=""
  local runtime_source=""
  ok_capture_mode_state "$root" local_mode local_file_present
  ok_read_runtime_mode "$base_url" runtime_mode runtime_source
  if [[ $local_file_present == "1" ]]; then
    [[ $runtime_source == "mode-file" && $runtime_mode == "$local_mode" ]] \
      || ok_die "Mode-Datei und laufende Tiles-API melden unterschiedliche Zustände."
  else
    [[ $runtime_source == "environment" || $runtime_source == "environment-invalid" ]] \
      || ok_die "Tiles-API meldet unerwartet eine Mode-Datei."
  fi
  printf -v "$mode_variable" '%s' "$runtime_mode"
  if [[ -n $file_present_variable ]]; then
    printf -v "$file_present_variable" '%s' "$local_file_present"
  fi
  if [[ -n $source_variable ]]; then
    printf -v "$source_variable" '%s' "$runtime_source"
  fi
}

ok_restore_mode_state() {
  local root=$1
  local mode=$2
  local file_present=$3
  if [[ $file_present == "1" ]]; then
    ok_atomic_mode "$root" "$mode"
    return
  fi
  [[ $file_present == "0" ]] || ok_die "Ungültiger interner Mode-Dateistatus."
  if [[ ${OK_DRY_RUN:-0} == "1" ]]; then
    ok_log "Dry-run: würde ${root}/mode entfernen und den Umgebungsmodus '$mode' wiederherstellen."
    return
  fi
  if [[ -L ${root}/mode ]]; then
    return 1
  fi
  if [[ -e ${root}/mode ]]; then
    unlink -- "${root}/mode"
  fi
}

ok_link_version() {
  local root=$1
  local name=$2
  local link_path="${root}/${name}"
  local target
  local version
  [[ $name == "active" || $name == "previous" ]] \
    || ok_die "Interner Fehler: unbekannter Pointer $name"
  if [[ ! -L $link_path ]]; then
    return 1
  fi
  target=$(readlink -- "$link_path")
  [[ $target == versions/* ]] \
    || ok_die "Unsicherer ${name}-Pointer: $target"
  version=${target#versions/}
  [[ $target == "versions/${version}" ]] \
    || ok_die "Unsicherer ${name}-Pointer: $target"
  ok_validate_version "$version"
  [[ -d ${root}/${target} && ! -L ${root}/${target} ]] \
    || ok_die "${name}-Pointer zeigt nicht auf eine reguläre Version: $target"
  printf '%s\n' "$version"
}

ok_capture_link_version() {
  local root=$1
  local name=$2
  local output_variable=$3
  local link_path="${root}/${name}"
  local value=""
  if [[ -L $link_path ]]; then
    if ! value=$(ok_link_version "$root" "$name"); then
      ok_die "Ungültiger $name-Pointer: $link_path"
    fi
  elif [[ -e $link_path ]]; then
    ok_die "$name-Pointer ist kein Symlink: $link_path"
  fi
  printf -v "$output_variable" '%s' "$value"
}

ok_count_versions() {
  local root=$1
  if [[ ! -d ${root}/versions ]]; then
    printf '0\n'
    return 0
  fi
  find "${root}/versions" -mindepth 1 -maxdepth 1 -type d \
    \( -name 'europe-????????-z15' -o -name 'europe-de-at-????????-z15' \) \
    -printf '.' | wc -c | tr -d ' '
}

ok_preflight_disk() {
  local path=$1
  local total_kib
  local available_kib
  local percent_required_kib
  local fixed_required_kib
  local required_kib
  local existing_path=$path

  while [[ ! -e $existing_path ]]; do
    existing_path=$(dirname -- "$existing_path")
  done
  IFS=$'\t' read -r total_kib available_kib < <(
    df -Pk -- "$existing_path" \
      | awk 'NR == 2 {printf "%s\t%s\n", $2, $4}'
  )
  [[ $total_kib =~ ^[0-9]+$ && $available_kib =~ ^[0-9]+$ ]] \
    || ok_die "Freier Speicher konnte für $existing_path nicht bestimmt werden."
  percent_required_kib=$(
    awk -v total="$total_kib" -v percent="$OK_EUROPE_MIN_FREE_PERCENT" \
      'BEGIN {printf "%.0f", (total * percent / 100) + 0.999999}'
  )
  fixed_required_kib=$((OK_EUROPE_MIN_FREE_GIB * 1024 * 1024))
  required_kib=$fixed_required_kib
  if ((percent_required_kib > required_kib)); then
    required_kib=$percent_required_kib
  fi
  if ((available_kib < required_kib)); then
    ok_die "Zu wenig freier Speicher: ${available_kib} KiB frei, mindestens ${required_kib} KiB erforderlich (max. aus ${OK_EUROPE_MIN_FREE_PERCENT}% und ${OK_EUROPE_MIN_FREE_GIB} GiB)."
  fi
  ok_log "Speicher-Preflight bestanden: ${available_kib} KiB frei, ${required_kib} KiB erforderlich."
}

ok_assert_version_capacity() {
  local root=$1
  local requested_version=$2
  local allowed_incoming=${3:-}
  local count
  local incoming_file
  if [[ -d ${root}/.incoming ]]; then
    while IFS= read -r -d '' incoming_file; do
      if [[ -n $allowed_incoming && $incoming_file == "$allowed_incoming" ]]; then
        continue
      fi
      ok_die "Unvollständiger PMTiles-Bestand liegt noch in .incoming: $incoming_file. Vor einem neuen Build erst offene Handles und Fehlerursache prüfen und den Bestand bewusst bereinigen."
    done < <(
      find "${root}/.incoming" -type f \
        \( -name '*.pmtiles' -o -name '*.pmtiles.part' \) -print0
    )
  fi
  if [[ -d ${root}/archive ]] && find "${root}/archive" -mindepth 1 -maxdepth 1 \
    -type d \
    \( -name 'europe-????????-z15' -o -name 'europe-de-at-????????-z15' \) \
    -print -quit | grep -q .; then
    ok_die "Unter ${root}/archive liegt mindestens eine weitere Runtime auf demselben Volume. Archive müssen auf ein anderes Dateisystem verschoben werden."
  fi
  if [[ -d ${root}/versions/${requested_version} ]]; then
    return 0
  fi
  count=$(ok_count_versions "$root")
  if ((count >= OK_EUROPE_MAX_VERSIONS)); then
    ok_die "Bereits ${count} Versionen vorhanden. Es wird nichts automatisch gelöscht. Vor einem neuen Build eine nicht aktive Version bewusst archivieren."
  fi
}

ok_remove_pointer() {
  local pointer=$1
  if [[ ${OK_DRY_RUN:-0} == "1" ]]; then
    ok_log "Dry-run: würde Pointer $pointer entfernen."
    return 0
  fi
  if [[ -L $pointer ]]; then
    unlink -- "$pointer"
  elif [[ -e $pointer ]]; then
    ok_die "Pointer-Pfad ist kein Symlink: $pointer"
  fi
}

ok_require_bash
