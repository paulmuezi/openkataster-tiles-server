#!/usr/bin/env bash

# Reproducible production inputs for the OpenKataster Europe basemap.
# This file is sourced by the operational scripts; it intentionally has no
# executable side effects.
# shellcheck disable=SC2034

if [[ -n ${OK_EUROPE_BASEMAP_CONSTANTS_LOADED:-} ]]; then
  return 0
fi
readonly OK_EUROPE_BASEMAP_CONSTANTS_LOADED=1

readonly OK_EUROPE_DEFAULT_ROOT="/srv/openkataster-tiles/basemaps/europe"
readonly OK_EUROPE_DEFAULT_TOOLS_ROOT="/srv/openkataster-tiles/tools/pmtiles"
readonly OK_EUROPE_DEFAULT_BUILD_DATE="20260723"
readonly OK_EUROPE_COVERAGE_PROFILE="de-at-buffer-v1"
readonly OK_EUROPE_BBOX="5,45.5,18,55.75"
readonly OK_EUROPE_LEGACY_COVERAGE_PROFILE="legacy-europe-v1"
readonly OK_EUROPE_LEGACY_BBOX="-25,34,45,72"
readonly OK_EUROPE_MIN_ZOOM="0"
readonly OK_EUROPE_MAX_ZOOM="15"
readonly OK_EUROPE_DOWNLOAD_THREADS="4"
readonly OK_EUROPE_OVERFETCH="0.05"
readonly OK_EUROPE_MIN_FREE_GIB="130"
readonly OK_EUROPE_MIN_FREE_PERCENT="15"
readonly OK_EUROPE_MAX_VERSIONS="2"

readonly OK_PMTILES_VERSION="1.31.2"
readonly OK_PMTILES_RELEASE_URL="https://github.com/protomaps/go-pmtiles/releases/download/v1.31.2/go-pmtiles_1.31.2_Linux_x86_64.tar.gz"
readonly OK_PMTILES_RELEASE_SHA256="3ed7dbf4ec2e6dfe5e25b6f70d1ffc932729f93c86db353bf514dd71010a312f"
readonly OK_PMTILES_BINARY_SHA256="a7e9ae10184d109c83f456ccdf6df4f3e2a64ba6cf69d9ed0f9f1840305055c1"

readonly OK_EUROPE_DEFAULT_API_URL="http://127.0.0.1:8081"
readonly OK_EUROPE_DEFAULT_PUBLIC_URL="https://tiles.openkataster.de"
readonly OK_EUROPE_DEFAULT_CONTAINER="openkataster-tiles-api"
