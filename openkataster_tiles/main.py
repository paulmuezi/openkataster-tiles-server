from __future__ import annotations

import asyncio
import base64
import gzip
import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import subprocess
import threading
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Annotated

import mapbox_vector_tile
from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pmtiles.reader import Compression, MmapSource, Reader
from shapely import wkb
from shapely.geometry import Point, mapping, shape
from shapely.errors import GEOSException

try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from cryptography.hazmat.primitives.serialization import load_pem_public_key
except Exception:  # pragma: no cover - optional deployment dependency
    Ed25519PublicKey = None  # type: ignore[assignment]
    load_pem_public_key = None  # type: ignore[assignment]


DATA_DIR = Path(os.environ.get("OPENKATASTER_TILE_DATA_DIR", "/srv/openkataster-tiles/data"))
VIEWER_ROOT = Path(os.environ.get("OPENKATASTER_VIEWER_ROOT", "/srv/openkataster-tiles/live-viewer"))
GN250_PLACES_DB = Path(os.environ.get("OPENKATASTER_GN250_PLACES_DB", str(DATA_DIR / "places.sqlite")))
OPENPLZ_DB = Path(os.environ.get("OPENKATASTER_OPENPLZ_DB", "/srv/openkataster-tiles/plz/openplz.sqlite"))
POSTCODE_AREAS_DB = Path(os.environ.get("OPENKATASTER_POSTCODE_AREAS_DB", "/srv/openkataster-tiles/plz/postcode_areas.sqlite"))
GEOCODER_DB = Path(os.environ.get("OPENKATASTER_GEOCODER_DB", "/srv/openkataster-tiles/geocoder/geocoder.sqlite"))
FAST_GEOCODER_DB = Path(os.environ.get("OPENKATASTER_FAST_GEOCODER_DB", "/srv/openkataster-tiles/geocoder/geocoder_fast.sqlite"))
_GEOCODER_THREAD_LOCAL = threading.local()
_FAST_GEOCODER_THREAD_LOCAL = threading.local()
_GEOCODER_GLOBAL_CONNECTION: sqlite3.Connection | None = None
_GEOCODER_GLOBAL_SIGNATURE: tuple[int, int] | None = None
_FAST_GEOCODER_GLOBAL_CONNECTION: sqlite3.Connection | None = None
_FAST_GEOCODER_GLOBAL_SIGNATURE: tuple[int, int] | None = None
_GEOCODER_GLOBAL_LOCK = threading.Lock()
_FAST_GEOCODER_GLOBAL_LOCK = threading.Lock()
PUBLIC_BASE_URL = os.environ.get("OPENKATASTER_TILE_PUBLIC_BASE_URL", "").rstrip("/")
ADMIN_API_BASE_URL = os.environ.get("OPENKATASTER_ADMIN_API_BASE_URL", "https://api.openkataster.de").rstrip("/")
LUFTBILD_TILE_SIZE = int(os.environ.get("OPENKATASTER_LUFTBILD_TILE_SIZE", "1024"))
LUFTBILD_CACHE_DIR = Path(os.environ.get("OPENKATASTER_LUFTBILD_CACHE_DIR", str(DATA_DIR / "luftbild_cache")))
LUFTBILD_CACHE_MAX_BYTES = int(os.environ.get("OPENKATASTER_LUFTBILD_CACHE_MAX_BYTES", str(3 * 1024 * 1024 * 1024)))
LUFTBILD_CACHE_TARGET_BYTES = int(os.environ.get("OPENKATASTER_LUFTBILD_CACHE_TARGET_BYTES", str(2 * 1024 * 1024 * 1024)))
LUFTBILD_MIN_FREE_BYTES = int(os.environ.get("OPENKATASTER_LUFTBILD_MIN_FREE_BYTES", str(8 * 1024 * 1024 * 1024)))
LUFTBILD_CACHE_CLEANUP_INTERVAL_SECONDS = int(os.environ.get("OPENKATASTER_LUFTBILD_CACHE_CLEANUP_INTERVAL_SECONDS", "300"))
_LUFTBILD_CACHE_LAST_CLEANUP = 0.0
PMTILES_CACHE_DIR = Path(os.environ.get("OPENKATASTER_PMTILES_CACHE_DIR", str(DATA_DIR / "pmtiles_range_cache")))
PMTILES_CACHE_MAX_BYTES = int(os.environ.get("OPENKATASTER_PMTILES_CACHE_MAX_BYTES", str(40 * 1024 * 1024 * 1024)))
PMTILES_CACHE_TARGET_BYTES = int(os.environ.get("OPENKATASTER_PMTILES_CACHE_TARGET_BYTES", str(32 * 1024 * 1024 * 1024)))
PMTILES_MIN_FREE_BYTES = int(os.environ.get("OPENKATASTER_PMTILES_MIN_FREE_BYTES", str(8 * 1024 * 1024 * 1024)))
PMTILES_CACHE_CLEANUP_INTERVAL_SECONDS = int(os.environ.get("OPENKATASTER_PMTILES_CACHE_CLEANUP_INTERVAL_SECONDS", "300"))
_PMTILES_CACHE_LAST_CLEANUP = 0.0
SEARCH_CACHE_SECONDS = int(os.environ.get("OPENKATASTER_TILE_SEARCH_CACHE_SECONDS", "120"))
_SEARCH_RESPONSE_CACHE: dict[tuple, tuple[float, dict]] = {}
_SEARCH_RESPONSE_CACHE_MAX = int(os.environ.get("OPENKATASTER_TILE_SEARCH_CACHE_MAX", "512"))
DATASET_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,80}$")
VIEWER_VERSION_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,80}$")
VIEWER_ASSET_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,120}$")
ASSET_RE = re.compile(r"^[a-z0-9_-]+\.json$")
OVERVIEW_ASSET_RE = re.compile(r"^([a-z0-9][a-z0-9_-]{0,80})_overview_(boundaries|labels)\.json$")
ALLOWED_ASSETS = {
    "alkis_overview_boundaries.json",
    "alkis_overview_labels.json",
    "germany.json",
    "state_labels.json",
    "niedersachsen_city_labels.json",
    "states.json",
}
VIRTUAL_GERMANY_DATASET = os.environ.get("OPENKATASTER_TILE_GERMANY_DATASET", "deutschland")
OVERVIEW_MAX_ZOOM = int(os.environ.get("OPENKATASTER_TILE_OVERVIEW_MAX_ZOOM", "11"))
SOURCE_MAX_ZOOM = int(os.environ.get("OPENKATASTER_TILE_SOURCE_MAX_ZOOM", "17"))
MOSAIC_CACHE_SIZE = int(os.environ.get("OPENKATASTER_TILE_MOSAIC_CACHE_SIZE", "2048"))
MOSAIC_DISK_CACHE_DIR = os.environ.get("OPENKATASTER_TILE_MOSAIC_DISK_CACHE_DIR", "").strip()
MOSAIC_DISK_CACHE_MAX_ZOOM = int(os.environ.get("OPENKATASTER_TILE_MOSAIC_DISK_CACHE_MAX_ZOOM", "14"))
RASTER_DISK_CACHE_DIR = os.environ.get("OPENKATASTER_TILE_RASTER_CACHE_DIR", "").strip()
RASTER_ON_DEMAND = os.environ.get("OPENKATASTER_TILE_RASTER_ON_DEMAND", "").strip().lower() in {"1", "true", "yes"}
HYBRID_RASTER_MIN_ZOOM = float(os.environ.get("OPENKATASTER_TILE_HYBRID_RASTER_MIN_ZOOM", "9"))
HYBRID_RASTER_MAX_ZOOM = float(os.environ.get("OPENKATASTER_TILE_HYBRID_RASTER_MAX_ZOOM", "15.8"))
HYBRID_VECTOR_MIN_ZOOM = float(os.environ.get("OPENKATASTER_TILE_HYBRID_VECTOR_MIN_ZOOM", "15.5"))
RASTER_TILE_SIZE = int(os.environ.get("OPENKATASTER_TILE_RASTER_TILE_SIZE", "512"))
DATE_SUFFIX_RE = re.compile(r"_(?:\d{8}|\d{4}-\d{2}-\d{2}(?:_\d{6})?)$")
DETAIL_SHARD_SUFFIX_RE = re.compile(r"^(?P<base>.+)_detail_(?P<shard>\d{3})$")
FEATURE_DB_SUFFIX = ".features.sqlite"
SEARCH_DB_SUFFIX = ".search.sqlite"
LUFTBILD_WMS_CONFIGS = {
    "baden-wurttemberg": {"url": "https://owsproxy.lgl-bw.de/owsproxy/ows/WMS_LGL-BW_ATKIS_DOP_20_C", "layer": "IMAGES_DOP_20_RGB", "crs": "EPSG:25832", "format": "image/png", "version": "1.3.0"},
    "berlin": {"url": "https://isk.geobasis-bb.de/mapproxy/dop20c/service/wms", "layer": "bebb_dop20c", "crs": "EPSG:25833", "format": "image/png"},
    "brandenburg": {"url": "https://isk.geobasis-bb.de/mapproxy/dop20c/service/wms", "layer": "bebb_dop20c", "crs": "EPSG:25833", "format": "image/png"},
    "bremen": {"url": "https://geodienste.bremen.de/wms_dop20_2023", "layer": "DOP20_2023_HB", "layer_alt": "DOP20_2023_BHV", "crs": "EPSG:25832", "format": "image/png"},
    "hamburg": {"url": "https://geodienste.hamburg.de/wms_dop_zeitreihe_belaubt", "layer": "dop_zeitreihe_belaubt", "crs": "EPSG:25832", "format": "image/png"},
    "hessen": {"url": "https://www.gds-srv.hessen.de/cgi-bin/lika-services/ogc-free-images.ows", "layer": "he_dop20_rgb", "crs": "EPSG:25832", "format": "image/png"},
    "mecklenburg-vorpommern": {"url": "https://www.geodaten-mv.de/dienste/adv_dop", "layer": "mv_dop", "crs": "EPSG:25833", "format": "image/png"},
    "niedersachsen": {"url": "https://opendata.lgln.niedersachsen.de/doorman/noauth/dop_wms", "layer": "ni_dop20", "crs": "EPSG:25832", "format": "image/png"},
    "nordrhein-westfalen": {"url": "https://www.wms.nrw.de/geobasis/wms_nw_dop", "layer": "nw_dop_rgb", "crs": "EPSG:25832", "format": "image/png"},
    "rheinland-pfalz": {"url": "https://geo4.service24.rlp.de/wms/rp_dop20.fcgi", "layer": "rp_dop20", "crs": "EPSG:25832", "format": "image/png"},
    "saarland": {"url": "https://geoportal.saarland.de/freewms/dop2020", "layer": "sl_dop2020", "crs": "EPSG:25832", "format": "image/png"},
    "sachsen": {"url": "https://geodienste.sachsen.de/wms_geosn_dop-rgb/guest", "layer": "sn_dop_020", "crs": "EPSG:25833", "format": "image/png"},
    "sachsen-anhalt": {"url": "https://www.geodatenportal.sachsen-anhalt.de/wss/service/ST_LVermGeo_DOP_WMS_OpenData/guest", "layer": "lsa_lvermgeo_dop20_2", "crs": "EPSG:25832", "format": "image/png"},
    "schleswig-holstein": {"url": "https://dienste.gdi-sh.de/WMS_SH_DOP20col_OpenGBD", "layer": "sh_dop20_rgb", "crs": "EPSG:25832", "format": "image/png"},
    "thueringen": {"url": "https://www.geoproxy.geoportal-th.de/geoproxy/services/DOP20", "layer": "th_dop", "crs": "EPSG:25832", "format": "image/png"},
    "thuringen": {"url": "https://www.geoproxy.geoportal-th.de/geoproxy/services/DOP20", "layer": "th_dop", "crs": "EPSG:25832", "format": "image/png"},
}


def _tile_lonlat_bounds(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    n = 2.0 ** z
    west = x / n * 360.0 - 180.0
    east = (x + 1) / n * 360.0 - 180.0
    north = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    south = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / n))))
    return west, south, east, north


def _tile_webmercator_bounds(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    origin_shift = 20037508.342789244
    tile_size = (origin_shift * 2) / (2.0**z)
    minx = -origin_shift + x * tile_size
    maxx = -origin_shift + (x + 1) * tile_size
    maxy = origin_shift - y * tile_size
    miny = origin_shift - (y + 1) * tile_size
    return minx, miny, maxx, maxy


def _lonlat_to_utm(lon: float, lat: float, zone: int) -> tuple[float, float]:
    a = 6378137.0
    f = 1 / 298.257222101
    k0 = 0.9996
    e2 = f * (2 - f)
    ep2 = e2 / (1 - e2)
    lat_rad = math.radians(lat)
    lon_rad = math.radians(lon)
    lon0 = math.radians(zone * 6 - 183)
    n = a / math.sqrt(1 - e2 * math.sin(lat_rad) ** 2)
    t = math.tan(lat_rad) ** 2
    c = ep2 * math.cos(lat_rad) ** 2
    aa = math.cos(lat_rad) * (lon_rad - lon0)
    m = a * (
        (1 - e2 / 4 - 3 * e2**2 / 64 - 5 * e2**3 / 256) * lat_rad
        - (3 * e2 / 8 + 3 * e2**2 / 32 + 45 * e2**3 / 1024) * math.sin(2 * lat_rad)
        + (15 * e2**2 / 256 + 45 * e2**3 / 1024) * math.sin(4 * lat_rad)
        - (35 * e2**3 / 3072) * math.sin(6 * lat_rad)
    )
    easting = k0 * n * (
        aa
        + (1 - t + c) * aa**3 / 6
        + (5 - 18 * t + t**2 + 72 * c - 58 * ep2) * aa**5 / 120
    ) + 500000.0
    northing = k0 * (
        m
        + n * math.tan(lat_rad) * (
            aa**2 / 2
            + (5 - t + 9 * c + 4 * c**2) * aa**4 / 24
            + (61 - 58 * t + t**2 + 600 * c - 330 * ep2) * aa**6 / 720
        )
    )
    return easting, northing


def _luftbild_wms_bbox(config: dict, z: int, x: int, y: int) -> tuple[list[float], float]:
    if config["crs"] in {"EPSG:3857", "EPSG:900913"}:
        return list(_tile_webmercator_bounds(z, x, y)), 0.0

    west, south, east, north = _tile_lonlat_bounds(z, x, y)
    center_lat = (south + north) / 2
    zone = 33 if config["crs"] == "EPSG:25833" else 32
    points = [
        _lonlat_to_utm(west, south, zone),
        _lonlat_to_utm(west, north, zone),
        _lonlat_to_utm(east, south, zone),
        _lonlat_to_utm(east, north, zone),
    ]
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return [min(xs), min(ys), max(xs), max(ys)], center_lat


def _luftbild_cache_path(state_slug: str, layer: str, crs: str, z: int, x: int, y: int) -> Path:
    safe_layer = re.sub(r"[^a-zA-Z0-9_.-]+", "_", layer).strip("_") or "layer"
    safe_crs = re.sub(r"[^a-zA-Z0-9_.-]+", "_", crs).strip("_") or "crs"
    return LUFTBILD_CACHE_DIR / str(LUFTBILD_TILE_SIZE) / state_slug / safe_layer / safe_crs / str(z) / str(x) / f"{y}.png"


def _luftbild_cache_usage() -> tuple[int, list[tuple[float, int, Path]]]:
    total = 0
    files: list[tuple[float, int, Path]] = []
    if not LUFTBILD_CACHE_DIR.exists():
        return total, files
    for path in LUFTBILD_CACHE_DIR.rglob("*.png"):
        try:
            stat = path.stat()
        except OSError:
            continue
        total += stat.st_size
        files.append((stat.st_mtime, stat.st_size, path))
    files.sort(key=lambda item: item[0])
    return total, files


def _luftbild_cache_disk_free() -> int:
    usage_path = LUFTBILD_CACHE_DIR if LUFTBILD_CACHE_DIR.exists() else DATA_DIR
    try:
        return shutil.disk_usage(usage_path).free
    except OSError:
        return 0


def _cleanup_luftbild_cache(force: bool = False) -> None:
    global _LUFTBILD_CACHE_LAST_CLEANUP
    now = time.time()
    if not force and now - _LUFTBILD_CACHE_LAST_CLEANUP < LUFTBILD_CACHE_CLEANUP_INTERVAL_SECONDS:
        return
    _LUFTBILD_CACHE_LAST_CLEANUP = now
    try:
        total, files = _luftbild_cache_usage()
        free_bytes = _luftbild_cache_disk_free()
        if total <= LUFTBILD_CACHE_MAX_BYTES and free_bytes >= LUFTBILD_MIN_FREE_BYTES:
            return
        target_bytes = min(LUFTBILD_CACHE_TARGET_BYTES, LUFTBILD_CACHE_MAX_BYTES)
        for _, size, path in files:
            if total <= target_bytes and _luftbild_cache_disk_free() >= LUFTBILD_MIN_FREE_BYTES:
                break
            try:
                path.unlink()
            except OSError:
                continue
            total -= size
    except Exception:
        return


def _write_luftbild_cache(cache_path: Path, data: bytes) -> bool:
    _cleanup_luftbild_cache()
    try:
        if _luftbild_cache_disk_free() - len(data) < LUFTBILD_MIN_FREE_BYTES:
            _cleanup_luftbild_cache(force=True)
        if _luftbild_cache_disk_free() - len(data) < LUFTBILD_MIN_FREE_BYTES:
            return False
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = cache_path.with_name(f".{cache_path.name}.{os.getpid()}.tmp")
        try:
            tmp_path.write_bytes(data)
            os.replace(tmp_path, cache_path)
        finally:
            tmp_path.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def _pmtiles_cache_path(object_key: str, offset: int, length: int) -> Path:
    digest = hashlib.sha256(object_key.encode("utf-8")).hexdigest()
    return PMTILES_CACHE_DIR / digest[:2] / digest / f"{offset}-{length}.bin"


def _pmtiles_cache_usage() -> tuple[int, list[tuple[float, int, Path]]]:
    total = 0
    files: list[tuple[float, int, Path]] = []
    if not PMTILES_CACHE_DIR.exists():
        return total, files
    for path in PMTILES_CACHE_DIR.rglob("*.bin"):
        try:
            stat = path.stat()
        except OSError:
            continue
        total += stat.st_size
        files.append((stat.st_mtime, stat.st_size, path))
    files.sort(key=lambda item: item[0])
    return total, files


def _pmtiles_cache_disk_free() -> int:
    usage_path = PMTILES_CACHE_DIR if PMTILES_CACHE_DIR.exists() else DATA_DIR
    try:
        return shutil.disk_usage(usage_path).free
    except OSError:
        return 0


def _cleanup_pmtiles_cache(force: bool = False) -> None:
    global _PMTILES_CACHE_LAST_CLEANUP
    now = time.time()
    if not force and now - _PMTILES_CACHE_LAST_CLEANUP < PMTILES_CACHE_CLEANUP_INTERVAL_SECONDS:
        return
    _PMTILES_CACHE_LAST_CLEANUP = now
    try:
        total, files = _pmtiles_cache_usage()
        free_bytes = _pmtiles_cache_disk_free()
        if total <= PMTILES_CACHE_MAX_BYTES and free_bytes >= PMTILES_MIN_FREE_BYTES:
            return
        target_bytes = min(PMTILES_CACHE_TARGET_BYTES, PMTILES_CACHE_MAX_BYTES)
        for _, size, path in files:
            if total <= target_bytes and _pmtiles_cache_disk_free() >= PMTILES_MIN_FREE_BYTES:
                break
            try:
                path.unlink()
            except OSError:
                continue
            total -= size
    except Exception:
        return


def _write_pmtiles_cache(cache_path: Path, data: bytes) -> bool:
    _cleanup_pmtiles_cache()
    try:
        if _pmtiles_cache_disk_free() - len(data) < PMTILES_MIN_FREE_BYTES:
            _cleanup_pmtiles_cache(force=True)
        if _pmtiles_cache_disk_free() - len(data) < PMTILES_MIN_FREE_BYTES:
            return False
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = cache_path.with_name(f".{cache_path.name}.{os.getpid()}.tmp")
        try:
            tmp_path.write_bytes(data)
            os.replace(tmp_path, cache_path)
        finally:
            tmp_path.unlink(missing_ok=True)
        return True
    except OSError:
        return False

TILE_BUCKET_PREFIX = os.environ.get("OPENKATASTER_TILE_BUCKET_PREFIX", "tiles").strip("/") or "tiles"
TILE_BUCKET_NAME = os.environ.get("OPENKATASTER_TILE_BUCKET") or os.environ.get("EXPORT_BUCKET")
TILE_BUCKET_ENDPOINT = os.environ.get("OPENKATASTER_TILE_BUCKET_ENDPOINT") or os.environ.get("EXPORT_BUCKET_ENDPOINT")
TILE_BUCKET_REGION = os.environ.get("OPENKATASTER_TILE_BUCKET_REGION") or os.environ.get("EXPORT_BUCKET_REGION")
TILE_BUCKET_ACCESS_KEY_ID = os.environ.get("OPENKATASTER_TILE_BUCKET_ACCESS_KEY_ID") or os.environ.get("HETZNER_BUCKET_ACCESS_KEY_ID")
TILE_BUCKET_SECRET_ACCESS_KEY = os.environ.get("OPENKATASTER_TILE_BUCKET_SECRET_ACCESS_KEY") or os.environ.get("HETZNER_BUCKET_SECRET_ACCESS_KEY")
ACTIVE_BUCKET_CACHE_SECONDS = int(os.environ.get("OPENKATASTER_TILE_ACTIVE_BUCKET_CACHE_SECONDS", "300"))
STATE_METADATA_CACHE_SECONDS = int(os.environ.get("OPENKATASTER_TILE_STATE_METADATA_CACHE_SECONDS", "900"))
STATE_METADATA_ENDPOINT = os.environ.get("OPENKATASTER_TILE_STATE_METADATA_ENDPOINT", "https://api.openkataster.de/v1/metadata").rstrip("/")
WEB_MIN_ZOOM = float(os.environ.get("OPENKATASTER_TILE_WEB_MIN_ZOOM", "4.0"))
GERMANY_BOUNDS = [5.5, 47.0, 15.6, 55.2]
GLYPHS_URL = os.environ.get(
    "OPENKATASTER_TILE_GLYPHS_URL",
    "/glyphs/{fontstack}/{range}.pbf",
)
GLYPHS_VERSION = os.environ.get("OPENKATASTER_TILE_GLYPHS_VERSION", "arial-fontnik-20260608")
USES_DEMO_GLYPHS = "demotiles.maplibre.org" in GLYPHS_URL
GLYPH_STACK_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,80}$")
GLYPH_RANGE_RE = re.compile(r"^\d+-\d+\.pbf$")
GLYPHS_DIR = Path(os.environ.get("OPENKATASTER_TILE_GLYPHS_DIR", str(DATA_DIR / "glyphs")))
ACTIVE_VOLUME_ROOT = Path(os.environ.get("OPENKATASTER_TILE_ACTIVE_VOLUME_ROOT", "/mnt/HC_Volume_105964091/openkataster-active"))
VOLUME_UPLOAD_PART_BYTES = int(os.environ.get("OPENKATASTER_TILE_VOLUME_UPLOAD_PART_BYTES", str(64 * 1024 * 1024)))
VOLUME_UPLOAD_MAX_PART_BYTES = int(os.environ.get("OPENKATASTER_TILE_VOLUME_UPLOAD_MAX_PART_BYTES", str(128 * 1024 * 1024)))
VOLUME_REQUIRED_FILES = {"alkis.pmtiles", "features.sqlite", "search.sqlite"}
VERSION_RE = re.compile(r"^[A-Za-z0-9_.-]{1,120}$")

NATIONAL_STYLE_PATH = Path(
    os.environ.get(
        "OPENKATASTER_TILE_NATIONAL_STYLE_PATH",
        str(Path(__file__).with_name("deutschland.style.json")),
    )
)


def style_glyphs_url() -> str:
    if not GLYPHS_VERSION:
        return GLYPHS_URL
    separator = "&" if "?" in GLYPHS_URL else "?"
    return f"{GLYPHS_URL}{separator}v={GLYPHS_VERSION}"
STATE_LABEL_SOURCE_ID = "openkataster_state_labels"
STATE_OUTLINE_SOURCE_ID = "openkataster_state_outlines"
STATE_LABEL_POINTS = {
    "baden-wurttemberg": ("Baden-Württemberg", 9.0, 48.62),
    "bayern": ("Bayern", 11.45, 48.95),
    "berlin": ("Berlin", 13.405, 52.52),
    "brandenburg": ("Brandenburg", 13.4, 52.05),
    "bremen": ("Bremen", 8.8, 53.12),
    "hamburg": ("Hamburg", 10.0, 53.55),
    "hessen": ("Hessen", 9.05, 50.65),
    "mecklenburg-vorpommern": ("Mecklenburg-Vorpommern", 12.55, 53.85),
    "niedersachsen": ("Niedersachsen", 9.6, 52.75),
    "nordrhein-westfalen": ("Nordrhein-Westfalen", 7.55, 51.45),
    "rheinland-pfalz": ("Rheinland-Pfalz", 7.45, 49.95),
    "saarland": ("Saarland", 6.95, 49.38),
    "sachsen": ("Sachsen", 13.45, 51.05),
    "sachsen-anhalt": ("Sachsen-Anhalt", 11.65, 51.95),
    "schleswig-holstein": ("Schleswig-Holstein", 9.75, 54.2),
    "thueringen": ("Thüringen", 11.0, 50.9),
}

_STATE_METADATA_CACHE: dict[str, object] = {"expires_at": 0.0, "states": []}
LOCAL_STATE_METADATA = {
    "sachsen-anhalt": {
        "bundesland": "Sachsen-Anhalt",
        "datenstand": "01.06.2026",
        "datenjahr": 2026,
        "quellenvermerk": "© GeoBasis-DE / LVermGeo LSA, dl-de/by-2-0",
        "lizenz": "dl-de/by-2-0",
    }
}


def _configured_keys() -> set[str]:
    raw = os.environ.get("OPENKATASTER_TILE_KEYS", "")
    return {part.strip() for part in raw.split(",") if part.strip()}


def _extract_bearer(value: str | None) -> str | None:
    if not value:
        return None
    prefix = "Bearer "
    if value.startswith(prefix):
        return value[len(prefix) :].strip()
    return None


def _base64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


@lru_cache(maxsize=1)
def _viewer_token_public_key():
    raw = (
        os.environ.get("OPENKATASTER_VIEWER_TOKEN_PUBLIC_KEY")
        or os.environ.get("PRIVATE_API_TOKEN_PUBLIC_KEY")
        or os.environ.get("API_TOKEN_PUBLIC_KEY")
        or ""
    ).strip()
    if not raw or load_pem_public_key is None:
        return None
    key = load_pem_public_key(raw.replace("\\n", "\n").encode("utf-8"))
    if Ed25519PublicKey is not None and not isinstance(key, Ed25519PublicKey):
        raise RuntimeError("viewer token public key must be Ed25519")
    return key


def _viewer_token_audience() -> str:
    return os.environ.get("OPENKATASTER_VIEWER_TOKEN_AUDIENCE") or os.environ.get("PRIVATE_API_TOKEN_AUDIENCE") or "lageplaner-api"


def _verify_viewer_token(token: str) -> dict | None:
    key = _viewer_token_public_key()
    if key is None:
        return None
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        header = json.loads(_base64url_decode(parts[0]).decode("utf-8"))
        if header.get("alg") != "EdDSA":
            return None
        signing_input = f"{parts[0]}.{parts[1]}".encode("ascii")
        signature = _base64url_decode(parts[2])
        key.verify(signature, signing_input)
        claims = json.loads(_base64url_decode(parts[1]).decode("utf-8"))
        now = int(time.time())
        if int(claims.get("nbf", 0)) > now:
            return None
        if int(claims.get("exp", 0)) < now:
            return None
        expected_audience = _viewer_token_audience()
        audience = claims.get("aud")
        if expected_audience and audience != expected_audience:
            return None
        return claims if isinstance(claims, dict) else None
    except Exception:
        return None


def _claims_grant_pro_access(claims: dict) -> bool:
    scopes = claims.get("scopes")
    if isinstance(scopes, list) and ("feature:read" in scopes or "measure" in scopes):
        return True
    plan = str(claims.get("plan") or "").lower()
    return plan in {"pro", "onoffice_pro", "professional", "starter", "beta"}


def _public_access_claims(access: "ApiAccessContext") -> dict:
    claims = access.claims or {}
    scopes = claims.get("scopes")
    if not isinstance(scopes, list):
        scopes = [
            "map:view",
            "search:basic",
            "layers:basic",
            "layers:advanced",
            "feature:read",
            "measure",
            "export:map",
            "export:cadastre",
        ] if access.is_pro else ["map:view", "search:basic", "layers:basic"]
    return {
        "access": access.mode,
        "authenticated": access.is_pro,
        "plan": claims.get("plan") or ("pro" if access.is_pro else "free"),
        "integration": claims.get("integration"),
        "subject": claims.get("sub"),
        "name": claims.get("name"),
        "scopes": scopes,
        "expires_at": claims.get("exp"),
    }


def _state_metadata_slug(value: str) -> str:
    normalized = (value or "").strip().lower()
    replacements = (
        ("ä", "ae"),
        ("ö", "oe"),
        ("ü", "ue"),
        ("ß", "ss"),
        (" ", "-"),
    )
    for source, target in replacements:
        normalized = normalized.replace(source, target)
    normalized = re.sub(r"[^a-z0-9-]+", "-", normalized)
    return normalized.strip("-")


def _merge_local_state_metadata(states: list[dict]) -> list[dict]:
    merged = list(states)
    existing = {
        _state_metadata_slug(str(state.get("bundesland") or state.get("state") or state.get("name") or ""))
        for state in merged
    }
    for slug, metadata in LOCAL_STATE_METADATA.items():
        if slug not in existing:
            merged.append(dict(metadata))
    return merged


def _state_metadata_cache() -> list[dict]:
    now = time.time()
    if _STATE_METADATA_CACHE["expires_at"] >= now:
        return _STATE_METADATA_CACHE["states"]  # type: ignore[return-value]

    request = urllib.request.Request(
        STATE_METADATA_ENDPOINT,
        headers={"Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            if response.status != 200:
                return _STATE_METADATA_CACHE["states"]  # type: ignore[return-value]
            payload = json.loads(response.read().decode("utf-8"))
            if isinstance(payload, dict):
                states = payload.get("states", [])
            else:
                states = payload
            if not isinstance(states, list):
                return _STATE_METADATA_CACHE["states"]  # type: ignore[return-value]
            states = [state for state in states if isinstance(state, dict)]
            if not states:
                return _STATE_METADATA_CACHE["states"]  # type: ignore[return-value]
            states = _merge_local_state_metadata(states)
            _STATE_METADATA_CACHE["states"] = states
            _STATE_METADATA_CACHE["expires_at"] = now + STATE_METADATA_CACHE_SECONDS
            return states
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError):
        return _STATE_METADATA_CACHE["states"]  # type: ignore[return-value]


def require_api_key(
    key: Annotated[str | None, Query()] = None,
    api_key: Annotated[str | None, Query()] = None,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> str:
    allowed = _configured_keys()
    if not allowed:
        raise HTTPException(status_code=503, detail="tile service has no API keys configured")

    provided = key or api_key or x_api_key or _extract_bearer(authorization)
    if not provided or provided not in allowed:
        raise HTTPException(status_code=401, detail="invalid API key")
    return provided




@dataclass(frozen=True)
class ApiAccessContext:
    mode: str
    token: str | None = None
    claims: dict | None = None

    @property
    def is_pro(self) -> bool:
        return self.mode in {"pro", "partner"}


def _configured_pro_tokens() -> set[str]:
    raw = os.environ.get("OPENKATASTER_TILE_PRO_TOKENS", "")
    tokens = {part.strip() for part in raw.split(",") if part.strip()}
    return tokens or _configured_keys()


def api_access_context(
    token: Annotated[str | None, Query()] = None,
    api_key: Annotated[str | None, Query()] = None,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> ApiAccessContext:
    provided = token or api_key or x_api_key or _extract_bearer(authorization)
    if provided and provided in _configured_pro_tokens():
        return ApiAccessContext(mode="pro", token=provided)
    if provided:
        claims = _verify_viewer_token(provided)
        if claims and _claims_grant_pro_access(claims):
            mode = "partner" if claims.get("integration") else "pro"
            return ApiAccessContext(mode=mode, token=provided, claims=claims)
    return ApiAccessContext(mode="free")

def _configured_admin_keys() -> set[str]:
    raw = os.environ.get("OPENKATASTER_TILE_ADMIN_KEYS", "")
    configured = {part.strip() for part in raw.split(",") if part.strip()}
    return configured or _configured_keys()


def require_admin_key(
    key: Annotated[str | None, Query()] = None,
    api_key: Annotated[str | None, Query()] = None,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> str:
    allowed = _configured_admin_keys()
    if not allowed:
        raise HTTPException(status_code=503, detail="tile service has no admin API keys configured")

    provided = key or api_key or x_api_key or _extract_bearer(authorization)
    if not provided or provided not in allowed:
        raise HTTPException(status_code=401, detail="invalid admin API key")
    return provided


def require_openkataster_admin_token(
    authorization: Annotated[str | None, Header()] = None,
) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing admin token")
    request = urllib.request.Request(
        f"{ADMIN_API_BASE_URL}/v1/admin/auth/me",
        headers={"Authorization": authorization, "Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            if response.status != 200:
                raise HTTPException(status_code=401, detail="invalid admin token")
    except urllib.error.HTTPError as exc:
        raise HTTPException(status_code=401, detail="invalid admin token") from exc
    except urllib.error.URLError as exc:
        raise HTTPException(status_code=502, detail="admin api validation failed") from exc
    return authorization[7:].strip()


class Dataset:
    def __init__(self, path: Path):
        self.path = path
        self.size = path.stat().st_size
        self.file = path.open("rb")
        self.reader = Reader(MmapSource(self.file))
        self.header = self.reader.header()
        self.metadata = self.reader.metadata()

    def tile(self, z: int, x: int, y: int) -> bytes | None:
        return self.reader.get(z, x, y)

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        return (
            float(self.header.get("min_lon_e7", -1800000000)) / 1e7,
            float(self.header.get("min_lat_e7", -850000000)) / 1e7,
            float(self.header.get("max_lon_e7", 1800000000)) / 1e7,
            float(self.header.get("max_lat_e7", 850000000)) / 1e7,
        )

    @property
    def min_zoom(self) -> int:
        return int(self.header.get("min_zoom", 0))

    @property
    def max_zoom(self) -> int:
        return int(self.header.get("max_zoom", 20))

    @property
    def is_gzip(self) -> bool:
        compression = self.header.get("tile_compression")
        return (
            compression == Compression.GZIP
            or str(compression).lower().endswith("gzip")
            or compression == 2
        )

    def close(self) -> None:
        self.file.close()


class CachedBucketRangeSource:
    def __init__(self, object_key: str):
        self.object_key = object_key
        self.bucket_name = _normalize_bucket_name(TILE_BUCKET_NAME)

    def __call__(self, offset: int, length: int) -> bytes:
        if length <= 0:
            return b""
        cache_path = _pmtiles_cache_path(self.object_key, offset, length)
        if cache_path.exists():
            try:
                cache_path.touch()
                return cache_path.read_bytes()
            except OSError:
                pass
        response = _s3_client().get_object(
            Bucket=self.bucket_name,
            Key=self.object_key,
            Range=f"bytes={offset}-{offset + length - 1}",
        )
        data = response["Body"].read()
        _write_pmtiles_cache(cache_path, data)
        return data


class BucketDataset:
    def __init__(self, object_key: str, size: int = 0):
        self.path = Path(object_key)
        self.object_key = object_key
        self.size = size
        self.reader = Reader(CachedBucketRangeSource(object_key))
        self.header = self.reader.header()
        self.metadata = self.reader.metadata()

    def tile(self, z: int, x: int, y: int) -> bytes | None:
        return self.reader.get(z, x, y)

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        return (
            float(self.header.get("min_lon_e7", -1800000000)) / 1e7,
            float(self.header.get("min_lat_e7", -850000000)) / 1e7,
            float(self.header.get("max_lon_e7", 1800000000)) / 1e7,
            float(self.header.get("max_lat_e7", 850000000)) / 1e7,
        )

    @property
    def min_zoom(self) -> int:
        return int(self.header.get("min_zoom", 0))

    @property
    def max_zoom(self) -> int:
        return int(self.header.get("max_zoom", 20))

    @property
    def is_gzip(self) -> bool:
        compression = self.header.get("tile_compression")
        return (
            compression == Compression.GZIP
            or str(compression).lower().endswith("gzip")
            or compression == 2
        )

    def close(self) -> None:
        return None


@dataclass(frozen=True)
class MosaicEntry:
    name: str
    kind: str
    path: Path
    dataset: Dataset | BucketDataset


@dataclass(frozen=True)
class FeatureDbEntry:
    name: str
    path: Path


@dataclass(frozen=True)
class BucketPmtilesRef:
    state: str
    kind: str
    filename: str
    object_key: str
    size: int
    etag: str


def dataset_path(dataset: str) -> Path:
    if not DATASET_RE.match(dataset):
        raise HTTPException(status_code=404, detail="dataset not found")
    path = DATA_DIR / f"{dataset}.pmtiles"
    if not path.exists():
        raise HTTPException(status_code=404, detail="dataset not found")
    return path


@lru_cache(maxsize=32)
def load_dataset(dataset: str, mtime_ns: int) -> Dataset:
    return Dataset(DATA_DIR / f"{dataset}.pmtiles")


@lru_cache(maxsize=128)
def load_bucket_dataset(object_key: str, size: int, etag: str) -> BucketDataset:
    del etag
    return BucketDataset(object_key, size)


@lru_cache(maxsize=64)
def load_volume_dataset(path_text: str, mtime_ns: int, size: int) -> Dataset:
    del mtime_ns, size
    return Dataset(Path(path_text))


def get_dataset(dataset: str) -> Dataset:
    path = dataset_path(dataset)
    return load_dataset(dataset, path.stat().st_mtime_ns)


def _direct_source_for_dataset(dataset: str) -> dict | None:
    if not DATASET_RE.match(dataset):
        return None
    try:
        style = national_style_template()
    except Exception:
        return None
    for source in style.get("sources", {}).values():
        if not isinstance(source, dict):
            continue
        if str(source.get("openkataster_direct_dataset") or "").strip() == dataset:
            return source
    return None


def _direct_shard_names(dataset: str) -> tuple[str, ...]:
    source = _direct_source_for_dataset(dataset)
    if not source:
        return ()
    raw_shards = source.get("openkataster_direct_shards") or ()
    if not isinstance(raw_shards, list):
        return ()
    shards = []
    for item in raw_shards:
        name = str(item or "").strip().removesuffix(".pmtiles")
        if DATASET_RE.match(name) and (DATA_DIR / f"{name}.pmtiles").exists():
            shards.append(name)
    return tuple(dict.fromkeys(shards))


def _direct_shard_signature(dataset: str) -> tuple[tuple[str, int, int], ...]:
    return tuple(
        (
            f"{name}.pmtiles",
            (DATA_DIR / f"{name}.pmtiles").stat().st_mtime_ns,
            (DATA_DIR / f"{name}.pmtiles").stat().st_size,
        )
        for name in _direct_shard_names(dataset)
    )


def direct_shard_datasets(dataset: str) -> tuple[Dataset, ...]:
    result = []
    for name in _direct_shard_names(dataset):
        path = DATA_DIR / f"{name}.pmtiles"
        result.append(load_dataset(name, path.stat().st_mtime_ns))
    return tuple(result)


def compression_header(ds: Dataset) -> dict[str, str]:
    if ds.is_gzip:
        return {"Content-Encoding": "gzip"}
    return {}


def is_virtual_germany_dataset(dataset: str) -> bool:
    return dataset == VIRTUAL_GERMANY_DATASET


def tile_bounds(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    n = 2.0**z
    west = x / n * 360.0 - 180.0
    east = (x + 1) / n * 360.0 - 180.0

    def tile_y_to_lat(tile_y: int) -> float:
        mercator_y = math.pi * (1 - 2 * tile_y / n)
        return math.degrees(math.atan(math.sinh(mercator_y)))

    north = tile_y_to_lat(y)
    south = tile_y_to_lat(y + 1)
    return west, south, east, north


def bounds_intersect(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> bool:
    left_w, left_s, left_e, left_n = left
    right_w, right_s, right_e, right_n = right
    return left_w <= right_e and left_e >= right_w and left_s <= right_n and left_n >= right_s


def _strip_active_suffix(stem: str) -> tuple[str, str, int]:
    detail_shard_match = DETAIL_SHARD_SUFFIX_RE.match(stem)
    if detail_shard_match:
        return detail_shard_match.group("base"), "detail", 4
    if stem.endswith("_overview"):
        return stem.removesuffix("_overview"), "overview", 3
    if stem.endswith("_detail"):
        return stem.removesuffix("_detail"), "detail", 3
    return stem, "detail", 1


def _mosaic_state_key(path: Path) -> tuple[str, str, tuple[int, str, int]]:
    base, kind, kind_rank = _strip_active_suffix(path.stem)
    unversioned_base = DATE_SUFFIX_RE.sub("", base)
    is_unversioned = int(unversioned_base == base)
    return unversioned_base, kind, (is_unversioned, base, kind_rank)


def _mosaic_scan_signature() -> tuple[tuple[str, int, int], ...]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    active_state_key = ",".join(active_bucket_state_keys())
    active_marker = ("__active_states__", int(hashlib.sha1(active_state_key.encode("utf-8")).hexdigest()[:15], 16), len(active_state_key))
    bucket_refs = tuple(
        (
            f"bucket:{ref.object_key}",
            int(hashlib.sha1(ref.etag.encode("utf-8")).hexdigest()[:15], 16),
            ref.size,
        )
        for ref in bucket_pmtiles_refs()
    )
    volume_refs = tuple(
        (f"volume:{state_key}:{pmtiles_path}", pmtiles_path.stat().st_mtime_ns, pmtiles_path.stat().st_size)
        for state_key, pmtiles_path in active_volume_pmtiles_paths()
    )
    return tuple(
        sorted(
            (path.name, path.stat().st_mtime_ns, path.stat().st_size)
            for path in DATA_DIR.glob("*.pmtiles")
            if not path.name.startswith(f"{VIRTUAL_GERMANY_DATASET}.")
            and path.stem != VIRTUAL_GERMANY_DATASET
        )
        + [active_marker]
        + list(volume_refs)
        + list(bucket_refs)
    )


def _pmtiles_ref_kind(filename: str) -> str | None:
    if filename == "overview.pmtiles" or filename.endswith("_overview.pmtiles"):
        return "overview"
    if filename == "alkis.pmtiles" or filename.endswith("_detail.pmtiles"):
        return "detail"
    if filename.startswith("alkis_detail_") and filename.endswith(".pmtiles"):
        return "detail"
    return None


def _manifest_file_size(item: dict) -> int:
    for key in ("bytes", "size", "content_length"):
        value = item.get(key)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return 0


@lru_cache(maxsize=8)
def _bucket_pmtiles_refs_cached(slot: int) -> tuple[BucketPmtilesRef, ...]:
    del slot
    refs: list[BucketPmtilesRef] = []
    for state_slug in active_bucket_state_keys():
        try:
            manifest = _read_bucket_json(_active_manifest_key(state_slug, None))
        except Exception:
            continue
        files = manifest.get("files")
        if not isinstance(files, list):
            continue
        for item in files:
            if not isinstance(item, dict):
                continue
            filename = str(item.get("filename") or "")
            object_key = str(item.get("object_key") or "")
            kind = _pmtiles_ref_kind(filename)
            if not kind or not object_key:
                continue
            etag = str(item.get("etag") or item.get("md5") or manifest.get("version_name") or object_key)
            refs.append(
                BucketPmtilesRef(
                    state=state_slug,
                    kind=kind,
                    filename=filename,
                    object_key=object_key,
                    size=_manifest_file_size(item),
                    etag=etag,
                )
            )
    return tuple(refs)


def bucket_pmtiles_refs() -> tuple[BucketPmtilesRef, ...]:
    return _bucket_pmtiles_refs_cached(_active_bucket_cache_slot())


@lru_cache(maxsize=8)
def discover_mosaic_entries(signature: tuple[tuple[str, int, int], ...]) -> tuple[MosaicEntry, ...]:
    del signature
    active_states = set(active_bucket_state_keys())
    active_states -= direct_runtime_state_keys()
    selected: dict[tuple[str, str], tuple[Path, tuple[int, str, int]]] = {}
    local_detail_sharded_states: set[str] = set()
    entries = []

    active_volume_states: set[str] = set()
    for state_key, path in active_volume_pmtiles_paths():
        if state_key not in active_states:
            continue
        active_volume_states.add(state_key)
        stat = path.stat()
        entries.append(
            MosaicEntry(
                name=state_key,
                kind="detail",
                path=path,
                dataset=load_volume_dataset(str(path), stat.st_mtime_ns, stat.st_size),
            )
        )

    for path in DATA_DIR.glob("*.pmtiles"):
        if path.name.startswith(f"{VIRTUAL_GERMANY_DATASET}.") or path.stem == VIRTUAL_GERMANY_DATASET:
            continue
        state_key, kind, rank = _mosaic_state_key(path)
        if state_key in active_volume_states:
            continue
        if state_key not in active_states:
            continue
        if DETAIL_SHARD_SUFFIX_RE.match(path.stem):
            local_detail_sharded_states.add(state_key)
            entries.append(
                MosaicEntry(
                    name=state_key,
                    kind=kind,
                    path=path,
                    dataset=load_dataset(path.stem, path.stat().st_mtime_ns),
                )
            )
            continue
        current = selected.get((state_key, kind))
        if current is None or rank > current[1]:
            selected[(state_key, kind)] = (path, rank)

    for (state_key, kind), (path, _) in sorted(selected.items()):
        entries.append(
            MosaicEntry(
                name=state_key,
                kind=kind,
                path=path,
                dataset=load_dataset(path.stem, path.stat().st_mtime_ns),
            )
        )
    local_state_kinds = {(entry.name, entry.kind) for entry in entries}
    direct_runtime_states = direct_runtime_state_keys()
    for ref in bucket_pmtiles_refs():
        if ref.state in direct_runtime_states:
            continue
        if ref.kind == "detail" and ref.state in local_detail_sharded_states:
            continue
        if (ref.state, ref.kind) in local_state_kinds:
            continue
        entries.append(
            MosaicEntry(
                name=ref.state,
                kind=ref.kind,
                path=Path(ref.object_key),
                dataset=load_bucket_dataset(ref.object_key, ref.size, ref.etag),
            )
        )
    return tuple(entries)


def mosaic_entries() -> tuple[MosaicEntry, ...]:
    return discover_mosaic_entries(_mosaic_scan_signature())


def _feature_state_key(path: Path) -> tuple[str, tuple[int, str]]:
    name = path.name
    if not name.endswith(FEATURE_DB_SUFFIX):
        return path.stem, (0, path.stem)
    base = name[: -len(FEATURE_DB_SUFFIX)]
    unversioned_base = DATE_SUFFIX_RE.sub("", base)
    is_unversioned = int(unversioned_base == base)
    return unversioned_base, (is_unversioned, base)


def _feature_db_scan_signature() -> tuple[tuple[str, int, int], ...]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    active_state_key = ",".join(active_bucket_state_keys())
    active_marker = ("__bucket_active__", int(hashlib.sha1(active_state_key.encode("utf-8")).hexdigest()[:15], 16), len(active_state_key))
    return tuple(
        sorted(
            (path.name, path.stat().st_mtime_ns, path.stat().st_size)
            for path in DATA_DIR.glob(f"*{FEATURE_DB_SUFFIX}")
        )
        + [active_marker]
    )


@lru_cache(maxsize=8)
def discover_feature_db_entries(signature: tuple[tuple[str, int, int], ...]) -> tuple[FeatureDbEntry, ...]:
    del signature
    active_states = set(active_bucket_state_keys())
    selected: dict[str, tuple[Path, tuple[int, str]]] = {}
    for path in DATA_DIR.glob(f"*{FEATURE_DB_SUFFIX}"):
        state_key, rank = _feature_state_key(path)
        if state_key not in active_states:
            continue
        current = selected.get(state_key)
        if current is None or rank > current[1]:
            selected[state_key] = (path, rank)
    return tuple(FeatureDbEntry(name=name, path=path) for name, (path, _) in sorted(selected.items()))


@lru_cache(maxsize=8)
def discover_all_local_feature_db_entries(signature: tuple[tuple[str, int, int], ...]) -> tuple[FeatureDbEntry, ...]:
    del signature
    selected: dict[str, tuple[Path, tuple[int, str]]] = {}
    for path in DATA_DIR.glob(f"*{FEATURE_DB_SUFFIX}"):
        state_key, rank = _feature_state_key(path)
        current = selected.get(state_key)
        if current is None or rank > current[1]:
            selected[state_key] = (path, rank)
    return tuple(FeatureDbEntry(name=name, path=path) for name, (path, _) in sorted(selected.items()))


def feature_db_entries() -> tuple[FeatureDbEntry, ...]:
    return discover_feature_db_entries(_feature_db_scan_signature())


def all_local_feature_db_entries() -> tuple[FeatureDbEntry, ...]:
    return discover_all_local_feature_db_entries(_feature_db_scan_signature())


def feature_db_entries_for_dataset(dataset: str) -> tuple[FeatureDbEntry, ...]:
    entries = feature_db_entries()
    if is_virtual_germany_dataset(dataset):
        selected = {entry.name: entry for entry in all_local_feature_db_entries()}
        selected.update({entry.name: entry for entry in entries})
        return tuple(entry for _, entry in sorted(selected.items()))
    state_key, _, _ = _mosaic_state_key(DATA_DIR / f"{dataset}.pmtiles")
    matched = tuple(entry for entry in entries if entry.name == state_key)
    if matched:
        return matched
    # Prototype datasets can have a local feature index before their tiles are active.
    # Keep the global Germany endpoint tied to active states, but allow explicit
    # /api/features/{dataset}/... calls to use a matching local SQLite index.
    explicit_path = DATA_DIR / f"{state_key}{FEATURE_DB_SUFFIX}"
    if explicit_path.exists():
        return (FeatureDbEntry(name=state_key, path=explicit_path),)
    return ()


def _search_state_key(path: Path) -> tuple[str, tuple[int, str]]:
    name = path.name
    if not name.endswith(SEARCH_DB_SUFFIX):
        return path.stem, (0, path.stem)
    base = name[: -len(SEARCH_DB_SUFFIX)]
    unversioned_base = DATE_SUFFIX_RE.sub("", base)
    is_unversioned = int(unversioned_base == base)
    return unversioned_base, (is_unversioned, base)


def _search_db_scan_signature() -> tuple[tuple[str, int, int], ...]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    active_state_key = ",".join(active_bucket_state_keys())
    active_marker = ("__bucket_active__", int(hashlib.sha1(active_state_key.encode("utf-8")).hexdigest()[:15], 16), len(active_state_key))
    return tuple(
        sorted(
            (path.name, path.stat().st_mtime_ns, path.stat().st_size)
            for path in DATA_DIR.glob(f"*{SEARCH_DB_SUFFIX}")
        )
        + [active_marker]
    )


@lru_cache(maxsize=8)
def discover_search_db_entries(signature: tuple[tuple[str, int, int], ...]) -> tuple[FeatureDbEntry, ...]:
    del signature
    active_states = set(active_bucket_state_keys())
    selected: dict[str, tuple[Path, tuple[int, str]]] = {}
    for path in DATA_DIR.glob(f"*{SEARCH_DB_SUFFIX}"):
        state_key, rank = _search_state_key(path)
        if state_key not in active_states:
            continue
        current = selected.get(state_key)
        if current is None or rank > current[1]:
            selected[state_key] = (path, rank)
    return tuple(FeatureDbEntry(name=name, path=path) for name, (path, _) in sorted(selected.items()))


@lru_cache(maxsize=8)
def discover_all_local_search_db_entries(signature: tuple[tuple[str, int, int], ...]) -> tuple[FeatureDbEntry, ...]:
    del signature
    selected: dict[str, tuple[Path, tuple[int, str]]] = {}
    for path in DATA_DIR.glob(f"*{SEARCH_DB_SUFFIX}"):
        state_key, rank = _search_state_key(path)
        current = selected.get(state_key)
        if current is None or rank > current[1]:
            selected[state_key] = (path, rank)
    return tuple(FeatureDbEntry(name=name, path=path) for name, (path, _) in sorted(selected.items()))


def search_db_entries() -> tuple[FeatureDbEntry, ...]:
    return discover_search_db_entries(_search_db_scan_signature())


def all_local_search_db_entries() -> tuple[FeatureDbEntry, ...]:
    return discover_all_local_search_db_entries(_search_db_scan_signature())


def search_db_entries_for_dataset(dataset: str) -> tuple[FeatureDbEntry, ...]:
    entries = search_db_entries()
    if is_virtual_germany_dataset(dataset):
        selected = {entry.name: entry for entry in all_local_search_db_entries()}
        selected.update({entry.name: entry for entry in entries})
        return tuple(entry for _, entry in sorted(selected.items()))
    state_key, _, _ = _mosaic_state_key(DATA_DIR / f"{dataset}.pmtiles")
    matched = tuple(entry for entry in entries if entry.name == state_key)
    if matched:
        return matched
    explicit_path = DATA_DIR / f"{state_key}{SEARCH_DB_SUFFIX}"
    if explicit_path.exists():
        return (FeatureDbEntry(name=state_key, path=explicit_path),)
    return ()


def search_db_entries_for_states(states: set[str] | tuple[str, ...] | list[str]) -> tuple[FeatureDbEntry, ...]:
    wanted = {state for state in states if state}
    if not wanted:
        return ()
    selected = {entry.name: entry for entry in all_local_search_db_entries() if entry.name in wanted}
    selected.update({entry.name: entry for entry in search_db_entries() if entry.name in wanted})
    return tuple(entry for _, entry in sorted(selected.items()))


def search_db_signature_for_states(states: set[str] | tuple[str, ...] | list[str]) -> tuple[tuple[str, str, int, int], ...]:
    return tuple((entry.name, str(entry.path), *sqlite_file_signature(entry.path)) for entry in search_db_entries_for_states(states))


_SEARCH_DB_CONNECTIONS: dict[str, tuple[tuple[int, int], sqlite3.Connection]] = {}
_SEARCH_DB_CONNECTION_LOCK = threading.Lock()


def search_db_connection(path: Path) -> sqlite3.Connection:
    key = str(path)
    signature = sqlite_file_signature(path)
    current = _SEARCH_DB_CONNECTIONS.get(key)
    if current and current[0] == signature:
        return current[1]
    with _SEARCH_DB_CONNECTION_LOCK:
        current = _SEARCH_DB_CONNECTIONS.get(key)
        if current and current[0] == signature:
            return current[1]
        if current:
            try:
                current[1].close()
            except Exception:
                pass
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10, check_same_thread=False)
        con.row_factory = sqlite3.Row
        try:
            con.execute("PRAGMA query_only = ON")
            con.execute("PRAGMA mmap_size = 1073741824")
            con.execute("PRAGMA cache_size = -131072")
            con.execute("PRAGMA temp_store = MEMORY")
        except sqlite3.Error:
            pass
        _SEARCH_DB_CONNECTIONS[key] = (signature, con)
        return con


def _normalize_bucket_name(raw_value: str | None) -> str:
    if not raw_value:
        raise HTTPException(status_code=503, detail="tile bucket is not configured")
    normalized = raw_value.strip().rstrip("/")
    if "://" in normalized:
        normalized = normalized.split("://", 1)[1]
    return normalized.split("/", 1)[0]


def _normalize_endpoint(raw_value: str | None) -> str | None:
    if not raw_value:
        return None
    value = raw_value.strip().rstrip("/")
    if "://" not in value:
        value = f"https://{value}"
    return value


@lru_cache(maxsize=1)
def _s3_client():
    try:
        import boto3
        from botocore.config import Config
    except ImportError as exc:
        raise HTTPException(status_code=503, detail="boto3 is not installed on tile service") from exc

    kwargs = {
        "config": Config(
            connect_timeout=2,
            read_timeout=5,
            retries={"max_attempts": 2},
        )
    }
    endpoint = _normalize_endpoint(TILE_BUCKET_ENDPOINT)
    if endpoint:
        kwargs["endpoint_url"] = endpoint
    if TILE_BUCKET_REGION:
        kwargs["region_name"] = TILE_BUCKET_REGION
    if TILE_BUCKET_ACCESS_KEY_ID:
        kwargs["aws_access_key_id"] = TILE_BUCKET_ACCESS_KEY_ID
    if TILE_BUCKET_SECRET_ACCESS_KEY:
        kwargs["aws_secret_access_key"] = TILE_BUCKET_SECRET_ACCESS_KEY
    return boto3.client("s3", **kwargs)


def _read_bucket_json(object_key: str) -> dict:
    bucket_name = _normalize_bucket_name(TILE_BUCKET_NAME)
    response = _s3_client().get_object(Bucket=bucket_name, Key=object_key)
    return json.loads(response["Body"].read().decode("utf-8"))


def _active_bucket_cache_slot() -> int:
    return int(time.time() // max(1, ACTIVE_BUCKET_CACHE_SECONDS))


@lru_cache(maxsize=8)
def _active_bucket_state_keys_cached(slot: int) -> tuple[str, ...]:
    del slot
    if not TILE_BUCKET_NAME:
        return tuple()

    try:
        bucket_name = _normalize_bucket_name(TILE_BUCKET_NAME)
        prefix = f"{TILE_BUCKET_PREFIX}/"
        active_states: set[str] = set()
        for state_slug in STATE_LABEL_POINTS:
            try:
                _s3_client().head_object(Bucket=bucket_name, Key=f"{prefix}{state_slug}/active.json")
            except Exception:
                continue
            active_states.add(state_slug)
        return tuple(sorted(active_states))
    except Exception:
        return tuple()


def local_active_state_keys() -> tuple[str, ...]:
    """Direct-volume deployments can activate states by placing PMTiles symlinks in DATA_DIR."""
    states: set[str] = set()
    try:
        for path in DATA_DIR.glob("*.pmtiles"):
            if path.name.startswith(f"{VIRTUAL_GERMANY_DATASET}.") or path.stem == VIRTUAL_GERMANY_DATASET:
                continue
            state_key, _, _ = _mosaic_state_key(path)
            if state_key in STATE_LABEL_POINTS:
                states.add(state_key)
    except Exception:
        return tuple()
    return tuple(sorted(states))


def active_volume_pmtiles_paths() -> tuple[tuple[str, Path], ...]:
    """Active tile-volume versions store alkis.pmtiles below ACTIVE_VOLUME_ROOT/versions."""
    active_dir = ACTIVE_VOLUME_ROOT / "active"
    versions_root = (ACTIVE_VOLUME_ROOT / "versions").resolve()
    result: list[tuple[str, Path]] = []
    try:
        manifest_paths = sorted(active_dir.glob("*.json"))
    except OSError:
        return tuple()
    for manifest_path in manifest_paths:
        try:
            manifest = json.loads(manifest_path.read_text())
        except Exception:
            continue
        state_slug = str(manifest.get("state_slug") or manifest_path.stem).strip()
        if state_slug not in STATE_LABEL_POINTS:
            continue
        raw_version_path = str(manifest.get("remote_version_path") or "").strip()
        if not raw_version_path:
            continue
        try:
            version_dir = Path(raw_version_path).resolve()
            version_dir.relative_to(versions_root)
        except Exception:
            continue
        pmtiles_path = version_dir / "alkis.pmtiles"
        if pmtiles_path.is_file():
            result.append((state_slug, pmtiles_path))
    return tuple(result)


def active_volume_state_keys() -> tuple[str, ...]:
    return tuple(sorted({state_key for state_key, _ in active_volume_pmtiles_paths()}))


def active_bucket_state_keys() -> tuple[str, ...]:
    return tuple(
        sorted(
            set(_active_bucket_state_keys_cached(_active_bucket_cache_slot()))
            | set(local_active_state_keys())
            | set(active_volume_state_keys())
        )
    )


def direct_runtime_state_keys() -> set[str]:
    """States served through explicit direct runtime sources should not also be included in the generic mosaic source."""
    try:
        style = national_style_template()
    except Exception:
        return set()
    result: set[str] = set()
    for source in style.get("sources", {}).values():
        if not isinstance(source, dict):
            continue
        direct_state = str(source.get("openkataster_direct_state") or "").strip()
        if direct_state and DATASET_RE.match(direct_state):
            result.add(direct_state)
            continue
        dataset = str(source.get("openkataster_direct_dataset") or "")
        if dataset.endswith("_runtime"):
            result.add(dataset.removesuffix("_runtime"))
    return result


def _download_bucket_object(object_key: str, target_path: Path) -> None:
    bucket_name = _normalize_bucket_name(TILE_BUCKET_NAME)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target_path.with_name(f".{target_path.name}.tmp")
    tmp_path.unlink(missing_ok=True)
    try:
        with tmp_path.open("wb") as handle:
            _s3_client().download_fileobj(bucket_name, object_key, handle)
        os.replace(tmp_path, target_path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def _clear_data_caches() -> None:
    load_dataset.cache_clear()
    load_bucket_dataset.cache_clear()
    discover_mosaic_entries.cache_clear()
    discover_feature_db_entries.cache_clear()
    _active_bucket_state_keys_cached.cache_clear()
    _bucket_pmtiles_refs_cached.cache_clear()
    _mosaic_tile_cached.cache_clear()
    gn250_place_entries.cache_clear()
    _search_places_for_dataset_cached.cache_clear()
    openplz_lookup_postcode.cache_clear()
    postcode_area_lookup.cache_clear()
    geocoder_lookup.cache_clear()
    geocoder_direct_lookup.cache_clear()
    fast_parcel_lookup.cache_clear()


def _active_manifest_key(state_slug: str, version_name: str | None) -> str:
    if version_name:
        safe_version = version_name.strip().strip("/")
        if not safe_version or "/" in safe_version or ".." in safe_version:
            raise HTTPException(status_code=400, detail="invalid tile version")
        return f"{TILE_BUCKET_PREFIX}/{state_slug}/{safe_version}/manifest.json"
    return f"{TILE_BUCKET_PREFIX}/{state_slug}/active.json"


def _tile_sync_targets(state_slug: str) -> dict[str, list[str]]:
    return {
        "alkis.pmtiles": [f"{state_slug}_detail.pmtiles"],
        "overview.pmtiles": [f"{state_slug}_overview.pmtiles"],
        "features.sqlite": [f"{state_slug}{FEATURE_DB_SUFFIX}"],
        "search.sqlite": [f"{state_slug}{SEARCH_DB_SUFFIX}"],
        "alkis_overview_boundaries.json": [f"{state_slug}_overview_boundaries.json"],
        "alkis_overview_labels.json": [f"{state_slug}_overview_labels.json"],
    }


def sync_bucket_tiles(state_slug: str, version_name: str | None = None) -> dict:
    if not DATASET_RE.match(state_slug):
        raise HTTPException(status_code=400, detail="invalid state slug")

    manifest_key = _active_manifest_key(state_slug, version_name)
    manifest = _read_bucket_json(manifest_key)
    files = manifest.get("files")
    if not isinstance(files, list):
        raise HTTPException(status_code=400, detail="tile manifest has no files")

    by_name = {
        str(item.get("filename")): str(item.get("object_key"))
        for item in files
        if isinstance(item, dict) and item.get("filename") and item.get("object_key")
    }
    targets = _tile_sync_targets(state_slug)
    required_files = {"alkis.pmtiles", "overview.pmtiles", "features.sqlite"}
    missing = sorted(required_files - set(by_name))
    if missing:
        raise HTTPException(status_code=400, detail=f"tile manifest missing files: {', '.join(missing)}")

    written: list[str] = []
    for filename, target_names in targets.items():
        if filename not in by_name:
            continue
        source_key = by_name[filename]
        primary_target = DATA_DIR / target_names[0]
        _download_bucket_object(source_key, primary_target)
        written.append(primary_target.name)
        for extra_name in target_names[1:]:
            extra_target = DATA_DIR / extra_name
            extra_target.unlink(missing_ok=True)
            try:
                os.link(primary_target, extra_target)
            except OSError:
                shutil.copy2(primary_target, extra_target)
            written.append(extra_target.name)
    for obsolete_name in (f"{state_slug}_detail.style.json", f"{state_slug}.style.json"):
        (DATA_DIR / obsolete_name).unlink(missing_ok=True)

    _clear_data_caches()
    return {
        "status": "success",
        "state": state_slug,
        "version": manifest.get("version_name") or version_name,
        "manifest": manifest_key,
        "files": written,
    }


def mosaic_entries_for_zoom(z: int) -> tuple[MosaicEntry, ...]:
    entries = mosaic_entries()
    preferred_kind = "overview" if z <= OVERVIEW_MAX_ZOOM else "detail"
    preferred = tuple(entry for entry in entries if entry.kind == preferred_kind)
    if preferred:
        return preferred
    fallback_kind = "detail" if preferred_kind == "overview" else "overview"
    return tuple(entry for entry in entries if entry.kind == fallback_kind)


def mosaic_entry_kinds_for_zoom(z: int) -> tuple[str, ...]:
    preferred_kind = "overview" if z <= OVERVIEW_MAX_ZOOM else "detail"
    fallback_kind = "detail" if preferred_kind == "overview" else "overview"
    return preferred_kind, fallback_kind


def mosaic_metadata() -> dict:
    entries = mosaic_entries()
    if not entries:
        raise HTTPException(status_code=404, detail="mosaic has no datasets")

    min_zoom = min(entry.dataset.min_zoom for entry in entries)
    max_zoom = max(entry.dataset.max_zoom for entry in entries)
    west = min(entry.dataset.bounds[0] for entry in entries)
    south = min(entry.dataset.bounds[1] for entry in entries)
    east = max(entry.dataset.bounds[2] for entry in entries)
    north = max(entry.dataset.bounds[3] for entry in entries)
    web_min_zoom = min(min_zoom, WEB_MIN_ZOOM)

    vector_layers = []
    seen_layers: set[str] = set()
    for entry in entries:
        for layer in (entry.dataset.metadata or {}).get("vector_layers", []):
            layer_id = layer.get("id")
            if layer_id and layer_id not in seen_layers:
                vector_layers.append(layer)
                seen_layers.add(layer_id)

    return {
        "minzoom": web_min_zoom,
        "maxzoom": max_zoom,
        "bounds": [west, south, east, north],
        "center": [(west + east) / 2, (south + north) / 2, min(max(web_min_zoom, 5), max_zoom)],
        "vector_layers": vector_layers,
    }



def direct_vector_metadata(style: dict) -> dict:
    datasets: list[Dataset] = []
    seen: set[str] = set()
    for source in style.get("sources", {}).values():
        if not isinstance(source, dict):
            continue
        dataset_name = str(source.get("openkataster_direct_dataset") or "").strip()
        if not dataset_name or dataset_name in seen or not DATASET_RE.match(dataset_name):
            continue
        seen.add(dataset_name)
        shard_names = _direct_shard_names(dataset_name)
        if shard_names:
            datasets.extend(direct_shard_datasets(dataset_name))
            continue
        path = DATA_DIR / f"{dataset_name}.pmtiles"
        if path.exists():
            datasets.append(load_dataset(dataset_name, path.stat().st_mtime_ns))
    if not datasets:
        raise HTTPException(status_code=404, detail="direct style has no datasets")

    min_zoom = min(ds.min_zoom for ds in datasets)
    max_zoom = max(ds.max_zoom for ds in datasets)
    west = min(ds.bounds[0] for ds in datasets)
    south = min(ds.bounds[1] for ds in datasets)
    east = max(ds.bounds[2] for ds in datasets)
    north = max(ds.bounds[3] for ds in datasets)
    web_min_zoom = min(min_zoom, WEB_MIN_ZOOM)

    vector_layers = []
    seen_layers: set[str] = set()
    for ds in datasets:
        for layer in (ds.metadata or {}).get("vector_layers", []):
            layer_id = layer.get("id")
            if layer_id and layer_id not in seen_layers:
                vector_layers.append(layer)
                seen_layers.add(layer_id)

    return {
        "minzoom": web_min_zoom,
        "maxzoom": max_zoom,
        "bounds": [west, south, east, north],
        "center": [(west + east) / 2, (south + north) / 2, min(max(web_min_zoom, 5), max_zoom)],
        "vector_layers": vector_layers,
    }

def _decompress_tile(data: bytes, is_gzip: bool) -> bytes:
    return gzip.decompress(data) if is_gzip else data


def _transform_overzoom_coordinates(value, *, extent: int, scale: int, child_x: int, child_y: int):
    if (
        isinstance(value, (list, tuple))
        and len(value) >= 2
        and isinstance(value[0], (int, float))
        and isinstance(value[1], (int, float))
    ):
        offset_x = child_x * extent / scale
        offset_y = child_y * extent / scale
        return [
            (float(value[0]) - offset_x) * scale,
            (float(value[1]) - offset_y) * scale,
        ]
    if isinstance(value, list):
        return [
            _transform_overzoom_coordinates(item, extent=extent, scale=scale, child_x=child_x, child_y=child_y)
            for item in value
        ]
    return value


def _overzoom_mvt_tile(data: bytes, is_gzip: bool, delta: int, child_x: int, child_y: int) -> bytes:
    if delta <= 0:
        return data if is_gzip else gzip.compress(data)

    raw_tile = _decompress_tile(data, is_gzip)
    decoded = mapbox_vector_tile.decode(
        raw_tile,
        default_options={"geojson": False, "y_coord_down": True},
    )
    scale = 1 << delta
    encoded_layers = []
    per_layer_options = {}
    for layer_name, layer in decoded.items():
        extent = int(layer.get("extent", 4096) or 4096)
        features = []
        for feature in layer.get("features", []):
            geometry = dict(feature.get("geometry") or {})
            geometry["coordinates"] = _transform_overzoom_coordinates(
                geometry.get("coordinates") or [],
                extent=extent,
                scale=scale,
                child_x=child_x,
                child_y=child_y,
            )
            features.append(
                {
                    "geometry": geometry,
                    "properties": feature.get("properties") or {},
                }
            )
        if features:
            encoded_layers.append({"name": layer_name, "features": features})
            per_layer_options[layer_name] = {"extents": extent, "y_coord_down": True}
    if not encoded_layers:
        return gzip.compress(b"")
    return gzip.compress(
        mapbox_vector_tile.encode(
            encoded_layers,
            per_layer_options=per_layer_options,
            default_options={"y_coord_down": True},
        )
    )


LEGACY_ST_RUNTIME_LAYER_MAP = {
    "parcels": "surfaces",
    "buildings": "building_fills",
    "parcel_lines": "parcel_outline_lines",
    "building_lines": "building_lines",
    "parcel_labels": "labels",
    "house_numbers": "labels",
    "street_names": "labels",
}


def _runtime_layer_name(layer_name: str) -> str:
    return LEGACY_ST_RUNTIME_LAYER_MAP.get(layer_name, layer_name)


def _normalize_legacy_st_properties(layer_name: str, properties: dict) -> dict:
    props = dict(properties or {})
    fill = props.get("fill")
    if layer_name == "parcels":
        props.setdefault("theme_index", 8)
        props.setdefault("thema", props.get("usage") or "Fläche")
        if fill:
            props.setdefault("fill_color", fill)
        return props
    if layer_name == "buildings":
        props.setdefault("theme_index", 1)
        props.setdefault("thema", props.get("funktion") or "Gebäude")
        if fill:
            props.setdefault("fill_color", fill)
        if props.get("underground") is True:
            props.setdefault("render_fill_role", "underground")
        return props
    if layer_name == "parcel_lines":
        props.setdefault("theme_index", 0)
        props.setdefault("stroke_color", "#5f6670")
        return props
    if layer_name == "building_lines":
        props.setdefault("theme_index", 1)
        props.setdefault("stroke_color", "#1f2328")
        if props.get("underground") is True:
            props.setdefault("render_pattern_kind", "dash")
            props.setdefault("render_dash_key", "short")
        return props
    if layer_name == "parcel_labels":
        props.setdefault("theme_index", 0)
    elif layer_name == "house_numbers":
        props.setdefault("theme_index", 1)
        props.setdefault("sub_thema", "Gebäude")
    elif layer_name == "street_names":
        props.setdefault("theme_index", 2)
        props.setdefault("font_weight", "bold")
    if layer_name in {"parcel_labels", "house_numbers", "street_names"}:
        text = props.get("text_content") or props.get("label")
        if text is not None:
            props["text_content"] = str(text)
        angle = props.get("angle")
        if angle is not None and "render_rotation" not in props:
            try:
                props["render_rotation"] = float(angle)
            except (TypeError, ValueError):
                pass
    return props


def _encode_mvt_layers(layers_by_name: dict[str, dict]) -> bytes:
    encoded_layers = [
        {"name": layer["name"], "features": layer["features"]}
        for layer in layers_by_name.values()
        if layer["features"]
    ]
    if not encoded_layers:
        return gzip.compress(b"")
    per_layer_options = {
        layer["name"]: {"extents": layer["extent"], "y_coord_down": True}
        for layer in layers_by_name.values()
    }
    return gzip.compress(
        mapbox_vector_tile.encode(
            encoded_layers,
            per_layer_options=per_layer_options,
            default_options={"y_coord_down": True},
        )
    )


def _normalize_legacy_st_mvt_tile(data: bytes, is_gzip: bool) -> bytes:
    raw_tile = _decompress_tile(data, is_gzip)
    decoded = mapbox_vector_tile.decode(
        raw_tile,
        default_options={"geojson": False, "y_coord_down": True},
    )
    if not any(layer_name in LEGACY_ST_RUNTIME_LAYER_MAP for layer_name in decoded):
        return data if is_gzip else gzip.compress(data)
    layers_by_name: dict[str, dict] = {}
    for layer_name, layer in decoded.items():
        target_name = _runtime_layer_name(layer_name)
        target = layers_by_name.setdefault(
            target_name,
            {
                "name": target_name,
                "features": [],
                "extent": int(layer.get("extent", 4096) or 4096),
            },
        )
        for feature in layer.get("features", []):
            next_feature = dict(feature)
            next_feature["properties"] = _normalize_legacy_st_properties(
                layer_name,
                feature.get("properties") or {},
            )
            target["features"].append(next_feature)
        target["extent"] = max(target["extent"], int(layer.get("extent", 4096) or 4096))
    return _encode_mvt_layers(layers_by_name)


def _merge_mvt_tiles(tiles: list[tuple[bytes, bool]]) -> bytes:
    layers_by_name: dict[str, dict] = {}
    seen_feature_keys: set[tuple[str, ...]] = set()

    for tile_data, is_gzip in tiles:
        raw_tile = _decompress_tile(tile_data, is_gzip)
        decoded = mapbox_vector_tile.decode(
            raw_tile,
            default_options={"geojson": False, "y_coord_down": True},
        )
        for layer_name, layer in decoded.items():
            target_layer_name = _runtime_layer_name(layer_name)
            target = layers_by_name.setdefault(
                target_layer_name,
                {
                    "name": target_layer_name,
                    "features": [],
                    "extent": int(layer.get("extent", 4096) or 4096),
                },
            )
            for feature in layer.get("features", []):
                properties = _normalize_legacy_st_properties(layer_name, feature.get("properties") or {})
                gml_id = properties.get("gml_id")
                if gml_id:
                    geometry_key = json.dumps(
                        feature.get("geometry") or {},
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    key = (
                        target_layer_name,
                        str(gml_id),
                        str(properties.get("thema") or ""),
                        str(properties.get("sub_thema") or ""),
                        str(properties.get("signaturnummer") or ""),
                        str(properties.get("text_content") or ""),
                        str(properties.get("raw_valign") or ""),
                        str(properties.get("raw_halign") or ""),
                        str((feature.get("geometry") or {}).get("type") or ""),
                        geometry_key,
                    )
                    if key in seen_feature_keys:
                        continue
                    seen_feature_keys.add(key)
                next_feature = dict(feature)
                next_feature["properties"] = properties
                target["features"].append(next_feature)
            target["extent"] = max(target["extent"], int(layer.get("extent", 4096) or 4096))

    return _encode_mvt_layers(layers_by_name)


@lru_cache(maxsize=MOSAIC_CACHE_SIZE)
def _mosaic_tile_cached(signature: tuple[tuple[str, int, int], ...], z: int, x: int, y: int) -> bytes | None:
    del signature
    bbox = tile_bounds(z, x, y)
    entries = mosaic_entries()
    tiles: list[tuple[bytes, bool]] = []

    for kind in mosaic_entry_kinds_for_zoom(z):
        tiles = []
        for entry in entries:
            if entry.kind != kind:
                continue
            ds = entry.dataset
            if z < ds.min_zoom:
                continue
            if not bounds_intersect(ds.bounds, bbox):
                continue
            tile_z = min(z, ds.max_zoom)
            delta = z - tile_z
            tile_x = x >> delta if delta > 0 else x
            tile_y = y >> delta if delta > 0 else y
            tile_data = ds.tile(tile_z, tile_x, tile_y)
            if tile_data:
                if delta > 0:
                    tile_data = _overzoom_mvt_tile(
                        tile_data,
                        ds.is_gzip,
                        delta,
                        x - (tile_x << delta),
                        y - (tile_y << delta),
                    )
                    tiles.append((tile_data, True))
                else:
                    tiles.append((tile_data, ds.is_gzip))
        if tiles:
            break

    if not tiles:
        return None
    if len(tiles) == 1:
        data, is_gzip = tiles[0]
        return _normalize_legacy_st_mvt_tile(data, is_gzip)
    return _merge_mvt_tiles(tiles)


def mosaic_tile(z: int, x: int, y: int) -> bytes | None:
    signature = _mosaic_scan_signature()
    cache_path = _mosaic_disk_cache_path(signature, z, x, y)
    if cache_path is not None:
        try:
            return cache_path.read_bytes()
        except FileNotFoundError:
            pass
        except OSError:
            pass

    data = _mosaic_tile_cached(signature, z, x, y)
    if data is not None and cache_path is not None:
        _write_mosaic_disk_cache(cache_path, data)
    return data


@lru_cache(maxsize=MOSAIC_CACHE_SIZE)
def _direct_shard_tile_cached(
    dataset: str,
    signature: tuple[tuple[str, int, int], ...],
    z: int,
    x: int,
    y: int,
) -> bytes | None:
    del signature
    bbox = tile_bounds(z, x, y)
    tiles: list[tuple[bytes, bool]] = []

    for ds in direct_shard_datasets(dataset):
        if z < ds.min_zoom:
            continue
        if not bounds_intersect(ds.bounds, bbox):
            continue
        tile_z = min(z, ds.max_zoom)
        delta = z - tile_z
        tile_x = x >> delta if delta > 0 else x
        tile_y = y >> delta if delta > 0 else y
        tile_data = ds.tile(tile_z, tile_x, tile_y)
        if tile_data:
            if delta > 0:
                tile_data = _overzoom_mvt_tile(
                    tile_data,
                    ds.is_gzip,
                    delta,
                    x - (tile_x << delta),
                    y - (tile_y << delta),
                )
                tiles.append((tile_data, True))
            else:
                tiles.append((tile_data, ds.is_gzip))

    if not tiles:
        return None
    if len(tiles) == 1:
        data, is_gzip = tiles[0]
        return data if is_gzip else gzip.compress(data)
    return _merge_mvt_tiles(tiles)


def direct_shard_tile(dataset: str, z: int, x: int, y: int) -> bytes | None:
    signature = _direct_shard_signature(dataset)
    if not signature:
        return None
    return _direct_shard_tile_cached(dataset, signature, z, x, y)


def _mosaic_signature_token(signature: tuple[tuple[str, int, int], ...]) -> str:
    payload = json.dumps(signature, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:20]


def _mosaic_disk_cache_path(
    signature: tuple[tuple[str, int, int], ...],
    z: int,
    x: int,
    y: int,
) -> Path | None:
    if not MOSAIC_DISK_CACHE_DIR or z > MOSAIC_DISK_CACHE_MAX_ZOOM:
        return None
    token = _mosaic_signature_token(signature)
    return Path(MOSAIC_DISK_CACHE_DIR) / VIRTUAL_GERMANY_DATASET / token / str(z) / str(x) / f"{y}.pbf"


def _write_mosaic_disk_cache(path: Path, data: bytes) -> None:
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path.write_bytes(data)
        os.replace(tmp_path, path)
    except OSError:
        tmp_path.unlink(missing_ok=True)



def _raster_disk_cache_path(
    signature: tuple[tuple[str, int, int], ...],
    z: int,
    x: int,
    y: int,
) -> Path | None:
    if not RASTER_DISK_CACHE_DIR:
        return None
    token = _mosaic_signature_token(signature)
    return Path(RASTER_DISK_CACHE_DIR) / VIRTUAL_GERMANY_DATASET / token / str(z) / str(x) / f"{y}.png"


def _point_to_px(point, extent: int) -> tuple[float, float]:
    return (
        float(point[0]) / extent * RASTER_TILE_SIZE,
        float(point[1]) / extent * RASTER_TILE_SIZE,
    )


def _line_to_px(line, extent: int) -> list[tuple[float, float]]:
    return [_point_to_px(point, extent) for point in line]


def _polygon_rings(geometry: dict) -> list[list]:
    geom_type = geometry.get("type")
    coordinates = geometry.get("coordinates") or []
    if geom_type == "Polygon":
        return [ring for ring in coordinates if ring]
    if geom_type == "MultiPolygon":
        rings = []
        for polygon in coordinates:
            rings.extend(ring for ring in polygon if ring)
        return rings
    return []


def _line_parts(geometry: dict) -> list[list]:
    geom_type = geometry.get("type")
    coordinates = geometry.get("coordinates") or []
    if geom_type == "LineString":
        return [coordinates] if coordinates else []
    if geom_type == "MultiLineString":
        return [part for part in coordinates if part]
    return []


def _feature_theme(feature: dict) -> str:
    properties = feature.get("properties") or {}
    return str(properties.get("thema") or properties.get("theme") or "")


def _draw_polygon_theme(draw, features: list[dict], extent: int, theme: str, fill: str, outline: str | None = None) -> None:
    for feature in features:
        if _feature_theme(feature) != theme:
            continue
        for ring in _polygon_rings(feature.get("geometry") or {}):
            points = _line_to_px(ring, extent)
            if len(points) >= 3:
                draw.polygon(points, fill=fill, outline=outline)


def _draw_line_theme(draw, features: list[dict], extent: int, theme: str, fill: str, width: int = 1) -> None:
    for feature in features:
        if _feature_theme(feature) != theme:
            continue
        for part in _line_parts(feature.get("geometry") or {}):
            points = _line_to_px(part, extent)
            if len(points) >= 2:
                draw.line(points, fill=fill, width=width, joint="curve")


def _decode_mvt_for_raster(data: bytes) -> dict:
    raw = gzip.decompress(data)
    try:
        return mapbox_vector_tile.decode(raw, default_options={"y_coord_down": True})
    except TypeError:
        return mapbox_vector_tile.decode(raw)


def _render_mosaic_raster(data: bytes) -> bytes:
    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:
        raise HTTPException(status_code=503, detail="Pillow is not installed on tile service") from exc

    decoded = _decode_mvt_for_raster(data)
    polygon_layer = decoded.get("polygons") or {}
    line_layer = decoded.get("lines") or {}
    polygon_features = polygon_layer.get("features") or []
    line_features = line_layer.get("features") or []
    extent = int(polygon_layer.get("extent") or line_layer.get("extent") or 4096)

    image = Image.new("RGBA", (RASTER_TILE_SIZE, RASTER_TILE_SIZE), "#fbf8f3ff")
    draw = ImageDraw.Draw(image, "RGBA")

    _draw_polygon_theme(draw, polygon_features, extent, "Flurstücke", "#eee6d966", "#d0c6b933")
    _draw_polygon_theme(draw, polygon_features, extent, "Vegetation", "#d8edccaa")
    _draw_polygon_theme(draw, polygon_features, extent, "Gewässer", "#c8e7ecbb")
    _draw_polygon_theme(draw, polygon_features, extent, "Verkehr", "#f5f0e7cc")
    _draw_polygon_theme(draw, polygon_features, extent, "Industrie und Gewerbe", "#e8e5dfaa")
    _draw_polygon_theme(draw, polygon_features, extent, "Gebäude", "#a9a39aff", "#3f3a36dd")

    _draw_line_theme(draw, line_features, extent, "Gewässer", "#74aeb8bb", 1)
    _draw_line_theme(draw, line_features, extent, "Verkehr", "#b9afa3aa", 1)
    _draw_line_theme(draw, line_features, extent, "Politische Grenzen", "#c45bd7dd", 2)
    _draw_line_theme(draw, line_features, extent, "Flurstücke", "#98605ccc", 1)
    _draw_line_theme(draw, line_features, extent, "Gebäude", "#1f1d1bdd", 2)

    from io import BytesIO

    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _write_raster_disk_cache(path: Path, data: bytes) -> None:
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path.write_bytes(data)
        os.replace(tmp_path, path)
    except OSError:
        tmp_path.unlink(missing_ok=True)


def mosaic_raster_tile(z: int, x: int, y: int) -> bytes | None:
    signature = _mosaic_scan_signature()
    cache_path = _raster_disk_cache_path(signature, z, x, y)
    if cache_path is not None:
        try:
            return cache_path.read_bytes()
        except FileNotFoundError:
            pass
        except OSError:
            pass

    if not RASTER_ON_DEMAND:
        return None

    data = mosaic_tile(z, x, y)
    if data is None:
        return None
    rendered = _render_mosaic_raster(data)
    if cache_path is not None:
        _write_raster_disk_cache(cache_path, rendered)
    return rendered


def _mercator_from_lonlat(lon: float, lat: float) -> tuple[float, float]:
    origin_shift = 20037508.342789244
    lat = max(-85.05112878, min(85.05112878, lat))
    mx = lon * origin_shift / 180.0
    my = math.log(math.tan((90.0 + lat) * math.pi / 360.0)) / (math.pi / 180.0)
    my = my * origin_shift / 180.0
    return mx, my


def _tile_range_for_mercator_bounds(z: int, minx: float, miny: float, maxx: float, maxy: float) -> tuple[int, int, int, int]:
    origin_shift = 20037508.342789244
    n = 2**z
    west = max(0, min(n - 1, int(math.floor((minx + origin_shift) / (origin_shift * 2) * n))))
    east = max(0, min(n - 1, int(math.floor((maxx + origin_shift) / (origin_shift * 2) * n))))
    north = max(0, min(n - 1, int(math.floor((origin_shift - maxy) / (origin_shift * 2) * n))))
    south = max(0, min(n - 1, int(math.floor((origin_shift - miny) / (origin_shift * 2) * n))))
    return west, east, north, south


def _pdf_escape(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _pdf_color(value: str | None, fallback: tuple[float, float, float]) -> tuple[float, float, float]:
    if not value or not isinstance(value, str):
        return fallback
    match = re.match(r"^#?([0-9a-fA-F]{6})$", value.strip())
    if not match:
        return fallback
    raw = match.group(1)
    return tuple(int(raw[index : index + 2], 16) / 255.0 for index in (0, 2, 4))  # type: ignore[return-value]


def _pdf_stream_object(data: bytes, dictionary: str = "") -> dict:
    prefix = dictionary.strip()
    if prefix:
        prefix = prefix[:-2].rstrip() + f" /Length {len(data)} >>"
    else:
        prefix = f"<< /Length {len(data)} >>"
    return {"dict": prefix, "stream": data}


def _build_simple_pdf(page_w: float, page_h: float, content: str, title: str) -> bytes:
    content_bytes = content.encode("utf-8")
    objects: list[str | dict] = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {page_w:.2f} {page_h:.2f}] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        _pdf_stream_object(content_bytes),
    ]
    chunks: list[bytes] = []
    offsets = [0]
    current_offset = 0

    def add(part: str | bytes) -> None:
        nonlocal current_offset
        data = part if isinstance(part, bytes) else part.encode("utf-8")
        chunks.append(data)
        current_offset += len(data)

    add("%PDF-1.4\n")
    for index, obj in enumerate(objects, start=1):
        offsets.append(current_offset)
        add(f"{index} 0 obj\n")
        if isinstance(obj, str):
            add(obj + "\n")
        else:
            add(obj["dict"] + "\nstream\n")
            add(obj["stream"])
            add("\nendstream\n")
        add("endobj\n")
    xref_offset = current_offset
    add(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n")
    for offset in offsets[1:]:
        add(f"{offset:010d} 00000 n \n")
    add(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R /Info << /Title ({_pdf_escape(title)}) >> >>\nstartxref\n{xref_offset}\n%%EOF")
    return b"".join(chunks)


def _export_paper_mm(paper: str, orientation: str) -> tuple[float, float]:
    sizes = {
        "a4": (210.0, 297.0),
        "a3": (297.0, 420.0),
    }
    width, height = sizes.get(paper, sizes["a4"])
    if orientation == "landscape":
        width, height = height, width
    return width, height


def _iter_pdf_paths(geometry: dict) -> list[list[list[tuple[float, float]]]]:
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates") or []
    if geometry_type == "Polygon":
        return [coordinates]
    if geometry_type == "MultiPolygon":
        return coordinates
    if geometry_type == "LineString":
        return [[coordinates]]
    if geometry_type == "MultiLineString":
        return [[line] for line in coordinates]
    return []


def _pdf_point_geometry(geometry: dict) -> tuple[float, float] | None:
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates") or []
    if geometry_type == "Point" and len(coordinates) >= 2:
        return float(coordinates[0]), float(coordinates[1])
    if geometry_type == "MultiPoint" and coordinates and len(coordinates[0]) >= 2:
        return float(coordinates[0][0]), float(coordinates[0][1])
    return None


def _vector_export_content(
    center_lon: float,
    center_lat: float,
    paper: str,
    orientation: str,
    scale: int,
) -> tuple[bytes, str]:
    paper_w_mm, paper_h_mm = _export_paper_mm(paper, orientation)
    page_w = paper_w_mm / 25.4 * 72.0
    page_h = paper_h_mm / 25.4 * 72.0
    ground_w = paper_w_mm / 1000.0 * scale
    ground_h = paper_h_mm / 1000.0 * scale
    latitude_factor = max(0.08, math.cos(math.radians(center_lat)))
    center_x, center_y = _mercator_from_lonlat(center_lon, center_lat)
    mercator_w = ground_w / latitude_factor
    mercator_h = ground_h / latitude_factor
    minx = center_x - mercator_w / 2.0
    maxx = center_x + mercator_w / 2.0
    miny = center_y - mercator_h / 2.0
    maxy = center_y + mercator_h / 2.0
    z = min(18, max((entry.dataset.max_zoom for entry in mosaic_entries()), default=18))
    west, east, north, south = _tile_range_for_mercator_bounds(z, minx, miny, maxx, maxy)
    tile_count = (east - west + 1) * (south - north + 1)
    if tile_count > 160:
        z = max(12, z - math.ceil(math.log(tile_count / 160, 4)))
        west, east, north, south = _tile_range_for_mercator_bounds(z, minx, miny, maxx, maxy)

    origin_shift = 20037508.342789244
    tile_mercator_size = (origin_shift * 2) / (2**z)
    commands: list[str] = [
        "1 1 1 rg 0 0 %.2f %.2f re f" % (page_w, page_h),
        "0.93 0.91 0.87 RG 0.35 w 0 0 %.2f %.2f re S" % (page_w, page_h),
    ]

    def to_page(tile_x: int, tile_y: int, extent: int, point: tuple[float, float] | list[float]) -> tuple[float, float]:
        px, py = float(point[0]), float(point[1])
        tile_minx = -origin_shift + tile_x * tile_mercator_size
        tile_maxy = origin_shift - tile_y * tile_mercator_size
        mx = tile_minx + px / extent * tile_mercator_size
        my = tile_maxy - py / extent * tile_mercator_size
        return ((mx - minx) / (maxx - minx) * page_w, (my - miny) / (maxy - miny) * page_h)

    labels: list[tuple[int, float, str]] = []
    for kind in ("polygons", "lines", "labels"):
        for tile_y in range(north, south + 1):
            for tile_x in range(west, east + 1):
                tile_data = mosaic_tile(z, tile_x, tile_y)
                if not tile_data:
                    continue
                raw_tile = gzip.decompress(tile_data) if tile_data[:2] == b"\x1f\x8b" else tile_data
                decoded = mapbox_vector_tile.decode(raw_tile, default_options={"geojson": False, "y_coord_down": True})
                for layer_name, layer in decoded.items():
                    if kind == "polygons" and layer_name != "polygons":
                        continue
                    if kind == "lines" and layer_name != "lines":
                        continue
                    if kind == "labels" and layer_name != "labels":
                        continue
                    extent = int(layer.get("extent", 4096) or 4096)
                    features = layer.get("features") or []
                    features = sorted(features, key=lambda item: (item.get("properties") or {}).get("z_index", (item.get("properties") or {}).get("z_index_base", 0)))
                    for feature in features:
                        props = feature.get("properties") or {}
                        scale_min = int(props.get("scale_min", 0) or 0)
                        scale_max = int(props.get("scale_max", 999999999) or 999999999)
                        if scale < scale_min or scale > scale_max:
                            continue
                        if kind == "labels":
                            label_text = str(props.get("text_content") or "").strip()
                            point = _pdf_point_geometry(feature.get("geometry") or {})
                            if not label_text or not point:
                                continue
                            page_x, page_y = to_page(tile_x, tile_y, extent, point)
                            if page_x < -20 or page_x > page_w + 20 or page_y < -20 or page_y > page_h + 20:
                                continue
                            color = _pdf_color(props.get("font_color"), (0.0, 0.0, 0.0))
                            font_m = float(props.get("render_font_m", 1.8) or 1.8)
                            font_pt = max(4.0, min(12.0, font_m / scale * 1000.0 / 25.4 * 72.0))
                            rotation = math.radians(float(props.get("render_rotation", 0.0) or 0.0))
                            cos_r = math.cos(rotation)
                            sin_r = math.sin(rotation)
                            escaped = _pdf_escape(label_text[:80])
                            label_command = (
                                f"{color[0]:.3f} {color[1]:.3f} {color[2]:.3f} rg "
                                f"BT /F1 {font_pt:.2f} Tf "
                                f"{cos_r:.4f} {sin_r:.4f} {-sin_r:.4f} {cos_r:.4f} {page_x:.2f} {page_y:.2f} Tm "
                                f"({escaped}) Tj ET"
                            )
                            labels.append((int(props.get("theme_index", 0) or 0), font_pt, label_command))
                            continue
                        paths = _iter_pdf_paths(feature.get("geometry") or {})
                        if not paths:
                            continue
                        path_commands: list[str] = []
                        label_points: list[tuple[float, float]] = []
                        for part in paths:
                            for ring in part:
                                if len(ring) < 2:
                                    continue
                                first_x, first_y = to_page(tile_x, tile_y, extent, ring[0])
                                path_commands.append(f"{first_x:.2f} {first_y:.2f} m")
                                for point in ring[1:]:
                                    page_x, page_y = to_page(tile_x, tile_y, extent, point)
                                    path_commands.append(f"{page_x:.2f} {page_y:.2f} l")
                                if feature.get("geometry", {}).get("type") in {"Polygon", "MultiPolygon"}:
                                    path_commands.append("h")
                                label_points.extend(to_page(tile_x, tile_y, extent, point) for point in ring[: min(len(ring), 8)])
                        if not path_commands:
                            continue
                        if kind == "polygons":
                            fill = _pdf_color(props.get("fill_color"), (0.98, 0.965, 0.93))
                            if props.get("fill_color"):
                                commands.append(f"{fill[0]:.3f} {fill[1]:.3f} {fill[2]:.3f} rg " + " ".join(path_commands) + " f*")
                        else:
                            color = _pdf_color(props.get("stroke_color"), (0.0, 0.0, 0.0))
                            width_mm = float(props.get("width_100mm", 25) or 25) / 100.0
                            width_pt = max(0.12, min(1.8, width_mm / 25.4 * 72.0))
                            commands.append(f"{color[0]:.3f} {color[1]:.3f} {color[2]:.3f} RG {width_pt:.2f} w " + " ".join(path_commands) + " S")
    for _, _, label_command in sorted(labels, key=lambda item: (item[0], item[1]))[:1200]:
        commands.append(label_command)

    label = f"OpenKataster {paper.upper()} {'Querformat' if orientation == 'landscape' else 'Hochformat'} 1:{scale}"
    commands.append(f"0.35 0.35 0.35 rg BT /F1 7 Tf 18 10 Td ({_pdf_escape(label)}) Tj ET")
    title = f"OpenKataster Vektor Export {paper.upper()} 1:{scale}"
    return _build_simple_pdf(page_w, page_h, "\n".join(commands) + "\n", title), title


def style_source_maxzoom(max_zoom: int | float) -> int:
    max_zoom = int(max_zoom)
    if SOURCE_MAX_ZOOM <= 0:
        return max_zoom
    return min(max_zoom, SOURCE_MAX_ZOOM)


def public_base_url(request: Request) -> str:
    if PUBLIC_BASE_URL:
        return PUBLIC_BASE_URL
    return str(request.base_url).rstrip("/")



def raster_tile_url(base: str, dataset_name: str, key: str) -> str:
    return f"{base}/tiles/{dataset_name}/{{z}}/{{x}}/{{y}}.png?key={key}&v=20260609-hybrid-raster"

def tilejson_for(request: Request, dataset_name: str, ds: Dataset, key: str) -> dict:
    metadata = ds.metadata or {}
    header = ds.header or {}
    base = public_base_url(request)
    result = {
        "tilejson": "3.0.0",
        "name": metadata.get("name") or dataset_name,
        "scheme": "xyz",
        "tiles": [f"{base}/tiles/{dataset_name}/{{z}}/{{x}}/{{y}}.mvt?key={key}&v=20260609-composite-lines"],
        "minzoom": int(header.get("min_zoom", 0)),
        "maxzoom": style_source_maxzoom(int(header.get("max_zoom", 20))),
        "attribution": metadata.get("attribution", "OpenKataster"),
    }
    for field in ("bounds", "center", "vector_layers"):
        if field in metadata:
            result[field] = metadata[field]
    return result


def mosaic_tilejson_for(request: Request, key: str) -> dict:
    generic_entries = mosaic_entries()
    if generic_entries:
        metadata = mosaic_metadata()
    else:
        metadata = direct_vector_metadata(national_style_template())
    base = public_base_url(request)
    result = {
        "tilejson": "3.0.0",
        "name": "OpenKataster Deutschland",
        "scheme": "xyz",
        "tiles": ([] if not generic_entries else [f"{base}/tiles/{VIRTUAL_GERMANY_DATASET}/{{z}}/{{x}}/{{y}}.mvt?key={key}&v=20260609-composite-lines"]),
        "minzoom": metadata["minzoom"],
        "maxzoom": style_source_maxzoom(metadata["maxzoom"]),
        "bounds": metadata["bounds"],
        "center": metadata["center"],
        "attribution": "© OpenKataster, ALKIS-Daten",
        "openkataster_architecture": ("direct-volume" if not generic_entries else "mosaic"),
    }
    if metadata["vector_layers"]:
        result["vector_layers"] = metadata["vector_layers"]
    return result


def style_path(dataset: str) -> Path:
    if not DATASET_RE.match(dataset):
        raise HTTPException(status_code=404, detail="style not found")
    path = DATA_DIR / f"{dataset}.style.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="style not found")
    return path


def source_layers_for_style(style: dict) -> set[str]:
    layer_ids: set[str] = set()
    for layer in style.get("layers", []):
        source_layer = layer.get("source-layer")
        if isinstance(source_layer, str):
            layer_ids.add(source_layer)
    return layer_ids


def find_style_template(dataset: str) -> Path:
    direct_path = DATA_DIR / f"{dataset}.style.json"
    if direct_path.exists():
        return direct_path

    generic_path = DATA_DIR / "style.json"
    if generic_path.exists():
        return generic_path

    for path in sorted(DATA_DIR.glob("*.style.json")):
        if not path.name.startswith(f"{VIRTUAL_GERMANY_DATASET}."):
            return path

    raise HTTPException(status_code=404, detail="style not found")


def asset_path(asset_name: str) -> Path:
    if not ASSET_RE.match(asset_name):
        raise HTTPException(status_code=404, detail="asset not found")
    if asset_name not in ALLOWED_ASSETS:
        match = OVERVIEW_ASSET_RE.match(asset_name)
        if not match or match.group(1) not in set(active_bucket_state_keys()):
            raise HTTPException(status_code=404, detail="asset not found")
    path = DATA_DIR / asset_name
    if not path.exists():
        raise HTTPException(status_code=404, detail="asset not found")
    return path


def glyph_path(fontstack: str, glyph_range: str) -> Path:
    if not GLYPH_STACK_RE.match(fontstack) or not GLYPH_RANGE_RE.match(glyph_range):
        raise HTTPException(status_code=404, detail="glyph not found")
    path = GLYPHS_DIR / fontstack / glyph_range
    if not path.exists() and fontstack in {"Arial Bold", "Arial Italic", "Arial Bold Italic"}:
        path = GLYPHS_DIR / "Arial" / glyph_range
    if not path.exists():
        raise HTTPException(status_code=404, detail="glyph not found")
    return path


def rewrite_geojson_source(source: dict, base: str, key: str) -> dict:
    result = json.loads(json.dumps(source))
    data = result.get("data")
    if isinstance(data, str):
        asset_name = data.rsplit("/", 1)[-1].split("?", 1)[0]
        if asset_name in ALLOWED_ASSETS:
            result["data"] = f"{base}/assets/{asset_name}?key={key}"
    return result


def qgis_expression(value):
    if not isinstance(value, list) or not value:
        return value

    operator = value[0]
    if operator == "coalesce" and len(value) >= 2:
        return qgis_expression(value[1])

    if operator in {"max", "min"} and len(value) == 3:
        left = qgis_expression(value[1])
        right = qgis_expression(value[2])
        if isinstance(left, (int, float)) and not isinstance(right, (int, float)):
            return right
        if isinstance(right, (int, float)) and not isinstance(left, (int, float)):
            return left
        return left

    return [qgis_expression(item) for item in value]


def qgis_layer(layer: dict) -> dict:
    result = json.loads(json.dumps(layer))
    if "paint" in result:
        result["paint"] = {
            key: qgis_expression(value)
            for key, value in result["paint"].items()
        }
    if "layout" in result:
        result["layout"] = {
            key: qgis_expression(value)
            for key, value in result["layout"].items()
            if not key.endswith("-sort-key")
        }
    return result


def national_style_template() -> dict:
    data_style_path = DATA_DIR / f"{VIRTUAL_GERMANY_DATASET}.style.json"
    if data_style_path.exists():
        with data_style_path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    if NATIONAL_STYLE_PATH.exists():
        with NATIONAL_STYLE_PATH.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    return {
        "version": 8,
        "name": "OpenKataster Deutschland Standard",
        "sources": {"alkis": {"type": "vector", "tiles": []}},
        "layers": [
            {
                "id": "background",
                "type": "background",
                "paint": {"background-color": "#fbf8f3"},
            }
        ],
    }


def _web_font_stack(font_stack: list | str | None) -> list[str]:
    if isinstance(font_stack, str):
        values = [font_stack]
    elif isinstance(font_stack, list):
        values = [str(value) for value in font_stack]
    else:
        return ["Noto Sans Regular"]

    mapped: list[str] = []
    for value in values:
        lowered = value.lower()
        if not USES_DEMO_GLYPHS:
            mapped.append(value)
            continue

        is_bold = "bold" in lowered
        is_italic = "italic" in lowered
        if is_bold and is_italic:
            mapped.append("Noto Sans Bold")
        elif is_bold:
            mapped.append("Noto Sans Bold")
        elif is_italic:
            mapped.append("Noto Sans Italic")
        else:
            mapped.append("Noto Sans Regular")
    return mapped or ["Noto Sans Regular"]


def web_layer(layer: dict) -> dict:
    result = json.loads(json.dumps(layer))
    layout = result.get("layout")
    if isinstance(layout, dict):
        if "text-font" in layout:
            layout["text-font"] = _web_font_stack(layout.get("text-font"))
        if result.get("type") == "symbol" and "text-field" in layout:
            layout["text-max-width"] = 999
    return result


def runtime_fallback_layers(source_id: str = "alkis") -> list[dict]:
    return [
        {
            "id": "runtime-surface-fills",
            "type": "fill",
            "source": source_id,
            "source-layer": "surfaces",
            "minzoom": 13,
            "paint": {
                "fill-color": ["coalesce", ["get", "fill_color"], "#fbf8f3"],
                "fill-opacity": ["interpolate", ["linear"], ["zoom"], 13, 0.0, 15, 0.82, 18, 0.9],
            },
        },
        {
            "id": "runtime-building-fills",
            "type": "fill",
            "source": source_id,
            "source-layer": "building_fills",
            "minzoom": 14,
            "paint": {
                "fill-color": ["coalesce", ["get", "fill_color"], "#aaa9a7"],
                "fill-opacity": ["interpolate", ["linear"], ["zoom"], 14, 0.0, 15.1, 0.86, 18, 0.95],
            },
        },
        {
            "id": "runtime-surface-lines",
            "type": "line",
            "source": source_id,
            "source-layer": "lines",
            "minzoom": 16,
            "paint": {
                "line-color": ["coalesce", ["get", "stroke_color"], "#8d8478"],
                "line-opacity": ["interpolate", ["linear"], ["zoom"], 16, 0.0, 17, 0.52, 20, 0.72],
                "line-width": ["interpolate", ["linear"], ["zoom"], 16, 0.35, 18, 0.8, 20, 1.2],
            },
        },
        {
            "id": "runtime-parcel-outline-lines",
            "type": "line",
            "source": source_id,
            "source-layer": "parcel_outline_lines",
            "minzoom": 17,
            "paint": {
                "line-color": ["coalesce", ["get", "stroke_color"], "#5f6670"],
                "line-opacity": ["interpolate", ["linear"], ["zoom"], 17, 0.5, 20, 0.78],
                "line-width": ["interpolate", ["linear"], ["zoom"], 17, 0.45, 19, 0.75, 20, 1.0],
            },
        },
        {
            "id": "runtime-building-lines",
            "type": "line",
            "source": source_id,
            "source-layer": "building_lines",
            "minzoom": 16,
            "paint": {
                "line-color": ["coalesce", ["get", "stroke_color"], "#1f2328"],
                "line-opacity": ["interpolate", ["linear"], ["zoom"], 16, 0.72, 20, 0.92],
                "line-width": ["interpolate", ["linear"], ["zoom"], 16, 0.45, 19, 0.8, 20, 1.1],
            },
        },
        {
            "id": "runtime-fallback-label-theme-0-parcel-labels",
            "type": "symbol",
            "source": source_id,
            "source-layer": "labels",
            "filter": ["==", "theme_index", 0],
            "minzoom": 17,
            "layout": {
                "text-field": ["get", "text_content"],
                "text-font": ["Noto Sans Regular"],
                "text-size": ["interpolate", ["linear"], ["zoom"], 17, 10, 19, 12],
                "text-allow-overlap": False,
                "text-ignore-placement": False,
            },
            "paint": {
                "text-color": "#374151",
                "text-halo-color": "#ffffff",
                "text-halo-width": 1.1,
                "text-opacity": ["interpolate", ["linear"], ["zoom"], 17, 0.72, 18, 0.92],
            },
        },
        {
            "id": "runtime-fallback-label-theme-1-house-numbers",
            "type": "symbol",
            "source": source_id,
            "source-layer": "labels",
            "filter": ["==", "theme_index", 1],
            "minzoom": 17,
            "layout": {
                "text-field": ["get", "text_content"],
                "text-font": ["Noto Sans Regular"],
                "text-size": ["interpolate", ["linear"], ["zoom"], 17, 10.5, 19, 12.5],
                "text-rotate": ["coalesce", ["to-number", ["get", "render_rotation"]], 0],
                "text-rotation-alignment": "map",
                "text-allow-overlap": False,
                "text-ignore-placement": False,
            },
            "paint": {
                "text-color": "#f8fafc",
                "text-halo-color": "#4b5563",
                "text-halo-width": 1.2,
                "text-opacity": ["interpolate", ["linear"], ["zoom"], 17, 0.8, 18, 0.96],
            },
        },
        {
            "id": "runtime-fallback-label-theme-2-street-names",
            "type": "symbol",
            "source": source_id,
            "source-layer": "labels",
            "filter": ["==", "theme_index", 2],
            "minzoom": 16.5,
            "layout": {
                "symbol-placement": "line",
                "symbol-spacing": 340,
                "text-field": ["get", "text_content"],
                "text-font": ["Noto Sans Bold"],
                "text-size": ["interpolate", ["linear"], ["zoom"], 16, 11, 19, 14],
                "text-rotation-alignment": "map",
                "text-pitch-alignment": "viewport",
                "text-keep-upright": True,
                "text-allow-overlap": False,
                "text-ignore-placement": False,
            },
            "paint": {
                "text-color": "#1f2937",
                "text-halo-color": "#ffffff",
                "text-halo-width": 1.4,
                "text-opacity": ["interpolate", ["linear"], ["zoom"], 16.5, 0.0, 17, 0.9],
            },
        },
    ]


def runtime_boundary_point_layers(source_id: str = "alkis") -> list[dict]:
    outer_filter = [
        "any",
        ["==", ["get", "boundary_point_part"], "outer"],
        ["!", ["has", "boundary_point_part"]],
    ]
    inner_filter = ["==", ["get", "boundary_point_part"], "inner"]
    return [
        {
            "id": f"runtime-{source_id}-boundary-point-outer-fill",
            "type": "fill",
            "source": source_id,
            "source-layer": "boundary_point_geometries",
            "minzoom": 17,
            "filter": outer_filter,
            "paint": {
                "fill-color": ["coalesce", ["get", "fill_color"], "#ffffff"],
                "fill-opacity": 1,
            },
        },
        {
            "id": f"runtime-{source_id}-boundary-point-outer-line",
            "type": "line",
            "source": source_id,
            "source-layer": "boundary_point_geometries",
            "minzoom": 17,
            "filter": outer_filter,
            "layout": {"line-cap": "round", "line-join": "round"},
            "paint": {
                "line-color": ["coalesce", ["get", "stroke_color"], "#222222"],
                "line-opacity": 1,
                "line-width": ["interpolate", ["linear"], ["zoom"], 17, 0.8, 19, 1.05, 20, 1.25],
            },
        },
        {
            "id": f"runtime-{source_id}-boundary-point-inner-fill",
            "type": "fill",
            "source": source_id,
            "source-layer": "boundary_point_geometries",
            "minzoom": 17,
            "filter": inner_filter,
            "paint": {
                "fill-color": ["coalesce", ["get", "fill_color"], "#000000"],
                "fill-opacity": 1,
            },
        },
    ]


def _style_has_source_layer(layers: list[dict], source_id: str, source_layer: str) -> bool:
    return any(
        isinstance(layer, dict)
        and layer.get("source") == source_id
        and layer.get("source-layer") == source_layer
        for layer in layers
    )


def overview_geojson_sources(base: str, key: str) -> tuple[dict[str, dict], list[tuple[str, str, str]]]:
    sources: dict[str, dict] = {}
    available: list[tuple[str, str, str]] = []
    for state_slug in active_bucket_state_keys():
        safe_state = state_slug.replace("-", "_")
        for kind in ("boundaries", "labels"):
            asset_name = f"{state_slug}_overview_{kind}.json"
            if not (DATA_DIR / asset_name).exists():
                continue
            source_id = f"alkis_overview_{kind}_{safe_state}"
            sources[source_id] = {
                "type": "geojson",
                "data": f"{base}/assets/{asset_name}?key={key}&v=20260609-admin-label-points-v2",
            }
            available.append((state_slug, kind, source_id))
    return sources, available


def state_label_source() -> dict:
    return {
        "type": "geojson",
        "data": {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {
                        "slug": slug,
                        "name": name,
                    },
                    "geometry": {
                        "type": "Point",
                        "coordinates": [lon, lat],
                    },
                }
                for slug, (name, lon, lat) in STATE_LABEL_POINTS.items()
            ],
        },
    }


def state_label_layer(source_id: str) -> dict:
    return {
        "id": "state-labels",
        "type": "symbol",
        "source": source_id,
        "minzoom": 4.0,
        "maxzoom": 9.0,
        "layout": {
            "text-field": ["get", "name"],
            "text-font": ["Arial Bold"],
            "text-size": ["interpolate", ["linear"], ["zoom"], 4.0, 10, 5.5, 13, 7.5, 17],
            "text-allow-overlap": True,
            "text-ignore-placement": True,
            "text-padding": 14,
            "text-anchor": "center",
            "text-justify": "center",
        },
        "paint": {
            "text-color": "#4b5563",
            "text-halo-color": "#ffffff",
            "text-halo-width": 1.4,
            "text-opacity": ["interpolate", ["linear"], ["zoom"], 3.2, 0.0, 3.8, 0.0, 4.3, 0.95, 8.2, 0.95, 9.0, 0.0],
        },
    }


def state_outline_layer(source_id: str) -> dict:
    return {
        "id": "state-outlines",
        "type": "line",
        "source": source_id,
        "minzoom": 3.0,
        "maxzoom": 24.0,
        "paint": {
            "line-color": "#f86d14",
            "line-width": ["interpolate", ["linear"], ["zoom"], 3.0, 0.7, 5.5, 1.0, 8.0, 1.45, 12.0, 1.8, 18.0, 2.2],
            "line-opacity": ["interpolate", ["linear"], ["zoom"], 3.0, 0.45, 5.5, 0.62, 7.2, 0.5, 8.4, 0.18, 9.2, 0.0],
        },
    }


def overview_boundary_layers(source_id: str, suffix: str) -> list[dict]:
    return [
        {
            "id": f"own-county-boundary-{suffix}",
            "type": "line",
            "source": source_id,
            "filter": ["==", ["get", "class"], "county_boundary"],
            "minzoom": 7.0,
            "maxzoom": 10.4,
            "paint": {
                "line-color": "#c56ad7",
                "line-width": ["interpolate", ["linear"], ["zoom"], 7, 0.7, 10, 1.5],
                "line-opacity": ["interpolate", ["linear"], ["zoom"], 7, 0.0, 7.8, 0.7],
            },
        },
        {
            "id": f"own-city-boundary-{suffix}",
            "type": "line",
            "source": source_id,
            "filter": ["==", ["get", "class"], "city_boundary"],
            "minzoom": 8.6,
            "maxzoom": 11.8,
            "paint": {
                "line-color": "#d585df",
                "line-width": ["interpolate", ["linear"], ["zoom"], 8.6, 0.45, 11.8, 1.15],
                "line-opacity": ["interpolate", ["linear"], ["zoom"], 8.6, 0.0, 9.6, 0.62],
            },
        },
        {
            "id": f"own-cadastral-district-boundary-{suffix}",
            "type": "line",
            "source": source_id,
            "filter": ["==", ["get", "class"], "cadastral_district_boundary"],
            "minzoom": 10.9,
            "maxzoom": 14.8,
            "paint": {
                "line-color": "#e39be9",
                "line-width": ["interpolate", ["linear"], ["zoom"], 10.9, 0.3, 14.8, 0.9],
                "line-opacity": ["interpolate", ["linear"], ["zoom"], 10.9, 0.0, 11.7, 0.48],
            },
        },
        {
            "id": f"own-cadastral-section-boundary-{suffix}",
            "type": "line",
            "source": source_id,
            "filter": ["==", ["get", "class"], "cadastral_section_boundary"],
            "minzoom": 14.3,
            "maxzoom": 17.1,
            "paint": {
                "line-color": "#e9b3ec",
                "line-width": ["interpolate", ["linear"], ["zoom"], 14.3, 0.25, 17.1, 0.8],
                "line-opacity": ["interpolate", ["linear"], ["zoom"], 14.3, 0.0, 15.1, 0.42],
            },
        },
        {
            "id": f"own-county-label-{suffix}",
            "type": "symbol",
            "source": source_id,
            "filter": [
                "all",
                ["==", ["get", "class"], "county_label"],
                ["!=", ["get", "bez"], "Kreisfreie Stadt"],
            ],
            "minzoom": 7.6,
            "maxzoom": 10.8,
            "layout": {
                "text-field": [
                    "match",
                    ["get", "name"],
                    "Region Hannover",
                    "Region Hannover",
                    ["concat", "Landkreis ", ["get", "name"]],
                ],
                "text-font": _web_font_stack(["Arial Bold"]),
                "text-size": ["interpolate", ["linear"], ["zoom"], 7.6, 11, 10.2, 15],
                "text-max-width": 12,
                "text-allow-overlap": False,
                "text-ignore-placement": False,
                "text-padding": 18,
                "text-anchor": "center",
            },
            "paint": {
                "text-color": "#76517f",
                "text-halo-color": "#fbf8f3",
                "text-halo-width": 1.4,
                "text-opacity": ["interpolate", ["linear"], ["zoom"], 7.6, 0.0, 8.2, 0.92, 10.2, 0.92, 10.8, 0.0],
            },
        },
        {
            "id": f"own-city-label-{suffix}",
            "type": "symbol",
            "source": source_id,
            "filter": ["==", ["get", "class"], "city_label"],
            "minzoom": 9.2,
            "maxzoom": 13.4,
            "layout": {
                "text-field": ["get", "name"],
                "text-font": _web_font_stack(["Arial Bold"]),
                "text-size": ["interpolate", ["linear"], ["zoom"], 9.2, 10.5, 12.6, 14],
                "text-max-width": 10,
                "text-allow-overlap": False,
                "text-ignore-placement": False,
                "text-padding": 10,
                "text-anchor": "center",
            },
            "paint": {
                "text-color": "#84678c",
                "text-halo-color": "#fbf8f3",
                "text-halo-width": 1.2,
                "text-opacity": ["interpolate", ["linear"], ["zoom"], 9.2, 0.0, 9.8, 0.9, 12.8, 0.9, 13.4, 0.0],
            },
        },
    ]


def overview_label_layer(
    source_id: str,
    suffix: str,
    label_class: str,
    minzoom: float,
    maxzoom: float,
    text_size: list | int | float,
    color: str,
    font: list[str],
) -> dict:
    return {
        "id": f"overview-label-{label_class}-{suffix}",
        "type": "symbol",
        "source": source_id,
        "filter": ["==", ["get", "class"], label_class],
        "minzoom": minzoom,
        "maxzoom": maxzoom,
        "layout": {
            "text-field": ["get", "name"],
            "text-font": _web_font_stack(font),
            "text-size": text_size,
            "text-max-width": 999,
            "text-allow-overlap": False,
            "text-ignore-placement": False,
            "text-padding": 8,
            "text-rotation-alignment": "map",
            "text-rotate": ["*", -1, ["coalesce", ["to-number", ["get", "rotation"]], 0]],
            "text-keep-upright": True,
        },
        "paint": {
            "text-color": color,
            "text-halo-color": "#fbf8f3",
            "text-halo-width": 1.2,
            "text-opacity": ["interpolate", ["linear"], ["zoom"], minzoom, 0.0, minzoom + 0.8, 0.9],
        },
    }


def overview_label_layers(source_id: str, suffix: str) -> list[dict]:
    return [
        overview_label_layer(
            source_id,
            suffix,
            "city",
            8.8,
            12.4,
            ["interpolate", ["linear"], ["zoom"], 8.8, 12, 11.5, 17],
            "#364f5b",
            ["Arial Bold"],
        ),
        overview_label_layer(source_id, suffix, "local_place", 10.8, 15.8, 11, "#846d8e", ["Arial Bold"]),
        overview_label_layer(source_id, suffix, "cadastral_district", 11.6, 14.8, 12, "#876593", ["Arial Bold"]),
        overview_label_layer(source_id, suffix, "square", 14.0, 16.8, 10.5, "#8b7892", ["Arial Bold"]),
        overview_label_layer(source_id, suffix, "street_overview", 14.4, 17.25, 10.5, "#668996", ["Arial Bold"]),
        overview_label_layer(source_id, suffix, "water_name", 14.8, 17.4, 10.5, "#5d8d9f", ["Arial Bold"]),
        overview_label_layer(source_id, suffix, "cadastral_section", 15.0, 17.1, 11, "#876593", ["Arial Bold"]),
    ]



def _hybrid_raster_layer() -> dict:
    return {
        "id": "alkis-raster-overview",
        "type": "raster",
        "source": "alkis_raster",
        "minzoom": HYBRID_RASTER_MIN_ZOOM,
        "maxzoom": HYBRID_RASTER_MAX_ZOOM,
        "paint": {
            "raster-opacity": [
                "interpolate",
                ["linear"],
                ["zoom"],
                max(HYBRID_RASTER_MIN_ZOOM, HYBRID_VECTOR_MIN_ZOOM - 0.8),
                1.0,
                HYBRID_RASTER_MAX_ZOOM,
                0.0,
            ],
            "raster-fade-duration": 120,
        },
    }


def overview_raster_manifest() -> list[dict]:
    path = DATA_DIR / "overview_raster" / "manifest.json"
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    states = data.get("states") if isinstance(data, dict) else []
    return states if isinstance(states, list) else []


def overview_raster_sources_and_layers(base: str, key: str) -> tuple[dict, list[dict]]:
    sources: dict[str, dict] = {}
    layers: list[dict] = []
    for item in overview_raster_manifest():
        if not isinstance(item, dict):
            continue
        slug = str(item.get("slug") or "")
        raster_id = str(item.get("id") or slug)
        filename = str(item.get("file") or f"{slug}.png")
        coordinates = item.get("coordinates")
        if (
            not DATASET_RE.match(slug)
            or not DATASET_RE.match(raster_id)
            or not re.match(r"^[a-z0-9][a-z0-9_-]{0,80}\.png$", filename)
            or not isinstance(coordinates, list)
            or len(coordinates) != 4
        ):
            continue
        path = DATA_DIR / "overview_raster" / filename
        if not path.exists() or path.parent != DATA_DIR / "overview_raster":
            continue
        zoom = int(item.get("zoom") or 8)
        source_id = f"state_overview_raster_{raster_id.replace('-', '_')}"
        version = f"{path.stat().st_mtime_ns:x}"
        sources[source_id] = {
            "type": "image",
            "url": f"{base}/overview-raster/{filename}?key={key}&v={version}",
            "coordinates": coordinates,
        }
        if zoom <= 8:
            minzoom = 4
            maxzoom = min(HYBRID_RASTER_MAX_ZOOM, 10.4)
            opacity_stops = [4, 1.0, 8.8, 1.0, maxzoom, 0.0]
        else:
            minzoom = 8.4
            maxzoom = min(HYBRID_RASTER_MAX_ZOOM, 11.4)
            opacity_stops = [minzoom, 0.0, 9.0, 1.0, 10.8, 1.0, maxzoom, 0.0]
        layers.append({
            "id": f"state-overview-raster-{raster_id}",
            "type": "raster",
            "source": source_id,
            "minzoom": minzoom,
            "maxzoom": maxzoom,
            "paint": {
                "raster-opacity": ["interpolate", ["linear"], ["zoom"], *opacity_stops],
                "raster-fade-duration": 120,
            },
        })
    return sources, layers


def _state_overview_layers() -> list[dict]:
    if not (DATA_DIR / "states.json").exists():
        return []
    return [
        {
            "id": "state-overview-fill",
            "type": "fill",
            "source": STATE_OUTLINE_SOURCE_ID if "STATE_OUTLINE_SOURCE_ID" in globals() else "state_areas",
            "minzoom": 4,
            "maxzoom": max(HYBRID_RASTER_MIN_ZOOM + 2, 12),
            "paint": {
                "fill-color": [
                    "match",
                    ["coalesce", ["get", "slug"], ["get", "gen"], ["get", "name"], ["get", "NAME"], ["get", "GEN"]],
                    "niedersachsen", "#d8edcc",
                    "Niedersachsen", "#d8edcc",
                    "bremen", "#dbe9f6",
                    "Bremen", "#dbe9f6",
                    "hamburg", "#f5dfca",
                    "Hamburg", "#f5dfca",
                    "sachsen-anhalt", "#eadfcb",
                    "Sachsen-Anhalt", "#eadfcb",
                    "#ede6da",
                ],
                "fill-opacity": ["interpolate", ["linear"], ["zoom"], 4, 0.3, 8, 0.2, 11.5, 0.0],
            },
        },
        {
            "id": "state-overview-outline",
            "type": "line",
            "source": STATE_OUTLINE_SOURCE_ID if "STATE_OUTLINE_SOURCE_ID" in globals() else "state_areas",
            "minzoom": 4,
            "maxzoom": max(HYBRID_RASTER_MIN_ZOOM + 2, 12),
            "paint": {
                "line-color": "#8d8478",
                "line-opacity": ["interpolate", ["linear"], ["zoom"], 4, 0.42, 10, 0.24, 11.5, 0.0],
                "line-width": ["interpolate", ["linear"], ["zoom"], 4, 0.7, 9, 1.2],
            },
        },
    ]


def _raise_hybrid_vector_minzoom(layer: dict) -> dict:
    result = json.loads(json.dumps(layer))
    if result.get("source-layer") in {"polygons", "lines", "points"}:
        minzoom = result.get("minzoom")
        if isinstance(minzoom, (int, float)):
            result["minzoom"] = max(float(minzoom), HYBRID_VECTOR_MIN_ZOOM)
        else:
            result["minzoom"] = HYBRID_VECTOR_MIN_ZOOM
    return result


def style_for(request: Request, dataset_name: str, ds: Dataset, key: str) -> dict:
    style = national_style_template()

    base = public_base_url(request)
    header = ds.header or {}
    style["name"] = style.get("name") or f"OpenKataster {dataset_name}"
    style["sources"] = {
        "alkis": {
            "type": "vector",
            "tiles": [f"{base}/tiles/{dataset_name}/{{z}}/{{x}}/{{y}}.mvt?key={key}&v=20260609-composite-lines"],
            "minzoom": int(header.get("min_zoom", 0)),
            "maxzoom": style_source_maxzoom(int(header.get("max_zoom", 20))),
            "attribution": "© OpenKataster, ALKIS-Daten",
        }
    }

    style["layers"] = [
        web_layer(layer)
        for layer in style.get("layers", [])
        if layer.get("type") == "background" or layer.get("source") == "alkis"
    ]
    available_layers = {
        layer.get("id")
        for layer in (ds.metadata or {}).get("vector_layers", [])
        if isinstance(layer, dict) and layer.get("id")
    }
    if "boundary_point_geometries" in available_layers and not _style_has_source_layer(style["layers"], "alkis", "boundary_point_geometries"):
        style["layers"].extend(web_layer(layer) for layer in runtime_boundary_point_layers("alkis"))
    if GLYPHS_URL:
        style["glyphs"] = style_glyphs_url()
    style.pop("sprite", None)
    return style


def mosaic_style_for(request: Request, key: str) -> dict:
    style = national_style_template()

    generic_entries = mosaic_entries()
    if generic_entries:
        metadata = mosaic_metadata()
    else:
        metadata = direct_vector_metadata(style)
    base = public_base_url(request)
    original_sources = style.get("sources", {})
    direct_vector_sources = {
        source_id: source
        for source_id, source in original_sources.items()
        if (
            isinstance(source, dict)
            and source.get("type") == "vector"
            and source.get("openkataster_direct_dataset")
        )
    }
    vector_source_ids = {
        source_id
        for source_id, source in original_sources.items()
        if (
            isinstance(source, dict)
            and source.get("type") == "vector"
            and source_id not in direct_vector_sources
        )
    }
    next_sources = {}
    if generic_entries:
        next_sources["alkis"] = {
            "type": "vector",
            "tiles": [f"{base}/tiles/{VIRTUAL_GERMANY_DATASET}/{{z}}/{{x}}/{{y}}.mvt?key={key}&v=20260609-composite-lines"],
            "minzoom": max(int(metadata["minzoom"]), int(HYBRID_VECTOR_MIN_ZOOM)),
            "maxzoom": int(metadata["maxzoom"]),
            "attribution": "© OpenKataster, ALKIS-Daten",
        }
    for source_id, source in direct_vector_sources.items():
        dataset_name = str(source.get("openkataster_direct_dataset") or "").strip()
        if not dataset_name or not DATASET_RE.match(dataset_name):
            continue
        cache_key = str(source.get("openkataster_cache_key") or "direct-vector")
        next_sources[source_id] = {
            "type": "vector",
            "tiles": [f"{base}/tiles/{dataset_name}/{{z}}/{{x}}/{{y}}.mvt?key={key}&v={cache_key}"],
            "minzoom": int(source.get("minzoom", 0)),
            "maxzoom": int(source.get("maxzoom", 20)),
            "attribution": source.get("attribution", "© OpenKataster, ALKIS-Daten"),
        }
        if "bounds" in source:
            next_sources[source_id]["bounds"] = source["bounds"]
    for source_id, source in original_sources.items():
        if not isinstance(source, dict) or source.get("type") == "vector":
            continue
        if source.get("type") == "geojson":
            next_sources[source_id] = rewrite_geojson_source(source, base, key)
        else:
            next_sources[source_id] = json.loads(json.dumps(source))
    direct_volume_style = style.get("metadata", {}).get("openkataster_architecture") == "direct-volume"
    if direct_volume_style:
        overview_raster_sources, overview_raster_layers = {}, []
    else:
        overview_raster_sources, overview_raster_layers = overview_raster_sources_and_layers(base, key)
    next_sources.update(overview_raster_sources)
    overview_sources, overview_source_refs = overview_geojson_sources(base, key)
    next_sources[STATE_LABEL_SOURCE_ID] = state_label_source()
    next_sources[STATE_OUTLINE_SOURCE_ID] = {
        "type": "geojson",
        "data": f"{base}/assets/states.json?key={key}",
    }
    next_sources.update(overview_sources)

    available_layers = {
        layer.get("id")
        for layer in metadata.get("vector_layers", [])
        if isinstance(layer, dict) and layer.get("id")
    }
    next_layers = []
    overview_layers_added = False
    overview_layers = _state_overview_layers() + overview_raster_layers
    seen_vector_layer_keys: set[str] = set()
    for layer in style.get("layers", []):
        layer_source = layer.get("source")
        if layer.get("type") == "background":
            next_layers.append(json.loads(json.dumps(layer)))
            if not overview_layers_added:
                next_layers.extend(overview_layers)
                overview_layers_added = True
            continue
        if layer_source in vector_source_ids:
            if not generic_entries:
                continue
            source_layer = layer.get("source-layer")
            if available_layers and source_layer not in available_layers:
                continue
            rewritten_layer = _raise_hybrid_vector_minzoom(layer)
            rewritten_layer["source"] = "alkis"
            layer_key_data = {
                key_name: value
                for key_name, value in rewritten_layer.items()
                if key_name not in {"id", "source"}
            }
            layer_key = json.dumps(layer_key_data, sort_keys=True, separators=(",", ":"))
            if layer_key in seen_vector_layer_keys:
                continue
            seen_vector_layer_keys.add(layer_key)
            next_layers.append(web_layer(rewritten_layer))
            continue
        if isinstance(layer_source, str) and layer_source in next_sources:
            next_layers.append(web_layer(layer))

    if generic_entries and "alkis" in next_sources and not any(layer.get("source") == "alkis" for layer in next_layers):
        next_layers.extend(web_layer(layer) for layer in runtime_fallback_layers("alkis"))
    if (
        "boundary_point_geometries" in available_layers
        and "alkis" in next_sources
        and not _style_has_source_layer(next_layers, "alkis", "boundary_point_geometries")
    ):
        next_layers.extend(web_layer(layer) for layer in runtime_boundary_point_layers("alkis"))

    if not overview_layers_added:
        next_layers = overview_layers + next_layers

    for state_slug, kind, source_id in overview_source_refs:
        suffix = state_slug.replace("-", "_")
        if kind == "boundaries":
            next_layers.extend(overview_boundary_layers(source_id, suffix))
        elif kind == "labels":
            next_layers.extend(overview_label_layers(source_id, suffix))

    next_layers.append(web_layer(state_outline_layer(STATE_OUTLINE_SOURCE_ID)))
    next_layers.append(web_layer(state_label_layer(STATE_LABEL_SOURCE_ID)))

    style["name"] = style.get("name") or "OpenKataster Deutschland"
    style["sources"] = next_sources
    style["layers"] = next_layers
    if GLYPHS_URL:
        style["glyphs"] = style_glyphs_url()
    style.pop("sprite", None)
    return style


def web_fallback_style_for(request: Request, key: str) -> dict:
    metadata = mosaic_metadata()
    base = public_base_url(request)
    source = {
        "type": "vector",
        "tiles": [f"{base}/tiles/{VIRTUAL_GERMANY_DATASET}/{{z}}/{{x}}/{{y}}.mvt?key={key}&v=20260609-composite-lines"],
        "minzoom": metadata["minzoom"],
        "maxzoom": metadata["maxzoom"],
        "attribution": "© OpenKataster, ALKIS-Daten",
    }

    return {
        "version": 8,
        "name": "OpenKataster Deutschland",
        "sources": {"alkis": source},
        "glyphs": "https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf",
        "layers": [
            {
                "id": "background",
                "type": "background",
                "paint": {"background-color": "#fbf8f3"},
            },
            {
                "id": "parcel-fill",
                "type": "fill",
                "source": "alkis",
                "source-layer": "polygons",
                "filter": ["==", "thema", "Flurstücke"],
                "minzoom": 8,
                "paint": {
                    "fill-color": "#eee6d9",
                    "fill-opacity": ["interpolate", ["linear"], ["zoom"], 8, 0.02, 10, 0.18, 12.5, 0.42, 15, 0.28],
                },
            },
            {
                "id": "vegetation-fill",
                "type": "fill",
                "source": "alkis",
                "source-layer": "polygons",
                "filter": ["==", "thema", "Vegetation"],
                "minzoom": 9,
                "paint": {
                    "fill-color": "#dcead0",
                    "fill-opacity": ["interpolate", ["linear"], ["zoom"], 9, 0.0, 11.5, 0.42, 15, 0.32],
                },
            },
            {
                "id": "water-fill",
                "type": "fill",
                "source": "alkis",
                "source-layer": "polygons",
                "filter": ["==", "thema", "Gewässer"],
                "minzoom": 9,
                "paint": {
                    "fill-color": "#cfe8ec",
                    "fill-opacity": ["interpolate", ["linear"], ["zoom"], 9, 0.0, 11.5, 0.56, 16, 0.72],
                },
            },
            {
                "id": "transport-fill",
                "type": "fill",
                "source": "alkis",
                "source-layer": "polygons",
                "filter": ["==", "thema", "Verkehr"],
                "minzoom": 10.5,
                "paint": {
                    "fill-color": "#f5f0e7",
                    "fill-opacity": ["interpolate", ["linear"], ["zoom"], 10.5, 0.0, 12.5, 0.48, 16, 0.62],
                },
            },
            {
                "id": "industry-fill",
                "type": "fill",
                "source": "alkis",
                "source-layer": "polygons",
                "filter": ["==", "thema", "Industrie und Gewerbe"],
                "minzoom": 11,
                "paint": {
                    "fill-color": "#e8e5df",
                    "fill-opacity": ["interpolate", ["linear"], ["zoom"], 11, 0.0, 13, 0.42, 16, 0.58],
                },
            },
            {
                "id": "parcel-outline",
                "type": "line",
                "source": "alkis",
                "source-layer": "lines",
                "filter": ["==", "thema", "Flurstücke"],
                "minzoom": 13.2,
                "paint": {
                    "line-color": "#a99d8f",
                    "line-opacity": ["interpolate", ["linear"], ["zoom"], 13.2, 0.0, 14.8, 0.36, 17, 0.46],
                    "line-width": ["interpolate", ["linear"], ["zoom"], 13, 0.35, 16, 0.65, 20, 1.1],
                },
            },
            {
                "id": "transport-line",
                "type": "line",
                "source": "alkis",
                "source-layer": "lines",
                "filter": ["==", "thema", "Verkehr"],
                "minzoom": 13.5,
                "paint": {
                    "line-color": "#c8bfb3",
                    "line-opacity": ["interpolate", ["linear"], ["zoom"], 13.5, 0.0, 15, 0.36, 18, 0.5],
                    "line-width": ["interpolate", ["linear"], ["zoom"], 13, 0.35, 16, 0.7, 20, 1.4],
                },
            },
            {
                "id": "water-line",
                "type": "line",
                "source": "alkis",
                "source-layer": "lines",
                "filter": ["==", "thema", "Gewässer"],
                "minzoom": 12,
                "paint": {
                    "line-color": "#7db5bd",
                    "line-opacity": ["interpolate", ["linear"], ["zoom"], 12, 0.0, 13.5, 0.45, 18, 0.58],
                    "line-width": ["interpolate", ["linear"], ["zoom"], 12, 0.35, 16, 0.8, 20, 1.4],
                },
            },
            {
                "id": "cadastral-boundary",
                "type": "line",
                "source": "alkis",
                "source-layer": "lines",
                "filter": ["==", "thema", "Politische Grenzen"],
                "minzoom": 10.8,
                "paint": {
                    "line-color": "#c56ad7",
                    "line-opacity": ["interpolate", ["linear"], ["zoom"], 10.8, 0.0, 11.8, 0.42, 13.8, 0.32, 14.9, 0.0],
                    "line-width": ["interpolate", ["linear"], ["zoom"], 10, 0.7, 13, 1.3, 15, 0.6],
                    "line-dasharray": [3, 2],
                },
            },
            {
                "id": "building-fill",
                "type": "fill",
                "source": "alkis",
                "source-layer": "polygons",
                "filter": ["==", "thema", "Gebäude"],
                "minzoom": 14,
                "paint": {
                    "fill-color": "#cfc8bd",
                    "fill-opacity": ["interpolate", ["linear"], ["zoom"], 14, 0.0, 15.1, 0.82, 18, 0.9],
                },
            },
            {
                "id": "building-outline",
                "type": "line",
                "source": "alkis",
                "source-layer": "lines",
                "filter": ["==", "thema", "Gebäude"],
                "minzoom": 14.8,
                "paint": {
                    "line-color": "#81786d",
                    "line-opacity": ["interpolate", ["linear"], ["zoom"], 14.8, 0.0, 16, 0.72, 20, 0.9],
                    "line-width": ["interpolate", ["linear"], ["zoom"], 14, 0.35, 17, 0.75, 20, 1.2],
                },
            },
        ],
    }


def sqlite_feature_connection(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise HTTPException(status_code=404, detail="feature index not found")
    con = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA query_only = ON")
        con.execute("PRAGMA temp_store = MEMORY")
    except sqlite3.Error:
        pass
    return con


def load_properties(raw: str | bytes | None) -> dict:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def sqlite_table_exists(con: sqlite3.Connection, name: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE name = ? LIMIT 1",
        (name,),
    ).fetchone()
    return row is not None


def sqlite_column_exists(con: sqlite3.Connection, table: str, column: str) -> bool:
    return any(row["name"] == column for row in con.execute(f"PRAGMA table_info({table})"))


def compact_feature_schema(con: sqlite3.Connection) -> bool:
    return (
        sqlite_table_exists(con, "features")
        and sqlite_column_exists(con, "features", "search_text")
        and not sqlite_column_exists(con, "features", "geometry_wkb")
    )


def search_tokens(query: str) -> list[str]:
    tokens: list[str] = []
    for token in re.findall(r"[0-9A-Za-zÀ-ÖØ-öø-ÿ]+", query):
        normalized = token.lower()
        if len(normalized) >= 2 or normalized.isdigit():
            tokens.append(normalized)
    return tokens[:8]


def german_token_variants(token: str) -> list[str]:
    variants = [token]
    replacements = (("ae", "ä"), ("oe", "ö"), ("ue", "ü"), ("ss", "ß"))
    for source, target in replacements:
        if source in token:
            variants.append(token.replace(source, target))
    return list(dict.fromkeys(variants))


def fts_token_query(token: str) -> str:
    token = token.strip().lower()
    if not token:
        return ""
    if token.isdigit() or len(token) <= 2:
        return token
    return f"{token}*"


def fts_query(query: str) -> str:
    groups = []
    for token in search_tokens(query):
        variants = [fts_token_query(variant) for variant in german_token_variants(token)]
        variants = [variant for variant in variants if variant]
        if not variants:
            continue
        if len(variants) == 1:
            groups.append(variants[0])
        else:
            groups.append("(" + " OR ".join(variants) + ")")
    return " AND ".join(groups)


def like_pattern(query: str) -> str:
    return f"%{query.strip()}%"


def text_contains(value: str | None, query: str) -> bool:
    return query.casefold() in str(value or "").casefold()


def text_has_word(value: str | None, query: str) -> bool:
    tokens = {token.casefold() for token in re.findall(r"[0-9A-Za-zÀ-ÖØ-öø-ÿ]+", str(value or ""))}
    query_tokens = [token.casefold() for token in re.findall(r"[0-9A-Za-zÀ-ÖØ-öø-ÿ]+", query)]
    return bool(query_tokens) and all(token in tokens for token in query_tokens)


def normalize_parcel_number(value: str | None) -> str:
    return re.sub(r"\s+", "", str(value or "").strip()).casefold()


def query_parcel_number(query: str) -> str:
    fraction_match = re.search(r"\d+\s*/\s*\d+", query)
    if fraction_match:
        return normalize_parcel_number(fraction_match.group(0))
    numbers = re.findall(r"\d+", query)
    if len(numbers) > 1 and re.search(r"\bflur\b", query, flags=re.IGNORECASE):
        return normalize_parcel_number(numbers[-1])
    match = re.search(r"\d+", query)
    return normalize_parcel_number(match.group(0)) if match else ""


def state_display_name(state_slug: str) -> str:
    return STATE_LABEL_POINTS.get(state_slug, (state_slug.replace("-", " ").title(), 0, 0))[0]


def normalize_state_key(value: str | None) -> str:
    key = (value or "").strip().casefold().replace("_", "-")
    return {
        "baden-wuerttemberg": "baden-wurttemberg",
        "nordrhein-westfalen": "nordrhein-westfalen",
        "rheinland-pfalz": "rheinland-pfalz",
        "sachsen-anhalt": "sachsen-anhalt",
        "schleswig-holstein": "schleswig-holstein",
        "mecklenburg-vorpommern": "mecklenburg-vorpommern",
        "thuringen": "thueringen",
    }.get(key, key)


def state_search_results(query: str, allowed_states: set[str], limit: int) -> list[dict]:
    folded = normalize_place_search_text(query)
    compact = compact_place_search_text(query)
    if not folded:
        return []
    results = []
    for state in sorted(allowed_states):
        if state not in STATE_LABEL_POINTS:
            continue
        label, lon, lat = STATE_LABEL_POINTS[state]
        label_folded = normalize_place_search_text(label)
        slug_folded = normalize_place_search_text(state.replace("-", " "))
        label_compact = compact_place_search_text(label)
        slug_compact = compact_place_search_text(state)
        if folded not in {label_folded, slug_folded} and compact not in {label_compact, slug_compact}:
            continue
        results.append({
            "kind": "place",
            "result_type": "place",
            "label": label,
            "subtitle": "Bundesland",
            "state": state,
            "state_label": label,
            "center": [lon, lat],
            "bbox": PLACE_BOUNDS.get(state, {}).get(label),
            "zoom": 8.0,
            "feature": {"name": label, "state": state, "class": "Bundesland"},
        })
    return results[:limit]


STATIC_PLACE_RESULTS = [
    ("hamburg", "Hamburg", 10.0000, 53.5500, "Stadt", 11.8),
    ("bremen", "Bremen", 8.8072, 53.0758, "Stadt", 11.8),
    ("bremen", "Bremerhaven", 8.5809, 53.5396, "Stadt", 12.0),
    ("saarland", "Saarbrücken", 6.9969, 49.2402, "Stadt", 12.0),
    ("saarland", "Saarlouis", 6.7523, 49.3137, "Stadt", 12.2),
    ("saarland", "Neunkirchen", 7.1772, 49.3445, "Stadt", 12.2),
    ("saarland", "Homburg", 7.3387, 49.3208, "Stadt", 12.2),
    ("saarland", "Völklingen", 6.8589, 49.2516, "Stadt", 12.2),
    ("saarland", "Sankt Ingbert", 7.1167, 49.2767, "Stadt", 12.2),
    ("saarland", "Merzig", 6.6387, 49.4431, "Stadt", 12.4),
    ("saarland", "St. Wendel", 7.1695, 49.4660, "Stadt", 12.4),
    ("niedersachsen", "Hannover", 9.7320, 52.3759, "Landeshauptstadt", 11.8),
    ("niedersachsen", "Braunschweig", 10.5268, 52.2689, "Stadt", 12.0),
    ("niedersachsen", "Oldenburg", 8.2146, 53.1435, "Stadt", 12.0),
    ("niedersachsen", "Osnabrück", 8.0472, 52.2799, "Stadt", 12.0),
    ("niedersachsen", "Wolfsburg", 10.7865, 52.4227, "Stadt", 12.0),
    ("niedersachsen", "Göttingen", 9.9352, 51.5413, "Stadt", 12.0),
    ("niedersachsen", "Hildesheim", 9.9511, 52.1548, "Stadt", 12.2),
    ("niedersachsen", "Salzgitter", 10.3899, 52.1379, "Stadt", 12.0),
    ("niedersachsen", "Wilhelmshaven", 8.1069, 53.5323, "Stadt", 12.0),
    ("niedersachsen", "Delmenhorst", 8.6327, 53.0511, "Stadt", 12.2),
    ("niedersachsen", "Lüneburg", 10.4070, 53.2487, "Stadt", 12.2),
    ("niedersachsen", "Celle", 10.0805, 52.6226, "Stadt", 12.2),
    ("niedersachsen", "Hameln", 9.3564, 52.1031, "Stadt", 12.2),
    ("niedersachsen", "Emden", 7.2060, 53.3671, "Stadt", 12.2),
    ("niedersachsen", "Lingen", 7.3188, 52.5214, "Stadt", 12.2),
    ("niedersachsen", "Nordhorn", 7.0714, 52.4308, "Stadt", 12.2),
    ("niedersachsen", "Garbsen", 9.5969, 52.4275, "Stadt", 12.4),
    ("niedersachsen", "Wunstorf", 9.4359, 52.4246, "Stadt", 12.4),
    ("niedersachsen", "Uelzen", 10.5589, 52.9657, "Stadt", 12.4),
    ("niedersachsen", "Soltau", 9.8430, 52.9866, "Stadt", 12.4),
    ("niedersachsen", "Rotenburg", 9.3960, 53.1114, "Stadt", 12.4),
    ("niedersachsen", "Verden", 9.2350, 52.9234, "Stadt", 12.4),
    ("niedersachsen", "Stade", 9.4765, 53.5998, "Stadt", 12.4),
    ("niedersachsen", "Cuxhaven", 8.6947, 53.8593, "Stadt", 12.2),
    ("niedersachsen", "Aurich", 7.4836, 53.4692, "Stadt", 12.4),
    ("niedersachsen", "Leer", 7.4679, 53.2316, "Stadt", 12.4),
    ("niedersachsen", "Meppen", 7.2974, 52.6906, "Stadt", 12.4),
    ("niedersachsen", "Cloppenburg", 8.0537, 52.8475, "Stadt", 12.4),
    ("niedersachsen", "Vechta", 8.2850, 52.7265, "Stadt", 12.4),
    ("niedersachsen", "Nienburg", 9.2070, 52.6461, "Stadt", 12.4),
    ("niedersachsen", "Peine", 10.2289, 52.3197, "Stadt", 12.4),
    ("niedersachsen", "Gifhorn", 10.5460, 52.4860, "Stadt", 12.4),
    ("niedersachsen", "Helmstedt", 11.0106, 52.2280, "Stadt", 12.4),
    ("niedersachsen", "Goslar", 10.4298, 51.9059, "Stadt", 12.4),
    ("niedersachsen", "Northeim", 9.9958, 51.7066, "Stadt", 12.4),
    ("niedersachsen", "Einbeck", 9.8690, 51.8202, "Stadt", 12.4),
    ("niedersachsen", "Osterode", 10.2508, 51.7279, "Stadt", 12.4),
    ("niedersachsen", "Buxtehude", 9.7000, 53.4769, "Stadt", 12.4),
]

PLACE_BOUNDS = {
    "hamburg": {
        "Hamburg": [9.73, 53.39, 10.33, 53.74],
    },
    "bremen": {
        "Bremen": [8.48, 52.99, 8.99, 53.23],
        "Bremerhaven": [8.48, 53.48, 8.66, 53.62],
    },
    "saarland": {
        "Saarland": [6.35, 49.10, 7.40, 49.65],
        "Saarbrücken": [6.82, 49.15, 7.15, 49.32],
        "Saarlouis": [6.65, 49.25, 6.86, 49.38],
        "Neunkirchen": [7.10, 49.29, 7.27, 49.40],
        "Homburg": [7.25, 49.25, 7.43, 49.40],
        "Völklingen": [6.74, 49.18, 6.94, 49.31],
        "Sankt Ingbert": [7.04, 49.22, 7.22, 49.34],
        "Merzig": [6.52, 49.36, 6.76, 49.52],
        "St. Wendel": [7.08, 49.40, 7.28, 49.54],
    },
}


def place_scan_signature() -> tuple[tuple[str, int, int], ...]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return tuple(
        sorted(
            (path.name, path.stat().st_mtime_ns, path.stat().st_size)
            for path in list(DATA_DIR.glob("*_overview_labels.json")) + list(DATA_DIR.glob("*_overview_boundaries.json"))
        )
    )


def normalize_place_class(value: str) -> str:
    return {
        "city": "Stadt",
        "town": "Ort",
        "village": "Ort",
        "local_place": "Ort",
        "cadastral_district": "Gemarkung",
    }.get(value, value.replace("_", " ").strip().title() or "Ort")


def normalize_place_search_text(value: str | None) -> str:
    text = (value or "").strip().casefold()
    replacements = (
        ("ä", "ae"),
        ("ö", "oe"),
        ("ü", "ue"),
        ("ß", "ss"),
    )
    for source, target in replacements:
        text = text.replace(source, target)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text).strip()


def compact_place_search_text(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", normalize_place_search_text(value))


def plain_place_search_text(value: str | None) -> str:
    text = (value or "").strip().casefold()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text).strip()


def compact_plain_place_search_text(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", plain_place_search_text(value))


def sqlite_file_signature(path: Path) -> tuple[int, int]:
    try:
        stat = path.stat()
    except OSError:
        return (0, 0)
    return (stat.st_mtime_ns, stat.st_size)


def gn250_places_signature() -> tuple[int, int]:
    return sqlite_file_signature(GN250_PLACES_DB)


def openplz_signature() -> tuple[int, int]:
    return sqlite_file_signature(OPENPLZ_DB)


def postcode_areas_signature() -> tuple[int, int]:
    return sqlite_file_signature(POSTCODE_AREAS_DB)


def geocoder_signature() -> tuple[int, int]:
    return sqlite_file_signature(GEOCODER_DB)


def fast_geocoder_signature() -> tuple[int, int]:
    return sqlite_file_signature(FAST_GEOCODER_DB)


@lru_cache(maxsize=65536)
def postcode_area_lookup(lon: float, lat: float, signature: tuple[int, int]) -> str:
    if signature == (0, 0):
        return ""
    try:
        con = sqlite3.connect(f"file:{POSTCODE_AREAS_DB}?mode=ro", uri=True)
        rows = con.execute(
            """
            SELECT a.postcode, a.geom_wkb, ((a.maxx - a.minx) * (a.maxy - a.miny)) AS bbox_area
            FROM areas_rtree r
            JOIN areas a ON a.id = r.id
            WHERE r.minx <= ? AND r.maxx >= ? AND r.miny <= ? AND r.maxy >= ?
            ORDER BY bbox_area ASC
            """,
            (lon, lon, lat, lat),
        ).fetchall()
        con.close()
    except sqlite3.Error:
        return ""
    if not rows:
        return ""
    point = Point(lon, lat)
    for postcode, geom_wkb, _bbox_area in rows:
        try:
            if wkb.loads(geom_wkb).covers(point):
                return str(postcode or "").strip()
        except GEOSException:
            continue
    return ""


def normalize_openplz_street(value: str | None) -> str:
    text = normalize_place_search_text(value)
    text = re.sub(r"\bstr\b", "strasse", text)
    text = re.sub(r"\bstrae\b", "strasse", text)
    text = re.sub(r"\bstrasse\b", "strasse", text)
    return re.sub(r"[^a-z0-9]+", "", text)


def normalize_openplz_place(value: str | None) -> str:
    return normalize_place_search_text(value)


@lru_cache(maxsize=16384)
def openplz_lookup_postcode(street_norm: str, place_norm: str, state_key: str, signature: tuple[int, int]) -> dict | None:
    if signature == (0, 0) or not street_norm or not place_norm:
        return None
    try:
        con = sqlite3.connect(f"file:{OPENPLZ_DB}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            """
            SELECT postal_code, locality, borough, suburb, priority
            FROM aliases
            WHERE street_norm = ?
              AND place_norm = ?
              AND (? = '' OR state_key = ?)
            ORDER BY priority ASC, postal_code ASC, locality ASC
            LIMIT 8
            """,
            (street_norm, place_norm, state_key, state_key),
        ).fetchall()
        con.close()
    except sqlite3.Error:
        return None
    if not rows:
        return None
    row = rows[0]
    return {
        "post_code": str(row["postal_code"] or "").strip(),
        "locality": str(row["locality"] or "").strip(),
        "borough": str(row["borough"] or "").strip(),
        "suburb": str(row["suburb"] or "").strip(),
    }


def enrich_address_postcode(address: dict, lon: float, lat: float) -> None:
    address.setdefault("country", "Deutschland")
    if address.get("post_code") or address.get("postal_code"):
        return
    try:
        lon_value = round(float(lon), 7)
        lat_value = round(float(lat), 7)
    except (TypeError, ValueError):
        return
    postcode = postcode_area_lookup(lon_value, lat_value, postcode_areas_signature())
    if not postcode:
        return
    address["post_code"] = postcode
    address["postal_code"] = postcode


def allowed_place_states(dataset: str) -> set[str]:
    if is_virtual_germany_dataset(dataset):
        return set(active_bucket_state_keys()) or set(STATE_LABEL_POINTS)
    state_key, _, _ = _mosaic_state_key(DATA_DIR / f"{dataset}.pmtiles")
    return {state_key}


def search_suggestion_states_for_dataset(dataset: str, state: str = "") -> set[str]:
    state_key = normalize_state_key(state)
    if state_key:
        return {state_key}
    if is_virtual_germany_dataset(dataset):
        local_states = {entry.name for entry in all_local_search_db_entries()}
        return local_states or allowed_place_states(dataset)
    return allowed_place_states(dataset)


@lru_cache(maxsize=8)
def gn250_place_entries(signature: tuple[int, int]) -> tuple[dict, ...]:
    if not signature or signature == (0, 0) or not GN250_PLACES_DB.exists():
        return tuple()
    rows: list[dict] = []
    try:
        con = sqlite3.connect(f"file:{GN250_PLACES_DB}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            for row in con.execute(
                """
                SELECT
                  state_key, state_name, class, name, municipality, district, ags,
                  lon, lat, min_lon, min_lat, max_lon, max_lat, population
                FROM places
                ORDER BY
                  CASE class WHEN 'Gemeinde' THEN 0 WHEN 'Ort' THEN 1 ELSE 2 END,
                  COALESCE(population, 0) DESC, name
                """
            ):
                bbox = None
                if row["min_lon"] is not None and row["min_lat"] is not None and row["max_lon"] is not None and row["max_lat"] is not None:
                    bbox = [float(row["min_lon"]), float(row["min_lat"]), float(row["max_lon"]), float(row["max_lat"])]
                rows.append(
                    {
                        "state": normalize_state_key(row["state_key"]),
                        "state_label": row["state_name"],
                        "name": row["name"],
                        "name_norm": normalize_place_search_text(row["name"]),
                        "name_ascii": compact_place_search_text(row["name"]),
                        "name_plain": plain_place_search_text(row["name"]),
                        "name_plain_ascii": compact_plain_place_search_text(row["name"]),
                        "class": row["class"] or "Ort",
                        "municipality": row["municipality"] or "",
                        "municipality_plain": plain_place_search_text(row["municipality"] or ""),
                        "district": row["district"] or "",
                        "ags": row["ags"] or "",
                        "center": [float(row["lon"]), float(row["lat"])],
                        "bbox": bbox,
                        "zoom": 11.0 if row["class"] == "Gemeinde" else (13.0 if row["class"] == "Ortsteil" else 12.5),
                        "priority": -10 if row["class"] == "Gemeinde" else (0 if row["class"] == "Ort" else 5),
                        "population": int(row["population"] or 0),
                    }
                )
        finally:
            con.close()
    except sqlite3.Error:
        return tuple()
    return tuple(rows)


@lru_cache(maxsize=8)
def place_entries(signature: tuple[tuple[str, int, int], ...]) -> tuple[dict, ...]:
    del signature
    entries: list[dict] = []
    for path in DATA_DIR.glob("*_overview_labels.json"):
        state = path.name.removesuffix("_overview_labels.json")
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        features = data.get("features") if isinstance(data, dict) else []
        if not isinstance(features, list):
            continue
        for feature in features:
            if not isinstance(feature, dict):
                continue
            props = feature.get("properties") or {}
            geom = feature.get("geometry") or {}
            coords = geom.get("coordinates") or []
            name = str(props.get("name") or "").strip()
            if not name or len(coords) < 2:
                continue
            place_class = str(props.get("class") or "Ort")
            if "street" in place_class.casefold():
                continue
            try:
                lon = float(coords[0])
                lat = float(coords[1])
                minzoom = float(props.get("minzoom") or 12.5)
                priority = int(float(props.get("priority") or 20))
            except (TypeError, ValueError):
                continue
            entries.append(
                {
                    "state": state,
                    "name": name,
                    "class": normalize_place_class(place_class),
                    "center": [lon, lat],
                    "zoom": max(10.5, min(13.5, minzoom + 1.0)),
                    "priority": priority,
                }
            )
    for path in DATA_DIR.glob("*_overview_boundaries.json"):
        state = path.name.removesuffix("_overview_boundaries.json")
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        for feature in data.get("features", []):
            props = feature.get("properties") or {}
            geom = feature.get("geometry") or {}
            coords = geom.get("coordinates") or []
            if props.get("class") != "city_label" or len(coords) < 2:
                continue
            name = str(props.get("name") or "").strip()
            if not name:
                continue
            entries.append({
                "state": state,
                "name": name,
                "class": "Gemeinde",
                "center": [float(coords[0]), float(coords[1])],
                "zoom": 12.0,
                "priority": 5,
            })
    for state, name, lon, lat, label, zoom in STATIC_PLACE_RESULTS:
        entries.append(
            {
                "state": state,
                "name": name,
                "class": label,
                "center": [lon, lat],
                "bbox": PLACE_BOUNDS.get(state, {}).get(name),
                "zoom": zoom,
                "priority": -10,
            }
        )
    for state, (name, lon, lat) in STATE_LABEL_POINTS.items():
        is_city_state = state in {"berlin", "bremen", "hamburg"}
        entries.append(
            {
                "state": state,
                "name": name,
                "class": "Stadt" if is_city_state else "Bundesland",
                "center": [lon, lat],
                "bbox": PLACE_BOUNDS.get(state, {}).get(name),
                "zoom": 11.6 if is_city_state else 8.0,
                "priority": -30 if is_city_state else -25,
            }
        )
    return tuple(entries)


def requested_municipality(query: str, allowed_states: set[str]) -> dict | None:
    cached = _requested_municipality_cached(
        query,
        tuple(sorted(allowed_states)),
        gn250_places_signature(),
    )
    return dict(cached) if cached else None


@lru_cache(maxsize=4096)
def _requested_municipality_cached(query: str, allowed_states_key: tuple[str, ...], signature: tuple[int, int]) -> dict | None:
    del signature
    allowed_states = set(allowed_states_key)
    normalized_query = normalize_place_search_text(query)
    compact_query = compact_place_search_text(query)
    plain_query = plain_place_search_text(query)
    compact_plain_query = compact_plain_place_search_text(query)
    candidates: list[dict] = []
    for entry in gn250_place_entries(gn250_places_signature()):
        state = normalize_state_key(str(entry.get("state") or ""))
        name = str(entry.get("name") or "").strip()
        municipality = str(entry.get("municipality") or "").strip()
        if state not in allowed_states or not name:
            continue
        names = [name]
        if municipality and normalize_place_search_text(municipality) != normalize_place_search_text(name):
            names.append(municipality)
        for candidate_name in names:
            name_norm = normalize_place_search_text(candidate_name)
            name_ascii = compact_place_search_text(candidate_name)
            name_plain = plain_place_search_text(candidate_name)
            name_plain_ascii = compact_plain_place_search_text(candidate_name)
            if (
                re.search(rf"(?<!\w){re.escape(name_norm)}(?!\w)", normalized_query)
                or compact_query == name_ascii
                or compact_query.endswith(name_ascii)
                or re.search(rf"(?<!\w){re.escape(name_plain)}(?!\w)", plain_query)
                or compact_plain_query == name_plain_ascii
                or compact_plain_query.endswith(name_plain_ascii)
            ):
                municipality_name = municipality or name
                candidates.append({
                    "state": state,
                    "name": municipality_name,
                    "folded": normalize_place_search_text(municipality_name),
                    "bbox": entry.get("bbox"),
                    "source_name": name,
                })
                break
    return max(candidates, key=lambda item: len(item["source_name"])) if candidates else None


def requested_place_context(query: str, allowed_states: set[str]) -> dict | None:
    cached = _requested_place_context_cached(
        query,
        tuple(sorted(allowed_states)),
        gn250_places_signature(),
    )
    return dict(cached) if cached else None


@lru_cache(maxsize=4096)
def _requested_place_context_cached(query: str, allowed_states_key: tuple[str, ...], signature: tuple[int, int]) -> dict | None:
    del signature
    allowed_states = set(allowed_states_key)
    folded_query = query.casefold()
    normalized_query = normalize_place_search_text(query)
    compact_query = compact_place_search_text(query)
    plain_query = plain_place_search_text(query)
    compact_plain_query = compact_plain_place_search_text(query)
    candidates: list[dict] = []
    for entry in gn250_place_entries(gn250_places_signature()):
        state = normalize_state_key(str(entry.get("state") or ""))
        name = str(entry.get("name") or "").strip()
        if state not in allowed_states or not name:
            continue
        name_norm = str(entry.get("name_norm") or normalize_place_search_text(name))
        name_ascii = str(entry.get("name_ascii") or compact_place_search_text(name))
        name_plain = str(entry.get("name_plain") or plain_place_search_text(name))
        name_plain_ascii = str(entry.get("name_plain_ascii") or compact_plain_place_search_text(name))
        if (
            re.search(rf"(?<!\w){re.escape(name_norm)}(?!\w)", normalized_query)
            or compact_query == name_ascii
            or compact_query.endswith(name_ascii)
            or re.search(rf"(?<!\w){re.escape(name_plain)}(?!\w)", plain_query)
            or compact_plain_query == name_plain_ascii
            or compact_plain_query.endswith(name_plain_ascii)
        ):
            candidates.append({
                "state": state,
                "name": name,
                "folded": name_norm,
                "bbox": entry.get("bbox"),
                "municipality": entry.get("municipality") or "",
            })
    return max(candidates, key=lambda item: len(item["name"])) if candidates else None


def requested_state_context(value: str, allowed_states: set[str]) -> str | None:
    folded = value.strip().casefold()
    if not folded:
        return None
    for state in allowed_states:
        label = state_display_name(state).casefold()
        slug = state.replace("-", " ").casefold()
        if folded in {state.casefold(), slug, label}:
            return state
    return None


def exact_place_key_variants(value: str | None) -> set[str]:
    text = str(value or "").strip()
    if not text:
        return set()
    return {
        normalize_place_search_text(text),
        compact_place_search_text(text),
        plain_place_search_text(text),
        compact_plain_place_search_text(text),
    }


def exact_place_context(value: str, allowed_states: set[str]) -> dict | None:
    cached = _exact_place_context_cached(
        value,
        tuple(sorted(allowed_states)),
        gn250_places_signature(),
    )
    return dict(cached) if cached else None


@lru_cache(maxsize=8)
def exact_place_context_index(signature: tuple[int, int]) -> dict[str, tuple[dict, ...]]:
    index: dict[str, list[dict]] = {}
    for entry in gn250_place_entries(signature):
        state = normalize_state_key(str(entry.get("state") or ""))
        name = str(entry.get("name") or "").strip()
        municipality = str(entry.get("municipality") or "").strip()
        values = [name]
        if municipality and normalize_place_search_text(municipality) != normalize_place_search_text(name):
            values.append(municipality)
        for value in values:
            context = {
                "state": state,
                "name": value,
                "folded": normalize_place_search_text(value),
                "bbox": entry.get("bbox"),
                "municipality": municipality,
            }
            for key in exact_place_key_variants(value):
                if key:
                    index.setdefault(key, []).append(context)
    return {key: tuple(value) for key, value in index.items()}


@lru_cache(maxsize=4096)
def _exact_place_context_cached(value: str, allowed_states_key: tuple[str, ...], signature: tuple[int, int]) -> dict | None:
    allowed_states = set(allowed_states_key)
    index = exact_place_context_index(signature)
    seen: set[tuple[str, str, str]] = set()
    for key in exact_place_key_variants(value):
        for context in index.get(key, tuple()):
            state = normalize_state_key(str(context.get("state") or ""))
            if state not in allowed_states:
                continue
            dedupe_key = (state, str(context.get("name") or ""), str(context.get("municipality") or ""))
            if dedupe_key in seen:
                continue
            return dict(context)
    return None


def states_for_place_context(value: str, allowed_states: set[str]) -> tuple[str, ...]:
    cached = _states_for_place_context_cached(
        value,
        tuple(sorted(allowed_states)),
        gn250_places_signature(),
    )
    return tuple(cached)


@lru_cache(maxsize=4096)
def _states_for_place_context_cached(value: str, allowed_states_key: tuple[str, ...], signature: tuple[int, int]) -> tuple[str, ...]:
    place = str(value or "").strip()
    if len(place) < 2:
        return tuple()
    allowed_states = set(allowed_states_key)
    index = exact_place_context_index(signature)
    matches: set[str] = set()
    for key in exact_place_key_variants(place):
        for context in index.get(key, tuple()):
            state = normalize_state_key(str(context.get("state") or ""))
            if state in allowed_states:
                matches.add(state)
    return tuple(sorted(matches))


def query_without_municipality(query: str, municipality: dict | None) -> str:
    if not municipality:
        return query
    cleaned = re.sub(
        rf"(?<!\w){re.escape(municipality['name'])}(?!\w)",
        " ",
        query,
        flags=re.IGNORECASE,
    )
    return re.sub(r"\s+", " ", cleaned).strip() or query


def query_without_place_context(query: str, context: dict | None) -> str:
    if not context:
        return query
    name = str(context.get("name") or "")
    folded = str(context.get("folded") or "")
    cleaned = query
    if name:
        cleaned = re.sub(
            rf"(?<!\w){re.escape(name)}(?!\w)",
            " ",
            cleaned,
            flags=re.IGNORECASE,
        )
    if folded and folded != name.casefold():
        cleaned = re.sub(
            rf"(?<!\w){re.escape(folded)}(?!\w)",
            " ",
            cleaned,
            flags=re.IGNORECASE,
        )
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned if len(cleaned) >= 2 else query


def place_context_as_municipality(context: dict | None) -> dict | None:
    if not context:
        return None
    municipality = str(context.get("municipality") or "").strip()
    name = municipality or str(context.get("name") or "").strip()
    folded = str(context.get("folded") or normalize_place_search_text(name)).strip()
    if not name or not folded:
        return None
    return {"name": name, "folded": folded}


def municipality_name_matches(value: str, wanted: dict | None) -> bool:
    if not wanted:
        return True
    value_norm = normalize_place_search_text(value)
    wanted_norm = normalize_place_search_text(str(wanted.get("name") or wanted.get("folded") or ""))
    wanted_folded = normalize_place_search_text(str(wanted.get("folded") or ""))
    return bool(value_norm and (value_norm == wanted_norm or value_norm == wanted_folded))


def normalized_bbox(value) -> tuple[float, float, float, float] | None:
    if not value or len(value) != 4:
        return None
    try:
        min_lon, min_lat, max_lon, max_lat = [float(item) for item in value]
    except (TypeError, ValueError):
        return None
    if min_lon > max_lon:
        min_lon, max_lon = max_lon, min_lon
    if min_lat > max_lat:
        min_lat, max_lat = max_lat, min_lat
    return min_lon, min_lat, max_lon, max_lat


def feature_bbox_sql(alias: str, bbox) -> tuple[str, tuple[float, float, float, float]]:
    normalized = normalized_bbox(bbox)
    if not normalized:
        return "", ()
    min_lon, min_lat, max_lon, max_lat = normalized
    return (
        f" AND {alias}.max_lon >= ? AND {alias}.min_lon <= ? AND {alias}.max_lat >= ? AND {alias}.min_lat <= ?",
        (min_lon, max_lon, min_lat, max_lat),
    )


def point_bbox_sql(alias: str, bbox) -> tuple[str, tuple[float, float, float, float]]:
    normalized = normalized_bbox(bbox)
    if not normalized:
        return "", ()
    min_lon, min_lat, max_lon, max_lat = normalized
    return (
        f" AND {alias}.lon >= ? AND {alias}.lon <= ? AND {alias}.lat >= ? AND {alias}.lat <= ?",
        (min_lon, max_lon, min_lat, max_lat),
    )


def nearest_municipality(state: str, lon: float, lat: float) -> dict | None:
    best = None
    for entry in gn250_place_entries(gn250_places_signature()):
        if normalize_state_key(str(entry.get("state") or "")) != normalize_state_key(state):
            continue
        bbox = entry.get("bbox") or []
        if len(bbox) != 4:
            continue
        min_lon, min_lat, max_lon, max_lat = [float(value) for value in bbox]
        cx = min(max(lon, min_lon), max_lon)
        cy = min(max(lat, min_lat), max_lat)
        distance = (lon - cx) * (lon - cx) + (lat - cy) * (lat - cy)
        area = max(0.0, max_lon - min_lon) * max(0.0, max_lat - min_lat)
        place_class = str(entry.get("class") or "")
        municipality = str(entry.get("municipality") or "").strip()
        name = str(entry.get("name") or "").strip()
        city = municipality or name
        if not city:
            continue
        class_rank = 0 if place_class == "Gemeinde" else 1
        candidate = (distance, class_rank, area, city)
        if best is None or candidate < best:
            best = candidate
    if not best:
        return None
    return {"name": best[3], "folded": normalize_place_search_text(best[3])}


def municipality_at(state: str, lon: float, lat: float) -> dict | None:
    matches = []
    for entry in gn250_place_entries(gn250_places_signature()):
        if normalize_state_key(str(entry.get("state") or "")) != normalize_state_key(state):
            continue
        bbox = entry.get("bbox") or []
        if len(bbox) != 4:
            continue
        min_lon, min_lat, max_lon, max_lat = [float(value) for value in bbox]
        if not (min_lon <= lon <= max_lon and min_lat <= lat <= max_lat):
            continue
        area = max(0.0, max_lon - min_lon) * max(0.0, max_lat - min_lat)
        place_class = str(entry.get("class") or "")
        municipality = str(entry.get("municipality") or "").strip()
        name = str(entry.get("name") or "").strip()
        city = municipality or name
        if not city:
            continue
        class_rank = 0 if place_class == "Gemeinde" else 1
        matches.append((class_rank, area, city))
    if not matches:
        return None
    _, _, name = min(matches, key=lambda item: item[:2])
    return {"name": name, "folded": normalize_place_search_text(name)}


def enrich_address_municipality(item: dict, state: str) -> dict:
    if item.get("result_type") != "address":
        return item
    center = item.get("center") or []
    if len(center) != 2:
        return item
    municipality = municipality_at(state, float(center[0]), float(center[1]))
    if not municipality:
        municipality = nearest_municipality(state, float(center[0]), float(center[1]))
    if not municipality:
        return item
    address = item.get("address") if isinstance(item.get("address"), dict) else {}
    address["city"] = municipality["name"]
    street = str(address.get("street") or "").strip()
    house_number = str(address.get("house_number") or "").strip()
    base = " ".join(part for part in (street, house_number) if part) or str(item.get("label") or "Adresse")
    locality = municipality["name"]
    item["label"] = f"{base}, {locality}" if locality else base
    item["subtitle"] = f"Adresse in {municipality['name']}"
    item["address"] = address
    feature = item.get("feature") if isinstance(item.get("feature"), dict) else {}
    feature["address"] = item["label"]
    feature["addresses"] = [address]
    item["feature"] = feature
    item["municipality"] = municipality["name"]
    return item


def search_places_for_dataset(dataset: str, query: str, limit: int) -> list[dict]:
    allowed_states_key = tuple(sorted(allowed_place_states(dataset)))
    cached = _search_places_for_dataset_cached(
        dataset,
        query,
        int(limit),
        gn250_places_signature(),
        allowed_states_key,
    )
    return [dict(item) for item in cached]


@lru_cache(maxsize=4096)
def _search_places_for_dataset_cached(
    dataset: str,
    query: str,
    limit: int,
    signature: tuple[int, int],
    allowed_states_key: tuple[str, ...],
) -> tuple[dict, ...]:
    del dataset, signature
    query_tokens = [normalize_place_search_text(token) for token in search_tokens(query)]
    if not query_tokens:
        return tuple()
    allowed_states = set(allowed_states_key)

    results: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for entry in gn250_place_entries(gn250_places_signature()):
        state = normalize_state_key(str(entry.get("state") or ""))
        name = str(entry.get("name") or "")
        if state not in allowed_states:
            continue
        haystack = " ".join(
            part
            for part in (
                str(entry.get("name_norm") or ""),
                str(entry.get("name_ascii") or ""),
                str(entry.get("name_plain") or ""),
                str(entry.get("name_plain_ascii") or ""),
                normalize_place_search_text(str(entry.get("municipality") or "")),
                str(entry.get("municipality_plain") or ""),
                str(entry.get("ags") or ""),
                str(entry.get("state_label") or ""),
            )
            if part
        )
        if not all(token and token in haystack for token in query_tokens):
            continue
        folded = str(entry.get("name_norm") or normalize_place_search_text(name))
        municipality = str(entry.get("municipality") or "")
        key = (state, folded)
        if key in seen:
            continue
        seen.add(key)
        subtitle_parts = [
            str(entry.get("class") or "Ort"),
            municipality if municipality and normalize_place_search_text(municipality) != folded else "",
            str(entry.get("state_label") or state_display_name(state)),
        ]
        results.append(
            {
                "kind": "place",
                "result_type": "place",
                "label": name,
                "subtitle": ", ".join(part for part in subtitle_parts if part),
                "state": state,
                "state_label": entry.get("state_label") or state_display_name(state),
                "center": entry.get("center"),
                "bbox": entry.get("bbox"),
                "zoom": entry.get("zoom") or 12.5,
                "feature": {
                    "name": name,
                    "state": state,
                    "class": entry.get("class") or "Ort",
                    "municipality": municipality,
                    "district": entry.get("district") or "",
                    "ags": entry.get("ags") or "",
                },
                "_place_priority": entry.get("priority") or 5,
            }
        )
    if results:
        def place_rank(item: dict) -> tuple[int, int, int, str]:
            label = str(item.get("label") or "")
            folded = normalize_place_search_text(label)
            query_folded = normalize_place_search_text(query)
            return (
                0 if folded == query_folded else 1,
                0 if folded.startswith(query_folded) else 1,
                int(item.get("_place_priority") or 20),
                folded,
            )

        results.sort(key=place_rank)
        for item in results:
            item.pop("_place_priority", None)
    return tuple(results[:limit])

    for item in cadastre_gemarkung_entries(cadastre_gemarkung_signature(dataset)):
        state = str(item.get("state") or "")
        name = str(item.get("gemarkung") or "").strip()
        if state not in allowed_states or not name:
            continue
        haystack = f"{normalize_place_search_text(name)} {compact_place_search_text(name)} {item.get('gemarkungsnummer') or ''}"
        if not all(
            any(normalize_place_search_text(variant) in haystack for variant in german_token_variants(token))
            for token in query_tokens
        ):
            continue
        folded = normalize_place_search_text(name)
        key = (state, folded)
        if key in seen:
            continue
        seen.add(key)
        results.append(
            {
                "kind": "place",
                "result_type": "place",
                "label": name,
                "subtitle": "Gemarkung",
                "state": state,
                "center": item.get("center"),
                "bbox": item.get("bbox"),
                "zoom": 13.0,
                "feature": {
                    "name": name,
                    "state": state,
                    "gemarkungsnummer": item.get("gemarkungsnummer") or "",
                },
                "_place_priority": 12,
            }
        )

    def place_rank(item: dict) -> tuple[int, int, int, str]:
        label = str(item.get("label") or "")
        folded = normalize_place_search_text(label)
        query_folded = normalize_place_search_text(query)
        return (
            0 if folded == query_folded else 1,
            0 if folded.startswith(query_folded) else 1,
            int(item.get("_place_priority") or 20),
            folded,
        )

    results.sort(key=place_rank)
    for item in results:
        item.pop("_place_priority", None)
    return results[:limit]


def feature_label(kind: str, properties: dict) -> str:
    if kind == "parcel":
        return f"Flurstück {properties.get('flurstueck')}" if properties.get("flurstueck") else "Flurstück"
    return properties.get("gebaeudefunktion_text") or properties.get("name") or "Gebäude"


def feature_subtitle(kind: str, properties: dict) -> str:
    if kind == "parcel":
        parts = [
            f"Gemarkung {properties.get('gemarkung')}" if properties.get("gemarkung") else "",
            f"Flur {properties.get('flur')}" if properties.get("flur") else "",
            properties.get("address") or "",
        ]
        return " · ".join(part for part in parts if part)
    parts = [
        properties.get("address") or "",
        f"{properties.get('geschosse_oberirdisch')} Vollgeschosse" if properties.get("geschosse_oberirdisch") is not None else "",
    ]
    return " · ".join(part for part in parts if part)


def result_from_feature(row: sqlite3.Row, geom=None) -> dict:
    if geom is None:
        geom = wkb.loads(bytes(row["geometry_wkb"]))
    properties = load_properties(row["properties_json"])
    kind = row["kind"]
    properties["source_db"] = properties.get("source_db") or row["source_db"]
    properties["gml_id"] = properties.get("gml_id") or row["gml_id"]
    properties["geometry"] = mapping(geom)
    min_lon, min_lat, max_lon, max_lat = geom.bounds
    point = geom.representative_point()
    return {
        "kind": kind,
        "label": feature_label(kind, properties),
        "subtitle": feature_subtitle(kind, properties),
        "source_db": properties["source_db"],
        "gml_id": properties["gml_id"],
        "center": [point.x, point.y],
        "bbox": [min_lon, min_lat, max_lon, max_lat],
        "feature": properties,
    }


def compact_feature_label(kind: str, properties: dict, fallback: str) -> str:
    label = str(fallback or properties.get("label") or "").strip()
    if kind == "parcel":
        return f"Flurstück {label}" if label else "Flurstück"
    return label or properties.get("address") or properties.get("funktion") or "Gebäude"


def compact_feature_subtitle(kind: str, properties: dict) -> str:
    if kind == "parcel":
        parts = [
            f"Gemarkung {properties.get('gemarkung')}" if properties.get("gemarkung") else "",
            f"Flur {properties.get('flur')}" if properties.get("flur") else "",
            properties.get("lage") or properties.get("address") or "",
        ]
        return " · ".join(part for part in parts if part)
    parts = [
        properties.get("address") or "",
        properties.get("funktion") or "",
    ]
    return " · ".join(part for part in parts if part)


def result_from_compact_feature(row: sqlite3.Row) -> dict:
    properties = load_properties(row["properties_json"])
    kind = str(row["kind"] or "")
    feature_id = str(row["id"] or "")
    properties["source_db"] = properties.get("source_db") or "sachsen-anhalt"
    properties["gml_id"] = properties.get("gml_id") or feature_id
    properties["id"] = properties.get("id") or feature_id
    if kind == "parcel":
        properties["flurstueck"] = properties.get("flurstueck") or properties.get("label") or row["label"]
    min_lon = float(row["min_lon"])
    min_lat = float(row["min_lat"])
    max_lon = float(row["max_lon"])
    max_lat = float(row["max_lat"])
    lon = float(row["lon"])
    lat = float(row["lat"])
    return {
        "kind": kind,
        "label": compact_feature_label(kind, properties, str(row["label"] or "")),
        "subtitle": compact_feature_subtitle(kind, properties),
        "source_db": properties["source_db"],
        "gml_id": properties["gml_id"],
        "center": [lon, lat],
        "bbox": [min_lon, min_lat, max_lon, max_lat],
        "feature": properties,
    }


def result_from_any_feature(row: sqlite3.Row) -> dict:
    if "geometry_wkb" in row.keys():
        return result_from_feature(row)
    return result_from_compact_feature(row)


def compact_address_properties(row: sqlite3.Row) -> dict:
    label = str(row["compact_address"] or row["compact_street_house"] or "").strip()
    street_house = str(row["compact_street_house"] or "").strip()
    address = {
        "label": label,
        "street_house": street_house,
        "source": row["compact_address_source"] if "compact_address_source" in row.keys() else "",
    }
    if "compact_address_lon" in row.keys() and row["compact_address_lon"] is not None:
        address["lon"] = float(row["compact_address_lon"])
    if "compact_address_lat" in row.keys() and row["compact_address_lat"] is not None:
        address["lat"] = float(row["compact_address_lat"])
    if "compact_address_parcel_id" in row.keys() and row["compact_address_parcel_id"]:
        address["parcel_id"] = row["compact_address_parcel_id"]
    match = re.match(r"^(.+?)\s+([0-9].*)$", street_house)
    if match:
        address["street"] = match.group(1).strip()
        address["house_number"] = match.group(2).strip()
    return address


def result_from_compact_feature_address(feature_row: sqlite3.Row, address_properties: dict) -> dict:
    result = result_from_compact_feature(feature_row)
    address_label = str(address_properties.get("label") or "").strip()
    if address_label:
        result["label"] = address_label
        result["subtitle"] = "Adresse"
        result["address"] = address_properties
        result["feature"]["address"] = address_label
        result["feature"]["addresses"] = [address_properties]
    result["result_type"] = "address"
    return result


def result_from_feature_address(feature_row: sqlite3.Row, address_properties: dict, geom=None) -> dict:
    result = result_from_feature(feature_row, geom)
    address_label = str(address_properties.get("label") or "").strip()
    if not address_label:
        street = str(address_properties.get("street") or "").strip()
        house_number = str(address_properties.get("house_number") or "").strip()
        address_label = " ".join(part for part in (street, house_number) if part)
    if address_label:
        result["label"] = address_label
        result["subtitle"] = "Adresse"
        result["address"] = address_properties
        result["feature"]["address"] = address_label
        result["feature"]["addresses"] = [address_properties]
    result["result_type"] = "address"
    return result


def street_from_address_properties(address: dict) -> str:
    street = str(address.get("street") or "").strip()
    if street:
        return street
    street_house = str(address.get("street_house") or "").strip()
    label = str(address.get("label") or "").strip()
    for value in (street_house, label):
        match = re.match(r"^(.+?)\s+[0-9].*$", value)
        if match:
            return match.group(1).strip()
    return ""


def search_streets_in_index(path: Path, query: str, limit: int, bbox=None, municipality: dict | None = None) -> list[dict]:
    normalized = normalized_bbox(bbox)
    if not normalized or not search_tokens(query):
        return []
    grouped: dict[str, dict] = {}

    def add_street(street: str, center: list[float] | None, item_bbox: list[float] | None):
        street = street.strip()
        if not street:
            return
        key = normalize_place_search_text(street)
        if not key:
            return
        entry = grouped.setdefault(
            key,
            {
                "street": street,
                "count": 0,
                "bbox": None,
                "sum_lon": 0.0,
                "sum_lat": 0.0,
                "center_count": 0,
                "points": [],
            },
        )
        entry["count"] += 1
        if center and len(center) == 2:
            entry["sum_lon"] += float(center[0])
            entry["sum_lat"] += float(center[1])
            entry["center_count"] += 1
        if item_bbox and len(item_bbox) == 4:
            numeric_bbox = [float(value) for value in item_bbox]
            if entry["bbox"] is None:
                entry["bbox"] = numeric_bbox[:]
            else:
                entry["bbox"][0] = min(entry["bbox"][0], numeric_bbox[0])
                entry["bbox"][1] = min(entry["bbox"][1], numeric_bbox[1])
                entry["bbox"][2] = max(entry["bbox"][2], numeric_bbox[2])
                entry["bbox"][3] = max(entry["bbox"][3], numeric_bbox[3])
            point = center if center and len(center) == 2 else center_from_bbox(numeric_bbox)
            if point and len(point) == 2:
                entry["points"].append((float(point[0]), float(point[1]), numeric_bbox))

    def dominant_street_geometry(entry: dict) -> tuple[list[float] | None, list[float] | None]:
        points = entry.get("points") or []
        if not points:
            bbox_value = entry.get("bbox")
            center_count = int(entry.get("center_count") or 0)
            if center_count:
                return [entry["sum_lon"] / center_count, entry["sum_lat"] / center_count], bbox_value
            return center_from_bbox(bbox_value), bbox_value
        threshold_sq = 0.012 * 0.012
        clusters: list[dict] = []
        for lon, lat, item_bbox in sorted(points, key=lambda point: (point[1], point[0])):
            selected = None
            best_distance = None
            for cluster in clusters:
                cx = cluster["sum_lon"] / cluster["count"]
                cy = cluster["sum_lat"] / cluster["count"]
                distance = (lon - cx) * (lon - cx) + (lat - cy) * (lat - cy)
                if distance <= threshold_sq and (best_distance is None or distance < best_distance):
                    selected = cluster
                    best_distance = distance
            if selected is None:
                selected = {"count": 0, "sum_lon": 0.0, "sum_lat": 0.0, "bbox": item_bbox[:]}
                clusters.append(selected)
            selected["count"] += 1
            selected["sum_lon"] += lon
            selected["sum_lat"] += lat
            selected["bbox"][0] = min(selected["bbox"][0], item_bbox[0])
            selected["bbox"][1] = min(selected["bbox"][1], item_bbox[1])
            selected["bbox"][2] = max(selected["bbox"][2], item_bbox[2])
            selected["bbox"][3] = max(selected["bbox"][3], item_bbox[3])
        cluster = max(clusters, key=lambda item: item["count"])
        return [cluster["sum_lon"] / cluster["count"], cluster["sum_lat"] / cluster["count"]], cluster["bbox"]

    with sqlite_feature_connection(path) as con:
        feature_bbox_clause, feature_bbox_params = feature_bbox_sql("f", normalized)
        if compact_feature_schema(con):
            if sqlite_table_exists(con, "feature_fts") and sqlite_table_exists(con, "feature_addresses"):
                try:
                    rows = con.execute(
                        f"""
                        SELECT
                          fa.address AS compact_address,
                          fa.street_house AS compact_street_house,
                          fa.parcel_id AS compact_address_parcel_id,
                          fa.lon AS compact_address_lon,
                          fa.lat AS compact_address_lat,
                          fa.source AS compact_address_source,
                          f.min_lon,
                          f.min_lat,
                          f.max_lon,
                          f.max_lat
                        FROM feature_fts
                        JOIN features f ON f.id = feature_fts.feature_id
                        LEFT JOIN feature_addresses fa ON fa.feature_id = f.id
                        WHERE feature_fts MATCH ?
                          AND f.kind = 'building'
                          {feature_bbox_clause}
                        ORDER BY bm25(feature_fts)
                        LIMIT ?
                        """,
                        (fts_query(query), *feature_bbox_params, 2000),
                    ).fetchall()
                except sqlite3.OperationalError:
                    rows = []
                for row in rows:
                    address = compact_address_properties(row)
                    street = street_from_address_properties(address)
                    item_bbox = [row["min_lon"], row["min_lat"], row["max_lon"], row["max_lat"]]
                    center = [address["lon"], address["lat"]] if address.get("lon") is not None and address.get("lat") is not None else center_from_bbox(item_bbox)
                    add_street(street, center, item_bbox)
        else:
            if sqlite_table_exists(con, "feature_address_search"):
                try:
                    rows = con.execute(
                        f"""
                        SELECT fa.properties_json AS address_properties_json,
                               f.min_lon,
                               f.min_lat,
                               f.max_lon,
                               f.max_lat
                        FROM feature_address_search s
                        JOIN feature_addresses fa ON fa.id = CAST(s.feature_address_id AS INTEGER)
                        JOIN features f
                          ON f.source_db = fa.source_db
                         AND f.kind = fa.kind
                         AND f.gml_id = fa.gml_id
                        WHERE feature_address_search MATCH ?
                          AND fa.kind = 'building'
                          {feature_bbox_clause}
                        ORDER BY bm25(feature_address_search)
                        LIMIT ?
                        """,
                        (fts_query(query), *feature_bbox_params, 2000),
                    ).fetchall()
                except sqlite3.OperationalError:
                    rows = []
                for row in rows:
                    address = load_properties(row["address_properties_json"])
                    street = street_from_address_properties(address)
                    item_bbox = [row["min_lon"], row["min_lat"], row["max_lon"], row["max_lat"]]
                    center = center_from_bbox(item_bbox)
                    add_street(street, center, item_bbox)

        point_bbox_clause, point_bbox_params = point_bbox_sql("a", normalized)
        if sqlite_table_exists(con, "address_search") and sqlite_table_exists(con, "address_points"):
            try:
                rows = con.execute(
                    f"""
                    SELECT a.*
                    FROM address_search
                    JOIN address_points a ON a.id = CAST(address_search.address_id AS INTEGER)
                    WHERE address_search MATCH ?
                    {point_bbox_clause}
                    ORDER BY bm25(address_search)
                    LIMIT ?
                    """,
                    (fts_query(query), *point_bbox_params, 2000),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []
            for row in rows:
                address = load_properties(row["properties_json"])
                street = street_from_address_properties(address)
                lon = float(row["lon"])
                lat = float(row["lat"])
                add_street(street, [lon, lat], [lon, lat, lon, lat])

    wanted = normalize_place_search_text(query)
    results: list[dict] = []
    municipality_name = str((municipality or {}).get("name") or "").strip()
    for entry in grouped.values():
        street_norm = normalize_place_search_text(entry["street"])
        center, bbox_value = dominant_street_geometry(entry)
        if not bbox_value or not center:
            continue
        label = f"{entry['street']}, {municipality_name}" if municipality_name else entry["street"]
        results.append(
            {
                "kind": "street",
                "result_type": "street",
                "label": label,
                "subtitle": "Straße",
                "center": center,
                "bbox": bbox_value,
                "zoom": 17.4,
                "feature": {
                    "street": entry["street"],
                    "municipality": municipality_name,
                    "address_count": entry["count"],
                },
                "_rank": (
                    0 if street_norm == wanted else 1,
                    0 if street_norm.startswith(wanted) else 1,
                    -int(entry["count"] or 0),
                    street_norm,
                ),
            }
        )
    results.sort(key=lambda item: item.pop("_rank"))
    return results[:limit]


def address_match_score(address: dict, wanted: dict) -> int:
    score = 0
    for key in ("street", "house_number", "label"):
        left = str(address.get(key) or "").strip().casefold()
        right = str(wanted.get(key) or "").strip().casefold()
        if right and left == right:
            score += 2 if key != "label" else 1
    return score


def matching_address_points(con: sqlite3.Connection, source_db: str, address_properties: dict) -> list:
    street = str(address_properties.get("street") or "").strip()
    house_number = str(address_properties.get("house_number") or "").strip()
    label = str(address_properties.get("label") or "").strip()
    clauses = ["source_db = ?"]
    params: list[str] = [source_db]
    if street:
        clauses.append("properties_json LIKE ?")
        params.append(like_pattern(street))
    if house_number:
        clauses.append("properties_json LIKE ?")
        params.append(like_pattern(house_number))
    if not street and not house_number and label:
        clauses.append("properties_json LIKE ?")
        params.append(like_pattern(label))
    rows = con.execute(
        f"""
        SELECT properties_json, geometry_wkb, lon, lat
        FROM address_points
        WHERE {" AND ".join(clauses)}
        LIMIT 80
        """,
        tuple(params),
    ).fetchall()
    matched = []
    for row in rows:
        properties = load_properties(row["properties_json"])
        if address_match_score(properties, address_properties) < 2:
            continue
        try:
            geom = wkb.loads(bytes(row["geometry_wkb"]))
        except (GEOSException, TypeError, ValueError):
            geom = Point(float(row["lon"]), float(row["lat"]))
        matched.append((properties, geom))
    return matched


def tile_xy_for_lonlat(lon: float, lat: float, z: int) -> tuple[int, int]:
    n = 2**z
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
    return x, y


def tile_coord_to_lonlat(z: int, x: int, y: int, coord: list | tuple, extent: int = 4096) -> tuple[float, float]:
    n = 2**z
    lon = (x + float(coord[0]) / extent) / n * 360.0 - 180.0
    mercator_y = math.pi * (1 - 2 * (y + float(coord[1]) / extent) / n)
    lat = math.degrees(math.atan(math.sinh(mercator_y)))
    return lon, lat


def tile_fraction_for_lonlat(lon: float, lat: float, z: int, extent: int = 4096) -> tuple[int, int, float, float]:
    n = 2**z
    tile_x_float = (lon + 180.0) / 360.0 * n
    tile_y_float = (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n
    tile_x = int(tile_x_float)
    tile_y = int(tile_y_float)
    return tile_x, tile_y, (tile_x_float - tile_x) * extent, (tile_y_float - tile_y) * extent


def tile_geometry_to_lonlat_geometry(z: int, x: int, y: int, geometry: dict, extent: int = 4096) -> dict | None:
    def convert_point(coord):
        if not isinstance(coord, (list, tuple)) or len(coord) < 2:
            return None
        lon, lat = tile_coord_to_lonlat(z, x, y, coord, extent)
        return [lon, lat]

    def convert_nested(coords, depth: int):
        if depth == 0:
            return convert_point(coords)
        converted = []
        for item in coords or []:
            next_item = convert_nested(item, depth - 1)
            if next_item is not None:
                converted.append(next_item)
        return converted

    geom_type = str((geometry or {}).get("type") or "")
    coords = (geometry or {}).get("coordinates")
    depth_by_type = {
        "Point": 0,
        "MultiPoint": 1,
        "LineString": 1,
        "MultiLineString": 2,
        "Polygon": 2,
        "MultiPolygon": 3,
    }
    depth = depth_by_type.get(geom_type)
    if depth is None:
        return None
    converted = convert_nested(coords, depth)
    if converted is None:
        return None
    return {"type": geom_type, "coordinates": converted}


def geometry_bbox(geometry: dict) -> list[float] | None:
    points: list[list[float]] = []

    def collect(coords):
        if isinstance(coords, (list, tuple)) and len(coords) >= 2 and all(isinstance(value, (int, float)) for value in coords[:2]):
            points.append([float(coords[0]), float(coords[1])])
            return
        if isinstance(coords, (list, tuple)):
            for item in coords:
                collect(item)

    collect((geometry or {}).get("coordinates"))
    if not points:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return [min(xs), min(ys), max(xs), max(ys)]


def center_from_bbox(bbox: list[float] | None) -> list[float] | None:
    if not bbox or len(bbox) != 4:
        return None
    return [(bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2]


def runtime_tile_feature_result(kind: str, feature: dict, geometry: dict, z: int, x: int, y: int, extent: int) -> dict | None:
    properties = dict(feature.get("properties") or {})
    lonlat_geometry = tile_geometry_to_lonlat_geometry(z, x, y, geometry, extent)
    if not lonlat_geometry:
        return None
    bbox = geometry_bbox(lonlat_geometry)
    center = center_from_bbox(bbox)
    if kind == "parcel":
        properties["flurstueck"] = properties.get("flurstueck") or properties.get("label") or properties.get("text_content") or ""
    properties["source_db"] = properties.get("source_db") or "sachsen-anhalt"
    properties["gml_id"] = properties.get("gml_id") or properties.get("id") or ""
    properties["id"] = properties.get("id") or properties["gml_id"]
    properties["geometry"] = lonlat_geometry
    if bbox:
        properties["bbox"] = bbox
    if center:
        properties["center"] = center
    if kind == "building":
        properties.setdefault("addresses", [])
        properties.setdefault("address", "")
    return properties


def query_runtime_tile_features_for_point(lon: float, lat: float) -> tuple[list[dict], list[dict]]:
    z = 18
    tile_x, tile_y, local_x, local_y = tile_fraction_for_lonlat(lon, lat, z)
    tile_data = mosaic_tile(z, tile_x, tile_y)
    if not tile_data:
        return [], []
    try:
        raw_tile = gzip.decompress(tile_data)
    except gzip.BadGzipFile:
        raw_tile = tile_data
    try:
        decoded = mapbox_vector_tile.decode(
            raw_tile,
            default_options={"geojson": False, "y_coord_down": True},
        )
    except Exception:
        return [], []

    click = Point(local_x, local_y)
    matches: list[tuple[str, float, dict]] = []
    for layer_name, kind in (("building_fills", "building"), ("surfaces", "parcel")):
        layer = decoded.get(layer_name) or {}
        extent = int(layer.get("extent", 4096) or 4096)
        if extent != 4096:
            scaled_click = Point(local_x * extent / 4096, local_y * extent / 4096)
        else:
            scaled_click = click
        for feature in layer.get("features", []):
            geometry = feature.get("geometry") or {}
            geom_type = geometry.get("type")
            if geom_type not in {"Polygon", "MultiPolygon"}:
                continue
            try:
                tile_geom = shape(geometry)
            except (GEOSException, TypeError, ValueError):
                continue
            if not (tile_geom.covers(scaled_click) or tile_geom.intersects(scaled_click)):
                continue
            result = runtime_tile_feature_result(kind, feature, geometry, z, tile_x, tile_y, extent)
            if not result:
                continue
            matches.append((kind, float(tile_geom.area or 0.0), result))

    parcels: list[dict] = []
    buildings: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for kind, _, feature in sorted(matches, key=lambda item: item[1]):
        key = (kind, str(feature.get("id") or feature.get("gml_id") or feature.get("label") or ""))
        if key in seen:
            continue
        seen.add(key)
        if kind == "building" and len(buildings) < 8:
            buildings.append(feature)
        elif kind == "parcel" and len(parcels) < 8:
            parcels.append(feature)
    return parcels, buildings


def detail_pmtiles_path_for_source(source_db: str) -> Path | None:
    normalized = source_db.replace("_", "-").casefold()
    for state_slug in sorted(active_bucket_state_keys(), key=len, reverse=True):
        if state_slug.casefold() in normalized:
            path = DATA_DIR / f"{state_slug}_detail.pmtiles"
            return path if path.exists() else None
    match = re.match(r"alkis[_-]([a-z0-9_-]+?)(?:[_-]\d+)?$", source_db.casefold())
    if not match:
        return None
    state_slug = match.group(1).replace("_", "-")
    path = DATA_DIR / f"{state_slug}_detail.pmtiles"
    return path if path.exists() else None


def building_label_points_for_parcel(parcel_row: sqlite3.Row, address_properties: dict, parcel_geom) -> list[Point]:
    house_number = str(address_properties.get("house_number") or "").strip()
    if not house_number:
        return []
    detail_path = detail_pmtiles_path_for_source(str(parcel_row["source_db"]))
    if not detail_path:
        return []

    min_lon, min_lat, max_lon, max_lat = parcel_geom.bounds
    z = 18
    try:
        with detail_path.open("rb") as header_handle:
            z = int(Reader(MmapSource(header_handle)).header().get("max_zoom") or z)
    except Exception:
        z = 18
    min_x, max_y = tile_xy_for_lonlat(min_lon, min_lat, z)
    max_x, min_y = tile_xy_for_lonlat(max_lon, max_lat, z)
    min_x, max_x = sorted((min_x, max_x))
    min_y, max_y = sorted((min_y, max_y))
    points: list[Point] = []

    try:
        with detail_path.open("rb") as handle:
            reader = Reader(MmapSource(handle))
            for tile_x in range(min_x, max_x + 1):
                for tile_y in range(min_y, max_y + 1):
                    tile_data = reader.get(z, tile_x, tile_y)
                    if not tile_data:
                        continue
                    try:
                        raw_tile = gzip.decompress(tile_data)
                    except gzip.BadGzipFile:
                        raw_tile = tile_data
                    decoded = mapbox_vector_tile.decode(
                        raw_tile,
                        default_options={"geojson": False, "y_coord_down": True},
                    )
                    label_layer = decoded.get("labels") or {}
                    extent = int(label_layer.get("extent", 4096) or 4096)
                    for feature in label_layer.get("features", []):
                        properties = feature.get("properties") or {}
                        if str(properties.get("thema") or "") != "Gebäude":
                            continue
                        if str(properties.get("text_content") or "").strip().casefold() != house_number.casefold():
                            continue
                        geometry = feature.get("geometry") or {}
                        if geometry.get("type") != "Point":
                            continue
                        lon, lat = tile_coord_to_lonlat(z, tile_x, tile_y, geometry.get("coordinates") or [], extent)
                        point = Point(lon, lat)
                        if parcel_geom.buffer(0.000001).covers(point):
                            points.append(point)
    except Exception:
        return []

    return points


def building_for_parcel_address(
    con: sqlite3.Connection,
    parcel_row: sqlite3.Row,
    address_properties: dict,
) -> tuple[sqlite3.Row, object] | None:
    try:
        parcel_geom = wkb.loads(bytes(parcel_row["geometry_wkb"]))
    except (GEOSException, TypeError, ValueError):
        return None

    label_points = [(address_properties, point) for point in building_label_points_for_parcel(parcel_row, address_properties, parcel_geom)]
    address_points = label_points or matching_address_points(con, parcel_row["source_db"], address_properties)

    min_lon, min_lat, max_lon, max_lat = parcel_geom.bounds
    building_rows = con.execute(
        """
        SELECT f.*
        FROM feature_index i
        JOIN features f ON f.id = i.id
        WHERE f.source_db = ?
          AND f.kind = 'building'
          AND i.min_lon <= ? AND i.max_lon >= ?
          AND i.min_lat <= ? AND i.max_lat >= ?
        LIMIT 300
        """,
        (parcel_row["source_db"], max_lon, min_lon, max_lat, min_lat),
    ).fetchall()

    best: tuple[float, sqlite3.Row, object] | None = None
    for building_row in building_rows:
        try:
            building_geom = wkb.loads(bytes(building_row["geometry_wkb"]))
        except (GEOSException, TypeError, ValueError):
            continue
        if not (parcel_geom.contains(building_geom.representative_point()) or parcel_geom.intersects(building_geom)):
            continue
        if address_points:
            rank = min(
                0.0 if building_geom.covers(address_geom) else building_geom.distance(address_geom)
                for _, address_geom in address_points
            )
        else:
            # Live fallback for indexes that only have parcel relation addresses:
            # choose the building with the largest footprint inside the addressed parcel.
            rank = -float(building_geom.intersection(parcel_geom).area or building_geom.area or 0.0)
        if best is None or rank < best[0]:
            best = (rank, building_row, building_geom)

    if best is None:
        return None
    return best[1], best[2]


def dedupe_addresses(addresses: list[dict]) -> list[dict]:
    best: dict[tuple[str, str], dict] = {}
    for address in addresses:
        street = str(address.get("street") or "").strip()
        house_number = str(address.get("house_number") or "").strip()
        label = str(address.get("label") or "").strip()
        key = (street.casefold(), house_number.casefold())
        if not key[0] and not key[1]:
            key = (label.casefold(), "")
        if not key[0] and not key[1]:
            continue
        previous = best.get(key)
        if previous is None:
            best[key] = address
            continue
        previous_score = int(bool(previous.get("post_code"))) + int(bool(previous.get("city")))
        score = int(bool(address.get("post_code"))) + int(bool(address.get("city")))
        if score > previous_score:
            best[key] = address
    return sorted(best.values(), key=lambda item: (item.get("street", ""), item.get("house_number", ""), item.get("label", "")))


def state_key_for_feature_path(path: Path) -> str:
    name = path.name
    if name.endswith(FEATURE_DB_SUFFIX):
        return name[: -len(FEATURE_DB_SUFFIX)]
    if name == "features.sqlite":
        return path.parent.name
    return path.stem


def address_display_parts(address: dict) -> tuple[str, str]:
    street = str(address.get("street") or "").strip()
    house_number = str(address.get("house_number") or "").strip()
    if street and house_number:
        return street, house_number

    for value in (
        str(address.get("street_house") or "").strip(),
        str(address.get("label") or "").strip().split(",", 1)[0].strip(),
    ):
        if not value:
            continue
        match = re.match(r"^(.+?)\s+([0-9][0-9A-Za-zÄÖÜäöüß/ -]*)$", value)
        if not match:
            continue
        street = street or match.group(1).strip()
        house_number = house_number or match.group(2).strip()
        if street and house_number:
            return street, house_number
    return street, house_number


def house_number_sort_key(value: str) -> tuple[int, str]:
    match = re.match(r"^\s*(\d+)", str(value or ""))
    number = int(match.group(1)) if match else 10**9
    return number, str(value or "").casefold()


def canonical_address_label(street: str, house_numbers: list[str], postcode: str, city: str, fallback: str = "") -> str:
    base = " ".join(part for part in (street, "/".join(house_numbers)) if part).strip()
    locality = " ".join(part for part in (postcode, city) if part).strip()
    if base and locality:
        return f"{base}, {locality}"
    return base or locality or fallback


def group_addresses_for_display(addresses: list[dict]) -> list[dict]:
    grouped: dict[tuple[str, str, str], dict] = {}
    passthrough: list[dict] = []
    for address in addresses:
        street, house_number = address_display_parts(address)
        postcode = str(address.get("post_code") or address.get("postal_code") or "").strip()
        city = str(address.get("city") or address.get("municipality") or address.get("locality") or "").strip()
        if not street or not house_number:
            continue
        key = (street.casefold(), postcode, city.casefold())
        group = grouped.setdefault(
            key,
            {
                **address,
                "street": street,
                "post_code": postcode,
                "postal_code": postcode,
                "city": city,
                "_house_numbers": [],
            },
        )
        if house_number not in group["_house_numbers"]:
            group["_house_numbers"].append(house_number)

    results: list[dict] = []
    for group in grouped.values():
        house_numbers = sorted(group.pop("_house_numbers", []), key=house_number_sort_key)
        group["house_number"] = "/".join(house_numbers)
        group["label"] = canonical_address_label(
            str(group.get("street") or ""),
            house_numbers,
            str(group.get("post_code") or group.get("postal_code") or ""),
            str(group.get("city") or ""),
            str(group.get("label") or ""),
        )
        results.append(group)
    results.extend(passthrough)
    return sorted(results, key=lambda item: (item.get("street", ""), item.get("house_number", ""), item.get("label", "")))


def enrich_addresses_with_postcode(addresses: list[dict], lon: float | None = None, lat: float | None = None, state: str = "") -> list[dict]:
    enriched: list[dict] = []
    for address in addresses:
        address = dict(address)
        try:
            address_lon = float(address.get("lon") if address.get("lon") is not None else lon)
            address_lat = float(address.get("lat") if address.get("lat") is not None else lat)
        except (TypeError, ValueError):
            street, house_number = address_display_parts(address)
            if street:
                address["street"] = street
            if house_number:
                address["house_number"] = house_number
            enriched.append(address)
            continue
        enrich_address_postcode(address, address_lon, address_lat)
        if state and not str(address.get("city") or "").strip():
            municipality = municipality_at(state, address_lon, address_lat) or nearest_municipality(state, address_lon, address_lat)
            if municipality:
                address["city"] = str(municipality.get("name") or "").strip()
        street, house_number = address_display_parts(address)
        if street:
            address["street"] = street
        if house_number:
            address["house_number"] = house_number
        enriched.append(address)
    return group_addresses_for_display(enriched)


def feature_relation_addresses(con: sqlite3.Connection, source_db: str, kind: str, gml_id: str) -> list[dict]:
    rows = con.execute(
        """
        SELECT properties_json
        FROM feature_addresses
        WHERE source_db = ? AND kind = ? AND gml_id = ?
        LIMIT 25
        """,
        (source_db, kind, gml_id),
    ).fetchall()
    return [load_properties(row["properties_json"]) for row in rows]


def feature_spatial_addresses(con: sqlite3.Connection, source_db: str, geom) -> list[dict]:
    min_lon, min_lat, max_lon, max_lat = geom.bounds
    rows = con.execute(
        """
        SELECT a.properties_json, a.geometry_wkb
        FROM address_index i
        JOIN address_points a ON a.id = i.id
        WHERE a.source_db = ?
          AND i.min_lon <= ? AND i.max_lon >= ?
          AND i.min_lat <= ? AND i.max_lat >= ?
        LIMIT 100
        """,
        (source_db, max_lon, min_lon, max_lat, min_lat),
    ).fetchall()
    addresses = []
    for row in rows:
        try:
            point_geom = wkb.loads(bytes(row["geometry_wkb"]))
        except (GEOSException, TypeError, ValueError):
            continue
        if geom.covers(point_geom) or geom.intersects(point_geom):
            addresses.append(load_properties(row["properties_json"]))
    return addresses


def parcel_relation_addresses_for_geometry(con: sqlite3.Connection, source_db: str, geom) -> list[dict]:
    min_lon, min_lat, max_lon, max_lat = geom.bounds
    rows = con.execute(
        """
        SELECT f.geometry_wkb, fa.properties_json
        FROM feature_index i
        JOIN features f ON f.id = i.id
        JOIN feature_addresses fa
          ON fa.source_db = f.source_db
         AND fa.kind = f.kind
         AND fa.gml_id = f.gml_id
        WHERE f.source_db = ?
          AND f.kind = 'parcel'
          AND i.min_lon <= ? AND i.max_lon >= ?
          AND i.min_lat <= ? AND i.max_lat >= ?
        LIMIT 80
        """,
        (source_db, max_lon, min_lon, max_lat, min_lat),
    ).fetchall()
    addresses = []
    point = geom.representative_point()
    for row in rows:
        try:
            parcel_geom = wkb.loads(bytes(row["geometry_wkb"]))
        except (GEOSException, TypeError, ValueError):
            continue
        if parcel_geom.covers(point) or parcel_geom.intersects(geom):
            addresses.append(load_properties(row["properties_json"]))
    return addresses


def addresses_for_feature(con: sqlite3.Connection, feature: dict, geom) -> list[dict]:
    source_db = feature.get("source_db") or ""
    kind = feature.get("kind") or ""
    gml_id = feature.get("gml_id") or ""
    addresses = []
    if kind in {"building", "parcel"}:
        addresses.extend(feature_relation_addresses(con, source_db, kind, gml_id))
    addresses.extend(feature_spatial_addresses(con, source_db, geom))
    return dedupe_addresses(addresses)[:25]


def compact_feature_area_m2(con: sqlite3.Connection, feature_id: str) -> int | float | None:
    if not feature_id or not sqlite_table_exists(con, "feature_areas"):
        return None
    row = con.execute(
        "SELECT amtliche_flaeche_m2 FROM feature_areas WHERE feature_id = ? LIMIT 1",
        (feature_id,),
    ).fetchone()
    if not row:
        return None
    value = row["amtliche_flaeche_m2"]
    if value is None:
        return None
    try:
        area = float(value)
    except (TypeError, ValueError):
        return None
    return int(area) if area.is_integer() else area


_GEMARKUNG_LOOKUP_CACHE: dict[str, tuple[float, dict[str, str]]] = {}


def gemarkung_lookup_path_for_feature_db(path: Path) -> Path:
    name = path.name
    if name.endswith(".features.sqlite"):
        return path.with_name(name.replace(".features.sqlite", ".gemarkungen.json"))
    return path.with_suffix(path.suffix + ".gemarkungen.json")


def gemarkung_lookup_for_feature_db(path: Path) -> dict[str, str]:
    lookup_path = gemarkung_lookup_path_for_feature_db(path)
    if not lookup_path.exists():
        return {}
    try:
        mtime = lookup_path.stat().st_mtime
    except OSError:
        return {}
    cache_key = str(lookup_path)
    cached = _GEMARKUNG_LOOKUP_CACHE.get(cache_key)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        data = json.loads(lookup_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    lookup = {str(key): str(value) for key, value in data.items() if value}
    _GEMARKUNG_LOOKUP_CACHE[cache_key] = (mtime, lookup)
    return lookup


def enrich_gemarkung_from_lookup(path: Path, properties: dict) -> None:
    if properties.get("gemarkung"):
        return
    number = str(properties.get("gemarkungsnummer") or "").strip()
    key_candidates = [number] if number else []
    identifier = str(properties.get("flurstueckskennzeichen") or "").strip()
    if len(identifier) >= 6:
        key_candidates.extend([identifier[:6], identifier[2:6]])
    lookup = gemarkung_lookup_for_feature_db(path)
    for key in key_candidates:
        if not key:
            continue
        name = lookup.get(key) or lookup.get(key.lstrip("0"))
        if name:
            properties["gemarkung"] = name
            return


def query_features_in_index(path: Path, lon: float, lat: float) -> tuple[list[dict], list[dict]]:
    click = Point(lon, lat)
    parcels: list[dict] = []
    buildings: list[dict] = []
    state_key = state_key_for_feature_path(path)
    with sqlite_feature_connection(path) as con:
        if compact_feature_schema(con):
            if sqlite_table_exists(con, "feature_geometries"):
                if sqlite_table_exists(con, "feature_bbox_index"):
                    rows = con.execute(
                        """
                        SELECT f.*, g.geometry_wkb
                        FROM feature_bbox_index i
                        JOIN features f ON f.rowid = i.rowid
                        JOIN feature_geometries g ON g.feature_id = f.id
                        WHERE i.min_lon <= ? AND i.max_lon >= ?
                          AND i.min_lat <= ? AND i.max_lat >= ?
                        LIMIT 200
                        """,
                        (lon, lon, lat, lat),
                    ).fetchall()
                else:
                    rows = con.execute(
                        """
                        SELECT f.*, g.geometry_wkb
                        FROM features f
                        JOIN feature_geometries g ON g.feature_id = f.id
                        WHERE f.min_lon <= ? AND f.max_lon >= ?
                          AND f.min_lat <= ? AND f.max_lat >= ?
                        LIMIT 200
                        """,
                        (lon, lon, lat, lat),
                    ).fetchall()
                matched = []
                for row in rows:
                    try:
                        geom = wkb.loads(bytes(row["geometry_wkb"]))
                    except (GEOSException, TypeError, ValueError):
                        continue
                    if not (geom.covers(click) or geom.intersects(click)):
                        continue
                    result = result_from_compact_feature(row)
                    properties = result["feature"]
                    properties["bbox"] = result["bbox"]
                    properties["center"] = result["center"]
                    properties["geometry"] = mapping(geom)
                    if row["kind"] == "building" and sqlite_table_exists(con, "feature_addresses"):
                        address_rows = con.execute(
                            """
                            SELECT
                              address AS compact_address,
                              street_house AS compact_street_house,
                              parcel_id AS compact_address_parcel_id,
                              lon AS compact_address_lon,
                              lat AS compact_address_lat,
                              source AS compact_address_source
                            FROM feature_addresses
                            WHERE feature_id = ?
                            LIMIT 25
                            """,
                            (row["id"],),
                        ).fetchall()
                        properties["addresses"] = enrich_addresses_with_postcode([compact_address_properties(address_row) for address_row in address_rows], properties.get("center", [None, None])[0], properties.get("center", [None, None])[1], state_key)
                        properties["address"] = properties["addresses"][0]["label"] if properties["addresses"] else properties.get("address", "")
                    if row["kind"] == "parcel" and sqlite_table_exists(con, "feature_addresses"):
                        address_rows = con.execute(
                            """
                            SELECT
                              address AS compact_address,
                              street_house AS compact_street_house,
                              parcel_id AS compact_address_parcel_id,
                              lon AS compact_address_lon,
                              lat AS compact_address_lat,
                              source AS compact_address_source
                            FROM feature_addresses
                            WHERE parcel_id = ?
                            LIMIT 25
                            """,
                            (row["id"],),
                        ).fetchall()
                        properties["addresses"] = enrich_addresses_with_postcode([compact_address_properties(address_row) for address_row in address_rows], properties.get("center", [None, None])[0], properties.get("center", [None, None])[1], state_key)
                        properties["address"] = properties["addresses"][0]["label"] if properties["addresses"] else properties.get("address", "")
                    if row["kind"] == "parcel":
                        area_m2 = compact_feature_area_m2(con, row["id"])
                        if area_m2 is not None:
                            properties["amtliche_flaeche_m2"] = area_m2
                    matched.append((row["kind"], float(geom.area or 0.0), properties))
                if matched:
                    for kind, _, properties in sorted(matched, key=lambda item: item[1]):
                        if kind == "parcel" and len(parcels) < 8:
                            parcels.append(properties)
                        elif kind == "building" and len(buildings) < 8:
                            buildings.append(properties)
                    return parcels, buildings

            if sqlite_table_exists(con, "feature_bbox_index"):
                rows = con.execute(
                    """
                    SELECT f.*
                    FROM feature_bbox_index i
                    JOIN features f ON f.rowid = i.rowid
                    WHERE i.min_lon <= ? AND i.max_lon >= ?
                      AND i.min_lat <= ? AND i.max_lat >= ?
                    LIMIT 200
                    """,
                    (lon, lon, lat, lat),
                ).fetchall()
            else:
                rows = con.execute(
                    """
                    SELECT *
                    FROM features
                    WHERE min_lon <= ? AND max_lon >= ?
                      AND min_lat <= ? AND max_lat >= ?
                    LIMIT 200
                    """,
                    (lon, lon, lat, lat),
                ).fetchall()
            matched = []
            for row in rows:
                result = result_from_compact_feature(row)
                properties = result["feature"]
                properties["bbox"] = result["bbox"]
                properties["center"] = result["center"]
                if row["kind"] == "building" and sqlite_table_exists(con, "feature_addresses"):
                    address_rows = con.execute(
                        """
                        SELECT
                          address AS compact_address,
                          street_house AS compact_street_house,
                          parcel_id AS compact_address_parcel_id,
                          lon AS compact_address_lon,
                          lat AS compact_address_lat,
                          source AS compact_address_source
                        FROM feature_addresses
                        WHERE feature_id = ?
                        LIMIT 25
                        """,
                        (row["id"],),
                    ).fetchall()
                    properties["addresses"] = [compact_address_properties(address_row) for address_row in address_rows]
                    properties["address"] = properties["addresses"][0]["label"] if properties["addresses"] else properties.get("address", "")
                if row["kind"] == "parcel":
                    area_m2 = compact_feature_area_m2(con, row["id"])
                    if area_m2 is not None:
                        properties["amtliche_flaeche_m2"] = area_m2
                area = (float(row["max_lon"]) - float(row["min_lon"])) * (float(row["max_lat"]) - float(row["min_lat"]))
                matched.append((row["kind"], area, properties))
            for kind, _, properties in sorted(matched, key=lambda item: item[1]):
                if kind == "parcel" and len(parcels) < 8:
                    parcels.append(properties)
                elif kind == "building" and len(buildings) < 8:
                    buildings.append(properties)
            return parcels, buildings

        rows = con.execute(
            """
            SELECT f.*
            FROM feature_index i
            JOIN features f ON f.id = i.id
            WHERE i.min_lon <= ? AND i.max_lon >= ?
              AND i.min_lat <= ? AND i.max_lat >= ?
            LIMIT 200
            """,
            (lon, lon, lat, lat),
        ).fetchall()
        matched = []
        for row in rows:
            try:
                geom = wkb.loads(bytes(row["geometry_wkb"]))
            except (GEOSException, TypeError, ValueError):
                continue
            if not (geom.covers(click) or geom.intersects(click)):
                continue
            properties = load_properties(row["properties_json"])
            properties["source_db"] = properties.get("source_db") or row["source_db"]
            properties["gml_id"] = properties.get("gml_id") or row["gml_id"]
            if row["kind"] == "parcel":
                enrich_gemarkung_from_lookup(path, properties)
            properties["addresses"] = enrich_addresses_with_postcode(addresses_for_feature(con, dict(row), geom), geom.representative_point().x, geom.representative_point().y, state_key)
            properties["address"] = properties["addresses"][0]["label"] if properties["addresses"] else ""
            properties["geometry"] = mapping(geom)
            matched.append((row["kind"], geom.area, properties))

    for kind, _, properties in sorted(matched, key=lambda item: item[1]):
        if kind == "parcel" and len(parcels) < 8:
            parcels.append(properties)
        elif kind == "building" and len(buildings) < 8:
            buildings.append(properties)
    return parcels, buildings


def features_at_point_for_dataset(dataset: str, lon: float, lat: float) -> dict:
    entries = feature_db_entries_for_dataset(dataset)
    if not entries:
        raise HTTPException(status_code=404, detail="feature index not found")

    parcels: list[dict] = []
    buildings: list[dict] = []
    for entry in entries:
        entry_parcels, entry_buildings = query_features_in_index(entry.path, lon, lat)
        parcels.extend(entry_parcels)
        buildings.extend(entry_buildings)

    return {
        "lon": lon,
        "lat": lat,
        "count": len(parcels) + len(buildings),
        "parcels": parcels[:16],
        "buildings": buildings[:16],
    }


def search_features_in_index(path: Path, query: str, limit: int) -> list[dict]:
    results: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    with sqlite_feature_connection(path) as con:
        if compact_feature_schema(con):
            rows = []
            if sqlite_table_exists(con, "feature_fts") and search_tokens(query):
                try:
                    rows = con.execute(
                        """
                        SELECT f.*
                        FROM feature_fts
                        JOIN features f ON f.id = feature_fts.feature_id
                        WHERE feature_fts MATCH ?
                        ORDER BY bm25(feature_fts)
                        LIMIT ?
                        """,
                        (fts_query(query), limit),
                    ).fetchall()
                except sqlite3.OperationalError:
                    rows = []
            if not rows and not sqlite_table_exists(con, "feature_fts"):
                rows = con.execute(
                    """
                    SELECT *
                    FROM features
                    WHERE search_text LIKE ? OR properties_json LIKE ?
                    LIMIT ?
                    """,
                    (like_pattern(query), like_pattern(query), limit),
                ).fetchall()
            compact_seen: set[tuple[str, str]] = set()
            for row in rows:
                key = (row["kind"], row["id"])
                if key in compact_seen:
                    continue
                result = result_from_compact_feature(row)
                result["result_type"] = "feature"
                results.append(result)
                compact_seen.add(key)
                if len(results) >= limit:
                    break
            return results

        rows = []
        if sqlite_table_exists(con, "feature_search") and search_tokens(query):
            try:
                rows = con.execute(
                    """
                    SELECT f.*
                    FROM feature_search
                    JOIN features f ON f.id = CAST(feature_search.feature_id AS INTEGER)
                    WHERE feature_search MATCH ?
                    ORDER BY bm25(feature_search)
                    LIMIT ?
                    """,
                    (fts_query(query), limit),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []
        if not rows and not sqlite_table_exists(con, "feature_search"):
            rows = con.execute(
                """
                SELECT *
                FROM features
                WHERE properties_json LIKE ?
                LIMIT ?
                """,
                (like_pattern(query), limit),
            ).fetchall()

        for row in rows:
            key = (row["kind"], row["source_db"], row["gml_id"])
            if key in seen:
                continue
            try:
                result = result_from_feature(row)
            except (GEOSException, TypeError, ValueError):
                continue
            result["result_type"] = "feature"
            results.append(result)
            seen.add(key)
            if len(results) >= limit:
                break
    return results


def search_cadastre_parcels_in_index(
    path: Path,
    *,
    gemarkung: str = "",
    flur: str = "",
    flurstueck: str = "",
    limit: int = 12,
) -> list[dict]:
    clauses = ["kind = 'parcel'"]
    params: list[object] = []
    gemarkung = gemarkung.strip()
    flur = flur.strip()
    flurstueck = normalize_parcel_number(flurstueck)
    if gemarkung:
        clauses.append("LOWER(json_extract(properties_json, '$.gemarkung')) = LOWER(?)")
        params.append(gemarkung)
    if flur:
        clauses.append("CAST(json_extract(properties_json, '$.flur') AS TEXT) = ?")
        params.append(flur)
    if flurstueck:
        clauses.append(
            "(REPLACE(json_extract(properties_json, '$.flurstueck'), ' ', '') = ? "
            "OR REPLACE(json_extract(properties_json, '$.zaehler'), ' ', '') = ? "
            "OR REPLACE(json_extract(properties_json, '$.label'), ' ', '') = ?)"
        )
        params.extend([flurstueck, flurstueck, flurstueck])
    if len(clauses) == 1:
        return []
    results: list[dict] = []
    with sqlite_feature_connection(path) as con:
        rows = con.execute(
            f"""
            SELECT *
            FROM features
            WHERE {' AND '.join(clauses)}
            ORDER BY
              CAST(json_extract(properties_json, '$.flur') AS INTEGER),
              CAST(json_extract(properties_json, '$.zaehler') AS INTEGER),
              CAST(json_extract(properties_json, '$.nenner') AS INTEGER)
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()
        for row in rows:
            try:
                result = result_from_any_feature(row)
            except (GEOSException, TypeError, ValueError):
                continue
            result["result_type"] = "feature"
            results.append(result)
    return results


@lru_cache(maxsize=8)
def cadastre_gemarkung_entries(
    signature: tuple[tuple[str, str, int, int], ...],
) -> tuple[dict, ...]:
    entries: list[dict] = []
    for state, raw_path, _mtime_ns, _size in signature:
        path = Path(raw_path)
        with sqlite_feature_connection(path) as con:
            rows = con.execute(
                """
                SELECT
                  json_extract(properties_json, '$.gemarkung') AS gemarkung,
                  json_extract(properties_json, '$.gemarkungsnummer') AS gemarkungsnummer,
                  COUNT(*) AS feature_count,
                  MIN(min_lon) AS min_lon,
                  MIN(min_lat) AS min_lat,
                  MAX(max_lon) AS max_lon,
                  MAX(max_lat) AS max_lat
                FROM features
                WHERE kind = 'parcel'
                  AND json_extract(properties_json, '$.gemarkung') IS NOT NULL
                  AND json_extract(properties_json, '$.gemarkung') != ''
                GROUP BY gemarkung, gemarkungsnummer
                ORDER BY gemarkung
                """
            ).fetchall()
        for row in rows:
            gemarkung = str(row["gemarkung"] or "").strip()
            if not gemarkung:
                continue
            bbox = [
                float(row["min_lon"]),
                float(row["min_lat"]),
                float(row["max_lon"]),
                float(row["max_lat"]),
            ]
            entries.append(
                {
                    "state": state,
                    "state_label": state_display_name(state),
                    "gemarkung": gemarkung,
                    "gemarkungsnummer": str(row["gemarkungsnummer"] or "").strip(),
                    "feature_count": int(row["feature_count"] or 0),
                    "bbox": bbox,
                    "center": [(bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2],
                }
            )
    return tuple(entries)


def cadastre_gemarkung_signature(dataset: str) -> tuple[tuple[str, str, int, int], ...]:
    entries = feature_db_entries_for_dataset(dataset)
    return tuple(
        (entry.name, str(entry.path), entry.path.stat().st_mtime_ns, entry.path.stat().st_size)
        for entry in entries
    )


def search_cadastre_gemarkungen_for_dataset(
    dataset: str,
    query: str = "",
    limit: int = 12,
    state: str = "",
) -> dict:
    entries = feature_db_entries_for_dataset(dataset)
    if not entries:
        raise HTTPException(status_code=404, detail="feature index not found")
    allowed_states = {entry.name for entry in entries}
    selected_state = requested_state_context(state, allowed_states)
    tokens = search_tokens(query)
    results = []
    for item in cadastre_gemarkung_entries(cadastre_gemarkung_signature(dataset)):
        if selected_state and item["state"] != selected_state:
            continue
        haystack = f"{item['gemarkung']} {item['gemarkungsnummer']}".casefold()
        if tokens and not all(
            any(variant.casefold() in haystack for variant in german_token_variants(token))
            for token in tokens
        ):
            continue
        folded_name = item["gemarkung"].casefold()
        folded_query = query.strip().casefold()
        if folded_query and folded_name.startswith(folded_query):
            rank = 0
        elif folded_query and any(part.startswith(folded_query) for part in folded_name.split()):
            rank = 1
        elif tokens:
            rank = 2
        else:
            rank = 3
        number = item["gemarkungsnummer"]
        subtitle_parts = []
        if number:
            subtitle_parts.append(f"Gemarkungsnummer {number}")
        subtitle_parts.append(item["state_label"])
        results.append(
            {
                "kind": "gemarkung",
                "label": item["gemarkung"],
                "gemarkung": item["gemarkung"],
                "gemarkungsnummer": number,
                "state": item["state"],
                "state_label": item["state_label"],
                "subtitle": " · ".join(subtitle_parts),
                "_rank": rank,
                "_count": item["feature_count"],
            }
        )
    results.sort(key=lambda item: (item["_rank"], item["label"].casefold(), -item["_count"]))
    clean_results = []
    seen: set[tuple[str, str, str]] = set()
    for item in results:
        key = (item["state"], item["gemarkung"].casefold(), item["gemarkungsnummer"])
        if key in seen:
            continue
        seen.add(key)
        item.pop("_rank", None)
        item.pop("_count", None)
        clean_results.append(item)
        if len(clean_results) >= limit:
            break
    return {"query": query, "count": len(clean_results), "results": clean_results}


def search_addresses_in_index(path: Path, query: str, limit: int, bbox=None) -> list[dict]:
    results: list[dict] = []
    with sqlite_feature_connection(path) as con:
        if not sqlite_table_exists(con, "address_points"):
            return results
        bbox_clause, bbox_params = point_bbox_sql("a", bbox)
        rows = []
        if sqlite_table_exists(con, "address_search") and search_tokens(query):
            try:
                rows = con.execute(
                    f"""
                    SELECT a.*
                    FROM address_search
                    JOIN address_points a ON a.id = CAST(address_search.address_id AS INTEGER)
                    WHERE address_search MATCH ?
                    {bbox_clause}
                    ORDER BY bm25(address_search)
                    LIMIT ?
                    """,
                    (fts_query(query), *bbox_params, limit),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []
        if not rows and not sqlite_table_exists(con, "address_search"):
            fallback_bbox_clause, fallback_bbox_params = point_bbox_sql("address_points", bbox)
            rows = con.execute(
                f"""
                SELECT *
                FROM address_points
                WHERE properties_json LIKE ?
                {fallback_bbox_clause}
                LIMIT ?
                """,
                (like_pattern(query), *fallback_bbox_params, limit),
            ).fetchall()

        seen_labels: set[str] = set()
        for row in rows:
            properties = load_properties(row["properties_json"])
            label = properties.get("label") or "Adresse"
            key = label.casefold()
            if key in seen_labels:
                continue
            results.append(
                {
                    "kind": "address",
                    "result_type": "address",
                    "label": label,
                    "subtitle": "Adresse",
                    "source_db": row["source_db"],
                    "center": [float(row["lon"]), float(row["lat"])],
                    "feature": properties,
                }
            )
            seen_labels.add(key)
            if len(results) >= limit:
                break
    return results


def search_relation_addresses_in_index(path: Path, query: str, limit: int, bbox=None) -> list[dict]:
    results: list[dict] = []
    seen: set[tuple[str, str, str, str]] = set()
    with sqlite_feature_connection(path) as con:
        if compact_feature_schema(con):
            rows = []
            tokens = search_tokens(query)
            bbox_clause, bbox_params = feature_bbox_sql("f", bbox)
            if sqlite_table_exists(con, "feature_fts") and sqlite_table_exists(con, "feature_addresses") and tokens:
                try:
                    candidate_limit = max(limit * 20, 400)
                    rows = con.execute(
                        f"""
                        SELECT
                          fa.address AS compact_address,
                          fa.street_house AS compact_street_house,
                          fa.parcel_id AS compact_address_parcel_id,
                          fa.lon AS compact_address_lon,
                          fa.lat AS compact_address_lat,
                          fa.source AS compact_address_source,
                          f.*
                        FROM feature_fts
                        JOIN features f ON f.id = feature_fts.feature_id
                        LEFT JOIN feature_addresses fa ON fa.feature_id = f.id
                        WHERE feature_fts MATCH ?
                          AND f.kind = 'building'
                          {bbox_clause}
                        ORDER BY bm25(feature_fts)
                        LIMIT ?
                        """,
                        (fts_query(query), *bbox_params, candidate_limit),
                    ).fetchall()
                except sqlite3.OperationalError:
                    rows = []
            if (
                not rows
                and not sqlite_table_exists(con, "feature_fts")
                and sqlite_table_exists(con, "feature_addresses")
                and len(query.strip()) >= 4
            ):
                rows = con.execute(
                    f"""
                    SELECT
                      fa.address AS compact_address,
                      fa.street_house AS compact_street_house,
                      fa.parcel_id AS compact_address_parcel_id,
                      fa.lon AS compact_address_lon,
                      fa.lat AS compact_address_lat,
                      fa.source AS compact_address_source,
                      f.*
                    FROM feature_addresses fa
                    JOIN features f ON f.id = fa.feature_id
                    WHERE fa.address LIKE ?
                       OR fa.street_house LIKE ?
                       OR f.search_text LIKE ?
                       {bbox_clause}
                    LIMIT ?
                    """,
                    (like_pattern(query), like_pattern(query), like_pattern(query), *bbox_params, limit),
                ).fetchall()

            query_folded = query.strip().casefold()
            token_list = [token.casefold() for token in tokens]

            def compact_address_rank(row: sqlite3.Row) -> tuple[int, int, int, str]:
                address = str(row["compact_address"] or "").casefold()
                street_house = str(row["compact_street_house"] or "").casefold()
                exact = street_house == query_folded or address == query_folded
                starts = street_house.startswith(query_folded) or address.startswith(query_folded)
                token_misses = sum(1 for token in token_list if token not in address and token not in street_house)
                return (0 if exact else 1, 0 if starts else 1, token_misses, address)

            rows = sorted(rows, key=compact_address_rank)
            compact_seen: set[tuple[str, str]] = set()
            compact_result_limit = limit
            for row in rows:
                address_properties = compact_address_properties(row)
                label = str(address_properties.get("label") or "").casefold()
                key = (row["id"], label)
                if key in compact_seen:
                    continue
                result = result_from_compact_feature_address(row, address_properties)
                results.append(result)
                compact_seen.add(key)
                if len(results) >= compact_result_limit:
                    break
            return results

        rows = []
        bbox_clause, bbox_params = feature_bbox_sql("f", bbox)
        if sqlite_table_exists(con, "feature_address_search") and search_tokens(query):
            try:
                rows = con.execute(
                    f"""
                    SELECT fa.properties_json AS address_properties_json, f.*
                    FROM feature_address_search s
                    JOIN feature_addresses fa ON fa.id = CAST(s.feature_address_id AS INTEGER)
                    JOIN features f
                      ON f.source_db = fa.source_db
                     AND f.kind = fa.kind
                     AND f.gml_id = fa.gml_id
                    WHERE feature_address_search MATCH ?
                      AND fa.kind = 'building'
                      {bbox_clause}
                    ORDER BY bm25(feature_address_search)
                    LIMIT ?
                    """,
                    (fts_query(query), *bbox_params, limit),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []
        if not rows and not sqlite_table_exists(con, "feature_address_search"):
            rows = con.execute(
                f"""
                SELECT fa.properties_json AS address_properties_json, f.*
                FROM feature_addresses fa
                JOIN features f
                  ON f.source_db = fa.source_db
                 AND f.kind = fa.kind
                 AND f.gml_id = fa.gml_id
                WHERE fa.properties_json LIKE ?
                  AND fa.kind = 'building'
                  {bbox_clause}
                LIMIT ?
                """,
                (like_pattern(query), *bbox_params, limit),
            ).fetchall()

        for row in rows:
            address_properties = load_properties(row["address_properties_json"])
            label = str(address_properties.get("label") or "").casefold()
            resolved_row = row
            resolved_geom = None
            key = (resolved_row["kind"], resolved_row["source_db"], resolved_row["gml_id"], label)
            if key in seen:
                continue
            try:
                result = result_from_feature_address(resolved_row, address_properties, resolved_geom)
            except (GEOSException, TypeError, ValueError):
                continue
            results.append(result)
            seen.add(key)
            if len(results) >= limit:
                break
    return results


def is_probable_address_query(query: str) -> bool:
    folded = query.casefold()
    if any(term in folded for term in ("flurstück", "flurstueck", "gemarkung", "flur ")):
        return False
    tokens = search_tokens(query)
    return any(any(ch.isdigit() for ch in token) for token in tokens) and any(any(ch.isalpha() for ch in token) for token in tokens)


def is_likely_street_name_query(query: str) -> bool:
    tokens = search_tokens(query)
    if not tokens:
        return False
    suffixes = (
        "strasse",
        "straße",
        "weg",
        "platz",
        "allee",
        "damm",
        "ring",
        "gasse",
        "ufer",
        "chaussee",
        "stieg",
        "kamp",
    )
    return any(token.endswith(suffix) for token in tokens for suffix in suffixes)


def normalize_geocoder_text(value: str | None) -> str:
    text = normalize_place_search_text(value)
    text = text.replace("str.", "strasse")
    text = re.sub(r"\bstr\b", "strasse", text)
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def normalize_geocoder_text_variants(value: str | None) -> tuple[str, ...]:
    variants: list[str] = []
    for candidate in (
        normalize_geocoder_text(value),
        re.sub(r"[^a-z0-9]+", " ", plain_place_search_text(value)).strip(),
    ):
        if candidate and candidate not in variants:
            variants.append(candidate)
    return tuple(variants)


def normalize_geocoder_house(value: str | None) -> str:
    return re.sub(r"\s+", "", normalize_geocoder_text(value))


def parse_geocoder_address_query(query: str) -> tuple[str, str]:
    text = re.sub(r"\s+", " ", (query or "").strip())
    match = re.match(r"^(.+?)\s+([0-9][0-9A-Za-zÄÖÜäöüß./\- ]*)$", text)
    if not match:
        return text, ""
    return match.group(1).strip(), match.group(2).strip()


def geocoder_bbox_clause(alias: str, bbox) -> tuple[str, list[float]]:
    normalized = normalized_bbox(bbox)
    if not normalized:
        return "", []
    min_lon, min_lat, max_lon, max_lat = normalized
    return (
        f" AND {alias}.max_lon >= ? AND {alias}.min_lon <= ? AND {alias}.max_lat >= ? AND {alias}.min_lat <= ?",
        [min_lon, max_lon, min_lat, max_lat],
    )


def geocoder_result_label(label: str, municipality: dict | None) -> str:
    label = str(label or "").strip()
    municipality_name = str((municipality or {}).get("name") or "").strip()
    if municipality_name and label and municipality_name.casefold() not in label.casefold():
        return f"{label}, {municipality_name}"
    return label


def geocoder_connection() -> sqlite3.Connection:
    global _GEOCODER_GLOBAL_CONNECTION, _GEOCODER_GLOBAL_SIGNATURE
    signature = geocoder_signature()
    con = _GEOCODER_GLOBAL_CONNECTION
    if con is not None and _GEOCODER_GLOBAL_SIGNATURE == signature:
        return con
    with _GEOCODER_GLOBAL_LOCK:
        con = _GEOCODER_GLOBAL_CONNECTION
        if con is not None and _GEOCODER_GLOBAL_SIGNATURE == signature:
            return con
        if con is not None:
            try:
                con.close()
            except Exception:
                pass
        con = sqlite3.connect(f"file:{GEOCODER_DB}?mode=ro", uri=True, timeout=10, check_same_thread=False)
        con.row_factory = sqlite3.Row
        try:
            con.execute("PRAGMA query_only = ON")
            con.execute("PRAGMA mmap_size = 1073741824")
            con.execute("PRAGMA cache_size = -262144")
            con.execute("PRAGMA temp_store = MEMORY")
        except sqlite3.Error:
            pass
        _GEOCODER_GLOBAL_CONNECTION = con
        _GEOCODER_GLOBAL_SIGNATURE = signature
        return con


def fast_geocoder_connection() -> sqlite3.Connection:
    global _FAST_GEOCODER_GLOBAL_CONNECTION, _FAST_GEOCODER_GLOBAL_SIGNATURE
    signature = fast_geocoder_signature()
    con = _FAST_GEOCODER_GLOBAL_CONNECTION
    if con is not None and _FAST_GEOCODER_GLOBAL_SIGNATURE == signature:
        return con
    with _FAST_GEOCODER_GLOBAL_LOCK:
        con = _FAST_GEOCODER_GLOBAL_CONNECTION
        if con is not None and _FAST_GEOCODER_GLOBAL_SIGNATURE == signature:
            return con
        if con is not None:
            try:
                con.close()
            except Exception:
                pass
        con = sqlite3.connect(f"file:{FAST_GEOCODER_DB}?mode=ro", uri=True, timeout=10, check_same_thread=False)
        con.row_factory = sqlite3.Row
        try:
            con.execute("PRAGMA query_only = ON")
            con.execute("PRAGMA mmap_size = 1073741824")
            con.execute("PRAGMA cache_size = -131072")
            con.execute("PRAGMA temp_store = MEMORY")
        except sqlite3.Error:
            pass
        _FAST_GEOCODER_GLOBAL_CONNECTION = con
        _FAST_GEOCODER_GLOBAL_SIGNATURE = signature
        return con


def fast_compact_norm(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", normalize_geocoder_text(value))


def fast_float(value, fallback: float = 0.0) -> float:
    try:
        if value is None:
            return fallback
        return float(value)
    except (TypeError, ValueError):
        return fallback


def fast_address_result_from_row(row: sqlite3.Row, city_fallback: str = "") -> dict:
    lon = fast_float(row["lon"])
    lat = fast_float(row["lat"])
    label = str(row["label"] or "").strip()
    city_label = str(row["city"] or city_fallback or "").strip()
    if not city_label:
        state_key = str(row["state"] or "")
        municipality = municipality_at(state_key, lon, lat) or nearest_municipality(state_key, lon, lat)
        if municipality:
            city_label = str(municipality.get("name") or "").strip()
    if label and city_label and city_label.casefold() not in label.casefold():
        label = f"{label}, {city_label}"
    address = {
        "label": label,
        "street": str(row["street"] or ""),
        "house_number": str(row["house_number"] or ""),
        "city": city_label,
        "country": str(row["country"] or "Deutschland"),
    }
    post_code = str(row["post_code"] or "").strip() if "post_code" in row.keys() else ""
    if post_code:
        address["post_code"] = post_code
        address["postal_code"] = post_code
    return {
        "kind": str(row["feature_kind"] or "address"),
        "result_type": "address",
        "label": label,
        "subtitle": "Adresse",
        "address": address,
        "state": str(row["state"] or ""),
        "state_label": str(row["state_label"] or ""),
        "center": [lon, lat],
        "bbox": [
            fast_float(row["min_lon"], lon),
            fast_float(row["min_lat"], lat),
            fast_float(row["max_lon"], lon),
            fast_float(row["max_lat"], lat),
        ],
        "zoom": 18.0,
        "feature": {
            "address": label,
            "addresses": [address],
            "source_db": str(row["source_db"] or ""),
            "gml_id": str(row["gml_id"] or ""),
        },
    }

def fast_street_result_from_row(row: sqlite3.Row, street_fallback: str = "", city_fallback: str = "") -> dict:
    lon = fast_float(row["lon"])
    lat = fast_float(row["lat"])
    street_label = str(row["street"] or street_fallback or "").strip()
    city_label = str(row["city"] or city_fallback or "").strip()
    post_code = str(row["post_code"] or "").strip() if "post_code" in row.keys() else ""
    place_label = " ".join(part for part in (post_code, city_label) if part)
    label = f"{street_label}, {place_label}" if place_label else street_label
    feature = {
        "street": street_label,
        "municipality": city_label,
        "address_count": int(row["address_count"] or 0),
        "country": str(row["country"] or "Deutschland"),
    }
    if post_code:
        feature["post_code"] = post_code
    return {
        "kind": "street",
        "result_type": "street",
        "label": label,
        "subtitle": "Straße",
        "state": str(row["state"] or ""),
        "state_label": str(row["state_label"] or ""),
        "center": [lon, lat],
        "bbox": [
            fast_float(row["min_lon"], lon),
            fast_float(row["min_lat"], lat),
            fast_float(row["max_lon"], lon),
            fast_float(row["max_lat"], lat),
        ],
        "zoom": 17.4,
        "feature": feature,
    }


def fast_street_row_needs_address_clusters(row: sqlite3.Row) -> bool:
    lon = fast_float(row["lon"])
    lat = fast_float(row["lat"])
    min_lon = fast_float(row["min_lon"], lon)
    max_lon = fast_float(row["max_lon"], lon)
    min_lat = fast_float(row["min_lat"], lat)
    max_lat = fast_float(row["max_lat"], lat)
    return abs(max_lon - min_lon) > 0.08 or abs(max_lat - min_lat) > 0.05


def fast_clustered_street_results_from_address_rows(
    rows: list[sqlite3.Row],
    street_fallback: str = "",
    city_fallback: str = "",
    limit: int = 10,
) -> list[dict]:
    clusters: list[dict] = []
    lon_pad = 0.055
    lat_pad = 0.035
    for row in rows:
        lon = fast_float(row["lon"])
        lat = fast_float(row["lat"])
        street_label = str(row["street"] or street_fallback or "").strip()
        city_label = str(row["city"] or city_fallback or "").strip()
        state_key = str(row["state"] or "")
        chosen = None
        for cluster in clusters:
            if cluster["state"] != state_key or normalize_geocoder_text(cluster["city"]) != normalize_geocoder_text(city_label):
                continue
            if (
                cluster["min_lon"] - lon_pad <= lon <= cluster["max_lon"] + lon_pad
                and cluster["min_lat"] - lat_pad <= lat <= cluster["max_lat"] + lat_pad
            ):
                chosen = cluster
                break
        if chosen is None:
            chosen = {
                "state": state_key,
                "state_label": str(row["state_label"] or ""),
                "street": street_label,
                "city": city_label,
                "country": str(row["country"] or "Deutschland"),
                "count": 0,
                "sum_lon": 0.0,
                "sum_lat": 0.0,
                "min_lon": fast_float(row["min_lon"], lon),
                "min_lat": fast_float(row["min_lat"], lat),
                "max_lon": fast_float(row["max_lon"], lon),
                "max_lat": fast_float(row["max_lat"], lat),
            }
            clusters.append(chosen)
        chosen["count"] += 1
        chosen["sum_lon"] += lon
        chosen["sum_lat"] += lat
        chosen["min_lon"] = min(chosen["min_lon"], fast_float(row["min_lon"], lon), lon)
        chosen["min_lat"] = min(chosen["min_lat"], fast_float(row["min_lat"], lat), lat)
        chosen["max_lon"] = max(chosen["max_lon"], fast_float(row["max_lon"], lon), lon)
        chosen["max_lat"] = max(chosen["max_lat"], fast_float(row["max_lat"], lat), lat)
    clusters.sort(key=lambda cluster: (-int(cluster["count"]), str(cluster["street"]), str(cluster["city"])))
    results: list[dict] = []
    for cluster in clusters[:limit]:
        count = max(int(cluster["count"]), 1)
        lon = float(cluster["sum_lon"]) / count
        lat = float(cluster["sum_lat"]) / count
        street_label = str(cluster["street"] or street_fallback or "").strip()
        city_label = str(cluster["city"] or city_fallback or "").strip()
        # Do not run polygon-based postcode enrichment in search suggestions.
        # It is useful for feature details, but too expensive for autocomplete.
        post_code = ""
        place_label = " ".join(part for part in (post_code, city_label) if part)
        label = f"{street_label}, {place_label}" if place_label else street_label
        feature = {
            "street": street_label,
            "municipality": city_label,
            "address_count": count,
            "country": str(cluster["country"] or "Deutschland"),
        }
        if post_code:
            feature["post_code"] = post_code
        results.append({
            "kind": "street",
            "result_type": "street",
            "label": label,
            "subtitle": "Straße",
            "state": str(cluster["state"] or ""),
            "state_label": str(cluster["state_label"] or ""),
            "center": [lon, lat],
            "bbox": [
                float(cluster["min_lon"]),
                float(cluster["min_lat"]),
                float(cluster["max_lon"]),
                float(cluster["max_lat"]),
            ],
            "zoom": 17.4,
            "feature": feature,
        })
    return results


def fast_parcel_result_from_row(row: sqlite3.Row) -> dict:
    lon = fast_float(row["lon"])
    lat = fast_float(row["lat"])
    bbox = [
        fast_float(row["min_lon"], lon),
        fast_float(row["min_lat"], lat),
        fast_float(row["max_lon"], lon),
        fast_float(row["max_lat"], lat),
    ]
    area = row["amtliche_flaeche_m2"]
    feature = {
        "source_db": str(row["source_db"] or ""),
        "gml_id": str(row["gml_id"] or ""),
        "gemarkung": str(row["gemarkung"] or ""),
        "gemarkungsnummer": str(row["gemarkungsnummer"] or ""),
        "flur": str(row["flur"] or ""),
        "flurstueck": str(row["flurstueck"] or ""),
        "zaehler": str(row["zaehler"] or ""),
        "nenner": str(row["nenner"] or ""),
    }
    if area is not None:
        feature["amtliche_flaeche_m2"] = fast_float(area)
    cadastre = {
        "flur": feature["flur"],
        "flurstueck": feature["flurstueck"],
        "gemarkung": feature["gemarkung"],
        "gemarkungsnummer": feature["gemarkungsnummer"],
    }
    return {
        "kind": "parcel",
        "result_type": "feature",
        "cadastre": cadastre,
        "label": str(row["label"] or "Flurstück"),
        "subtitle": "Flurstück",
        "state": str(row["state"] or ""),
        "state_label": str(row["state_label"] or ""),
        "center": [lon, lat],
        "bbox": bbox,
        "zoom": 18.0,
        "feature": feature,
    }


@lru_cache(maxsize=4096)
def fast_parcel_lookup(
    gemarkung: str,
    flur: str,
    flurstueck: str,
    states_key: tuple[str, ...],
    signature: tuple[int, int],
    limit: int,
) -> list[dict]:
    if signature == (0, 0) or not FAST_GEOCODER_DB.exists():
        return []
    states = [state for state in states_key if state]
    gemarkung = (gemarkung or "").strip()
    flur = (flur or "").strip()
    flurstueck = normalize_parcel_number(flurstueck)
    if not any((gemarkung, flur, flurstueck)):
        return []
    gemarkung_norm = normalize_geocoder_text(gemarkung)
    flur_norm = fast_compact_norm(flur)
    state_clause = ""
    state_params: list[object] = []
    if states:
        placeholders = ",".join("?" for _ in states)
        state_clause = f" AND state IN ({placeholders})"
        state_params.extend(states)

    query_variants: list[tuple[str, list[object]]] = []
    if gemarkung_norm and flur_norm and flurstueck:
        query_variants.append((
            f"gemarkung_norm = ? AND flur_norm = ? AND flurstueck_norm = ?{state_clause}",
            [gemarkung_norm, flur_norm, flurstueck, *state_params],
        ))
    if gemarkung and flur_norm and flurstueck:
        query_variants.append((
            f"gemarkungsnummer = ? AND flur_norm = ? AND flurstueck_norm = ?{state_clause}",
            [gemarkung, flur_norm, flurstueck, *state_params],
        ))
    if gemarkung_norm and flurstueck:
        query_variants.append((
            f"gemarkung_norm = ? AND flurstueck_norm = ?{state_clause}",
            [gemarkung_norm, flurstueck, *state_params],
        ))
    if gemarkung and flurstueck:
        query_variants.append((
            f"gemarkungsnummer = ? AND flurstueck_norm = ?{state_clause}",
            [gemarkung, flurstueck, *state_params],
        ))
    # In structured cadastre search, a supplied Gemarkung is a hard filter.
    # Falling back to only Flur/Flurstück produces many unrelated parcels with
    # identical parcel numbers across Germany.
    if not gemarkung_norm and not gemarkung:
        if flur_norm and flurstueck and states:
            query_variants.append((
                f"flur_norm = ? AND flurstueck_norm = ?{state_clause}",
                [flur_norm, flurstueck, *state_params],
            ))
        if flurstueck and states:
            query_variants.append((
                f"flurstueck_norm = ?{state_clause}",
                [flurstueck, *state_params],
            ))
    if not query_variants:
        return []

    seen: set[tuple[str, str, str]] = set()
    results: list[dict] = []
    try:
        con = fast_geocoder_connection()
        for where, params in query_variants:
            rows = con.execute(
                f"""
                SELECT *
                FROM parcel_exact
                WHERE {where}
                ORDER BY
                  CASE WHEN gemarkung_norm = ? THEN 0 ELSE 1 END,
                  CASE WHEN flur_norm = ? THEN 0 ELSE 1 END,
                  LENGTH(flurstueck_norm), flurstueck_norm
                LIMIT ?
                """,
                [*params, gemarkung_norm, flur_norm, max(int(limit) * 3, 12)],
            ).fetchall()
            for row in rows:
                key = (str(row["state"] or ""), str(row["source_db"] or ""), str(row["gml_id"] or ""))
                if key in seen:
                    continue
                seen.add(key)
                results.append(fast_parcel_result_from_row(row))
                if len(results) >= int(limit):
                    return results[:int(limit)]
            if rows and gemarkung_norm and flur_norm and flurstueck:
                return results[:int(limit)]
    except sqlite3.Error:
        return []
    return results[:int(limit)]


def search_fast_cadastre_parcels_for_dataset(
    gemarkung: str,
    flur: str,
    flurstueck: str,
    limit: int,
    search_states: set[str],
) -> list[dict]:
    sqlite_results = search_sqlite_parcel_lookup(
        gemarkung or "",
        flur or "",
        flurstueck or "",
        tuple(sorted(search_states)),
        search_db_signature_for_states(search_states),
        int(limit),
    )
    if sqlite_results:
        return sqlite_results[:int(limit)]
    return fast_parcel_lookup(
        gemarkung or "",
        flur or "",
        flurstueck or "",
        tuple(sorted(search_states)),
        fast_geocoder_signature(),
        int(limit),
    )


def search_address_result_from_row(row: sqlite3.Row, state: str, city_fallback: str = "") -> dict:
    lon = fast_float(row["lon"])
    lat = fast_float(row["lat"])
    street_label = str(row["street_label"] or "").strip()
    house_label = str(row["house_number_label"] or "").strip()
    city_label = str(row["city_label"] or city_fallback or "").strip()
    label = str(row["label"] or "").strip()
    base_label = " ".join(part for part in (street_label, house_label) if part).strip()
    if not label:
        label = base_label
    if label and city_label and city_label.casefold() not in label.casefold():
        label = f"{label}, {city_label}"
    address = {
        "label": label,
        "street": street_label,
        "house_number": house_label,
        "city": city_label,
        "country": "Deutschland",
    }
    post_code = str(row["post_code"] or "").strip()
    if post_code:
        address["post_code"] = post_code
        address["postal_code"] = post_code
    return {
        "kind": str(row["feature_kind"] or "address"),
        "result_type": "address",
        "label": label,
        "subtitle": "Adresse",
        "address": address,
        "state": state,
        "state_label": state_display_name(state),
        "center": [lon, lat],
        "bbox": [
            fast_float(row["min_lon"], lon),
            fast_float(row["min_lat"], lat),
            fast_float(row["max_lon"], lon),
            fast_float(row["max_lat"], lat),
        ],
        "zoom": 18.0,
        "feature": {
            "address": label,
            "addresses": [address],
            "source_db": str(row["source_db"] or ""),
            "gml_id": str(row["gml_id"] or ""),
        },
    }


def search_street_result_from_row(row: sqlite3.Row, state: str, street_fallback: str = "", city_fallback: str = "") -> dict:
    lon = fast_float(row["lon"])
    lat = fast_float(row["lat"])
    street_label = str(row["street_label"] or street_fallback or "").strip()
    city_label = str(row["city_label"] or city_fallback or "").strip()
    post_code = str(row["post_code"] or "").strip() if "post_code" in row.keys() else ""
    place_label = " ".join(part for part in (post_code, city_label) if part)
    label = f"{street_label}, {place_label}" if place_label else street_label
    return {
        "kind": "street",
        "result_type": "street",
        "label": label,
        "subtitle": "Straße",
        "state": state,
        "state_label": state_display_name(state),
        "center": [lon, lat],
        "bbox": [
            fast_float(row["min_lon"], lon),
            fast_float(row["min_lat"], lat),
            fast_float(row["max_lon"], lon),
            fast_float(row["max_lat"], lat),
        ],
        "zoom": 17.4,
        "feature": {
            "street": street_label,
            "municipality": city_label,
            "address_count": int(row["address_count"] or 0),
            "country": "Deutschland",
        },
    }


def search_clustered_street_results_from_address_rows(
    rows: list[sqlite3.Row],
    state: str,
    street_fallback: str = "",
    city_fallback: str = "",
    limit: int = 10,
) -> list[dict]:
    clusters: list[dict] = []
    lon_pad = 0.055
    lat_pad = 0.035
    for row in rows:
        lon = fast_float(row["lon"])
        lat = fast_float(row["lat"])
        street_label = str(row["street_label"] or street_fallback or "").strip()
        city_label = str(row["city_label"] or city_fallback or "").strip()
        post_code = str(row["post_code"] or "").strip() if "post_code" in row.keys() else ""
        chosen = None
        for cluster in clusters:
            if normalize_geocoder_text(cluster["city"]) != normalize_geocoder_text(city_label):
                continue
            if post_code and cluster["post_code"] and post_code != cluster["post_code"]:
                continue
            if (
                cluster["min_lon"] - lon_pad <= lon <= cluster["max_lon"] + lon_pad
                and cluster["min_lat"] - lat_pad <= lat <= cluster["max_lat"] + lat_pad
            ):
                chosen = cluster
                break
        if chosen is None:
            chosen = {
                "street": street_label,
                "city": city_label,
                "post_code": post_code,
                "count": 0,
                "sum_lon": 0.0,
                "sum_lat": 0.0,
                "min_lon": fast_float(row["min_lon"], lon),
                "min_lat": fast_float(row["min_lat"], lat),
                "max_lon": fast_float(row["max_lon"], lon),
                "max_lat": fast_float(row["max_lat"], lat),
            }
            clusters.append(chosen)
        chosen["count"] += 1
        chosen["sum_lon"] += lon
        chosen["sum_lat"] += lat
        chosen["min_lon"] = min(chosen["min_lon"], fast_float(row["min_lon"], lon), lon)
        chosen["min_lat"] = min(chosen["min_lat"], fast_float(row["min_lat"], lat), lat)
        chosen["max_lon"] = max(chosen["max_lon"], fast_float(row["max_lon"], lon), lon)
        chosen["max_lat"] = max(chosen["max_lat"], fast_float(row["max_lat"], lat), lat)
    clusters.sort(key=lambda cluster: (-int(cluster["count"]), str(cluster["street"]), str(cluster["post_code"])))
    results: list[dict] = []
    for cluster in clusters[:limit]:
        count = max(int(cluster["count"]), 1)
        lon = float(cluster["sum_lon"]) / count
        lat = float(cluster["sum_lat"]) / count
        street_label = str(cluster["street"] or street_fallback or "").strip()
        city_label = str(cluster["city"] or city_fallback or "").strip()
        # Do not run polygon-based postcode enrichment in search suggestions.
        # Keep only postcodes that were precomputed into search.sqlite.
        post_code = str(cluster["post_code"] or "").strip()
        place_label = " ".join(part for part in (post_code, city_label) if part)
        label = f"{street_label}, {place_label}" if place_label else street_label
        feature = {
            "street": street_label,
            "municipality": city_label,
            "address_count": count,
            "country": "Deutschland",
        }
        if post_code:
            feature["post_code"] = post_code
        results.append({
            "kind": "street",
            "result_type": "street",
            "label": label,
            "subtitle": "Straße",
            "state": state,
            "state_label": state_display_name(state),
            "center": [lon, lat],
            "bbox": [
                float(cluster["min_lon"]),
                float(cluster["min_lat"]),
                float(cluster["max_lon"]),
                float(cluster["max_lat"]),
            ],
            "zoom": 17.4,
            "feature": feature,
        })
    return results


def search_parcel_result_from_row(row: sqlite3.Row, state: str) -> dict:
    lon = fast_float(row["lon"])
    lat = fast_float(row["lat"])
    flur = str(row["flur_label"] or "")
    flurstueck = str(row["flurstueck_label"] or "")
    gemarkung = str(row["gemarkung_label"] or "")
    label = f"Flur {flur}, Flurstück {flurstueck}, {gemarkung}".strip().strip(",")
    feature = {
        "source_db": str(row["source_db"] or ""),
        "gml_id": str(row["gml_id"] or ""),
        "gemarkung": gemarkung,
        "gemarkungsnummer": str(row["gemarkungsnummer"] or ""),
        "flur": flur,
        "flurstueck": flurstueck,
        "zaehler": str(row["zaehler"] or ""),
        "nenner": str(row["nenner"] or ""),
    }
    area = row["amtliche_flaeche_m2"]
    if area is not None:
        feature["amtliche_flaeche_m2"] = fast_float(area)
    cadastre = {
        "flur": feature["flur"],
        "flurstueck": feature["flurstueck"],
        "gemarkung": feature["gemarkung"],
        "gemarkungsnummer": feature["gemarkungsnummer"],
    }
    return {
        "kind": "parcel",
        "result_type": "feature",
        "cadastre": cadastre,
        "label": label or "Flurstück",
        "subtitle": "Flurstück",
        "state": state,
        "state_label": state_display_name(state),
        "center": [lon, lat],
        "bbox": [
            fast_float(row["min_lon"], lon),
            fast_float(row["min_lat"], lat),
            fast_float(row["max_lon"], lon),
            fast_float(row["max_lat"], lat),
        ],
        "zoom": 18.0,
        "feature": feature,
    }


@lru_cache(maxsize=4096)
def search_sqlite_parcel_lookup(
    gemarkung: str,
    flur: str,
    flurstueck: str,
    states_key: tuple[str, ...],
    signature: tuple[tuple[str, str, int, int], ...],
    limit: int,
) -> list[dict]:
    if not signature:
        return []
    states = [state for state in states_key if state]
    gemarkung = (gemarkung or "").strip()
    flur = (flur or "").strip()
    flurstueck = normalize_parcel_number(flurstueck)
    if not any((gemarkung, flur, flurstueck)):
        return []
    gemarkung_norm = normalize_geocoder_text(gemarkung)
    flur_norm = fast_compact_norm(flur)
    query_variants: list[tuple[str, list[object]]] = []
    if gemarkung_norm and flur_norm and flurstueck:
        query_variants.append(("gemarkung_norm = ? AND flur_norm = ? AND flurstueck_norm = ?", [gemarkung_norm, flur_norm, flurstueck]))
    if gemarkung and flur_norm and flurstueck:
        query_variants.append(("gemarkungsnummer = ? AND flur_norm = ? AND flurstueck_norm = ?", [gemarkung, flur_norm, flurstueck]))
    if gemarkung_norm and flurstueck:
        query_variants.append(("gemarkung_norm = ? AND flurstueck_norm = ?", [gemarkung_norm, flurstueck]))
    if gemarkung and flurstueck:
        query_variants.append(("gemarkungsnummer = ? AND flurstueck_norm = ?", [gemarkung, flurstueck]))
    if not gemarkung_norm and not gemarkung:
        if flur_norm and flurstueck:
            query_variants.append(("flur_norm = ? AND flurstueck_norm = ?", [flur_norm, flurstueck]))
        if flurstueck:
            query_variants.append(("flurstueck_norm = ?", [flurstueck]))
    if not query_variants:
        return []
    entries = search_db_entries_for_states(states)
    seen: set[tuple[str, str, str]] = set()
    results: list[dict] = []
    for entry in entries:
        try:
            con = search_db_connection(entry.path)
            for where, params in query_variants:
                rows = con.execute(
                    f"""
                    SELECT *
                    FROM parcel_lookup
                    WHERE {where}
                    ORDER BY
                      CASE WHEN gemarkung_norm = ? THEN 0 ELSE 1 END,
                      CASE WHEN flur_norm = ? THEN 0 ELSE 1 END,
                      LENGTH(flurstueck_norm), flurstueck_norm
                    LIMIT ?
                    """,
                    [*params, gemarkung_norm, flur_norm, max(int(limit) * 3, 12)],
                ).fetchall()
                for row in rows:
                    key = (entry.name, str(row["source_db"] or ""), str(row["gml_id"] or ""))
                    if key in seen:
                        continue
                    seen.add(key)
                    results.append(search_parcel_result_from_row(row, entry.name))
                    if len(results) >= int(limit):
                        return results[:int(limit)]
                if rows and gemarkung_norm and flur_norm and flurstueck:
                    return results[:int(limit)]
        except sqlite3.Error:
            continue
    return results[:int(limit)]


@lru_cache(maxsize=4096)
def search_sqlite_direct_lookup(
    query: str,
    limit: int,
    states_key: tuple[str, ...],
    signature: tuple[tuple[str, str, int, int], ...],
    *,
    allow_plain_street: bool = False,
) -> list[dict]:
    if not signature:
        return []
    states = [state for state in states_key if state]
    if not states:
        return []
    entries = search_db_entries_for_states(states)
    results: list[dict] = []
    seen: set[tuple[str, str, str, str]] = set()
    for mode, street, house, city in geocoder_direct_candidates(query, allow_plain_street=allow_plain_street):
        street_norm = normalize_geocoder_text(street)
        city_norm = normalize_geocoder_text(city)
        city_norms = normalize_geocoder_text_variants(city)
        if not street_norm:
            continue
        for entry in entries:
            try:
                con = search_db_connection(entry.path)
                if mode == "address":
                    house_norm = normalize_geocoder_house(house)
                    if not house_norm:
                        continue
                    city_clause = f" AND city_norm IN ({','.join('?' for _ in city_norms)})" if city_norms else ""
                    city_params = list(city_norms)
                    rows = con.execute(
                        f"""
                        SELECT *
                        FROM address_lookup
                        WHERE street_norm = ?
                          AND house_number_norm = ?
                          AND feature_kind = 'building'
                          {city_clause}
                        ORDER BY label
                        LIMIT ?
                        """,
                        [street_norm, house_norm, *city_params, max(int(limit) * 3, 12)],
                    ).fetchall()
                    for row in rows:
                        result = search_address_result_from_row(row, entry.name, city)
                        key = (entry.name, str(row["source_db"] or ""), str(row["gml_id"] or ""), normalize_geocoder_text(str(result.get("label") or "")))
                        if key in seen:
                            continue
                        seen.add(key)
                        results.append(result)
                        if len(results) >= int(limit):
                            return results[:int(limit)]
                elif mode == "street":
                    if not city_norms:
                        continue
                    rows = con.execute(
                        f"""
                        SELECT *
                        FROM street_lookup
                        WHERE street_norm = ?
                          AND city_norm IN ({','.join('?' for _ in city_norms)})
                        ORDER BY address_count DESC, label
                        LIMIT ?
                        """,
                        [street_norm, *city_norms, int(limit)],
                    ).fetchall()
                    for row in rows:
                        result = search_street_result_from_row(row, entry.name, street, city)
                        key = (entry.name, "street", str(row["post_code"] or ""), normalize_geocoder_text(str(result.get("label") or "")))
                        if key in seen:
                            continue
                        seen.add(key)
                        results.append(result)
                        if len(results) >= int(limit):
                            return results[:int(limit)]
            except sqlite3.Error:
                continue
    return results[:int(limit)]


def _norm_prefix_bounds(prefix: str) -> tuple[str, str]:
    return prefix, f"{prefix}\uffff"


def search_place_suggestions_for_dataset(dataset: str, q: str, limit: int, state: str = "") -> dict:
    query = (q or "").strip()
    if len(query) < 2:
        return {"results": []}
    allowed_states = search_suggestion_states_for_dataset(dataset, state)
    query_norm = normalize_place_search_text(query)
    query_ascii = compact_place_search_text(query)
    query_plain = plain_place_search_text(query)
    query_plain_ascii = compact_plain_place_search_text(query)
    seen: set[tuple[str, str, str]] = set()
    candidates: list[tuple[tuple[int, int, int, str], dict]] = []
    for entry in gn250_place_entries(gn250_places_signature()):
        entry_state = normalize_state_key(str(entry.get("state") or ""))
        if entry_state not in allowed_states:
            continue
        name = str(entry.get("name") or "").strip()
        if not name:
            continue
        name_norm = str(entry.get("name_norm") or normalize_place_search_text(name))
        name_ascii = str(entry.get("name_ascii") or compact_place_search_text(name))
        name_plain = str(entry.get("name_plain") or plain_place_search_text(name))
        name_plain_ascii = str(entry.get("name_plain_ascii") or compact_plain_place_search_text(name))
        name_base = re.sub(r"\s*\([^)]*\)\s*$", "", name).strip()
        name_base_norm = normalize_place_search_text(name_base)
        name_base_plain = plain_place_search_text(name_base)
        name_base_ascii = compact_place_search_text(name_base)
        if not (
            name_norm.startswith(query_norm)
            or name_ascii.startswith(query_ascii)
            or name_plain.startswith(query_plain)
            or name_plain_ascii.startswith(query_plain_ascii)
            or name_base_norm.startswith(query_norm)
            or name_base_ascii.startswith(query_ascii)
        ):
            continue
        municipality = str(entry.get("municipality") or "").strip()
        state_label = str(entry.get("state_label") or state_display_name(entry_state))
        key = (entry_state, name_norm, normalize_place_search_text(municipality))
        if key in seen:
            continue
        seen.add(key)
        subtitle_parts = []
        if municipality and normalize_place_search_text(municipality) != name_norm:
            subtitle_parts.append(municipality)
        if state_label:
            subtitle_parts.append(state_label)
        place_class = str(entry.get("class") or "Ort")
        class_rank = 0 if place_class == "Gemeinde" else (1 if place_class == "Ortsteil" else 2)
        if query_norm in {name_norm, name_base_norm} or query_plain in {name_plain, name_base_plain}:
            match_rank = 0
        elif name_norm.startswith(f"{query_norm} ") or name_plain.startswith(f"{query_plain} "):
            match_rank = 1
        elif name_norm.startswith(query_norm) or name_plain.startswith(query_plain):
            match_rank = 2
        elif name_ascii.startswith(query_ascii) or name_plain_ascii.startswith(query_plain_ascii):
            match_rank = 3
        else:
            match_rank = 4
        payload = {
            "label": name,
            "value": name,
            "subtitle": ", ".join(subtitle_parts),
            "state": entry_state,
            "state_label": state_label,
            "class": place_class,
            "municipality": municipality,
        }
        population_rank = -int(entry.get("population") or 0)
        candidates.append(((match_rank, class_rank, population_rank, name.casefold()), payload))
    candidates.sort(key=lambda item: item[0])
    return {"results": [payload for _, payload in candidates[:int(limit)]]}


@lru_cache(maxsize=2048)
def search_street_suggestions_cached(
    place: str,
    q: str,
    limit: int,
    states_key: tuple[str, ...],
    signature: tuple[tuple[str, str, int, int], ...],
) -> tuple[dict, ...]:
    if not signature:
        return tuple()
    place = (place or "").strip()
    query = (q or "").strip()
    if len(place) < 2 or len(query) < 2:
        return tuple()
    allowed_states = set(states_key)
    place_context = requested_place_context(place, allowed_states)
    municipality = place_context_as_municipality(place_context)
    city_names: list[str] = []
    for value in (place, str((municipality or {}).get("name") or "")):
        value = value.strip()
        if value and normalize_geocoder_text(value) not in {normalize_geocoder_text(item) for item in city_names}:
            city_names.append(value)
    street_norm = normalize_geocoder_text(query)
    if not city_names or not street_norm:
        return tuple()
    results: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for entry in search_db_entries_for_states(states_key):
        try:
            con = search_db_connection(entry.path)
            rows = []
            for city_name in city_names:
                city_norms = normalize_geocoder_text_variants(city_name)
                if not city_norms:
                    continue
                rows.extend(con.execute(
                    f"""
                    SELECT street_label, city_label, label, SUM(address_count) AS address_count
                    FROM street_lookup
                    WHERE city_norm IN ({','.join('?' for _ in city_norms)})
                      AND street_norm LIKE ?
                    GROUP BY street_norm, city_norm
                    ORDER BY address_count DESC, street_label
                    LIMIT ?
                    """,
                    [*city_norms, f"{street_norm}%", int(limit) * 2],
                ).fetchall())
        except sqlite3.Error:
            continue
        for row in rows:
            street_label = str(row["street_label"] or "").strip()
            city_label = str(row["city_label"] or city_name).strip()
            key = (entry.name, normalize_geocoder_text(street_label), normalize_geocoder_text(city_label))
            if not street_label or key in seen:
                continue
            seen.add(key)
            results.append({
                "label": street_label,
                "value": street_label,
                "subtitle": city_label,
                "state": entry.name,
                "state_label": state_display_name(entry.name),
                "address_count": int(row["address_count"] or 0),
            })
            if len(results) >= int(limit):
                return tuple(results)
    return tuple(results[:int(limit)])


def search_street_suggestions_for_dataset(dataset: str, place: str, q: str, limit: int, state: str = "") -> dict:
    if not is_virtual_germany_dataset(dataset):
        get_dataset(dataset)
    states = search_suggestion_states_for_dataset(dataset, state)
    results = search_street_suggestions_cached(
        place,
        q,
        int(limit),
        tuple(sorted(state for state in states if state)),
        search_db_signature_for_states(states),
    )
    return {"results": list(results)}


@lru_cache(maxsize=2048)
def search_gemarkung_suggestions_cached(
    q: str,
    limit: int,
    states_key: tuple[str, ...],
    signature: tuple[tuple[str, str, int, int], ...],
) -> tuple[dict, ...]:
    if not signature:
        return tuple()
    query = (q or "").strip()
    if len(query) < 2:
        return tuple()
    query_norm = normalize_geocoder_text(query)
    if not query_norm:
        return tuple()
    results: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for entry in search_db_entries_for_states(states_key):
        try:
            con = search_db_connection(entry.path)
            rows = con.execute(
                """
                SELECT gemarkung_label, gemarkungsnummer, COUNT(*) AS parcel_count
                FROM parcel_lookup
                WHERE gemarkung_norm LIKE ?
                GROUP BY gemarkung_norm, gemarkungsnummer
                ORDER BY parcel_count DESC, gemarkung_label
                LIMIT ?
                """,
                [f"{query_norm}%", int(limit) * 2],
            ).fetchall()
        except sqlite3.Error:
            continue
        for row in rows:
            gemarkung_label = str(row["gemarkung_label"] or "").strip()
            key = (entry.name, normalize_geocoder_text(gemarkung_label))
            if not gemarkung_label or key in seen:
                continue
            seen.add(key)
            results.append({
                "label": gemarkung_label,
                "gemarkung": gemarkung_label,
                "subtitle": state_display_name(entry.name),
                "state": entry.name,
                "state_label": state_display_name(entry.name),
                "gemarkungsnummer": str(row["gemarkungsnummer"] or ""),
                "parcel_count": int(row["parcel_count"] or 0),
            })
            if len(results) >= int(limit):
                return tuple(results)
    return tuple(results[:int(limit)])


def search_gemarkung_suggestions_for_dataset(dataset: str, q: str, limit: int, state: str = "") -> dict:
    if not is_virtual_germany_dataset(dataset):
        get_dataset(dataset)
    states = search_suggestion_states_for_dataset(dataset, state)
    results = search_gemarkung_suggestions_cached(
        q,
        int(limit),
        tuple(sorted(state for state in states if state)),
        search_db_signature_for_states(states),
    )
    return {"results": list(results)}


@lru_cache(maxsize=4096)
def geocoder_lookup(
    query: str,
    limit: int,
    mode: str,
    states_key: tuple[str, ...],
    municipality_name: str,
    bbox_key: tuple[float, float, float, float] | tuple,
    signature: tuple[int, int],
) -> list[dict]:
    if signature == (0, 0) or not GEOCODER_DB.exists():
        return []
    street, house = parse_geocoder_address_query(query)
    street_norm = normalize_geocoder_text(street)
    house_norm = normalize_geocoder_house(house)
    municipality_norm = normalize_geocoder_text(municipality_name)
    if not street_norm:
        return []
    states = [state for state in states_key if state]
    if not states:
        return []
    bbox = tuple(float(value) for value in bbox_key) if len(bbox_key) == 4 else None
    bbox_clause, bbox_params = geocoder_bbox_clause("a", bbox)
    state_placeholders = ",".join("?" for _ in states)
    results: list[dict] = []
    try:
        con = geocoder_connection()
        if mode == "address" and house_norm:
            city_clause = ""
            city_params: list[str] = []
            if municipality_norm:
                city_clause = " AND a.city_norm = ?"
                city_params.append(municipality_norm)
            rows = con.execute(
                f"""
                SELECT a.*
                FROM addresses a
                WHERE a.street_norm = ?
                  AND a.house_norm = ?
                  AND a.feature_kind = 'building'
                  AND a.state IN ({state_placeholders})
                  {city_clause}
                  {bbox_clause}
                ORDER BY a.label
                LIMIT ?
                """,
                [street_norm, house_norm, *states, *city_params, *bbox_params, max(limit * 3, 12)],
            ).fetchall()
            if not rows and municipality_norm:
                rows = con.execute(
                    f"""
                    SELECT a.*
                    FROM addresses a
                    WHERE a.street_norm = ?
                      AND a.house_norm = ?
                      AND a.feature_kind = 'building'
                      AND a.state IN ({state_placeholders})
                      {bbox_clause}
                    ORDER BY a.label
                    LIMIT ?
                    """,
                    [street_norm, house_norm, *states, *bbox_params, max(limit * 3, 12)],
                ).fetchall()
            seen: set[tuple[str, str, str, int, int]] = set()
            for row in rows:
                label = geocoder_result_label(str(row["label"] or ""), {"name": municipality_name} if municipality_name else None)
                key = (
                    normalize_geocoder_text(label),
                    str(row["state"] or ""),
                )
                if key in seen:
                    continue
                seen.add(key)
                bbox_value = [float(row["min_lon"]), float(row["min_lat"]), float(row["max_lon"]), float(row["max_lat"])]
                result = {
                    "kind": str(row["feature_kind"] or "address"),
                    "result_type": "address",
                    "label": label,
                    "subtitle": "Adresse",
                    "state": str(row["state"] or ""),
                    "state_label": str(row["state_label"] or ""),
                    "center": [float(row["lon"]), float(row["lat"])],
                    "bbox": bbox_value,
                    "zoom": 18.0,
                    "feature": {
                        "address": label,
                        "addresses": [{
                            "label": label,
                            "street": str(row["street"] or ""),
                            "house_number": str(row["house_number"] or ""),
                            "city": str(row["city"] or municipality_name or ""),
                        }],
                        "source_db": str(row["source_db"] or ""),
                        "gml_id": str(row["gml_id"] or ""),
                    },
                }
                results.append(result)
                if len(results) >= limit:
                    break
        elif mode == "street":
            city_clause = ""
            city_params = []
            if municipality_norm:
                city_clause = " AND a.city_norm = ?"
                city_params.append(municipality_norm)
            rows = con.execute(
                f"""
                SELECT
                  a.state,
                  a.state_label,
                  MIN(a.street) AS street,
                  MIN(a.city) AS city,
                  AVG(a.lon) AS lon,
                  AVG(a.lat) AS lat,
                  MIN(a.min_lon) AS min_lon,
                  MIN(a.min_lat) AS min_lat,
                  MAX(a.max_lon) AS max_lon,
                  MAX(a.max_lat) AS max_lat,
                  COUNT(*) AS address_count
                FROM addresses a
                WHERE a.street_norm = ?
                  AND a.state IN ({state_placeholders})
                  {city_clause}
                  {bbox_clause}
                GROUP BY a.state, a.street_norm, a.city_norm
                ORDER BY address_count DESC
                LIMIT ?
                """,
                [street_norm, *states, *city_params, *bbox_params, limit],
            ).fetchall()
            if not rows and municipality_norm:
                rows = con.execute(
                    f"""
                    SELECT
                      a.state,
                      a.state_label,
                      MIN(a.street) AS street,
                      MIN(a.city) AS city,
                      AVG(a.lon) AS lon,
                      AVG(a.lat) AS lat,
                      MIN(a.min_lon) AS min_lon,
                      MIN(a.min_lat) AS min_lat,
                      MAX(a.max_lon) AS max_lon,
                      MAX(a.max_lat) AS max_lat,
                      COUNT(*) AS address_count
                    FROM addresses a
                    WHERE a.street_norm = ?
                      AND a.state IN ({state_placeholders})
                      {bbox_clause}
                    GROUP BY a.state, a.street_norm, a.city_norm
                    ORDER BY address_count DESC
                    LIMIT ?
                    """,
                    [street_norm, *states, *bbox_params, limit],
                ).fetchall()
            for row in rows:
                city = str(row["city"] or municipality_name or "").strip()
                street_label = str(row["street"] or street).strip()
                label = f"{street_label}, {city}" if city else street_label
                results.append({
                    "kind": "street",
                    "result_type": "street",
                    "label": label,
                    "subtitle": "Straße",
                    "state": str(row["state"] or ""),
                    "state_label": str(row["state_label"] or ""),
                    "center": [float(row["lon"]), float(row["lat"])],
                    "bbox": [float(row["min_lon"]), float(row["min_lat"]), float(row["max_lon"]), float(row["max_lat"])],
                    "zoom": 17.4,
                    "feature": {
                        "street": street_label,
                        "municipality": city,
                        "address_count": int(row["address_count"] or 0),
                    },
                })
    except sqlite3.Error:
        return []
    return results[:limit]


def search_geocoder_for_dataset(
    query: str,
    limit: int,
    search_states: set[str],
    wanted_municipality: dict | None,
    search_bbox,
    *,
    probable_address_query: bool,
    place_scoped_street_query: bool,
) -> list[dict]:
    if place_scoped_street_query and search_bbox:
        street_norm = normalize_geocoder_text(query)
        bbox = normalized_bbox(search_bbox)
        if not street_norm or not bbox:
            return []
        min_lon, min_lat, max_lon, max_lat = bbox
        results: list[dict] = []
        seen: set[tuple[str, str, str]] = set()
        for entry in search_db_entries_for_states(tuple(sorted(search_states))):
            try:
                con = search_db_connection(entry.path)
                rows = con.execute(
                    """
                    SELECT *
                    FROM address_lookup
                    WHERE feature_kind = 'building'
                      AND street_norm = ?
                      AND lon IS NOT NULL
                      AND lat IS NOT NULL
                      AND lon >= ?
                      AND lon <= ?
                      AND lat >= ?
                      AND lat <= ?
                    ORDER BY lon, lat
                    LIMIT 5000
                    """,
                    [street_norm, min_lon, max_lon, min_lat, max_lat],
                ).fetchall()
            except sqlite3.Error:
                continue
            municipality = str((wanted_municipality or {}).get("name") or "").strip()
            for item in search_clustered_street_results_from_address_rows(rows, entry.name, query, municipality, int(limit)):
                key = (
                    entry.name,
                    normalize_geocoder_text(str(item.get("label") or "")),
                    str(item.get("feature", {}).get("post_code") or ""),
                )
                if key in seen:
                    continue
                seen.add(key)
                results.append(item)
                if len(results) >= int(limit):
                    return results[:int(limit)]
        return results[:int(limit)]
    # Legacy geocoder/features.sqlite fallback is intentionally disabled for
    # interactive search. The fast geocoder must answer directly or return an
    # empty result quickly; otherwise typo queries can block for seconds.
    return []


def geocoder_prefix_upper_bound(prefix: str) -> str:
    if not prefix:
        return ""
    return prefix[:-1] + chr(ord(prefix[-1]) + 1)


def fast_address_rows_for_street_prefix(
    con: sqlite3.Connection,
    street_norm: str,
    house_norm: str,
    city_norm: str,
    states: list[str],
    limit: int,
) -> list[sqlite3.Row]:
    if len(street_norm) < 5 or not house_norm or not states:
        return []
    upper = geocoder_prefix_upper_bound(street_norm)
    if not upper:
        return []
    state_placeholders = ",".join("?" for _ in states)
    city_clause = " AND city_norm = ?" if city_norm else ""
    city_params = [city_norm] if city_norm else []
    return con.execute(
        f"""
        SELECT *
        FROM address_exact
        WHERE street_norm >= ?
          AND street_norm < ?
          AND house_norm = ?
          AND feature_kind = 'building'
          {city_clause}
          AND state IN ({state_placeholders})
        ORDER BY length(street_norm), label
        LIMIT ?
        """,
        [street_norm, upper, house_norm, *city_params, *states, max(limit * 3, 12)],
    ).fetchall()


def geocoder_direct_candidates(query: str, *, allow_plain_street: bool = False) -> list[tuple[str, str, str, str]]:
    tokens = re.sub(r"\s+", " ", (query or "").strip()).split(" ")
    candidates: list[tuple[str, str, str, str]] = []
    if len(tokens) < 2:
        return candidates
    for index, token in enumerate(tokens):
        if index <= 0:
            continue
        if not any(ch.isdigit() for ch in token):
            continue
        street = " ".join(tokens[:index]).strip()
        house = token.strip()
        city = " ".join(tokens[index + 1:]).strip()
        if street and house:
            candidates.append(("address", street, house, city))
    for split in range(len(tokens) - 1, 0, -1):
        street = " ".join(tokens[:split]).strip()
        city = " ".join(tokens[split:]).strip()
        if not street or not city:
            continue
        if not allow_plain_street and not is_likely_street_name_query(street):
            continue
        candidates.append(("street", street, "", city))
    seen: set[tuple[str, str, str, str]] = set()
    unique: list[tuple[str, str, str, str]] = []
    for candidate in candidates:
        key = tuple(normalize_geocoder_text(part) for part in candidate)
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


@lru_cache(maxsize=4096)
def geocoder_direct_lookup(
    query: str,
    limit: int,
    states_key: tuple[str, ...],
    signature: tuple[int, int],
    fast_signature: tuple[int, int],
) -> list[dict]:
    if signature == (0, 0) and fast_signature == (0, 0):
        return []
    states = [state for state in states_key if state]
    if not states:
        return []
    state_placeholders = ",".join("?" for _ in states)
    results: list[dict] = []
    try:
        for mode, street, house, city in geocoder_direct_candidates(query):
            street_norm = normalize_geocoder_text(street)
            city_norm = normalize_geocoder_text(city)
            city_norms = normalize_geocoder_text_variants(city)
            if not street_norm:
                continue
            if mode == "address":
                house_norm = normalize_geocoder_house(house)
                if not house_norm:
                    continue
                if fast_signature != (0, 0) and FAST_GEOCODER_DB.exists():
                    try:
                        fast_con = fast_geocoder_connection()
                        fast_city_clause = f" AND city_norm IN ({','.join('?' for _ in city_norms)})" if city_norms else ""
                        fast_city_params = list(city_norms)
                        fast_rows = fast_con.execute(
                            f"""
                            SELECT *
                            FROM address_exact
                            WHERE street_norm = ?
                              AND house_norm = ?
                              AND feature_kind = 'building'
                              {fast_city_clause}
                              AND state IN ({state_placeholders})
                            ORDER BY label
                            LIMIT ?
                            """,
                            [street_norm, house_norm, *fast_city_params, *states, max(limit * 3, 12)],
                        ).fetchall()
                    except sqlite3.Error:
                        fast_rows = []
                    if not fast_rows:
                        try:
                            for variant_city_norm in city_norms or ("",):
                                fast_rows = fast_address_rows_for_street_prefix(
                                    fast_con,
                                    street_norm,
                                    house_norm,
                                    variant_city_norm,
                                    states,
                                    limit,
                                )
                                if fast_rows:
                                    break
                        except sqlite3.Error:
                            fast_rows = []
                    seen_fast: set[tuple[str, str]] = set()
                    for row in fast_rows:
                        result = fast_address_result_from_row(row, city)
                        key = (normalize_geocoder_text(str(result.get("label") or "")), str(result.get("state") or ""))
                        if key in seen_fast:
                            continue
                        seen_fast.add(key)
                        results.append(result)
                        if len(results) >= limit:
                            return results[:limit]
                    # No legacy fallback: a missing fast result must stay a fast empty result.
                    continue
                if signature == (0, 0) or not GEOCODER_DB.exists():
                    continue
                con = geocoder_connection()
                city_clause = f" AND a.city_norm IN ({','.join('?' for _ in city_norms)})" if city_norms else ""
                city_params = list(city_norms)
                rows = con.execute(
                    f"""
                    SELECT a.*
                    FROM addresses a
                    WHERE a.street_norm = ?
                      AND a.house_norm = ?
                      AND a.feature_kind = 'building'
                      {city_clause}
                      AND a.state IN ({state_placeholders})
                    ORDER BY a.label
                    LIMIT ?
                    """,
                    [street_norm, house_norm, *city_params, *states, max(limit * 3, 12)],
                ).fetchall()
                seen: set[tuple[str, str]] = set()
                for row in rows:
                    label = geocoder_result_label(str(row["label"] or ""), {"name": city})
                    key = (normalize_geocoder_text(label), str(row["state"] or ""))
                    if key in seen:
                        continue
                    seen.add(key)
                    address = {
                        "label": label,
                        "street": str(row["street"] or ""),
                        "house_number": str(row["house_number"] or ""),
                        "city": str(row["city"] or city or ""),
                        "country": "Deutschland",
                    }
                    post_code = str(row["post_code"] or "").strip() if "post_code" in row.keys() else ""
                    if post_code:
                        address["post_code"] = post_code
                        address["postal_code"] = post_code
                    results.append({
                        "kind": str(row["feature_kind"] or "address"),
                        "result_type": "address",
                        "label": label,
                        "subtitle": "Adresse",
                        "address": address,
                        "state": str(row["state"] or ""),
                        "state_label": str(row["state_label"] or ""),
                        "center": [float(row["lon"]), float(row["lat"])],
                        "bbox": [float(row["min_lon"]), float(row["min_lat"]), float(row["max_lon"]), float(row["max_lat"])],
                        "zoom": 18.0,
                        "feature": {
                            "address": label,
                            "addresses": [address],
                            "source_db": str(row["source_db"] or ""),
                            "gml_id": str(row["gml_id"] or ""),
                        },
                    })
                    if len(results) >= limit:
                        return results[:limit]
            elif mode == "street":
                if not city_norms:
                    continue
                fast_rows = []
                if fast_signature != (0, 0) and FAST_GEOCODER_DB.exists():
                    fast_con = fast_geocoder_connection()
                    fast_rows = fast_con.execute(
                        f"""
                        SELECT *
                        FROM street_exact
                        WHERE street_norm = ?
                          AND city_norm IN ({','.join('?' for _ in city_norms)})
                          AND state IN ({state_placeholders})
                        ORDER BY address_count DESC
                        LIMIT ?
                        """,
                        [street_norm, *city_norms, *states, limit],
                    ).fetchall()
                    seen_streets: set[tuple[str, str, str]] = set()
                    for row in fast_rows:
                        row_results: list[dict]
                        if fast_street_row_needs_address_clusters(row):
                            address_rows = fast_con.execute(
                                """
                                SELECT *
                                FROM address_exact
                                WHERE street_norm = ?
                                  AND city_norm = ?
                                  AND state = ?
                                  AND feature_kind = 'building'
                                  AND lon IS NOT NULL
                                  AND lat IS NOT NULL
                                ORDER BY lon, lat
                                LIMIT 5000
                                """,
                                [street_norm, str(row["city_norm"] or ""), str(row["state"] or "")],
                            ).fetchall()
                            row_results = fast_clustered_street_results_from_address_rows(address_rows, street, city, limit) if address_rows else []
                        else:
                            row_results = []
                        if not row_results:
                            row_results = [fast_street_result_from_row(row, street, city)]
                        for result in row_results:
                            key = (
                                normalize_geocoder_text(str(result.get("label") or "")),
                                str(result.get("state") or ""),
                                str(result.get("result_type") or ""),
                            )
                            if key in seen_streets:
                                continue
                            seen_streets.add(key)
                            results.append(result)
                            if len(results) >= limit:
                                return results[:limit]
                # No legacy fallback: street search is served by street_exact only.
                continue
                if fast_rows or signature == (0, 0) or not GEOCODER_DB.exists():
                    continue
                con = geocoder_connection()
                rows = con.execute(
                    f"""
                    SELECT
                      a.state,
                      a.state_label,
                      MIN(a.street) AS street,
                      MIN(a.city) AS city,
                      AVG(a.lon) AS lon,
                      AVG(a.lat) AS lat,
                      MIN(a.min_lon) AS min_lon,
                      MIN(a.min_lat) AS min_lat,
                      MAX(a.max_lon) AS max_lon,
                      MAX(a.max_lat) AS max_lat,
                      COUNT(*) AS address_count
                    FROM addresses a
                    WHERE a.street_norm = ?
                      AND a.city_norm = ?
                      AND a.state IN ({state_placeholders})
                    GROUP BY a.state, a.street_norm, a.city_norm
                    ORDER BY address_count DESC
                    LIMIT ?
                    """,
                    [street_norm, city_norm, *states, limit],
                ).fetchall()
                for row in rows:
                    city_label = str(row["city"] or city or "").strip()
                    street_label = str(row["street"] or street).strip()
                    label = f"{street_label}, {city_label}" if city_label else street_label
                    results.append({
                        "kind": "street",
                        "result_type": "street",
                        "label": label,
                        "subtitle": "Straße",
                        "state": str(row["state"] or ""),
                        "state_label": str(row["state_label"] or ""),
                        "center": [float(row["lon"]), float(row["lat"])],
                        "bbox": [float(row["min_lon"]), float(row["min_lat"]), float(row["max_lon"]), float(row["max_lat"])],
                        "zoom": 17.4,
                        "feature": {
                            "street": street_label,
                            "municipality": city_label,
                            "address_count": int(row["address_count"] or 0),
                            "country": "Deutschland",
                        },
                    })
                    if len(results) >= limit:
                        return results[:limit]
    except sqlite3.Error:
        return []
    return results[:limit]


def search_direct_geocoder_for_dataset(query: str, limit: int, search_states: set[str], *, allow_plain_street: bool = False) -> list[dict]:
    sqlite_results = search_sqlite_direct_lookup(
        query,
        int(limit),
        tuple(sorted(search_states)),
        search_db_signature_for_states(search_states),
        allow_plain_street=allow_plain_street,
    )
    return sqlite_results[:int(limit)]


def search_features_for_dataset(
    dataset: str,
    query: str,
    limit: int,
    mode: str = "mixed",
    *,
    state: str = "",
    gemarkung: str = "",
    flur: str = "",
    flurstueck: str = "",
) -> dict:
    query = query.strip()
    search_mode = (mode or "mixed").strip().casefold()
    standard_mode = search_mode in {"standard", "adresse", "address", "places", "orte"}
    cadastre_mode = search_mode in {"cadastre", "kataster", "parcel", "parcels", "flurstueck", "flurstück"}
    if len(query) < 2:
        return {"query": query, "count": 0, "results": []}

    per_index_limit = max(8, min(limit * 2, 30))
    results: list[dict] = []
    active_states = set(active_bucket_state_keys())
    if is_virtual_germany_dataset(dataset):
        allowed_states = active_states
    else:
        state_key, _, _ = _mosaic_state_key(DATA_DIR / f"{dataset}.pmtiles")
        allowed_states = {state_key}
    structured_state = requested_state_context(state, allowed_states)
    preliminary_search_states = {structured_state} if structured_state else allowed_states
    if cadastre_mode and any(part.strip() for part in (gemarkung, flur, flurstueck)):
        fast_parcel_results = search_fast_cadastre_parcels_for_dataset(gemarkung, flur, flurstueck, limit, preliminary_search_states)
        if fast_parcel_results:
            return {"query": query, "count": len(fast_parcel_results[:limit]), "results": fast_parcel_results[:limit]}
        if gemarkung.strip() and flur.strip() and flurstueck.strip():
            return {"query": query, "count": 0, "results": []}
    place_context = requested_place_context(query, allowed_states)
    query_without_place = query_without_place_context(query, place_context)
    if not cadastre_mode and place_context and normalize_place_search_text(query_without_place) != normalize_place_search_text(query):
        place_states = {structured_state} if structured_state else {str(place_context["state"])}
        place_candidate_limit = max(int(limit) * 20, 100)
        place_direct_results = search_direct_geocoder_for_dataset(query_without_place, place_candidate_limit, place_states)
        place_bbox = normalized_bbox(place_context.get("bbox") if isinstance(place_context, dict) else None)
        if place_bbox:
            min_lon, min_lat, max_lon, max_lat = place_bbox
            place_direct_results = [
                item for item in place_direct_results
                if isinstance(item.get("center"), list)
                and len(item["center"]) >= 2
                and min_lon <= float(item["center"][0]) <= max_lon
                and min_lat <= float(item["center"][1]) <= max_lat
            ]
        if place_direct_results:
            return {"query": query, "count": len(place_direct_results[:limit]), "results": place_direct_results[:limit]}
    direct_geocoder_results = search_direct_geocoder_for_dataset(query, limit, preliminary_search_states) if not cadastre_mode else []
    if direct_geocoder_results:
        return {"query": query, "count": len(direct_geocoder_results[:limit]), "results": direct_geocoder_results[:limit]}
    if not cadastre_mode and is_probable_address_query(query) and not place_context:
        return {"query": query, "count": 0, "results": []}
    search_states = {structured_state} if structured_state else ({place_context["state"]} if place_context else allowed_states)
    probable_address_query = is_probable_address_query(query)
    wanted_municipality = requested_municipality(query, search_states)
    place_removed_text = normalize_place_search_text(query_without_place) != normalize_place_search_text(query)
    place_scoped_street_query = bool(
        place_context
        and place_removed_text
        and not probable_address_query
        and not cadastre_mode
        and search_tokens(query_without_place)
        and not any(term in query.casefold() for term in ("flurstück", "flurstueck", "gemarkung", "flur "))
    )
    unscoped_street_query = (
        is_likely_street_name_query(query)
        and not probable_address_query
        and not place_scoped_street_query
        and not cadastre_mode
    )
    if not wanted_municipality and (probable_address_query or place_scoped_street_query):
        wanted_municipality = place_context_as_municipality(place_context)
    address_query = query_without_municipality(query_without_place, wanted_municipality)
    address_scoped_query = probable_address_query or place_scoped_street_query
    search_bbox = place_context.get("bbox") if isinstance(place_context, dict) and address_scoped_query else None
    geocoder_results = search_geocoder_for_dataset(
        address_query,
        limit,
        search_states,
        wanted_municipality,
        search_bbox,
        probable_address_query=probable_address_query,
        place_scoped_street_query=place_scoped_street_query,
    )
    if geocoder_results:
        return {"query": query, "count": len(geocoder_results[:limit]), "results": geocoder_results[:limit]}
    if address_scoped_query:
        return {"query": query, "count": 0, "results": []}

    state_results = [] if cadastre_mode or address_scoped_query or unscoped_street_query else state_search_results(query, allowed_states, min(limit, 8))
    if state_results and any(normalize_place_search_text(str(item.get("label") or "")) == normalize_place_search_text(query) for item in state_results):
        return {"query": query, "count": len(state_results[:limit]), "results": state_results[:limit]}
    place_results = [] if cadastre_mode or address_scoped_query or unscoped_street_query else search_places_for_dataset(dataset, query, min(limit, 8))
    for item in place_results:
        item["state_label"] = state_display_name(str(item.get("state") or ""))
    exact_place_results = [
        item for item in place_results
        if normalize_place_search_text(str(item.get("label") or "")) == normalize_place_search_text(query)
        or plain_place_search_text(str(item.get("label") or "")) == plain_place_search_text(query)
    ]
    if exact_place_results:
        return {"query": query, "count": len(exact_place_results[:limit]), "results": exact_place_results[:limit]}
    if place_results and not state_results:
        return {"query": query, "count": len(place_results[:limit]), "results": place_results[:limit]}
    if place_results and state_results:
        combined_place_results = [*state_results, *place_results]
        return {"query": query, "count": len(combined_place_results[:limit]), "results": combined_place_results[:limit]}
    results.extend(state_results)
    results.extend(place_results)

    entries = feature_db_entries_for_dataset(dataset)
    if not entries:
        raise HTTPException(status_code=404, detail="feature index not found")

    for entry in entries:
        if entry.name not in search_states:
            continue
        if cadastre_mode and any(part.strip() for part in (gemarkung, flur, flurstueck)):
            for item in search_cadastre_parcels_in_index(
                entry.path,
                gemarkung=gemarkung,
                flur=flur,
                flurstueck=flurstueck,
                limit=per_index_limit,
            ):
                item["state"] = entry.name
                item["state_label"] = state_display_name(entry.name)
                results.append(item)
            continue
        if place_scoped_street_query:
            for item in search_streets_in_index(
                entry.path,
                address_query,
                limit,
                bbox=search_bbox,
                municipality=wanted_municipality,
            ):
                item["state"] = entry.name
                item["state_label"] = state_display_name(entry.name)
                results.append(item)
            continue
        if not cadastre_mode and not unscoped_street_query:
            relation_limit = max(per_index_limit * 8, 80) if wanted_municipality else per_index_limit
            for item in search_relation_addresses_in_index(entry.path, address_query, relation_limit, bbox=search_bbox):
                item["state"] = entry.name
                item["state_label"] = state_display_name(entry.name)
                if wanted_municipality:
                    enrich_address_municipality(item, entry.name)
                    if not municipality_name_matches(str(item.get("municipality") or ""), wanted_municipality):
                        continue
                results.append(item)
            for item in search_addresses_in_index(entry.path, address_query, per_index_limit, bbox=search_bbox):
                item["state"] = entry.name
                item["state_label"] = state_display_name(entry.name)
                if wanted_municipality:
                    enrich_address_municipality(item, entry.name)
                    if not municipality_name_matches(str(item.get("municipality") or ""), wanted_municipality):
                        continue
                results.append(item)
        if cadastre_mode or (not standard_mode and not wanted_municipality and not unscoped_street_query and not is_probable_address_query(address_query)):
            for item in search_features_in_index(entry.path, address_query, per_index_limit):
                if cadastre_mode and item.get("kind") != "parcel":
                    continue
                item["state"] = entry.name
                item["state_label"] = state_display_name(entry.name)
                results.append(item)

    def result_rank(item: dict) -> tuple:
        label = str(item.get("label") or "")
        subtitle = str(item.get("subtitle") or "")
        state = str(item.get("state") or "")
        state_label = str(item.get("state_label") or "")
        result_type = str(item.get("result_type") or "")
        kind = str(item.get("kind") or "")
        feature = item.get("feature") if isinstance(item.get("feature"), dict) else {}
        parcel_query = query_parcel_number(query)
        parcel_number = normalize_parcel_number(feature.get("flurstueck") or feature.get("flurstuecksnummer") or "")
        if result_type == "place":
            type_rank = 0
        elif result_type == "street":
            type_rank = 1
        elif result_type == "address" and kind == "building":
            type_rank = 2
        elif result_type == "address" and kind == "parcel":
            type_rank = 3
        elif result_type == "address":
            type_rank = 4
        elif kind == "parcel":
            type_rank = 0 if cadastre_mode else 5
        elif kind == "building":
            type_rank = 6
        else:
            type_rank = 7
        return (
            0 if parcel_query and kind == "parcel" and parcel_number == parcel_query else 1,
            0 if text_contains(state_label, query) or text_contains(state, query) else 1,
            0 if kind == "parcel" and text_contains(str(feature.get("gemarkung") or ""), query) else 1,
            type_rank,
            0 if text_has_word(label, query) else 1,
            0 if label.casefold().startswith(query.casefold()) else 1,
            0 if text_contains(label, query) else 1,
            0 if text_contains(subtitle, query) else 1,
            label.casefold(),
        )

    results.sort(key=result_rank)
    visible_results = results[:limit]
    for item in visible_results:
        enrich_address_municipality(item, str(item.get("state") or ""))

    return {"query": query, "count": len(visible_results), "results": visible_results}


def cached_search_features_for_dataset(
    dataset: str,
    query: str,
    limit: int,
    mode: str = "mixed",
    *,
    state: str = "",
    gemarkung: str = "",
    flur: str = "",
    flurstueck: str = "",
) -> dict:
    stripped_query = query.strip()
    normalized_mode = (mode or "mixed").strip().casefold()
    cadastre_mode = normalized_mode in {"cadastre", "kataster", "parcel", "parcels", "flurstueck", "flurstück"}
    street_mode = normalized_mode in {"street", "strasse", "straße"}
    direct_candidates = geocoder_direct_candidates(stripped_query, allow_plain_street=street_mode) if not cadastre_mode else []
    now = time.time()

    # Address/street autocomplete must stay independent from feature.sqlite scans.
    # Feature DB signatures require filesystem stats for every active state and can
    # turn typo/no-result searches into multi-second requests.
    if direct_candidates:
        active_states = set(active_bucket_state_keys())
        if is_virtual_germany_dataset(dataset):
            allowed_states = active_states
        else:
            state_key, _, _ = _mosaic_state_key(DATA_DIR / f"{dataset}.pmtiles")
            allowed_states = {state_key}
        structured_state = requested_state_context(state, allowed_states)
        candidate_places = [candidate[3] for candidate in direct_candidates if len(candidate) >= 4 and str(candidate[3] or "").strip()]
        if not structured_state:
            for candidate_place in candidate_places:
                inferred_states = states_for_place_context(candidate_place, allowed_states)
                if len(inferred_states) == 1:
                    structured_state = inferred_states[0]
                    break
        search_states = {structured_state} if structured_state else allowed_states
        place_context = None
        for candidate_place in candidate_places:
            place_context = exact_place_context(candidate_place, search_states)
            if place_context:
                break
        if place_context:
            place_query = query_without_place_context(stripped_query, place_context)
        elif structured_state:
            place_query = stripped_query
        else:
            place_context = requested_place_context(stripped_query, allowed_states)
            place_query = query_without_place_context(stripped_query, place_context)
        key = (
            "direct-geocoder-v5",
            dataset,
            stripped_query,
            int(limit),
            normalized_mode,
            state,
            tuple(sorted(search_states)),
            tuple(active_bucket_state_keys()),
            search_db_signature_for_states(search_states),
        )
        cached = _SEARCH_RESPONSE_CACHE.get(key)
        if cached and cached[0] > now:
            return json.loads(json.dumps(cached[1]))
        direct_results = []
        if place_context and normalize_place_search_text(place_query) != normalize_place_search_text(stripped_query):
            place_states = {structured_state} if structured_state else {str(place_context["state"])}
            place_candidate_limit = max(int(limit) * 20, 100)
            direct_results = search_direct_geocoder_for_dataset(place_query, place_candidate_limit, place_states, allow_plain_street=street_mode)
            place_bbox = normalized_bbox(place_context.get("bbox") if isinstance(place_context, dict) else None)
            if place_bbox:
                min_lon, min_lat, max_lon, max_lat = place_bbox
                direct_results = [
                    item for item in direct_results
                    if isinstance(item.get("center"), list)
                    and len(item["center"]) >= 2
                    and min_lon <= float(item["center"][0]) <= max_lon
                    and min_lat <= float(item["center"][1]) <= max_lat
                ]
        if not direct_results:
            direct_results = search_direct_geocoder_for_dataset(stripped_query, limit, search_states, allow_plain_street=street_mode)
        if direct_results or is_probable_address_query(stripped_query) or is_probable_address_query(place_query):
            result = {"query": stripped_query, "count": len(direct_results[:limit]), "results": direct_results[:limit]}
            if len(_SEARCH_RESPONSE_CACHE) >= _SEARCH_RESPONSE_CACHE_MAX:
                for old_key, (expires, _) in list(_SEARCH_RESPONSE_CACHE.items()):
                    if expires <= now or len(_SEARCH_RESPONSE_CACHE) >= _SEARCH_RESPONSE_CACHE_MAX:
                        _SEARCH_RESPONSE_CACHE.pop(old_key, None)
            _SEARCH_RESPONSE_CACHE[key] = (now + SEARCH_CACHE_SECONDS, json.loads(json.dumps(result)))
            return result

    entries = feature_db_entries_for_dataset(dataset)
    signature = tuple((entry.name, str(entry.path), *sqlite_file_signature(entry.path)) for entry in entries)
    search_signature = tuple((entry.name, str(entry.path), *sqlite_file_signature(entry.path)) for entry in search_db_entries_for_dataset(dataset))
    key = (dataset, stripped_query, int(limit), normalized_mode, state, gemarkung, flur, flurstueck, tuple(active_bucket_state_keys()), gn250_places_signature(), postcode_areas_signature(), signature, search_signature)
    cached = _SEARCH_RESPONSE_CACHE.get(key)
    if cached and cached[0] > now:
        return json.loads(json.dumps(cached[1]))
    result = search_features_for_dataset(dataset, stripped_query, limit, mode, state=state, gemarkung=gemarkung, flur=flur, flurstueck=flurstueck)
    if len(_SEARCH_RESPONSE_CACHE) >= _SEARCH_RESPONSE_CACHE_MAX:
        for old_key, (expires, _) in list(_SEARCH_RESPONSE_CACHE.items()):
            if expires <= now or len(_SEARCH_RESPONSE_CACHE) >= _SEARCH_RESPONSE_CACHE_MAX:
                _SEARCH_RESPONSE_CACHE.pop(old_key, None)
    _SEARCH_RESPONSE_CACHE[key] = (now + SEARCH_CACHE_SECONDS, json.loads(json.dumps(result)))
    return result


def viewer_key(provided: str | None = None) -> str:
    if provided:
        return provided
    keys = sorted(_configured_keys())
    if not keys:
        raise HTTPException(status_code=503, detail="tile service has no API keys configured")
    return keys[0]


def viewer_html(request: Request, dataset: str, key: str) -> str:
    base = public_base_url(request)
    asset_version = int(max(Path(__file__).stat().st_mtime, NATIONAL_STYLE_PATH.stat().st_mtime if NATIONAL_STYLE_PATH.exists() else 0))
    style_url = f"{base}/styles/{dataset}.json?key={key}&v={asset_version}"
    tilejson_url = f"{base}/tilejson/{dataset}.json?key={key}&v={asset_version}"
    feature_url = f"{base}/api/features/{dataset}/point?key={key}"
    search_url = f"{base}/api/search/{dataset}?key={key}"
    datasets_url = f"{base}/datasets?key={key}"
    metadata_url = f"{base}/api/state-metadata?key={key}"
    template = """<!doctype html>
<html lang=\"de\"> 
<head>
  <meta charset=\"utf-8\">
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
  <title>OpenKataster Karte - __DATASET__</title>
  <link rel=\"stylesheet\" href=\"https://unpkg.com/maplibre-gl@5.14.0/dist/maplibre-gl.css\"> 
  <style>
    :root {
      --ok-bg: #ffffff;
      --ok-line: #f0e5d5;
      --ok-text: #111827;
      --ok-muted: #4b5563;
      --ok-accent: #f86d14;
      --ok-accent-soft: #fff4ec;
      --ok-shadow: 0 16px 38px rgba(15, 23, 42, 0.16);
    }
    @font-face {
      font-family: \"IBM Plex Sans\";
      src: url(\"https://openkataster.de/fonts/ibm-plex-sans-400-latin.woff2\") format(\"woff2\");
      font-weight: 400;
      font-style: normal;
      font-display: swap;
    }
    @font-face {
      font-family: \"IBM Plex Sans\";
      src: url(\"https://openkataster.de/fonts/ibm-plex-sans-700-latin.woff2\") format(\"woff2\");
      font-weight: 700;
      font-style: normal;
      font-display: swap;
    }
    * {
      box-sizing: border-box;
    }
    html, body, #map {
      width: 100%;
      height: 100%;
      margin: 0;
    }
    body {
      font-family: \"IBM Plex Sans\", Inter, system-ui, -apple-system, BlinkMacSystemFont, \"Segoe UI\", sans-serif;
      color: var(--ok-text);
      background: var(--ok-bg);
    }
    #map {
      position: absolute;
      inset: 0;
      z-index: 1;
    }
    .map-bootstrap-backdrop {
      position: absolute;
      inset: 0;
      z-index: 0;
      width: 100%;
      height: 100%;
      object-fit: cover;
      opacity: 0;
      transition: opacity 220ms ease;
      pointer-events: none;
      background: #f8f5ef;
    }
    .map-bootstrap-backdrop.is-visible {
      opacity: 1;
    }
    .map-frame-fallback {
      position: fixed;
      inset: 0;
      z-index: 0;
      width: 100%;
      height: 100%;
      object-fit: cover;
      pointer-events: none;
      opacity: 0;
      transition: opacity 120ms linear;
      background: var(--ok-bg);
    }
    .map-frame-fallback.is-visible {
      opacity: 1;
    }
    #map .maplibregl-canvas-container {
      background: transparent;
    }
    .floating {
      position: absolute;
      z-index: 3;
      border: 1px solid var(--ok-line);
      border-radius: 8px;
      background: rgba(255, 255, 255, 0.98);
      box-shadow: 0 12px 30px rgba(17, 23, 19, 0.10);
      backdrop-filter: blur(2px);
      font-size: 13px;
    }
    .panel-label {
      text-transform: uppercase;
      font-size: 11px;
      color: var(--ok-muted);
      letter-spacing: 0.05em;
      padding: 8px 12px 0;
      margin-bottom: 4px;
      font-weight: 700;
    }
    #toolDock {
      left: 12px;
      top: 64px;
      padding: 6px;
      width: 52px;
    }
    #layerSwitch {
      right: 12px;
      top: 12px;
      padding: 0;
      border: 0;
      border-radius: 999px;
      overflow: visible;
      background: transparent;
      box-shadow: none;
      backdrop-filter: none;
      z-index: 7;
    }
    #layerToggle {
      width: 40px;
      height: 40px;
      border: 0;
      border-radius: 999px;
      background: #fff;
      color: #111713;
      display: grid;
      place-items: center;
      cursor: pointer;
      box-shadow: 0 10px 25px rgba(17, 23, 19, 0.14);
    }
    #layerToggle svg {
      width: 20px;
      height: 20px;
      stroke-width: 1.9;
    }
    #layerMenu {
      position: absolute;
      right: 0;
      top: calc(100% + 7px);
      width: 270px;
      max-height: min(74vh, 560px);
      overflow: auto;
      padding: 10px;
      border: 1px solid #ece9e5;
      border-radius: 12px;
      background: #fff;
      box-shadow: 0 18px 38px rgba(17, 23, 19, 0.14);
      z-index: 8;
    }
    #layerMenu[hidden] {
      display: none;
    }
    .layer-menu-title {
      margin: 0 0 8px;
      color: #111713;
      font-size: 12px;
      font-weight: 760;
    }
    .layer-group {
      border-top: 1px solid #f0ede8;
      padding: 7px 0 5px;
    }
    .layer-group:first-of-type {
      border-top: 0;
      padding-top: 0;
    }
    .layer-group summary {
      color: #111713;
      cursor: pointer;
      font-size: 11px;
      font-weight: 760;
      list-style-position: outside;
      padding: 1px 0 5px 2px;
    }
    .layer-group-check {
      display: inline-grid;
      grid-template-columns: 16px 1fr;
      gap: 7px;
      align-items: center;
      cursor: pointer;
    }
    .layer-group-check input {
      width: 14px;
      height: 14px;
      margin: 0;
      accent-color: var(--ok-accent);
      cursor: pointer;
    }
    .layer-check {
      display: grid;
      grid-template-columns: 16px 20px 1fr;
      gap: 7px;
      align-items: center;
      min-height: 28px;
      padding: 4px 3px;
      color: #374151;
      font-size: 12px;
      font-weight: 620;
      cursor: pointer;
      user-select: none;
    }
    .layer-check input {
      width: 14px;
      height: 14px;
      margin: 0;
      accent-color: var(--ok-accent);
      cursor: pointer;
    }
    .legend-swatch {
      width: 18px;
      height: 14px;
      border-radius: 3px;
      border: 1px solid rgba(17, 23, 19, 0.28);
      display: inline-block;
      position: relative;
      box-sizing: border-box;
    }
    .legend-swatch.aerial {
      background: linear-gradient(135deg, #486b3c 0 34%, #b7a985 34% 62%, #466f92 62%);
      border-color: #7d8b72;
    }
    .legend-swatch.fill {
      background: linear-gradient(90deg, #f8e8f1 0 34%, #e9ffd8 34% 68%, #e2ffff 68%);
    }
    .legend-swatch.building {
      background: #9d9d9d;
      border-color: #111;
    }
    .legend-swatch.boundary-point {
      width: 14px;
      height: 14px;
      margin-left: 2px;
      border-radius: 999px;
      background: #fff;
      border: 2px solid #111;
    }
    .legend-swatch.boundary-point::after {
      content: "";
      position: absolute;
      width: 4px;
      height: 4px;
      border-radius: 999px;
      background: #111;
      left: 50%;
      top: 50%;
      transform: translate(-50%, -50%);
    }
    .legend-swatch.parcel-line,
    .legend-swatch.outline,
    .legend-swatch.legal {
      background: transparent;
      border: 0;
    }
    .legend-swatch.parcel-line::after,
    .legend-swatch.outline::after,
    .legend-swatch.legal::after {
      content: "";
      position: absolute;
      left: 1px;
      right: 1px;
      top: 6px;
      border-top: 2px solid #111;
    }
    .legend-swatch.outline::after {
      border-top-color: #999;
    }
    .legend-swatch.legal::after {
      border-top-color: #f27fff;
      border-top-style: dashed;
    }
    .legend-swatch.parcel-label,
    .legend-swatch.text,
    .legend-swatch.text-muted,
    .legend-swatch.street-label,
    .legend-swatch.area-label {
      border: 0;
      background: transparent;
    }
    .legend-swatch.parcel-label::after,
    .legend-swatch.text::after,
    .legend-swatch.text-muted::after,
    .legend-swatch.street-label::after,
    .legend-swatch.area-label::after {
      content: "12";
      position: absolute;
      inset: -1px 0 0;
      color: #111;
      font-size: 11px;
      font-weight: 700;
      line-height: 14px;
      text-align: center;
    }
    .legend-swatch.text-muted::after {
      content: "II";
      color: #555;
    }
    .legend-swatch.street-label::after {
      content: "Aa";
      color: #333;
    }
    .legend-swatch.area-label::after {
      content: "VL";
      color: #777;
    }
    .legend-swatch.symbol {
      width: 14px;
      height: 14px;
      margin-left: 2px;
      border-radius: 999px;
      background: #fff;
      border: 2px solid #111;
    }
    #toolDockToggle {
      width: 100%;
      min-height: 34px;
      border: 0;
      border-radius: 999px;
      background: var(--ok-accent);
      color: #fff;
      font: inherit;
      font-weight: 700;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
    }
    #toolDockToggle span {
      display: none;
    }
    #toolDockToggle svg {
      width: 16px;
      height: 16px;
    }
    #toolDock[data-open="false"] .toolBar {
      display: none;
    }
    .toolBar {
      display: flex;
      flex-direction: column;
      gap: 5px;
      width: 100%;
      margin-top: 6px;
    }
    .toolbar-strip {
      display: flex;
      flex-direction: column;
      gap: 5px;
      flex-wrap: nowrap;
      overflow: visible;
      padding-bottom: 2px;
      max-width: 100%;
      align-items: stretch;
    }
    .toolbar-strip::-webkit-scrollbar {
      display: none;
    }
    .tool {
      border-radius: 999px;
      border: 1px solid #e8e5df;
      background: white;
      width: 40px;
      height: 40px;
      min-height: 40px;
      padding: 0;
      white-space: nowrap;
      font: inherit;
      cursor: pointer;
      font-weight: 500;
      color: #111713;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 6px;
      transition: border-color 120ms ease, background 120ms ease, color 120ms ease, box-shadow 120ms ease;
    }
    .tool span {
      display: none;
    }
    .hidden-tools {
      display: none;
    }
    .tool svg {
      width: 16px;
      height: 16px;
      stroke-width: 2;
      flex: 0 0 auto;
    }
    #toolMeasureArea svg,
    #toolErase svg {
      width: 20px;
      height: 20px;
    }
    #toolMeasureArea svg {
      width: 22px;
      height: 22px;
      stroke-width: 1.65;
    }
    #toolMapExport svg {
      width: 18px;
      height: 18px;
    }
    #exportPanel {
      left: 78px;
      top: 284px;
      width: 238px;
      padding: 12px;
      display: grid;
      gap: 10px;
      z-index: 16;
    }
    #exportPanel[hidden] {
      display: none;
    }
    .export-title {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      font-weight: 700;
      font-size: 13px;
      color: var(--ok-ink);
    }
    .export-mobile-row {
      display: none;
    }
    .export-summary {
      display: inline-flex;
      align-items: center;
      min-height: 34px;
      padding: 0 12px;
      border-radius: 8px;
      background: rgba(245, 242, 237, 0.9);
      color: #81786f;
      font-size: 13px;
      font-weight: 800;
      letter-spacing: -0.01em;
    }
    .export-settings-toggle {
      width: 52px;
      height: 52px;
      border: 0;
      border-radius: 999px;
      background: #f8f0e6;
      color: var(--ok-accent);
      box-shadow: 0 12px 28px rgba(15, 23, 42, 0.16);
      display: inline-flex;
      align-items: center;
      justify-content: center;
      cursor: pointer;
    }
    .export-settings-toggle svg {
      width: 25px;
      height: 25px;
      stroke-width: 2.5;
    }
    .export-close {
      width: 26px;
      height: 26px;
      border: 1px solid var(--ok-border);
      background: #fff;
      border-radius: 999px;
      cursor: pointer;
      font-size: 16px;
      line-height: 1;
      color: var(--ok-muted);
    }
    .export-help {
      margin: 0;
      font-size: 12px;
      line-height: 1.35;
      color: var(--ok-muted);
    }
    .export-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
    }
    .export-field {
      display: grid;
      gap: 4px;
      font-size: 11px;
      font-weight: 700;
      color: var(--ok-muted);
    }
    .export-field select {
      width: 100%;
      border: 1px solid var(--ok-border);
      border-radius: 10px;
      background: #fff;
      color: var(--ok-ink);
      padding: 8px 9px;
      font-size: 12px;
      font-weight: 700;
    }
    .export-actions {
      display: grid;
      gap: 7px;
    }
    .export-actions button {
      border: 1px solid var(--ok-border);
      border-radius: 12px;
      background: #fff;
      color: var(--ok-ink);
      padding: 9px 10px;
      font-size: 12px;
      font-weight: 700;
      cursor: pointer;
      text-align: left;
    }
    .export-actions button[data-primary="true"] {
      background: var(--ok-accent);
      border-color: var(--ok-accent);
      color: #fff;
      text-align: center;
    }
    .export-status {
      min-height: 16px;
      font-size: 11px;
      color: var(--ok-muted);
    }
    .export-selection-box {
      position: absolute;
      pointer-events: none;
      border: 2px solid var(--ok-accent);
      background: rgba(248, 109, 20, 0.10);
      box-shadow: 0 0 0 9999px rgba(15, 23, 42, 0.08);
      z-index: 12;
    }
    .maplibregl-canvas-container[data-export-crop="true"] canvas {
      cursor: crosshair !important;
    }
    .colorBar {
      display: flex;
      justify-content: center;
      padding-top: 3px;
    }
    .color-picker {
      width: 32px;
      height: 32px;
      border-radius: 999px;
      border: 1px solid var(--ok-border);
      box-shadow: 0 8px 18px rgba(15, 23, 42, 0.12);
      cursor: pointer;
      padding: 0;
      overflow: hidden;
      background: conic-gradient(#f00, #ff0, #0f0, #0ff, #00f, #f0f, #f00);
    }
    .color-picker::-webkit-color-swatch-wrapper {
      padding: 0;
    }
    .color-picker::-webkit-color-swatch {
      border: 2px solid #fff;
      border-radius: 999px;
    }
    .color-picker::-moz-color-swatch {
      border: 2px solid #fff;
      border-radius: 999px;
    }
    .tool:hover {
      border-color: rgba(248, 109, 20, 0.45);
      background: #fff8f3;
    }
    .tool[data-state=\"active\"] {
      background: var(--ok-accent);
      border-color: #f97316;
      color: #fff;
      box-shadow: 0 10px 24px rgba(248, 109, 20, 0.18);
    }
    .tool[data-compact=\"true\"] span {
      display: inline;
    }
    #searchPanel {
      left: 12px;
      top: 12px;
      width: min(320px, calc(100vw - 24px));
      padding: 0;
      transform: none;
      border: 0;
      border-radius: 0;
      background: transparent;
      box-shadow: none;
      backdrop-filter: none;
    }
    #searchPanel .panel-label {
      display: none;
    }
    #searchPanel .search-form {
      position: relative;
      padding: 0;
    }
    .search-icon {
      position: absolute;
      left: 12px;
      top: 50%;
      width: 16px;
      height: 16px;
      transform: translateY(-50%);
      color: var(--ok-muted);
      pointer-events: none;
      transition: color 120ms ease;
    }
    #searchPanel .search-form:focus-within .search-icon {
      color: var(--ok-accent);
    }
    #searchPanel input {
      min-width: 0;
      border-radius: 999px;
      border: 1px solid var(--ok-line);
      width: 100%;
      height: 40px;
      padding: 0 14px 0 40px;
      font: inherit;
      font-size: 16px;
      color: #111827;
      background: #fff;
      outline: none;
      box-shadow: 0 10px 25px rgba(17, 23, 19, 0.14);
    }
    #searchPanel input:focus {
      border-color: var(--ok-line);
    }
    #searchResults {
      position: absolute;
      left: 0;
      top: calc(100% + 8px);
      width: 100%;
      max-height: min(280px, calc(100vh - 210px));
      overflow: auto;
      border: 1px solid var(--ok-line);
      border-radius: 16px;
      background: #fff;
      box-shadow: 0 18px 45px rgba(17, 23, 19, 0.16);
      padding: 6px;
    }
    #searchResults[hidden] {
      display: none;
    }
    .search-result {
      border: 0;
      border-radius: 12px;
      display: block;
      width: 100%;
      text-align: left;
      padding: 10px 12px;
      font: inherit;
      background: transparent;
      cursor: pointer;
    }
    .search-result:first-child {
      border-top: 0;
    }
    .search-result:hover {
      background: var(--ok-accent-soft);
    }
    .search-title {
      margin: 0;
      font-weight: 700;
    }
    .search-meta {
      color: var(--ok-muted);
      font-size: 12px;
      margin-top: 2px;
    }
    #statusPanel {
      display: none;
    }
    #zoomReadout {
      position: absolute;
      left: 50%;
      bottom: 14px;
      transform: translateX(-50%);
      z-index: 6;
      min-width: 76px;
      padding: 6px 10px;
      border: 1px solid rgba(17, 23, 19, 0.12);
      border-radius: 999px;
      background: rgba(255, 255, 255, 0.88);
      box-shadow: 0 8px 22px rgba(15, 23, 42, 0.12);
      backdrop-filter: blur(8px);
      color: #111713;
      font: 700 12px/1.1 "IBM Plex Sans", Inter, system-ui, sans-serif;
      text-align: center;
      pointer-events: none;
      font-variant-numeric: tabular-nums;
    }
    .maplibregl-ctrl-bottom-left {
      bottom: 10px;
      left: 10px;
    }
    .maplibregl-ctrl-bottom-left .maplibregl-ctrl {
      border-radius: 8px;
      overflow: hidden;
      box-shadow: 0 8px 20px rgba(15, 23, 42, 0.12);
      background: rgba(255, 255, 255, 0.9);
    }
    .maplibregl-ctrl-bottom-left .maplibregl-ctrl button {
      width: 30px;
      height: 30px;
    }
    #statusRight:empty {
      display: none;
    }
    #sourcePanel {
      right: 0;
      bottom: 0;
      width: auto;
      padding: 0;
      overflow: visible;
      font-size: 12px;
      line-height: 1.35;
      border: 0;
      border-radius: 0;
      background: transparent;
      box-shadow: none;
      backdrop-filter: none;
      display: flex;
      flex-direction: row-reverse;
      align-items: center;
    }
    #sourceToggle {
      border: 0;
      width: 24px;
      height: 24px;
      padding: 0;
      border-radius: 4px 0 0 4px;
      background: rgba(255, 255, 255, 0.75);
      color: rgba(0, 0, 0, 0.75);
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      font: inherit;
      font-weight: 700;
      text-align: center;
      backdrop-filter: blur(2px);
      pointer-events: auto;
    }
    #sourceToggle svg {
      width: 14px;
      height: 14px;
      stroke-width: 2;
    }
    #sourceDetails[hidden] {
      display: none;
    }
    #sourcePanel[data-open="true"] #sourceToggle {
      border-radius: 4px 0 0 4px;
      padding: 0;
    }
    #sourceDetails {
      height: 24px;
      padding: 0 8px;
      max-height: none;
      overflow: hidden;
      background: rgba(255, 255, 255, 0.75);
      width: max-content;
      max-width: calc(100vw - 34px);
      backdrop-filter: blur(2px);
      display: flex;
      align-items: center;
      font-family: \"IBM Plex Sans\", sans-serif;
      font-size: 10px;
      line-height: 1;
      color: rgba(0, 0, 0, 0.75);
      white-space: nowrap;
    }
    #sourceTitle {
      display: none;
    }
    #sourceList {
      list-style: none;
      margin: 0;
      padding: 0;
      display: flex;
      gap: 4px;
      align-items: center;
      overflow-x: auto;
      white-space: nowrap;
      color: rgba(0, 0, 0, 0.75);
      scrollbar-width: none;
    }
    #sourceList::-webkit-scrollbar {
      display: none;
    }
    .source-item {
      padding: 0;
      border-radius: 0;
      border: 0;
      background: transparent;
      color: inherit;
    }
    .source-name {
      display: none;
    }
    .source-line {
      font-size: 10px;
      color: inherit;
    }
    .source-line a {
      color: inherit;
      text-decoration: underline;
    }
    .source-line + .source-line {
      margin-top: 2px;
    }
    #selectionPanel {
      right: 12px;
      top: 118px;
      width: min(340px, calc(100vw - 24px));
      max-height: calc(100vh - 192px);
      overflow: hidden;
      color: #111713;
      border-radius: 16px;
      display: flex;
      flex-direction: column;
    }
    #selectionPanel[hidden] {
      display: none;
    }
    .selection-head {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 10px 12px 8px;
      border-bottom: 1px solid #f1eee8;
      font-size: 13px;
      font-weight: 600;
      gap: 10px;
      flex: 0 0 auto;
    }
    .selection-close {
      border: 0;
      background: transparent;
      font-size: 20px;
      line-height: 1;
      cursor: pointer;
      color: #334155;
    }
    .selection-body {
      padding: 8px;
      overflow-y: scroll;
      overflow-x: auto;
      flex: 1 1 auto;
      scrollbar-gutter: stable;
      scrollbar-width: thin;
      scrollbar-color: #c9c1b8 transparent;
      min-height: 0;
    }
    .selection-body::-webkit-scrollbar {
      width: 9px;
      height: 9px;
    }
    .selection-body::-webkit-scrollbar-track {
      background: transparent;
    }
    .selection-body::-webkit-scrollbar-thumb {
      background: #c9c1b8;
      border-radius: 999px;
      border: 2px solid rgba(255, 255, 255, 0.9);
    }
    .selection-table {
      margin-right: 2px;
    }
    .selection-table {
      width: 100%;
      border-collapse: collapse;
      font-size: 12.5px;
      line-height: 1.25;
      background: #fff;
      border: 1px solid #eee8df;
      border-radius: 10px;
      overflow: hidden;
    }
    .selection-table th,
    .selection-table td {
      border-bottom: 1px solid #eee8df;
      padding: 7px 8px;
      text-align: left;
      vertical-align: top;
    }
    .selection-table th {
      position: sticky;
      top: 0;
      z-index: 1;
      background: #f7f4ef;
      color: #667085;
      font-weight: 700;
      white-space: nowrap;
    }
    .selection-table tr:last-child td {
      border-bottom: 0;
    }
    .selection-table tfoot td {
      background: #fbfaf7;
      font-weight: 700;
    }
    .selection-summary {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-bottom: 8px;
    }
    .selection-chip {
      display: inline-flex;
      align-items: center;
      min-height: 22px;
      border-radius: 999px;
      padding: 0 8px;
      background: #f7f4ef;
      color: #4b5563;
      font-size: 11px;
      font-weight: 600;
      white-space: nowrap;
    }
    .selection-muted {
      color: #667085;
    }
    #status {
      left: 12px;
      top: 12px;
      z-index: 5;
      padding: 8px 10px;
      border-radius: 10px;
      border: 1px solid rgba(15, 23, 42, 0.18);
      box-shadow: 0 10px 30px rgba(15, 23, 42, 0.2);
      max-width: min(430px, calc(100vw - 24px));
      background: rgba(255, 255, 255, 0.95);
      pointer-events: none;
    }
    #status[data-ready=\"true\"] {
      display: none;
    }
    @media (max-width: 860px) {
      #toolDock {
        left: 8px;
        top: 64px;
        width: 52px;
      }
      .toolbar-strip {
        width: 100%;
      }
      .toolBar {
        gap: 6px;
      }
      .tool {
        font-size: 12px;
        width: 40px;
        height: 40px;
        min-height: 40px;
      }
      #searchPanel {
        left: 8px;
        right: 8px;
        top: 12px;
        width: min(320px, calc(100vw - 64px));
        transform: none;
      }
      #layerSwitch {
        right: 8px;
        top: 12px;
      }
      #sourcePanel,
      #selectionPanel {
        left: 8px;
        right: 8px;
      }
      #selectionPanel {
        left: 8px;
        right: 8px;
        top: auto;
        bottom: 8px;
        width: auto;
        max-height: min(44vh, 340px);
        border-radius: 18px;
        z-index: 5;
      }
      #sourcePanel {
        left: auto;
        right: 0;
        bottom: 0;
        width: auto;
      }
      #exportPanel {
        position: fixed;
        left: 0;
        right: 0;
        top: auto;
        bottom: 0;
        width: auto;
        padding: 18px 16px calc(18px + env(safe-area-inset-bottom, 0px));
        border-radius: 22px 22px 0 0;
        border-left: 0;
        border-right: 0;
        border-bottom: 0;
        background: rgba(255, 255, 255, 0.96);
        box-shadow: 0 -18px 42px rgba(15, 23, 42, 0.12);
        z-index: 20;
      }
      #exportPanel .export-title {
        display: none;
      }
      #exportPanel .export-mobile-row {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 14px;
      }
      #exportPanel .export-grid {
        display: none;
        grid-template-columns: 1fr 1fr;
        padding-top: 8px;
      }
      #exportPanel[data-settings-open="true"] .export-grid {
        display: grid;
      }
      #exportPanel .export-actions button[data-primary="true"] {
        min-height: 58px;
        border-radius: 8px;
        font-size: 20px;
        text-align: center;
      }
      #exportPanel .export-status {
        display: none;
      }
      #sourceDetails {
        max-height: none;
        max-width: calc(100vw - 34px);
      }
      .source-item {
        padding: 0;
      }
      .maplibregl-ctrl-bottom-left {
        display: none;
      }
    }
  </style>
</head>
<body>
  <img id=\"mapBootstrapBackdrop\" class=\"map-bootstrap-backdrop\" alt=\"\" loading=\"eager\" decoding=\"async\">
  <img id=\"mapFrameFallback\" class=\"map-frame-fallback\" alt=\"\">
  <div id=\"map\"></div>
  <div id=\"zoomReadout\" aria-live=\"polite\">Zoom --</div>
  <aside id=\"toolDock\" class=\"floating\" data-open=\"true\">
    <button id=\"toolDockToggle\" type=\"button\" aria-expanded=\"true\">
      <svg viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\"><path d=\"M4 7h16\"/><path d=\"M4 12h16\"/><path d=\"M4 17h16\"/></svg>
      <span>Werkzeuge</span>
    </button>
      <div class=\"toolBar\">
      <div class=\"toolbar-strip\">
        <button id=\"toolMeasureArea\" class=\"tool\" data-kind=\"mode\" data-state=\"off\" type=\"button\" title=\"Messen: Seiten, Umfang und Fläche\">
          <svg viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\"><rect x=\"5\" y=\"9\" width=\"14\" height=\"6\" rx=\"1.5\" transform=\"rotate(-35 12 12)\"/><path d=\"M8.2 12.8l1.2 1.7\"/><path d=\"M10.8 11l1.2 1.7\"/><path d=\"M13.4 9.2l1.2 1.7\"/><path d=\"M16 7.4l1.2 1.7\"/></svg><span>Messen</span>
        </button>
        <button id=\"toolPin\" class=\"tool\" data-kind=\"mode\" data-state=\"off\" type=\"button\" title=\"Punkt setzen\">
          <svg viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\"><path d=\"M12 21s6-5.4 6-11a6 6 0 10-12 0c0 5.6 6 11 6 11z\"/><circle cx=\"12\" cy=\"10\" r=\"2\"/></svg><span>Punkt</span>
        </button>
        <button id=\"toolDrawLine\" class=\"tool\" data-kind=\"mode\" data-state=\"off\" type=\"button\" title=\"Linie markieren\">
          <svg viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\"><path d=\"M5 19L10 8l5 5 4-8\"/><circle cx=\"5\" cy=\"19\" r=\"1.4\"/><circle cx=\"10\" cy=\"8\" r=\"1.4\"/><circle cx=\"15\" cy=\"13\" r=\"1.4\"/><circle cx=\"19\" cy=\"5\" r=\"1.4\"/></svg><span>Linie</span>
        </button>
        <button id=\"toolDrawPolygon\" class=\"tool\" data-kind=\"mode\" data-state=\"off\" type=\"button\" title=\"Polygon markieren\">
          <svg viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\"><path d=\"M7 4l11 3 2 10-8 4-8-6z\"/><circle cx=\"7\" cy=\"4\" r=\"1.4\"/><circle cx=\"18\" cy=\"7\" r=\"1.4\"/><circle cx=\"20\" cy=\"17\" r=\"1.4\"/><circle cx=\"12\" cy=\"21\" r=\"1.4\"/><circle cx=\"4\" cy=\"15\" r=\"1.4\"/></svg><span>Polygon</span>
        </button>
        <button id=\"toolErase\" class=\"tool\" data-kind=\"mode\" data-state=\"off\" type=\"button\" title=\"Einzelne Markierung löschen\">
          <svg viewBox=\"0 0 28 28\" fill=\"none\" stroke=\"currentColor\"><g transform=\"rotate(-45 14 14)\"><rect x=\"8\" y=\"9\" width=\"14\" height=\"8\" rx=\"1.5\"/><path d=\"M15 9v8\"/></g></svg><span>Radieren</span>
        </button>
        <button id="toolClear" class="tool" data-kind="action" type="button" title="Auswahl und Messung löschen">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M19 6l-1 15H6L5 6"/></svg><span>Löschen</span>
        </button>
        <button id=\"toolMapExport\" class=\"tool\" data-kind=\"action\" type=\"button\" title=\"Kartenausschnitt exportieren\">
          <svg viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\"><path d=\"M12 3v10\"/><path d=\"M8 9l4 4 4-4\"/><path d=\"M5 15v4h14v-4\"/></svg><span>Export</span>
        </button>
      </div>
      <div class=\"colorBar\" aria-label=\"Markierungsfarbe\">
        <input id=\"annotationColorPicker\" class=\"color-picker\" type=\"color\" value=\"#f86d14\" title=\"Markierungsfarbe wählen\" aria-label=\"Markierungsfarbe wählen\" />
      </div>
    </div>
    <div class=\"hidden-tools\" aria-hidden=\"true\">
      <button id=\"toolSelect\" data-state=\"active\" type=\"button\"></button>
      <button id=\"toolMeasureLine\" data-state=\"off\" type=\"button\"></button>
      <button id=\"toolMeasureRadius\" data-state=\"off\" type=\"button\"></button>
      <button id=\"toolMeasureUndo\" type=\"button\"></button>
      <button id=\"toolCopyCursor\" type=\"button\"></button>
      <button id=\"toolCopyCoords\" type=\"button\"></button>
      <button id=\"toolSelectionReport\" type=\"button\"></button>
      <button id=\"toolZoomSelection\" type=\"button\"></button>
      <button id=\"toolExportCsv\" type=\"button\"></button>
      <button id=\"toolExport\" type=\"button\"></button>
      <button id=\"toolHome\" type=\"button\"></button>
      <button id=\"toolCopy\" type=\"button\"></button>
      <button id=\"toolExportView\" type=\"button\"></button>
      <button id=\"toolCopyViewport\" type=\"button\"></button>
    </div>
  </aside>
  <section id=\"exportPanel\" class=\"floating\" hidden>
    <div class=\"export-title\">
      <span>Karte exportieren</span>
      <button id=\"exportClose\" class=\"export-close\" type=\"button\" aria-label=\"Export schließen\">×</button>
    </div>
    <div class=\"export-mobile-row\">
      <div id=\"exportSummary\" class=\"export-summary\">A4 · 1:1000 · PDF</div>
      <button id=\"exportSettingsToggle\" class=\"export-settings-toggle\" type=\"button\" aria-label=\"Export-Einstellungen\" aria-expanded=\"false\">
        <svg viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\"><path d=\"M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7z\"/><path d=\"M19.4 15a1.8 1.8 0 0 0 .36 1.98l.04.04a2.1 2.1 0 0 1-2.97 2.97l-.04-.04a1.8 1.8 0 0 0-1.98-.36 1.8 1.8 0 0 0-1.1 1.66V21.4a2.1 2.1 0 0 1-4.2 0v-.15a1.8 1.8 0 0 0-1.1-1.66 1.8 1.8 0 0 0-1.98.36l-.04.04a2.1 2.1 0 0 1-2.97-2.97l.04-.04A1.8 1.8 0 0 0 3.6 15a1.8 1.8 0 0 0-1.66-1.1H1.8a2.1 2.1 0 0 1 0-4.2h.15A1.8 1.8 0 0 0 3.6 8a1.8 1.8 0 0 0-.36-1.98l-.04-.04a2.1 2.1 0 0 1 2.97-2.97l.04.04A1.8 1.8 0 0 0 8.2 3.4a1.8 1.8 0 0 0 1.1-1.66V1.6a2.1 2.1 0 0 1 4.2 0v.15a1.8 1.8 0 0 0 1.1 1.66 1.8 1.8 0 0 0 1.98-.36l.04-.04a2.1 2.1 0 0 1 2.97 2.97l-.04.04A1.8 1.8 0 0 0 19.4 8a1.8 1.8 0 0 0 1.66 1.1h.15a2.1 2.1 0 0 1 0 4.2h-.15A1.8 1.8 0 0 0 19.4 15z\"/></svg>
      </button>
    </div>
    <div class=\"export-grid\">
      <label class=\"export-field\">Format
        <select id=\"exportPaper\">
          <option value=\"a4\">A4</option>
          <option value=\"a3\">A3</option>
        </select>
      </label>
      <label class=\"export-field\">Maßstab
        <select id=\"exportScale\">
          <option value=\"500\">1:500</option>
          <option value=\"1000\" selected>1:1000</option>
          <option value=\"2000\">1:2000</option>
        </select>
      </label>
      <label class=\"export-field\">Ausrichtung
        <select id=\"exportOrientation\">
          <option value=\"portrait\" selected>Hochformat</option>
          <option value=\"landscape\">Querformat</option>
        </select>
      </label>
      <label class=\"export-field\">Datei
        <select id=\"exportOutput\">
          <option value=\"png\">PNG</option>
          <option value=\"pdf\">PDF</option>
        </select>
      </label>
    </div>
    <div class=\"export-actions\">
      <button id=\"exportDownloadPng\" type=\"button\" data-primary=\"true\">Export herunterladen</button>
    </div>
    <div id=\"exportStatus\" class=\"export-status\"></div>
  </section>
  <section id=\"searchPanel\" class=\"floating\">
    <div class=\"panel-label\">Suche</div>
    <form id=\"searchForm\" class=\"search-form\">
      <svg class=\"search-icon\" viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\"><path d=\"m21 21-4.34-4.34\"/><circle cx=\"11\" cy=\"11\" r=\"8\"/></svg>
      <input id=\"searchInput\" type=\"search\" placeholder=\"Straße, Ort oder Flurstück suchen...\" autocomplete=\"off\" autocapitalize=\"off\" autocorrect=\"off\" spellcheck=\"false\" inputmode=\"search\" data-1p-ignore=\"true\" data-lpignore=\"true\" data-form-type=\"other\">
    </form>
    <div id=\"searchResults\" hidden></div>
  </section>
  <section id=\"layerSwitch\" class=\"floating\" aria-label=\"Kartenlayer\">
    <button id=\"layerToggle\" type=\"button\" aria-label=\"Kartenlayer\" aria-expanded=\"false\">
      <svg viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\"><path d=\"M12 3l8 4-8 4-8-4z\"/><path d=\"M4 12l8 4 8-4\"/><path d=\"M4 17l8 4 8-4\"/></svg>
    </button>
    <div id=\"layerMenu\" hidden>
      <div class=\"layer-menu-title\">Layer</div>
      <details class=\"layer-group\" open>
        <summary><label class=\"layer-group-check\"><input type=\"checkbox\" data-layer-group=\"base\"><span>Basiskarte</span></label></summary>
      </details>
      <details class=\"layer-group\" open>
        <summary><label class=\"layer-group-check\"><input type=\"checkbox\" data-layer-group=\"aerial\"><span>Luftbild</span></label></summary>
        <label class=\"layer-check\"><input type=\"checkbox\" data-layer-setting=\"aerial\"><span class=\"legend-swatch aerial\"></span><span>Luftbild</span></label>
      </details>
      <details class=\"layer-group\" open>
        <summary><label class=\"layer-group-check\"><input type=\"checkbox\" data-layer-group=\"surfaces\"><span>ALKIS Flächen</span></label></summary>
        <label class=\"layer-check\"><input type=\"checkbox\" data-layer-setting=\"surfaceFills\"><span class=\"legend-swatch fill\"></span><span>Flächenfüllungen</span></label>
        <label class=\"layer-check\"><input type=\"checkbox\" data-layer-setting=\"buildings\"><span class=\"legend-swatch building\"></span><span>Gebäude</span></label>
      </details>
      <details class=\"layer-group\" open>
        <summary><label class=\"layer-group-check\"><input type=\"checkbox\" data-layer-group=\"lines\"><span>ALKIS Linien</span></label></summary>
        <label class=\"layer-check\"><input type=\"checkbox\" data-layer-setting=\"parcelLines\"><span class=\"legend-swatch parcel-line\"></span><span>Flurstücksgrenzen</span></label>
        <label class=\"layer-check\"><input type=\"checkbox\" data-layer-setting=\"surfaceOutlines\"><span class=\"legend-swatch outline\"></span><span>Flächenoutlines</span></label>
        <label class=\"layer-check\"><input type=\"checkbox\" data-layer-setting=\"legalLines\"><span class=\"legend-swatch legal\"></span><span>Rechtliche Festlegungen & Grenzen</span></label>
      </details>
      <details class=\"layer-group\" open>
        <summary><label class=\"layer-group-check\"><input type=\"checkbox\" data-layer-group=\"labels\"><span>Beschriftungen & Punkte</span></label></summary>
        <label class=\"layer-check\"><input type=\"checkbox\" data-layer-setting=\"parcelLabels\"><span class=\"legend-swatch parcel-label\"></span><span>Flurstücksbeschriftungen</span></label>
        <label class=\"layer-check\"><input type=\"checkbox\" data-layer-setting=\"houseNumbers\"><span class=\"legend-swatch text\"></span><span>Hausnummern</span></label>
        <label class=\"layer-check\"><input type=\"checkbox\" data-layer-setting=\"buildingLabels\"><span class=\"legend-swatch text-muted\"></span><span>Gebäudebeschriftungen</span></label>
        <label class=\"layer-check\"><input type=\"checkbox\" data-layer-setting=\"streetNames\"><span class=\"legend-swatch street-label\"></span><span>Straßennamen</span></label>
        <label class=\"layer-check\"><input type=\"checkbox\" data-layer-setting=\"surfaceLabels\"><span class=\"legend-swatch area-label\"></span><span>Flächenlabels</span></label>
        <label class=\"layer-check\"><input type=\"checkbox\" data-layer-setting=\"symbols\"><span class=\"legend-swatch symbol\"></span><span>Weitere Signaturen</span></label>
      </details>
    </div>
  </section>
  <section id=\"sourcePanel\" class=\"floating\" data-open=\"false\">
    <button id=\"sourceToggle\" type=\"button\" aria-label=\"Map Information\" title=\"Map Information\" aria-expanded=\"false\">
      <svg viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\"><circle cx=\"12\" cy=\"12\" r=\"10\"/><path d=\"M12 16v-4\"/><path d=\"M12 8h.01\"/></svg>
    </button>
    <div id=\"sourceDetails\" hidden>
      <ul id=\"sourceList\"></ul>
    </div>
  </section>
  <section id=\"statusPanel\" class=\"floating\">
    <div id=\"statusLeft\"></div>
    <div id=\"statusCenter\"></div>
    <div id=\"statusRight\"></div>
  </section>
  <section id=\"selectionPanel\" class=\"floating\" hidden>
    <div class=\"selection-head\"> <span>Auswahl</span> <button id=\"selectionClose\" class=\"selection-close\" aria-label=\"Auswahl schließen\">&times;</button></div>
    <div id=\"selectionBody\" class=\"selection-body\"></div>
  </section>
  <div id=\"status\" class=\"floating\" data-ready=\"false\">Karte wird geladen …</div>
  <script src=\"https://unpkg.com/maplibre-gl@5.14.0/dist/maplibre-gl.js\"></script>
  <script>
    const statusEl = document.getElementById(\"status\");
    const zoomReadout = document.getElementById(\"zoomReadout\");
    const statusLeft = document.getElementById(\"statusLeft\");
    const statusCenter = document.getElementById(\"statusCenter\");
    const statusRight = document.getElementById(\"statusRight\");
    const sourcePanel = document.getElementById(\"sourcePanel\");
    const sourceToggle = document.getElementById(\"sourceToggle\");
    const sourceDetails = document.getElementById(\"sourceDetails\");
    const sourceTitle = document.getElementById(\"sourceTitle\");
    const sourceList = document.getElementById(\"sourceList\");
    const selectionPanel = document.getElementById(\"selectionPanel\");
    const selectionBody = document.getElementById(\"selectionBody\");
    const selectionClose = document.getElementById(\"selectionClose\");
    const searchForm = document.getElementById(\"searchForm\");
    const searchInput = document.getElementById(\"searchInput\");
    const searchResults = document.getElementById(\"searchResults\");
    const bootstrapBackdrop = document.getElementById(\"mapBootstrapBackdrop\");
    const bootstrapStates = {
      saarland: { bounds: [6.355591, 49.111636, 7.404785, 49.639413], src: "/bootstrap/saarland.webp?v=saarland-bootstrap-overview-v1" },
    };
    function pickBootstrapState() {
      if (!map) return null;
      const center = map.getCenter();
      for (const cfg of Object.values(bootstrapStates)) {
        const b = cfg.bounds;
        if (center.lng >= b[0] && center.lng <= b[2] && center.lat >= b[1] && center.lat <= b[3]) return cfg;
      }
      return null;
    }
    function updateBootstrapBackdrop() {
      if (!bootstrapBackdrop || !map) return;
      const cfg = pickBootstrapState();
      if (!cfg || map.getZoom() > 14.5) {
        bootstrapBackdrop.classList.remove("is-visible");
        return;
      }
      if (bootstrapBackdrop.getAttribute("src") !== cfg.src) bootstrapBackdrop.src = cfg.src;
      bootstrapBackdrop.classList.add("is-visible");
    }

    const mapFrameFallback = document.getElementById(\"mapFrameFallback\");
    const isTouchLikeDevice = Boolean(
      (navigator.maxTouchPoints && navigator.maxTouchPoints > 0) ||
      (window.matchMedia && window.matchMedia("(pointer: coarse)").matches)
    );
    const layerToggle = document.getElementById("layerToggle");
    const layerMenu = document.getElementById("layerMenu");
    const layerSettingInputs = Array.from(document.querySelectorAll("[data-layer-setting]"));
    const layerGroupInputs = Array.from(document.querySelectorAll("[data-layer-group]"));
    const toolDock = document.getElementById(\"toolDock\");
    const toolDockToggle = document.getElementById(\"toolDockToggle\");
    const toolSelect = document.getElementById(\"toolSelect\");
    const toolMeasureLine = document.getElementById(\"toolMeasureLine\");
    const toolMeasureArea = document.getElementById(\"toolMeasureArea\");
    const toolDrawLine = document.getElementById("toolDrawLine");
    const toolDrawPolygon = document.getElementById("toolDrawPolygon");
    const toolErase = document.getElementById("toolErase");
    const toolMeasureRadius = document.getElementById(\"toolMeasureRadius\");
    const toolMeasureUndo = document.getElementById(\"toolMeasureUndo\");
    const toolPin = document.getElementById(\"toolPin\");
    const toolCopyCoords = document.getElementById(\"toolCopyCoords\");
    const toolExport = document.getElementById(\"toolExport\");
    const toolExportCsv = document.getElementById(\"toolExportCsv\");
    const toolExportView = document.getElementById(\"toolExportView\");
    const toolCopyViewport = document.getElementById(\"toolCopyViewport\");
    const toolCopyCursor = document.getElementById(\"toolCopyCursor\");
    const toolSelectionReport = document.getElementById(\"toolSelectionReport\");
    const toolZoomSelection = document.getElementById(\"toolZoomSelection\");
    const toolClear = document.getElementById(\"toolClear\");
    const toolHome = document.getElementById(\"toolHome\");
    const toolCopy = document.getElementById(\"toolCopy\");
    const toolMapExport = document.getElementById("toolMapExport");
    const exportPanel = document.getElementById("exportPanel");
    const exportClose = document.getElementById("exportClose");
    const exportSelectArea = document.getElementById("exportSelectArea");
    const exportUseView = document.getElementById("exportUseView");
    const exportDownloadPng = document.getElementById("exportDownloadPng");
    const exportPaper = document.getElementById("exportPaper");
    const exportScale = document.getElementById("exportScale");
    const exportOrientation = document.getElementById("exportOrientation");
    const exportOutput = document.getElementById("exportOutput");
    const exportSummary = document.getElementById("exportSummary");
    const exportSettingsToggle = document.getElementById("exportSettingsToggle");
    const exportStatus = document.getElementById("exportStatus");
    const annotationColorPicker = document.getElementById("annotationColorPicker");

    const featureUrl = __FEATURE_URL__;
    const searchUrl = __SEARCH_URL__;
    const datasetsUrl = __DATASETS_URL__;
    const metadataUrl = __METADATA_URL__;

    const selectedParcels = new Map();
    const selectedBuildings = new Map();
    let map;
    let lastCursorLngLat = null;
    let searchTimer = 0;
    let searchAbort = null;
    let lastFrameUrl = "";
    let captureFrameTimer = 0;
    let fallbackHideTimer = 0;
    let activeTool = \"none\";
    let measurePoints = [];
    let areaPoints = [];
    let radiusPoints = [];
    let pinnedPoints = [];
    let annotations = [];
    let annotationPoints = [];
    let annotationColor = "#f86d14";
    let dragAnnotationVertex = null;
    let dragMeasureVertex = null;
    let dragPin = false;
    let pinMoved = false;
    let eraseDragging = false;
    let suppressNextMapClick = false;
    let spacePanActive = false;
    let longPressTimer = 0;
    let longPressStart = null;
    let baseLayerMode = "custom";
    let exportCropMode = false;
    let exportCropStart = null;
    let exportCropRect = null;
    let exportSelectionBox = null;
    const baseLayerVisibilities = new Map();
    const baseLayerFilters = new Map();
    const layerSettings = {
      basemap: true,
      aerial: false,
      surfaceFills: true,
      buildings: true,
      parcelLines: true,
      surfaceOutlines: true,
      legalLines: true,
      parcelLabels: true,
      houseNumbers: true,
      buildingLabels: true,
      streetNames: true,
      surfaceLabels: true,
      symbols: true,
    };
    const layerSettingGroups = {
      base: ["basemap"],
      aerial: ["aerial"],
      surfaces: ["surfaceFills", "buildings"],
      lines: ["parcelLines", "surfaceOutlines", "legalLines"],
      labels: ["parcelLabels", "houseNumbers", "buildingLabels", "streetNames", "surfaceLabels", "symbols"],
    };
    window.__okLayerSettings = layerSettings;
    let activeStateSlugs = new Set();
    const stateMetadata = new Map();
    const STATE_CENTERS = __STATE_CENTERS__;
    const LUFTBILD_WMS = {
      "baden-wurttemberg": { url: "https://owsproxy.lgl-bw.de/owsproxy/ows/WMS_LGL-BW_ATKIS_DOP_20_C", layer: "IMAGES_DOP_20_RGB", format: "image/png", version: "1.3.0" },
      "berlin": { url: "https://isk.geobasis-bb.de/mapproxy/dop20c/service/wms", layer: "bebb_dop20c", format: "image/png" },
      "brandenburg": { url: "https://isk.geobasis-bb.de/mapproxy/dop20c/service/wms", layer: "bebb_dop20c", format: "image/png" },
      "bremen": { url: "https://geodienste.bremen.de/wms_dop20_2023", layer: "DOP20_2023_HB", format: "image/png" },
      "hamburg": { url: "https://geodienste.hamburg.de/wms_dop_zeitreihe_belaubt", layer: "dop_zeitreihe_belaubt", format: "image/png" },
      "hessen": { url: "https://www.gds-srv.hessen.de/cgi-bin/lika-services/ogc-free-images.ows", layer: "he_dop20_rgb", format: "image/png" },
      "mecklenburg-vorpommern": { url: "https://www.geodaten-mv.de/dienste/adv_dop", layer: "mv_dop", format: "image/png" },
      "niedersachsen": { url: "https://opendata.lgln.niedersachsen.de/doorman/noauth/dop_wms", layer: "ni_dop20", format: "image/png" },
      "nordrhein-westfalen": { url: "https://www.wms.nrw.de/geobasis/wms_nw_dop", layer: "nw_dop_rgb", format: "image/png" },
      "rheinland-pfalz": { url: "https://geo4.service24.rlp.de/wms/rp_dop20.fcgi", layer: "rp_dop20", format: "image/png" },
      "saarland": { url: "https://geoportal.saarland.de/freewms/dop2020", layer: "sl_dop2020", format: "image/png" },
      "sachsen": { url: "https://geodienste.sachsen.de/wms_geosn_dop-rgb/guest", layer: "sn_dop_020", format: "image/png" },
      "schleswig-holstein": { url: "https://dienste.gdi-sh.de/WMS_SH_DOP20col_OpenGBD", layer: "sh_dop20_rgb", format: "image/png" },
      "thueringen": { url: "https://www.geoproxy.geoportal-th.de/geoproxy/services/DOP20", layer: "th_dop", format: "image/png" },
    };
    const ERASER_CURSOR = `url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='28' height='28' viewBox='0 0 28 28'%3E%3Cg transform='rotate(-45 14 14)'%3E%3Crect x='8' y='9' width='14' height='8' rx='1.5' fill='%23ffffff' stroke='%231f2937' stroke-width='2'/%3E%3Cpath d='M15 9v8' stroke='%231f2937' stroke-width='2'/%3E%3C/g%3E%3C/svg%3E") 8 20, auto`;
    sourcePanel.dataset.open = "false";
    sourceDetails.hidden = true;
    sourceToggle.setAttribute("aria-expanded", "false");

    function setStatus(message) {
      statusEl.textContent = message;
      statusEl.dataset.ready = \"false\";
    }

    function showFrameFallback() {
      if (isTouchLikeDevice || !lastFrameUrl || !mapFrameFallback) return;
      window.clearTimeout(fallbackHideTimer);
      mapFrameFallback.classList.add("is-visible");
    }

    function hideFrameFallbackSoon() {
      if (isTouchLikeDevice || !mapFrameFallback) return;
      window.clearTimeout(fallbackHideTimer);
      fallbackHideTimer = window.setTimeout(() => {
        mapFrameFallback.classList.remove("is-visible");
      }, 180);
    }

    function captureStableFrame() {
      if (isTouchLikeDevice || !map || !mapFrameFallback) return;
      window.clearTimeout(captureFrameTimer);
      captureFrameTimer = window.setTimeout(() => {
        try {
          const canvas = map.getCanvas();
          if (!canvas || canvas.width < 32 || canvas.height < 32) return;
          const url = canvas.toDataURL("image/jpeg", 0.54);
          if (url && url.length > 256) {
            lastFrameUrl = url;
            mapFrameFallback.src = url;
          }
        } catch (_error) {
          // Canvas capture can fail if a future source is not CORS-clean.
        }
      }, 80);
    }

    function setExportStatus(message) {
      if (exportStatus) exportStatus.textContent = message || "";
    }

    function updateExportSummary() {
      if (!exportSummary) return;
      exportSummary.textContent = `${String(exportPaper?.value || "a4").toUpperCase()} · 1:${exportScale?.value || "1000"} · ${String(exportOutput?.value || "png").toUpperCase()}`;
    }

    function setExportSettingsOpen(open) {
      exportPanel.dataset.settingsOpen = open ? "true" : "false";
      exportSettingsToggle?.setAttribute("aria-expanded", open ? "true" : "false");
    }

    function ensureExportSelectionBox() {
      if (exportSelectionBox || !map) return exportSelectionBox;
      exportSelectionBox = document.createElement("div");
      exportSelectionBox.className = "export-selection-box";
      exportSelectionBox.hidden = true;
      map.getContainer().appendChild(exportSelectionBox);
      return exportSelectionBox;
    }

    function exportAspectRatio() {
      const paper = exportPaper?.value || "a4";
      const sizes = {
        a4: [210, 297],
        a3: [297, 420],
      };
      let [paperW, paperH] = sizes[paper] || sizes.a4;
      if ((exportOrientation?.value || "portrait") === "landscape") {
        [paperW, paperH] = [paperH, paperW];
      }
      return paperW / paperH;
    }

    function exportGroundSizeMeters() {
      const scale = Number(exportScale?.value || 1000);
      const mapArea = exportPaperSizeMillimeters();
      return {
        width: mapArea.width / 1000 * scale,
        height: mapArea.height / 1000 * scale,
      };
    }

    function exportPaperSizeMillimeters() {
      const paper = exportPaper?.value || "a4";
      const sizes = {
        a4: [210, 297],
        a3: [297, 420],
      };
      let [paperW, paperH] = sizes[paper] || sizes.a4;
      if ((exportOrientation?.value || "portrait") === "landscape") {
        [paperW, paperH] = [paperH, paperW];
      }
      return { width: paperW, height: paperH };
    }

    function officialLayoutInches() {
      const paperSize = exportPaperSizeMillimeters();
      const pageW = paperSize.width / 25.4;
      const pageH = paperSize.height / 25.4;
      const landscape = (exportOrientation?.value || "portrait") === "landscape";
      const marginTop = 0.3;
      const marginBottom = 0.3;
      const marginLeft = 0.7;
      const marginRight = 0.5;
      const headerHeight = landscape ? 0.5 : 0.8;
      const footerHeight = landscape ? 0.5 : 0.6;
      const padding = 0.15;
      const mapW = pageW - marginLeft - marginRight;
      const mapH = pageH - marginTop - marginBottom - headerHeight - footerHeight - (2 * padding);
      return { pageW, pageH, marginTop, marginBottom, marginLeft, marginRight, headerHeight, footerHeight, padding, mapW, mapH };
    }

    function exportMapAreaSizeMillimeters() {
      const layout = officialLayoutInches();
      return {
        width: layout.mapW * 25.4,
        height: layout.mapH * 25.4,
      };
    }

    function exportPixelSize() {
      const dpi = 200;
      const paperSize = exportPaperSizeMillimeters();
      return {
        width: Math.round(paperSize.width / 25.4 * dpi),
        height: Math.round(paperSize.height / 25.4 * dpi),
      };
    }

    function mapMetersPerCssPixel() {
      const center = map.getCenter();
      const latitudeFactor = Math.max(0.08, Math.cos(center.lat * Math.PI / 180));
      const worldMeters = 40075016.68557849;
      const worldCssPixels = 512 * Math.pow(2, map.getZoom());
      return worldMeters * latitudeFactor / worldCssPixels;
    }

    function exportRenderZoom() {
      const center = map.getCenter();
      const latitudeFactor = Math.max(0.08, Math.cos(center.lat * Math.PI / 180));
      const worldMeters = 40075016.68557849;
      const groundSize = exportGroundSizeMeters();
      const pixelSize = exportPixelSize();
      const metersPerPixel = groundSize.width / pixelSize.width;
      const zoom = Math.log2((worldMeters * latitudeFactor) / (metersPerPixel * 512));
      const minZoom = typeof map.getMinZoom === "function" ? map.getMinZoom() : 0;
      const maxZoom = typeof map.getMaxZoom === "function" ? map.getMaxZoom() : 22;
      return Math.max(minZoom, Math.min(maxZoom, zoom));
    }

    function centeredExportRect() {
      const containerRect = map.getContainer().getBoundingClientRect();
      const groundSize = exportGroundSizeMeters();
      const metersPerPixel = mapMetersPerCssPixel();
      const width = groundSize.width / metersPerPixel;
      const height = groundSize.height / metersPerPixel;
      return {
        x: (containerRect.width - width) / 2,
        y: (containerRect.height - height) / 2,
        width,
        height,
      };
    }

    function constrainedExportRect(start, end) {
      const containerRect = map.getContainer().getBoundingClientRect();
      const ratio = exportAspectRatio();
      const directionX = end.x < start.x ? -1 : 1;
      const directionY = end.y < start.y ? -1 : 1;
      const maxWidth = Math.max(1, directionX < 0 ? start.x : containerRect.width - start.x);
      const maxHeight = Math.max(1, directionY < 0 ? start.y : containerRect.height - start.y);
      let width = Math.abs(end.x - start.x);
      let height = Math.abs(end.y - start.y);
      if (width < 1 && height < 1) {
        width = 1;
        height = 1 / ratio;
      } else if (height < 1 || width / Math.max(1, height) > ratio) {
        height = width / ratio;
      } else {
        width = height * ratio;
      }
      if (width > maxWidth) {
        width = maxWidth;
        height = width / ratio;
      }
      if (height > maxHeight) {
        height = maxHeight;
        width = height * ratio;
      }
      return {
        x: directionX < 0 ? start.x - width : start.x,
        y: directionY < 0 ? start.y - height : start.y,
        width,
        height,
      };
    }

    function refitExportCropRectToOrientation() {
      if (!exportCropRect || !map) return;
      setExportCropRect(centeredExportRect());
      updateExportFrameStatus();
    }

    function exportRectFitsView() {
      if (!exportCropRect || !map) return true;
      const containerRect = map.getContainer().getBoundingClientRect();
      return exportCropRect.x >= 0
        && exportCropRect.y >= 0
        && exportCropRect.x + exportCropRect.width <= containerRect.width
        && exportCropRect.y + exportCropRect.height <= containerRect.height;
    }

    function exportFrameDescription() {
      const groundSize = exportGroundSizeMeters();
      return `${groundSize.width.toFixed(1)} m × ${groundSize.height.toFixed(1)} m`;
    }

    function updateExportFrameStatus() {
      if (!exportCropRect) return;
      setExportStatus("");
    }

    function setExportCropRect(rect) {
      exportCropRect = rect;
      const box = ensureExportSelectionBox();
      if (!box || !rect || rect.width < 2 || rect.height < 2) {
        if (box) box.hidden = true;
        return;
      }
      box.hidden = false;
      box.style.left = `${rect.x}px`;
      box.style.top = `${rect.y}px`;
      box.style.width = `${rect.width}px`;
      box.style.height = `${rect.height}px`;
    }

    function clearExportCropRect() {
      exportCropRect = null;
      if (exportSelectionBox) exportSelectionBox.hidden = true;
      setExportStatus("Aktuelle Kartenansicht wird exportiert.");
    }

    function setExportCropMode(enabled) {
      exportCropStart = null;
      if (!map) return;
      const container = map.getCanvasContainer();
      exportCropMode = false;
      delete container.dataset.exportCrop;
      if (map.dragPan?.enable) map.dragPan.enable();
      if (map.touchZoomRotate?.enable) map.touchZoomRotate.enable();
      if (enabled) {
        setExportCropRect(centeredExportRect());
        updateExportFrameStatus();
      } else {
        exportCropStart = null;
      }
    }

    function startExportCrop(event) {
      if (!exportCropMode || !event.point) return;
      exportCropStart = event.point;
      suppressNextMapClick = true;
      if (event.preventDefault) event.preventDefault();
      if (event.originalEvent?.preventDefault) event.originalEvent.preventDefault();
      setExportCropRect({ x: event.point.x, y: event.point.y, width: 1, height: 1 });
    }

    function updateExportCrop(event) {
      if (!exportCropMode || !exportCropStart || !event.point) return;
      if (event.preventDefault) event.preventDefault();
      if (event.originalEvent?.preventDefault) event.originalEvent.preventDefault();
      setExportCropRect(constrainedExportRect(exportCropStart, event.point));
    }

    function stopExportCrop() {
      if (!exportCropStart) return;
      exportCropStart = null;
      setExportCropMode(false);
      if (exportCropRect && exportCropRect.width >= 20 && exportCropRect.height >= 20) {
        setExportStatus("Ausschnitt gewählt. Export herunterladen.");
      } else {
        clearExportCropRect();
      }
    }

    function exportFileName(extension) {
      const paper = exportPaper?.value || "a4";
      const orientation = exportOrientation?.value || "portrait";
      const scale = exportScale?.value || "1000";
      const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
      return `openkataster-${paper}-${orientation}-1-${scale}-${stamp}.${extension}`;
    }

    function downloadBlob(blob, filename) {
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    }

    function apiKeyFromUrl(url) {
      try {
        return new URL(url, window.location.href).searchParams.get("key") || "";
      } catch (error) {
        return "";
      }
    }

    async function exportVectorPdfBlob() {
      if (!map) return null;
      setExportStatus("Vektor-PDF wird erstellt ...");
      const center = map.getCenter();
      const url = new URL("/export/vector.pdf", window.location.href);
      url.searchParams.set("key", apiKeyFromUrl(featureUrl));
      url.searchParams.set("center_lon", center.lng.toFixed(8));
      url.searchParams.set("center_lat", center.lat.toFixed(8));
      url.searchParams.set("paper", exportPaper?.value || "a4");
      url.searchParams.set("orientation", exportOrientation?.value || "portrait");
      url.searchParams.set("scale", exportScale?.value || "1000");
      const response = await fetch(url.toString(), { headers: { "Accept": "application/pdf" } });
      if (!response.ok) {
        setExportStatus("Vektor-PDF konnte nicht erstellt werden.");
        return null;
      }
      return response.blob();
    }

    function waitForNextRender() {
      return new Promise((resolve) => {
        if (!map) return resolve();
        map.once("render", resolve);
        map.triggerRepaint();
      });
    }

    function waitForMapIdle(targetMap, timeout = 9000) {
      return new Promise((resolve) => {
        let done = false;
        const finish = () => {
          if (done) return;
          done = true;
          window.clearTimeout(timer);
          resolve();
        };
        const timer = window.setTimeout(finish, timeout);
        targetMap.once("idle", finish);
      });
    }

    function drawExportAttribution(canvas) {
      const context = canvas.getContext("2d");
      if (!context) return canvas;
      const sourceText = pdfSourceLegalText();
      const paperText = `${String(exportPaper?.value || "a4").toUpperCase()}, 1:${exportScale?.value || "1000"}`;
      const scale = Math.max(1, Math.min(canvas.width, canvas.height) / 1650);
      const fontSize = Math.max(32, Math.round(30 * scale));
      const padX = Math.round(24 * scale);
      const padY = Math.round(14 * scale);
      context.save();
      context.font = `${fontSize}px Helvetica, Arial, sans-serif`;
      const maxSourceWidth = canvas.width * 0.70;
      let sourceLabel = sourceText;
      while (context.measureText(sourceLabel).width > maxSourceWidth && sourceLabel.length > 20) {
        sourceLabel = `${sourceLabel.slice(0, -2)}…`;
      }
      const paperWidth = context.measureText(paperText).width;
      const sourceWidth = Math.min(maxSourceWidth, context.measureText(sourceLabel).width);
      const height = fontSize + padY * 2;
      const paperBoxWidth = paperWidth + padX * 2;
      const sourceBoxWidth = sourceWidth + padX * 2;
      context.fillStyle = "rgba(255, 255, 255, 0.92)";
      context.fillRect(0, canvas.height - height, paperBoxWidth, height);
      context.fillRect(canvas.width - sourceBoxWidth, canvas.height - height, sourceBoxWidth, height);
      context.fillStyle = "#333333";
      context.textBaseline = "middle";
      context.fillText(paperText, padX, canvas.height - height / 2);
      context.fillText(sourceLabel, canvas.width - sourceBoxWidth + padX, canvas.height - height / 2);
      context.restore();
      return canvas;
    }

    async function exportMapCanvas() {
      if (!map) return null;
      setExportStatus("Export wird gerendert ...");
      const pixelSize = exportPixelSize();
      const container = document.createElement("div");
      container.style.position = "fixed";
      container.style.left = "-10000px";
      container.style.top = "0";
      container.style.width = `${pixelSize.width}px`;
      container.style.height = `${pixelSize.height}px`;
      container.style.pointerEvents = "none";
      container.style.opacity = "0";
      document.body.appendChild(container);
      let printMap = null;
      try {
        const style = JSON.parse(JSON.stringify(map.getStyle()));
        printMap = new maplibregl.Map({
          container,
          style,
          center: map.getCenter(),
          zoom: exportRenderZoom(),
          bearing: 0,
          pitch: 0,
          interactive: false,
          preserveDrawingBuffer: true,
          attributionControl: false,
          fadeDuration: 0,
        });
        await waitForMapIdle(printMap);
        const sourceCanvas = printMap.getCanvas();
        const output = document.createElement("canvas");
        output.width = sourceCanvas.width;
        output.height = sourceCanvas.height;
        const context = output.getContext("2d");
        context.drawImage(sourceCanvas, 0, 0);
        drawExportAttribution(output);
        return output;
      } catch (error) {
        console.error(error);
        setExportStatus("Druck-Render konnte nicht erstellt werden.");
        return null;
      } finally {
        if (printMap) printMap.remove();
        container.remove();
        if (exportSelectionBox && exportCropRect) exportSelectionBox.hidden = false;
      }
    }

    function canvasToBlob(canvas, type, quality) {
      return new Promise((resolve) => canvas.toBlob(resolve, type, quality));
    }

    function pdfEscape(value) {
      const slash = String.fromCharCode(92);
      const special = new Map([
        [0x20ac, 128], [0x201a, 130], [0x0192, 131], [0x201e, 132], [0x2026, 133],
        [0x2020, 134], [0x2021, 135], [0x02c6, 136], [0x2030, 137], [0x0160, 138],
        [0x2039, 139], [0x0152, 140], [0x017d, 142], [0x2018, 145], [0x2019, 146],
        [0x201c, 147], [0x201d, 148], [0x2022, 149], [0x2013, 150], [0x2014, 151],
        [0x02dc, 152], [0x2122, 153], [0x0161, 154], [0x203a, 155], [0x0153, 156],
        [0x017e, 158], [0x0178, 159],
      ]);
      const bytes = [];
      for (const char of String(value)) {
        const code = char.codePointAt(0);
        bytes.push(special.get(code) || (code <= 255 ? code : 63));
      }
      return bytes.map((byte) => {
        if (byte === 40 || byte === 41 || byte === 92) return slash + String.fromCharCode(byte);
        if (byte < 32 || byte > 126) return slash + byte.toString(8).padStart(3, "0");
        return String.fromCharCode(byte);
      }).join("");
    }

    function pdfSourceLegalText() {
      const sources = pdfVisibleSourceParts();
      return `${(sources.length ? sources.map((item) => item.source) : ["© Amtliches Liegenschaftskataster (ALKIS)"]).join(" | ")} | OpenKataster`;
    }

    function pdfVisibleSourceParts() {
      const states = visibleStateEntries().length
        ? visibleStateEntries()
        : [...activeStateSlugs].map((slug) => {
          const state = STATE_CENTERS[slug] || { name: slug };
          return { slug, name: state.name || slug };
        });
      return states.map((state) => {
        const meta = state.slug ? (stateMetadata.get(state.slug) || {}) : {};
        const source = meta.quellenvermerk || state.name || "Amtliches Liegenschaftskataster";
        const stand = meta.datenstand || meta.datestand || meta.datum || meta.letzte_aktualisierung || "";
        const license = meta.lizenz && !String(source).includes(meta.lizenz) ? `, ${meta.lizenz}` : "";
        return { source: `${source}${license}`, stand };
      }).filter(Boolean);
    }

    function pdfDataStandText() {
      const stands = [...new Set(pdfVisibleSourceParts().map((item) => item.stand).filter(Boolean))];
      return stands.length ? `Datenstand: ${stands.join(" / ")}` : "";
    }

    function buildPdfBlobFromCanvas(canvas) {
      const paperSize = exportPaperSizeMillimeters();
      const pageW = paperSize.width / 25.4 * 72;
      const pageH = paperSize.height / 25.4 * 72;
      const dataUrl = canvas.toDataURL("image/jpeg", 0.94);
      const binary = atob(dataUrl.split(",")[1]);
      const imageBytes = new Uint8Array(binary.length);
      for (let i = 0; i < binary.length; i++) imageBytes[i] = binary.charCodeAt(i);
      const encoder = new TextEncoder();
      const NL = String.fromCharCode(10);
      const content = [
        "1 1 1 rg",
        `0 0 ${pageW.toFixed(2)} ${pageH.toFixed(2)} re f`,
        "q",
        `${pageW.toFixed(2)} 0 0 ${pageH.toFixed(2)} 0 0 cm`,
        "/Im0 Do",
        "Q",
        "",
      ].join(NL);

      const contentBytes = encoder.encode(content);
      const objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${pageW.toFixed(2)} ${pageH.toFixed(2)}] /Resources << /XObject << /Im0 4 0 R >> /Font << /F1 5 0 R >> >> /Contents 6 0 R >>`,
        { stream: imageBytes, dict: `<< /Type /XObject /Subtype /Image /Width ${canvas.width} /Height ${canvas.height} /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length ${imageBytes.length} >>` },
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        { stream: contentBytes, dict: `<< /Length ${contentBytes.length} >>` },
      ];
      const chunks = [];
      const offsets = [0];
      let offset = 0;
      function add(part) {
        const bytes = part instanceof Uint8Array ? part : encoder.encode(String(part));
        chunks.push(bytes);
        offset += bytes.length;
      }
      add("%PDF-1.4" + NL);
      objects.forEach((object, index) => {
        offsets.push(offset);
        add(`${index + 1} 0 obj` + NL);
        if (typeof object === "string") {
          add(object + NL);
        } else {
          add(object.dict + NL + "stream" + NL);
          add(object.stream);
          add(NL + "endstream" + NL);
        }
        add("endobj" + NL);
      });
      const xrefOffset = offset;
      add(["xref", `0 ${objects.length + 1}`, "0000000000 65535 f ", ""].join(NL));
      for (let i = 1; i < offsets.length; i++) {
        add(`${String(offsets[i]).padStart(10, "0")} 00000 n ` + NL);
      }
      add(["trailer", `<< /Size ${objects.length + 1} /Root 1 0 R >>`, "startxref", String(xrefOffset), "%%EOF"].join(NL));
      return new Blob(chunks, { type: "application/pdf" });
    }

    async function exportMapFile() {
      const output = exportOutput?.value || "png";
      if (output === "pdf") {
        const canvas = await exportMapCanvas();
        if (!canvas) return;
        downloadBlob(buildPdfBlobFromCanvas(canvas), exportFileName("pdf"));
        setExportStatus("PDF wurde erstellt.");
        return;
      }
      const canvas = await exportMapCanvas();
      if (!canvas) return;
      const blob = await canvasToBlob(canvas, "image/png");
      if (!blob) {
        setExportStatus("PNG konnte nicht erstellt werden.");
        return;
      }
      downloadBlob(blob, exportFileName("png"));
      setExportStatus("PNG wurde erstellt.");
    }

    async function loadJson(url) {
      const response = await fetch(url);
      if (!response.ok) {
        throw new Error(`${response.status} ${response.statusText}`);
      }
      return await response.json();
    }

    function escapeHtml(value) {
      return String(value ?? \"\").replace(/[&<>\"']/g, (char) => ({
        \"&\": \"&amp;\",
        \"<\": \"&lt;\",
        \">\": \"&gt;\",
        \"\\\"\": \"&quot;\",
        \"'\": \"&#39;\",
      })[char]);
    }

    function formatCoordinate(value, axis) {
      const rounded = Math.abs(Number(value)).toFixed(5);
      const suffix = axis === "lat" ? (value >= 0 ? "N" : "S") : (value >= 0 ? "E" : "W");
      return `${rounded}° ${suffix}`;
    }

    function formatCoordinateDms(value, axis) {
      const absolute = Math.abs(Number(value));
      if (!Number.isFinite(absolute)) return "–";
      const deg = Math.floor(absolute);
      const minFloat = (absolute - deg) * 60;
      const min = Math.floor(minFloat);
      const sec = ((minFloat - min) * 60).toFixed(2);
      const suffix = axis === "lat" ? (value >= 0 ? "N" : "S") : (value >= 0 ? "E" : "W");
      return `${deg}°${String(min).padStart(2, "0")}′${sec.padStart(5, "0")}" ${suffix}`;
    }

    function copyText(text) {
      if (!navigator?.clipboard) {
        throw new Error("Zwischenablage ist nicht verfügbar");
      }
      return navigator.clipboard.writeText(text);
    }

    function mapScaleData() {
      if (!map) return null;
      const zoom = map.getZoom();
      const metersPerPixel = 156543.03392804097 * Math.cos(map.getCenter().lat * Math.PI / 180) / Math.pow(2, zoom);
      const denominator = Math.max(1, Math.round(metersPerPixel * 96 / 0.0254));
      return {
        zoom,
        metersPerPixel,
        denominator,
      };
    }

    function formatMetadataInfo(state) {
      const meta = state.slug ? (stateMetadata.get(state.slug) || {}) : {};
      const stand = [meta.datenstand, meta.datestand, meta.datum, meta.letzte_aktualisierung, meta.updated_at, meta.datenjahr]
        .filter(Boolean)
        .shift();
      const source = meta.quellenvermerk || state.name || "Quelle";
      const license = meta.lizenz && !source.includes(meta.lizenz) ? `, ${meta.lizenz}` : "";
      const date = stand && !source.includes(String(stand)) ? ` ${stand}` : "";
      const prefix = source.includes("©") ? source : `© ${source}`;
      return [`${prefix}${date}${license}`];
    }


    function formatCsvValue(value) {
      const safe = value === null || value === undefined ? \"\" : String(value).replace(/\"/g, '""');
      return `\"${safe}\"`;
    }

    function stateSlug(value) {
      const replacements = [
        [\"ä\", \"ae\"],
        [\"ö\", \"oe\"],
        [\"ü\", \"ue\"],
        [\"ß\", \"ss\"],
      ];
      let normalized = String(value || \"\").trim().toLowerCase();
      for (const [source, target] of replacements) {
        normalized = normalized.split(source).join(target);
      }
      return normalized.replace(/[^a-z0-9]+/g, \"-\").replace(/(^-|-$)/g, \"\");
    }

    function itemKey(item) {
      return `${item.source_db || \"\"}:${item.gml_id || \"\"}`;
    }

    function featureCollection(items) {
      return {
        type: \"FeatureCollection\",
        features: items
          .filter((item) => item.geometry)
          .map((item) => ({
            type: \"Feature\",
            properties: { id: itemKey(item) },
            geometry: item.geometry,
          })),
      };
    }

    function formatArea(value) {
      if (value === null || value === undefined || value === \"\") return \"\";
      return `${Number(value).toLocaleString(\"de-DE\")} m²`;
    }

    function formatReadableArea(value) {
      if (value === null || value === undefined || value === \"\") return \"–\";
      if (value < 1000) {
        return `${Math.round(value).toLocaleString(\"de-DE\")} m²`;
      }
      if (value < 1000000) {
        return `${(value / 10000).toLocaleString(\"de-DE\", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ha`;
      }
      return `${(value / 1000000).toLocaleString(\"de-DE\", { minimumFractionDigits: 3, maximumFractionDigits: 3 })} km²`;
    }

    function formatMeasuredArea(value) {
      if (value === null || value === undefined || value === \"\" || !Number.isFinite(Number(value))) return \"–\";
      return `${Number(value).toLocaleString(\"de-DE\", { minimumFractionDigits: 1, maximumFractionDigits: 1 })} m²`;
    }

    function geodesicArea(coords) {
      if (!coords || coords.length < 3) {
        return 0;
      }
      const R = 6371000;
      const toRad = (degree) => degree * Math.PI / 180;
      const ring = coords.map((item) => ({ lon: toRad(item[0]), lat: toRad(item[1]) }));
      let area = 0;
      for (let i = 0; i < ring.length; i++) {
        const p1 = ring[i];
        const p2 = ring[(i + 1) % ring.length];
        area += (p2.lon - p1.lon) * (2 + Math.sin(p1.lat) + Math.sin(p2.lat));
      }
      return Math.abs(area * R * R / 2);
    }

    function addSelectionLayers() {
      map.addSource(\"selected-parcels\", { type: \"geojson\", data: featureCollection([]) });
      map.addSource(\"selected-buildings\", { type: \"geojson\", data: featureCollection([]) });
      map.addSource(\"annotations\", { type: \"geojson\", data: { type: \"FeatureCollection\", features: [] } });
      map.addSource(\"measure\", { type: \"geojson\", data: { type: \"FeatureCollection\", features: [] } });
      map.addSource(\"measure-area\", { type: \"geojson\", data: { type: \"FeatureCollection\", features: [] } });
      map.addSource(\"measure-radius\", { type: \"geojson\", data: { type: \"FeatureCollection\", features: [] } });
      map.addSource(\"pins\", { type: \"geojson\", data: { type: \"FeatureCollection\", features: [] } });
      map.addSource(\"snap\", { type: \"geojson\", data: { type: \"FeatureCollection\", features: [] } });
      map.addLayer({
        id: \"selected-parcel-fill\",
        type: \"fill\",
        source: \"selected-parcels\",
        paint: { \"fill-color\": \"#facc15\", \"fill-opacity\": 0.3 }
      });
      map.addLayer({
        id: \"selected-parcel-outline\",
        type: \"line\",
        source: \"selected-parcels\",
        paint: { \"line-color\": \"#ca8a04\", \"line-width\": 2.2, \"line-opacity\": 0.95 }
      });
      map.addLayer({
        id: \"selected-building-fill\",
        type: \"fill\",
        source: \"selected-buildings\",
        paint: { \"fill-color\": \"#38bdf8\", \"fill-opacity\": 0.32 }
      });
      map.addLayer({
        id: \"selected-building-outline\",
        type: \"line\",
        source: \"selected-buildings\",
        paint: { \"line-color\": \"#0284c7\", \"line-width\": 2.2, \"line-opacity\": 0.95 }
      });
      map.addLayer({
        id: \"annotations-fill\",
        type: \"fill\",
        source: \"annotations\",
        filter: [\"==\", [\"get\", \"kind\"], \"polygon\"],
        paint: {
          \"fill-color\": [\"get\", \"color\"],
          \"fill-opacity\": 0.16
        }
      });
      map.addLayer({
        id: \"annotations-line\",
        type: \"line\",
        source: \"annotations\",
        filter: [\"any\", [\"==\", [\"get\", \"kind\"], \"line\"], [\"==\", [\"get\", \"kind\"], \"polygon-outline\"]],
        paint: {
          \"line-color\": [\"get\", \"color\"],
          \"line-width\": 3,
          \"line-opacity\": 0.92
        }
      });
      map.addLayer({
        id: \"annotations-point\",
        type: \"circle\",
        source: \"annotations\",
        filter: [\"==\", [\"get\", \"kind\"], \"vertex\"],
        paint: {
          \"circle-radius\": 5,
          \"circle-color\": \"#fff\",
          \"circle-stroke-color\": [\"get\", \"color\"],
          \"circle-stroke-width\": 2,
        }
      });
      map.addLayer({
        id: \"measure-line\",
        type: \"line\",
        source: \"measure\",
        paint: { \"line-color\": \"#f97316\", \"line-width\": 3, \"line-opacity\": 0.95 }
      });
      map.addLayer({
        id: \"measure-point\",
        type: \"circle\",
        source: \"measure\",
        filter: [\"all\", [\"==\", [\"geometry-type\"], \"Point\"], [\"==\", [\"get\", \"kind\"], \"vertex\"]],
        paint: {
          \"circle-radius\": 8,
          \"circle-color\": \"#fff\",
          \"circle-stroke-color\": \"#f97316\",
          \"circle-stroke-width\": 2.4,
        }
      });
      map.addLayer({
        id: \"measure-segment-label\",
        type: \"symbol\",
        source: \"measure\",
        filter: [\"==\", [\"get\", \"kind\"], \"segment-label\"],
        layout: {
          \"text-field\": [\"get\", \"label\"],
          \"text-font\": [\"Arial\"],
          \"text-size\": 12,
          \"text-allow-overlap\": true,
          \"text-ignore-placement\": true,
          \"text-anchor\": \"center\"
        },
        paint: {
          \"text-color\": \"#9a3412\",
          \"text-halo-color\": \"#fff\",
          \"text-halo-width\": 2
        }
      });
      map.addLayer({
        id: \"measure-total-label\",
        type: \"symbol\",
        source: \"measure\",
        filter: [\"==\", [\"get\", \"kind\"], \"total-label\"],
        layout: {
          \"text-field\": [\"get\", \"label\"],
          \"text-font\": [\"Arial\"],
          \"text-size\": 13,
          \"text-offset\": [0, -1.4],
          \"text-allow-overlap\": true,
          \"text-ignore-placement\": true,
          \"text-anchor\": \"bottom\"
        },
        paint: {
          \"text-color\": \"#111713\",
          \"text-halo-color\": \"#fff\",
          \"text-halo-width\": 2.5
        }
      });
      map.addLayer({
        id: \"measure-area-fill\",
        type: \"fill\",
        source: \"measure-area\",
        paint: { \"fill-color\": \"#22c55e\", \"fill-opacity\": 0.22 }
      });
      map.addLayer({
        id: \"measure-area-outline\",
        type: \"line\",
        source: \"measure-area\",
        paint: { \"line-color\": \"#16a34a\", \"line-width\": 2.4, \"line-opacity\": 0.95 }
      });
      map.addLayer({
        id: \"measure-area-point\",
        type: \"circle\",
        source: \"measure-area\",
        filter: [\"all\", [\"==\", [\"geometry-type\"], \"Point\"], [\"==\", [\"get\", \"kind\"], \"vertex\"]],
        paint: {
          \"circle-radius\": 8,
          \"circle-color\": \"#fff\",
          \"circle-stroke-color\": \"#16a34a\",
          \"circle-stroke-width\": 2.4,
        }
      });
      map.addLayer({
        id: \"measure-area-label\",
        type: \"symbol\",
        source: \"measure-area\",
        filter: [\"==\", [\"get\", \"kind\"], \"area-label\"],
        layout: {
          \"text-field\": [\"get\", \"label\"],
          \"text-font\": [\"Arial\"],
          \"text-size\": 13,
          \"text-allow-overlap\": true,
          \"text-ignore-placement\": true,
          \"text-anchor\": \"center\"
        },
        paint: {
          \"text-color\": \"#14532d\",
          \"text-halo-color\": \"#fff\",
          \"text-halo-width\": 2.5
        }
      });
      map.addLayer({
        id: \"measure-area-total-label\",
        type: \"symbol\",
        source: \"measure-area\",
        filter: [\"==\", [\"get\", \"kind\"], \"area-total-label\"],
        layout: {
          \"text-field\": [\"get\", \"label\"],
          \"text-font\": [\"Arial\"],
          \"text-size\": 13,
          \"text-offset\": [0, -1.4],
          \"text-allow-overlap\": true,
          \"text-ignore-placement\": true,
          \"text-anchor\": \"bottom\"
        },
        paint: {
          \"text-color\": \"#111713\",
          \"text-halo-color\": \"#fff\",
          \"text-halo-width\": 2.5
        }
      });
      map.addLayer({
        id: \"measure-area-segment-label\",
        type: \"symbol\",
        source: \"measure-area\",
        filter: [\"==\", [\"get\", \"kind\"], \"area-segment-label\"],
        layout: {
          \"text-field\": [\"get\", \"label\"],
          \"text-font\": [\"Arial\"],
          \"text-size\": 12,
          \"text-allow-overlap\": true,
          \"text-ignore-placement\": true,
          \"text-anchor\": \"center\"
        },
        paint: {
          \"text-color\": \"#14532d\",
          \"text-halo-color\": \"#fff\",
          \"text-halo-width\": 2
        }
      });
      map.addLayer({
        id: \"measure-radius-fill\",
        type: \"fill\",
        source: \"measure-radius\",
        paint: { \"fill-color\": \"#3b82f6\", \"fill-opacity\": 0.16 }
      });
      map.addLayer({
        id: \"measure-radius-line\",
        type: \"line\",
        source: \"measure-radius\",
        paint: { \"line-color\": \"#2563eb\", \"line-width\": 2.2, \"line-opacity\": 0.95 }
      });
      map.addLayer({
        id: \"pins-fill\",
        type: \"circle\",
        source: \"pins\",
        filter: [\"==\", [\"get\", \"kind\"], \"pin\"],
        paint: {
          \"circle-radius\": 7,
          \"circle-color\": \"#dc2626\",
          \"circle-stroke-color\": \"#fff\",
          \"circle-stroke-width\": 2,
        }
      });
      map.addLayer({
        id: \"pins-label\",
        type: \"symbol\",
        source: \"pins\",
        filter: [\"==\", [\"get\", \"kind\"], \"pin-label\"],
        layout: {
          \"text-field\": [\"get\", \"label\"],
          \"text-font\": [\"Arial\"],
          \"text-size\": 12,
          \"text-offset\": [0.8, 0],
          \"text-anchor\": \"left\",
          \"text-allow-overlap\": true,
          \"text-ignore-placement\": true
        },
        paint: {
          \"text-color\": \"#374151\",
          \"text-halo-color\": \"#fff\",
          \"text-halo-width\": 2.5
        }
      });
      map.addLayer({
        id: \"snap-point\",
        type: \"circle\",
        source: \"snap\",
        paint: {
          \"circle-radius\": 7,
          \"circle-color\": \"#f86d14\",
          \"circle-opacity\": 0.95,
          \"circle-stroke-color\": \"#fff\",
          \"circle-stroke-width\": 2.5,
        }
      });
      updateHandleVisibility();
    }

    function renderAddresses(item) {
      const addresses = item.addresses || [];
      if (!addresses.length) return \"\";
      return addresses.map((address) => address.label).filter(Boolean).slice(0, 2).join(" · ");
    }

    function numericArea(value) {
      const number = Number(value);
      return Number.isFinite(number) && number > 0 ? number : 0;
    }

    function geometryAreaMeters(geometry) {
      if (!geometry || !geometry.coordinates) return null;
      if (geometry.type === "Polygon") {
        const ring = geometry.coordinates[0] || [];
        return ring.length >= 4 ? geodesicArea(ring) : null;
      }
      if (geometry.type === "MultiPolygon") {
        let sum = 0;
        for (const polygon of geometry.coordinates || []) {
          const ring = polygon[0] || [];
          if (ring.length >= 4) sum += geodesicArea(ring);
        }
        return sum || null;
      }
      return null;
    }

    function parcelRow(item) {
      const title = [
        item.flurstueck ? `Flurstück ${item.flurstueck}` : \"\",
        item.gemarkung ? `Gemarkung ${item.gemarkung}` : \"\",
      ].filter(Boolean).join(\" · \") || \"Flurstück\";
      return `<tr><td>${escapeHtml(title)}</td><td>${item.flur ? escapeHtml(item.flur) : "–"}</td><td>${item.amtliche_flaeche_m2 ? escapeHtml(formatArea(item.amtliche_flaeche_m2)) : "–"}</td><td class="selection-muted">${escapeHtml(renderAddresses(item) || "–")}</td></tr>`;
    }

    function buildingFootprint(item) {
      return geometryAreaMeters(item.geometry) || 0;
    }

    function buildingRow(item) {
      const title = item.gebaeudefunktion_text || item.name || \"Gebäude\";
      const footprint = buildingFootprint(item);
      return `<tr><td>${escapeHtml(title)}</td><td>${footprint ? escapeHtml(formatMeasuredArea(footprint)) : "–"}</td><td>${item.geschosse_oberirdisch !== null && item.geschosse_oberirdisch !== undefined ? escapeHtml(`${item.geschosse_oberirdisch}`) : "–"}</td><td>${item.dachform_text ? escapeHtml(item.dachform_text) : "–"}</td><td class="selection-muted">${escapeHtml(renderAddresses(item) || "–")}</td></tr>`;
    }

    function renderParcelTable(parcels) {
      const total = parcels.reduce((sum, item) => sum + numericArea(item.amtliche_flaeche_m2), 0);
      const footer = parcels.length > 1 && total ? `<tfoot><tr><td colspan="2">Summe</td><td>${escapeHtml(formatArea(total))}</td><td></td></tr></tfoot>` : "";
      return `<table class="selection-table"><thead><tr><th>Flurstück</th><th>Flur</th><th>Fläche</th><th>Adresse</th></tr></thead><tbody>${parcels.map(parcelRow).join("")}</tbody>${footer}</table>`;
    }

    function renderBuildingTable(buildings) {
      const total = buildings.reduce((sum, item) => sum + buildingFootprint(item), 0);
      const footer = buildings.length > 1 && total ? `<tfoot><tr><td>Summe</td><td>${escapeHtml(formatMeasuredArea(total))}</td><td colspan="3"></td></tr></tfoot>` : "";
      return `<table class="selection-table"><thead><tr><th>Gebäude</th><th>Grundfläche</th><th>VG</th><th>Dach</th><th>Adresse</th></tr></thead><tbody>${buildings.map(buildingRow).join("")}</tbody>${footer}</table>`;
    }

    function formatCoordinatesForExport(item) {
      const center = item.center || [];
      const lon = Number(center[0]);
      const lat = Number(center[1]);
      return {
        lon: Number.isFinite(lon) ? lon : \"\",
        lat: Number.isFinite(lat) ? lat : \"\",
      };
    }

    function exportSelectionAsCsv() {
      const header = [
        \"typ\",
        \"source_db\",
        \"gml_id\",
        \"label\",
        \"subtitle\",
        \"adresse\",
        \"bundesland\",
        \"landkreis\",
        \"flaeche_m2\",
        \"lon\",
        \"lat\",
      ];
      const rows = [];
      const parcels = [...selectedParcels.values()];
      const buildings = [...selectedBuildings.values()];
      for (const parcel of parcels) {
        const coords = formatCoordinatesForExport(parcel);
        rows.push(
          [
            \"Flurstück\",
            parcel.source_db || \"\",
            parcel.gml_id || \"\",
            parcel.gemarkung || parcel.label || \"\",
            parcel.flurstueckskennzeichen || \"\",
            parcel.address || parcel.adresse || \"\",
            parcel.land || \"\",
            parcel.landkreis || \"\",
            parcel.amtliche_flaeche_m2 || \"\",
            coords.lon,
            coords.lat,
          ].map(formatCsvValue).join(\",\"),
        );
      }
      for (const building of buildings) {
        const coords = formatCoordinatesForExport(building);
        rows.push(
          [
            \"Gebäude\",
            building.source_db || \"\",
            building.gml_id || \"\",
            building.gebaeudefunktion_text || building.name || \"Gebäude\",
            building.address || \"\",
            building.adresse || \"\",
            building.land || \"\",
            building.landkreis || \"\",
            \"\",
            coords.lon,
            coords.lat,
          ].map(formatCsvValue).join(\",\"),
        );
      }
      return [header.map(formatCsvValue).join(\",\"), ...rows].join(\"\\n\");
    }

    function updateSelectionSources() {
      const parcelSource = map.getSource(\"selected-parcels\");
      const buildingSource = map.getSource(\"selected-buildings\");
      if (parcelSource) parcelSource.setData(featureCollection([...selectedParcels.values()]));
      if (buildingSource) buildingSource.setData(featureCollection([...selectedBuildings.values()]));
    }

    function compactSelectionFeature(kind, item) {
      const center = Array.isArray(item.center) ? item.center : null;
      const bbox = Array.isArray(item.bbox) ? item.bbox : null;
      const base = {
        kind,
        gml_id: item.gml_id || null,
        source_db: item.source_db || null,
        label: item.label || item.name || null,
        address: item.address || item.adresse || null,
        center: center && center.length >= 2 ? [Number(center[0]), Number(center[1])] : null,
        bbox: bbox && bbox.length >= 4 ? bbox.map(Number) : null,
      };
      if (kind === "parcel") {
        return {
          ...base,
          flur: item.flur ?? null,
          flurstueck: item.flurstueck || item.flurstuecksnummer || null,
          gemarkung: item.gemarkung || null,
          gemarkungsnummer: item.gemarkungsnummer || null,
          amtliche_flaeche_m2: item.amtliche_flaeche_m2 ?? null,
        };
      }
      return {
        ...base,
        gebaeudefunktion: item.gebaeudefunktion_text || item.gebaeudefunktion || null,
        dachform: item.dachform || item.dachform_text || null,
        vollgeschosse: item.vollgeschosse ?? item.anzahl_vollgeschosse ?? null,
      };
    }

    function notifyParentSelection(parcels, buildings) {
      if (!window.parent || window.parent === window) return;
      const params = new URLSearchParams(window.location.search);
      const targetOrigin = params.get("okParentOrigin") || "*";
      window.parent.postMessage(
        {
          type: "openkataster:selection",
          version: 1,
          dataset: "__DATASET__",
          counts: { parcels: parcels.length, buildings: buildings.length },
          parcels: parcels.map((item) => compactSelectionFeature("parcel", item)),
          buildings: buildings.map((item) => compactSelectionFeature("building", item)),
        },
        targetOrigin
      );
    }

    function firstOverlayLayerId() {
      const candidates = ["selected-parcel-fill", "measure-line", "pins-fill"];
      return candidates.find((id) => map.getLayer(id)) || undefined;
    }

    function firstAlkisLayerId() {
      for (const layer of map.getStyle().layers || []) {
        const id = String(layer.id || "");
        if (!id || id === "background" || id.startsWith("luftbild-")) continue;
        if (id === "state-outlines" || id === "state-labels") continue;
        if (id.startsWith("selected-") || id.startsWith("annotations") || id.startsWith("measure") || id.startsWith("pins") || id.startsWith("snap")) continue;
        return id;
      }
      return firstOverlayLayerId();
    }

    function wmsTileUrl(config) {
      return `${window.location.origin}/luftbild/${config.slug}/{z}/{x}/{y}.png?v=1024-webmercator`;
    }

    function ensureLuftbildLayer(slug) {
      const config = LUFTBILD_WMS[slug];
      if (!config || !map) return null;
      config.slug = slug;
      const sourceId = `luftbild-${slug}`;
      const layerId = `luftbild-${slug}`;
      if (!map.getSource(sourceId)) {
        map.addSource(sourceId, {
          type: "raster",
          tiles: [wmsTileUrl(config)],
          tileSize: 512,
          attribution: "",
        });
      }
      if (!map.getLayer(layerId)) {
        map.addLayer({
          id: layerId,
          type: "raster",
          source: sourceId,
          paint: { "raster-opacity": 1 },
        }, firstAlkisLayerId());
      }
      return layerId;
    }

    function isLabelLikeLayer(layer) {
      const id = String(layer.id || "").toLowerCase();
      const sourceLayer = String(layer["source-layer"] || "").toLowerCase();
      const filter = JSON.stringify(layer.filter || "").toLowerCase();
      const textField = JSON.stringify(layer.layout?.["text-field"] || "").toLowerCase();
      const combined = `${id} ${sourceLayer} ${filter} ${textField}`;
      const filterHasEquals = (needle) => filter.includes(`"==","${needle[0]}","${needle[1]}"`) || filter.includes(`'==','${needle[0]}','${needle[1]}'`);
      const isParcelNumberLine = layer.type === "line"
        && (id.includes("parcel-number")
          || (filterHasEquals(["thema", "flurstücke"]) && filterHasEquals(["sub_thema", "nummern (line)"])));
      const isFractionLine = layer.type === "line"
        && (isParcelNumberLine
          || combined.includes("bruch")
          || combined.includes("zaehler")
          || combined.includes("zähler")
          || combined.includes("nenner")
          || combined.includes("nummernstrich")
          || combined.includes("fraction"));
      return layer.type === "symbol"
        || id.includes("label")
        || id.includes("text")
        || id.includes("schrift")
        || id.includes("nummer")
        || id.includes("bruch")
        || id.includes("flurstueck")
        || id.includes("flurstück")
        || isFractionLine;
    }

    function isStreetNameLayer(layer) {
      const id = String(layer.id || "").toLowerCase();
      const sourceLayer = String(layer["source-layer"] || "").toLowerCase();
      const textField = JSON.stringify(layer.layout?.["text-field"] || "").toLowerCase();
      const filter = JSON.stringify(layer.filter || "").toLowerCase();
      const combined = `${id} ${sourceLayer} ${textField} ${filter}`;
      return layer.type === "symbol"
        && (combined.includes("strasse")
          || combined.includes("straße")
          || combined.includes("str_name")
          || combined.includes("strname")
          || combined.includes("verkehr")
          || combined.includes("road")
          || combined.includes("street"))
        && !combined.includes("flurst")
        && !combined.includes("flurstück")
        && !combined.includes("flurstueck")
        && !combined.includes("gemarkung")
        && !combined.includes("zaehler")
        && !combined.includes("nenner")
        && !combined.includes("bruch");
    }

    function isManagedStyleLayer(layer) {
      const id = String(layer.id || "");
      if (!id || id === "background") return false;
      if (id.startsWith("luftbild-")) return false;
      if (id === "state-outlines" || id === "state-labels") return false;
      if (id.startsWith("selected-") || id.startsWith("annotations") || id.startsWith("measure") || id.startsWith("pins") || id.startsWith("snap")) return false;
      return true;
    }

    function layerBelongsToCustomRendererState(layer) {
      const needle = "hamburg";
      const haystack = [
        layer.id,
        layer.source,
        layer["source-layer"],
        JSON.stringify(layer.filter || ""),
      ].map((value) => String(value || "").toLowerCase()).join(" ");
      return haystack.includes(needle);
    }

    function viewIntersectsHamburg() {
      if (!map) return false;
      const bounds = map.getBounds();
      const west = bounds.getWest();
      const east = bounds.getEast();
      const south = bounds.getSouth();
      const north = bounds.getNorth();
      return east >= 8.3 && west <= 10.4 && north >= 53.35 && south <= 53.95;
    }

    function layerThemeIndex(layer) {
      const id = String(layer.id || "");
      const match = id.match(/theme-(\\d+)/);
      if (match) return Number(match[1]);
      const filter = JSON.stringify(layer.filter || "");
      const oldMatch = filter.match(/"theme_index",(\\d+)/);
      return oldMatch ? Number(oldMatch[1]) : null;
    }

    function mergeFilters(baseFilter, extraFilters) {
      const extras = extraFilters.filter(Boolean);
      if (!baseFilter && !extras.length) return undefined;
      if (!baseFilter && extras.length === 1) return extras[0];
      return ["all", ...(baseFilter ? [baseFilter] : []), ...extras];
    }

    function layerBaseFilter(layer) {
      if (!baseLayerFilters.has(layer.id)) {
        baseLayerFilters.set(layer.id, layer.filter ? JSON.parse(JSON.stringify(layer.filter)) : undefined);
      }
      return baseLayerFilters.get(layer.id);
    }

    function filteredLayerFilter(layer) {
      const baseFilter = layerBaseFilter(layer);
      const id = String(layer.id || "");
      const filters = [];
      if (id.match(/^runtime-[a-z0-9_-]+-label-theme-1-/)) {
        if (layerSettings.houseNumbers && !layerSettings.buildingLabels) {
          filters.push(["==", "sub_thema", "Gebäude"]);
        } else if (!layerSettings.houseNumbers && layerSettings.buildingLabels) {
          filters.push(["!=", "sub_thema", "Gebäude"]);
        }
      }
      return mergeFilters(baseFilter, filters);
    }

    function isBuildingLabelLayer(id) {
      return /^runtime-[a-z0-9_-]+-label-theme-1-/.test(id);
    }

    function isStreetLabelLayer(id, theme) {
      return /^runtime-[a-z0-9_-]+-label-theme-[26]-/.test(id) || theme === 2 || theme === 6;
    }

    function isSurfaceLabelLayer(id, theme) {
      return /^runtime-[a-z0-9_-]+-label-theme-(8|10|11|12|13)-/.test(id)
        || [8, 10, 11, 12, 13].includes(theme);
    }

    function isLegalLayer(id, theme) {
      return /^runtime-[a-z0-9_-]+-label-theme-[34]-/.test(id)
        || theme === 3
        || theme === 4;
    }

    function isSurfaceOutlineLayer(id, theme) {
      return /^runtime-[a-z0-9_-]+-general-line-/.test(id) && !isLegalLayer(id, theme);
    }

    function isLayerEnabledBySettings(layer) {
      const id = String(layer.id || "");
      const sourceLayer = String(layer["source-layer"] || "");
      const theme = layerThemeIndex(layer);

      if (!layerSettings.basemap) {
        return false;
      }
      const isSymbolLayer = layer.type === "symbol";
      if (sourceLayer.includes("overview") || sourceLayer === "major_surfaces" || sourceLayer === "surfaces" || sourceLayer === "green_surfaces") {
        return layerSettings.surfaceFills;
      }
      if (/^runtime-[a-z0-9_-]+-building-(fills|lines)$/.test(id)) {
        return layerSettings.buildings;
      }
      if (/^runtime-[a-z0-9_-]+-parcel-outline-lines$/.test(id)) {
        return layerSettings.parcelLines;
      }
      if (/^runtime-[a-z0-9_-]+-parcel-number-lines$/.test(id) || id.includes("boundary-point") || /^runtime-[a-z0-9_-]+-label-theme-0-/.test(id)) {
        return layerSettings.parcelLabels;
      }
      if (/^runtime-[a-z0-9_-]+-point-symbol-static-fill$/.test(id)) {
        return layerSettings.symbols;
      }
      if (isSymbolLayer && isBuildingLabelLayer(id)) {
        return layerSettings.houseNumbers || layerSettings.buildingLabels;
      }
      if (isSymbolLayer && isStreetLabelLayer(id, theme)) {
        return layerSettings.streetNames;
      }
      if (isLegalLayer(id, theme)) {
        return layerSettings.legalLines;
      }
      if (isSymbolLayer && isSurfaceLabelLayer(id, theme)) {
        return layerSettings.surfaceLabels;
      }
      if (isSurfaceOutlineLayer(id, theme)) {
        return layerSettings.surfaceOutlines;
      }
      if (id.startsWith("mosaic-force-building-fill")
        || id === "mosaic-building-polygon-outline"
        || (id.startsWith("mosaic-") && sourceLayer === "polygons" && theme === 1)
        || (id.startsWith("mosaic-") && sourceLayer === "lines" && theme === 1)) {
        return layerSettings.buildings;
      }
      if (id.startsWith("mosaic-force-parcel-fill")
        || (id.startsWith("mosaic-") && sourceLayer === "polygons" && theme === 0)) {
        return layerSettings.surfaceFills;
      }
      if (id.startsWith("mosaic-force-surface-fill")
        || (id.startsWith("mosaic-") && sourceLayer === "polygons")) {
        return layerSettings.surfaceFills;
      }
      if (id.startsWith("mosaic-") && sourceLayer === "boundary_point_geometries") {
        return layerSettings.parcelLabels;
      }
      if (id.startsWith("mosaic-") && sourceLayer === "labels" && theme === 0) {
        return layerSettings.parcelLabels;
      }
      if (id.startsWith("mosaic-") && sourceLayer === "labels" && theme === 1) {
        return layerSettings.houseNumbers || layerSettings.buildingLabels;
      }
      if (id.startsWith("mosaic-") && sourceLayer === "labels" && isStreetLabelLayer(id, theme)) {
        return layerSettings.streetNames;
      }
      if (id.startsWith("mosaic-") && sourceLayer === "labels" && isSurfaceLabelLayer(id, theme)) {
        return layerSettings.surfaceLabels;
      }
      if (id.startsWith("mosaic-") && isLegalLayer(id, theme)) {
        return layerSettings.legalLines;
      }
      if (id.startsWith("mosaic-") && sourceLayer === "lines" && theme === 0) {
        return layerSettings.parcelLines;
      }
      if (id.startsWith("mosaic-") && sourceLayer === "lines") {
        return layerSettings.surfaceOutlines;
      }
      if (id.startsWith("mosaic-")) {
        return true;
      }
      return false;
    }

    function syncLayerSettingsUi() {
      for (const input of layerSettingInputs) {
        const key = input.dataset.layerSetting;
        input.checked = !!layerSettings[key];
      }
      for (const input of layerGroupInputs) {
        const keys = layerSettingGroups[input.dataset.layerGroup] || [];
        const activeCount = keys.filter((key) => !!layerSettings[key]).length;
        input.checked = keys.length > 0 && activeCount === keys.length;
        input.indeterminate = activeCount > 0 && activeCount < keys.length;
      }
    }

    function applyLayerSettings() {
      if (!map?.getStyle) return;
      for (const layer of map.getStyle().layers || []) {
        if (!isManagedStyleLayer(layer)) continue;
        if (!baseLayerVisibilities.has(layer.id)) {
          baseLayerVisibilities.set(layer.id, layer.layout?.visibility || "visible");
        }
        if (!baseLayerFilters.has(layer.id)) {
          baseLayerFilters.set(layer.id, layer.filter ? JSON.parse(JSON.stringify(layer.filter)) : undefined);
        }
        if (!map.getLayer(layer.id)) continue;
        const visibility = isLayerEnabledBySettings(layer) ? baseLayerVisibilities.get(layer.id) : "none";
        map.setLayoutProperty(layer.id, "visibility", visibility);
        try {
          map.setFilter(layer.id, filteredLayerFilter(layer));
        } catch (error) {
          console.warn("Could not update layer filter", layer.id, error);
        }
      }
      if (map.getLayer("background")) {
        try {
          map.setPaintProperty("background", "background-opacity", 1);
        } catch (error) {
          console.warn("Could not update background opacity", error);
        }
      }
      updateLuftbildLayers();
    }

    function setBaseStyleVisibility() {
      applyLayerSettings();
    }

    function liftStreetLabelsAboveAerial() {
      return;
    }

    function normalizeStreetLabelWrapping() {
      if (!map?.getStyle) return;
      for (const layer of map.getStyle().layers || []) {
        const hasText = layer.type === "symbol" && layer.layout && layer.layout["text-field"];
        if (!map.getLayer(layer.id) || !hasText) continue;
        try {
          map.setLayoutProperty(layer.id, "text-max-width", 999);
        } catch (error) {
          console.warn("Could not update street label wrapping", layer.id, error);
        }
      }
    }

    function updateLuftbildLayers() {
      if (!map?.getStyle) return;
      const visibleSlugs = new Set();
      if (layerSettings.aerial) {
        for (const state of visibleStateEntries()) {
          if (LUFTBILD_WMS[state.slug]) visibleSlugs.add(state.slug);
        }
      }
      for (const slug of visibleSlugs) {
        ensureLuftbildLayer(slug);
      }
      for (const layer of map.getStyle().layers || []) {
        if (!layer.id.startsWith("luftbild-") || !map.getLayer(layer.id)) continue;
        const slug = layer.id.replace("luftbild-", "");
        map.setLayoutProperty(layer.id, "visibility", visibleSlugs.has(slug) ? "visible" : "none");
      }
    }

    function setBaseLayerMode() {
      baseLayerMode = "custom";
      applyLayerSettings();
    }

    function renderSelection() {
      const parcels = [...selectedParcels.values()];
      const buildings = [...selectedBuildings.values()];
      if (!parcels.length && !buildings.length) {
        selectionPanel.hidden = true;
      } else {
        selectionPanel.hidden = false;
        const parts = [];
        if (parcels.length) {
          parts.push(renderParcelTable(parcels));
        }
        if (buildings.length) {
          parts.push(renderBuildingTable(buildings));
        }
        selectionBody.innerHTML = parts.join(\"\");
      }
      updateSelectionSources();
      notifyParentSelection(parcels, buildings);
    }

    function clearSelection() {
      selectedParcels.clear();
      selectedBuildings.clear();
      renderSelection();
    }

    function isMapReady() {
      return !!map && !!map.getSource("annotations") && !!map.getSource("measure") && !!map.getSource("measure-area") && !!map.getSource("measure-radius") && !!map.getSource("pins");
    }

    function setLayerVisibility(layerId, visible) {
      if (!map?.getLayer || !map.getLayer(layerId)) return;
      map.setLayoutProperty(layerId, "visibility", visible ? "visible" : "none");
    }

    function updateHandleVisibility() {
      if (!map?.getLayer) return;
      setLayerVisibility("annotations-point", activeTool === "drawLine" || activeTool === "drawPolygon");
      setLayerVisibility("measure-point", activeTool === "measureLine");
      setLayerVisibility("measure-area-point", activeTool === "measureArea");
      setLayerVisibility("pins-fill", activeTool === "pin" || activeTool === "erase");
    }

    function setPinSource() {
      if (!isMapReady()) return;
      const features = [];
      pinnedPoints.forEach((point, pinIndex) => {
        const label = `${formatCoordinate(point[1], "lat")} · ${formatCoordinate(point[0], "lon")}`;
        features.push({
          type: "Feature",
          geometry: { type: "Point", coordinates: point },
          properties: { kind: "pin", pinIndex },
        });
        features.push({
          type: "Feature",
          geometry: { type: "Point", coordinates: point },
          properties: { kind: "pin-label", label, pinIndex },
        });
      });
      map.getSource("pins").setData({
        type: "FeatureCollection",
        features,
      });
    }

    function annotationFeatures() {
      const features = [];
      annotations.forEach((annotation, annotationIndex) => {
        if (annotation.type === "line" && annotation.points.length >= 2) {
          features.push({
            type: "Feature",
            geometry: { type: "LineString", coordinates: annotation.points },
            properties: { kind: "line", color: annotation.color, annotationIndex },
          });
        }
        if (annotation.type === "polygon" && annotation.points.length >= 3) {
          features.push({
            type: "Feature",
            geometry: { type: "Polygon", coordinates: [[...annotation.points, annotation.points[0]]] },
            properties: { kind: "polygon", color: annotation.color, annotationIndex },
          });
          features.push({
            type: "Feature",
            geometry: { type: "LineString", coordinates: [...annotation.points, annotation.points[0]] },
            properties: { kind: "polygon-outline", color: annotation.color, annotationIndex },
          });
        }
        annotation.points.forEach((point, vertexIndex) => {
          features.push({
            type: "Feature",
            geometry: { type: "Point", coordinates: point },
            properties: { kind: "vertex", color: annotation.color, annotationIndex, vertexIndex },
          });
        });
      });
      if (activeTool === "drawLine" || activeTool === "drawPolygon") {
        const isPolygon = activeTool === "drawPolygon";
        if (annotationPoints.length >= 2) {
          features.push({
            type: "Feature",
            geometry: { type: "LineString", coordinates: isPolygon && annotationPoints.length >= 3 ? [...annotationPoints, annotationPoints[0]] : annotationPoints },
            properties: { kind: isPolygon ? "polygon-outline" : "line", color: annotationColor },
          });
        }
        if (isPolygon && annotationPoints.length >= 3) {
          features.push({
            type: "Feature",
            geometry: { type: "Polygon", coordinates: [[...annotationPoints, annotationPoints[0]]] },
            properties: { kind: "polygon", color: annotationColor },
          });
        }
        annotationPoints.forEach((point, vertexIndex) => {
          features.push({
            type: "Feature",
            geometry: { type: "Point", coordinates: point },
            properties: { kind: "vertex", color: annotationColor, draft: true, vertexIndex },
          });
        });
      }
      return features;
    }

    function applyAnnotationLayers() {
      if (!isMapReady()) return;
      map.getSource("annotations").setData({ type: "FeatureCollection", features: annotationFeatures() });
    }

    function eraseItemAt(event) {
      if (!map || !event?.point) return false;
      const eraseLayers = ["annotations-point", "annotations-line", "annotations-fill", "pins-fill"].filter((id) => map.getLayer(id));
      if (!eraseLayers.length) return false;
      const box = [
        [event.point.x - 8, event.point.y - 8],
        [event.point.x + 8, event.point.y + 8],
      ];
      const features = map.queryRenderedFeatures(box, { layers: eraseLayers });
      const annotationFeature = features.find((feature) => feature.properties?.annotationIndex !== undefined);
      if (annotationFeature) {
        const index = Number(annotationFeature.properties.annotationIndex);
        if (Number.isInteger(index) && annotations[index]) {
          annotations.splice(index, 1);
          applyAnnotationLayers();
          return true;
        }
      }
      const pinFeature = features.find((feature) => feature.layer?.id === "pins-fill" && feature.properties?.pinIndex !== undefined);
      if (pinFeature) {
        const index = Number(pinFeature.properties.pinIndex);
        if (Number.isInteger(index) && pinnedPoints[index]) {
          pinnedPoints.splice(index, 1);
          setPinSource();
          return true;
        }
      }
      return false;
    }

    function setEraserInteractionState() {
      if (!map) return;
      if (activeTool === "erase" && !spacePanActive) {
        if (map.dragPan?.disable) map.dragPan.disable();
        if (map.touchZoomRotate?.disable) map.touchZoomRotate.disable();
        map.getCanvas().style.cursor = ERASER_CURSOR;
      } else if (activeTool !== "erase") {
        if (map.dragPan?.enable) map.dragPan.enable();
        if (map.touchZoomRotate?.enable) map.touchZoomRotate.enable();
      }
    }

    function startEraseBrush(event) {
      if (activeTool !== "erase" || spacePanActive) return;
      eraseDragging = true;
      suppressNextMapClick = true;
      if (event.preventDefault) event.preventDefault();
      if (event.originalEvent?.preventDefault) event.originalEvent.preventDefault();
      setEraserInteractionState();
      eraseItemAt(event);
    }

    function updateEraseBrush(event) {
      if (!eraseDragging || activeTool !== "erase" || spacePanActive) return;
      if (event.preventDefault) event.preventDefault();
      if (event.originalEvent?.preventDefault) event.originalEvent.preventDefault();
      eraseItemAt(event);
    }

    function stopEraseBrush() {
      if (!eraseDragging) return;
      eraseDragging = false;
      suppressNextMapClick = true;
      setEraserInteractionState();
    }

    function addAnnotationPoint(coord) {
      annotationPoints.push(coord);
      applyAnnotationLayers();
    }

    function commitAnnotation() {
      if (activeTool === "drawLine" && annotationPoints.length >= 2) {
        annotations.push({ type: "line", points: annotationPoints.slice(), color: annotationColor });
      }
      if (activeTool === "drawPolygon" && annotationPoints.length >= 3) {
        annotations.push({ type: "polygon", points: annotationPoints.slice(), color: annotationColor });
      }
      annotationPoints = [];
      applyAnnotationLayers();
    }

    function dropDuplicateAnnotationEndpoint() {
      if (annotationPoints.length < 2) return;
      const last = annotationPoints[annotationPoints.length - 1];
      const previous = annotationPoints[annotationPoints.length - 2];
      if (haversine(last, previous) < 0.1) {
        annotationPoints.pop();
      }
    }

    function setToolMode(mode) {
      const next = mode || "none";
      if (activeTool === "drawLine" || activeTool === "drawPolygon") {
        commitAnnotation();
      }
      activeTool = activeTool === next ? "none" : next;
      const toolButtons = [toolSelect, toolMeasureLine, toolMeasureArea, toolDrawLine, toolDrawPolygon, toolErase, toolMeasureRadius, toolPin];
      for (const button of toolButtons) {
        const isActive =
          (button === toolSelect && activeTool === "none")
          || (button === toolMeasureLine && activeTool === "measureLine")
          || (button === toolMeasureArea && activeTool === "measureArea")
          || (button === toolDrawLine && activeTool === "drawLine")
          || (button === toolDrawPolygon && activeTool === "drawPolygon")
          || (button === toolErase && activeTool === "erase")
          || (button === toolMeasureRadius && activeTool === "measureRadius")
          || (button === toolPin && activeTool === "pin");
        if (button) button.dataset.state = isActive ? "active" : "off";
      }
      measurePoints = [];
      areaPoints = [];
      radiusPoints = [];
      if (isMapReady()) {
        map.getSource("measure").setData({ type: "FeatureCollection", features: [] });
        map.getSource("measure-area").setData({ type: "FeatureCollection", features: [] });
        map.getSource("measure-radius").setData({ type: "FeatureCollection", features: [] });
      }
      setSnapIndicator(null);

      if (activeTool === "none") {
        if (map) map.getCanvas().style.cursor = "";
        statusRight.textContent = "";
        if (radiusPoints.length) {
          radiusPoints = [];
        }
        if (isMapReady()) {
          map.getSource("measure-radius").setData({ type: "FeatureCollection", features: [] });
        }
        setMeasureStatus();
      } else if (activeTool === "measureLine") {
        if (map) map.getCanvas().style.cursor = "crosshair";
        statusRight.textContent = "";
      } else if (activeTool === "measureArea") {
        if (map) map.getCanvas().style.cursor = "crosshair";
        statusRight.textContent = "";
      } else if (activeTool === "measureRadius") {
        if (map) map.getCanvas().style.cursor = "crosshair";
        statusRight.textContent = "";
      } else if (activeTool === "pin") {
        if (map) map.getCanvas().style.cursor = "crosshair";
        statusRight.textContent = "";
      } else if (activeTool === "erase") {
        if (map) map.getCanvas().style.cursor = ERASER_CURSOR;
        statusRight.textContent = "";
      } else if (activeTool === "drawLine" || activeTool === "drawPolygon") {
        if (map) map.getCanvas().style.cursor = "crosshair";
        statusRight.textContent = "";
      }
      if (map?.doubleClickZoom) {
        if (activeTool === "drawLine" || activeTool === "drawPolygon" || activeTool === "measureArea") map.doubleClickZoom.disable();
        else map.doubleClickZoom.enable();
      }
      updateHandleVisibility();
      setEraserInteractionState();
      setMeasureStatus();
    }

    function setMeasureStatus() {
      statusRight.textContent = "";
    }

    function removeLastMeasurePoint() {
      if (activeTool === \"measureLine\" && measurePoints.length) {
        measurePoints.pop();
      } else if (activeTool === \"measureArea\" && areaPoints.length) {
        areaPoints.pop();
      } else if (activeTool === \"measureRadius\" && radiusPoints.length) {
        radiusPoints.pop();
      } else {
        return;
      }
      applyMeasureLayers();
      setMeasureStatus();
    }

    function estimatePerimeter(points) {
      if (!points || points.length < 2) return 0;
      let perimeter = 0;
      for (let i = 1; i < points.length; i++) {
        perimeter += haversine(points[i - 1], points[i]);
      }
      if (points.length > 2) perimeter += haversine(points[points.length - 1], points[0]);
      return perimeter;
    }

    function formatReadableDistance(meters) {
      if (!meters || !Number.isFinite(meters)) return \"–\";
      return meters >= 1000
        ? `${(meters / 1000).toLocaleString("de-DE", { minimumFractionDigits: 1, maximumFractionDigits: 1 })} km`
        : `${meters.toLocaleString("de-DE", { minimumFractionDigits: 1, maximumFractionDigits: 1 })} m`;
    }

    function clearMeasure() {
      measurePoints = [];
      areaPoints = [];
      radiusPoints = [];
      annotationPoints = [];
      annotations = [];
      if (!isMapReady()) return;
      map.getSource("annotations").setData({ type: "FeatureCollection", features: [] });
      map.getSource("measure").setData({ type: "FeatureCollection", features: [] });
      map.getSource("measure-area").setData({ type: "FeatureCollection", features: [] });
      map.getSource("measure-radius").setData({ type: "FeatureCollection", features: [] });
      setSnapIndicator(null);
      setMeasureStatus();
    }

    function buildCirclePoints(center, edge, steps = 72) {
      const radius = haversine(center, edge);
      const earthRadius = 6371000;
      const lat = center[1] * Math.PI / 180;
      const lon = center[0] * Math.PI / 180;
      const segments = Math.max(16, Math.floor(steps));
      const angular = radius / earthRadius;
      const points = [];
      for (let i = 0; i <= segments; i++) {
        const bearing = (Math.PI * 2 * i) / segments;
        const sinLat = Math.sin(lat) * Math.cos(angular) + Math.cos(lat) * Math.sin(angular) * Math.cos(bearing);
        const newLat = Math.asin(sinLat);
        const y = Math.sin(bearing) * Math.sin(angular) * Math.cos(lat);
        const x = Math.cos(angular) - Math.sin(lat) * sinLat;
        const newLon = lon + Math.atan2(y, x);
        points.push([((newLon * 180) / Math.PI + 540) % 360 - 180, (newLat * 180) / Math.PI]);
      }
      return points;
    }

    function copyViewportMetadata() {
      if (!map) return null;
      const center = map.getCenter();
      const bounds = map.getBounds();
      const viewStates = visibleStateEntries();
      const states = (viewStates.length ? viewStates : [...activeStateSlugs]).map((slugOrItem) => {
        if (typeof slugOrItem === "string") {
          const data = stateMetadata.get(slugOrItem) || {};
          return {
            slug: slugOrItem,
            name: (STATE_CENTERS[slugOrItem] && STATE_CENTERS[slugOrItem].name) || slugOrItem,
            data,
          };
        }
        return slugOrItem;
      });
      const payload = {
        createdAt: new Date().toISOString(),
        map: {
          center: [center.lng, center.lat],
          zoom: map.getZoom(),
          bounds: [bounds.getWest(), bounds.getSouth(), bounds.getEast(), bounds.getNorth()],
        },
        states: states,
        pins: pinnedPoints,
      };
      return payload;
    }

    function midpoint(a, b) {
      return [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
    }

    function polygonLabelPoint(points) {
      if (!points || !points.length) return null;
      const xs = points.map((point) => point[0]).filter(Number.isFinite);
      const ys = points.map((point) => point[1]).filter(Number.isFinite);
      if (!xs.length || !ys.length) return null;
      return [
        (Math.min(...xs) + Math.max(...xs)) / 2,
        (Math.min(...ys) + Math.max(...ys)) / 2,
      ];
    }

    function collectGeometryVertices(geometry, vertices = []) {
      if (!geometry || !geometry.coordinates) return vertices;
      if (geometry.type === "Point") {
        vertices.push(geometry.coordinates);
      } else if (geometry.type === "LineString" || geometry.type === "MultiPoint") {
        for (const point of geometry.coordinates) vertices.push(point);
      } else if (geometry.type === "Polygon" || geometry.type === "MultiLineString") {
        for (const ring of geometry.coordinates) {
          for (const point of ring) vertices.push(point);
        }
      } else if (geometry.type === "MultiPolygon") {
        for (const polygon of geometry.coordinates) {
          for (const ring of polygon) {
            for (const point of ring) vertices.push(point);
          }
        }
      } else if (geometry.type === "GeometryCollection") {
        for (const part of geometry.geometries || []) collectGeometryVertices(part, vertices);
      }
      return vertices;
    }

    function setSnapIndicator(coord) {
      if (!map?.getSource || !map.getSource("snap")) return;
      map.getSource("snap").setData({
        type: "FeatureCollection",
        features: coord ? [{
          type: "Feature",
          geometry: { type: "Point", coordinates: coord },
          properties: { kind: "snap" },
        }] : [],
      });
    }

    function addSnapFeatureVertices(feature, candidates) {
      if (!feature) return;
      collectGeometryVertices(feature.geometry, candidates);
    }

    function snapRadiusForZoom(event) {
      const zoom = map?.getZoom ? map.getZoom() : 18;
      const isTouch = Boolean(event?.originalEvent?.touches);
      const farRadius = isTouch ? 36 : 28;
      const nearRadius = isTouch ? 18 : 9;
      if (zoom <= 17) return farRadius;
      if (zoom >= 22) return nearRadius;
      const t = (zoom - 17) / 5;
      return farRadius + (nearRadius - farRadius) * t;
    }

    function nearestSnapCoordinate(event, fallbackCoord) {
      if (!map) return fallbackCoord;
      const eventPoint = event?.point || (fallbackCoord ? map.project(fallbackCoord) : null);
      if (!eventPoint || !fallbackCoord) return fallbackCoord;
      const snapRadius = snapRadiusForZoom(event);
      const layers = ["parcel-fill", "parcel-outline", "building-fill", "building-outline", "selected-parcel-outline", "selected-building-outline"];
      const box = [
        [eventPoint.x - snapRadius, eventPoint.y - snapRadius],
        [eventPoint.x + snapRadius, eventPoint.y + snapRadius],
      ];
      const candidates = [];
      try {
        const rendered = map.queryRenderedFeatures(box, { layers: layers.filter((id) => map.getLayer(id)) });
        for (const feature of rendered) addSnapFeatureVertices(feature, candidates);
      } catch (error) {
        console.warn("Snapping konnte sichtbare Features nicht lesen", error);
      }
      if (map.getSource("alkis")) {
        try {
          const polygonFeatures = map.querySourceFeatures("alkis", { sourceLayer: "polygons" });
          for (const feature of polygonFeatures) {
            const thema = feature.properties?.thema;
            if (thema === "Flurstücke" || thema === "Gebäude") addSnapFeatureVertices(feature, candidates);
          }
          const lineFeatures = map.querySourceFeatures("alkis", { sourceLayer: "lines" });
          for (const feature of lineFeatures) {
            const thema = feature.properties?.thema;
            if (thema === "Flurstücke" || thema === "Gebäude") addSnapFeatureVertices(feature, candidates);
          }
        } catch (error) {
          console.warn("Snapping konnte ALKIS-Quellfeatures nicht lesen", error);
        }
      }
      for (const item of [...selectedParcels.values(), ...selectedBuildings.values()]) {
        collectGeometryVertices(item.geometry, candidates);
      }
      let nearest = null;
      let nearestDistance = Infinity;
      for (const coord of candidates) {
        if (!Array.isArray(coord) || coord.length < 2) continue;
        const point = map.project(coord);
        const distance = Math.hypot(point.x - eventPoint.x, point.y - eventPoint.y);
        if (distance < nearestDistance && distance <= snapRadius) {
          nearest = coord;
          nearestDistance = distance;
        }
      }
      const snapped = nearest ? [nearest[0], nearest[1]] : null;
      setSnapIndicator(snapped);
      return snapped || fallbackCoord;
    }

    function applyMeasureLayers() {
      const linePointFeatures = measurePoints.map((coord, index) => ({
        type: "Feature",
        geometry: { type: "Point", coordinates: coord },
        properties: { index, tool: "line", kind: "vertex" }
      }));
      const lineFeatures = [];
      const lineLabelFeatures = [];
      if (measurePoints.length >= 2) {
        lineFeatures.push({
          type: "Feature",
          geometry: { type: "LineString", coordinates: measurePoints },
          properties: { kind: "line" }
        });
        let distance = 0;
        for (let i = 1; i < measurePoints.length; i++) {
          const segmentDistance = haversine(measurePoints[i - 1], measurePoints[i]);
          distance += segmentDistance;
          lineLabelFeatures.push({
            type: "Feature",
            geometry: { type: "Point", coordinates: midpoint(measurePoints[i - 1], measurePoints[i]) },
            properties: { kind: "segment-label", label: formatReadableDistance(segmentDistance) }
          });
        }
        lineLabelFeatures.push({
          type: "Feature",
          geometry: { type: "Point", coordinates: measurePoints[measurePoints.length - 1] },
          properties: { kind: "total-label", label: `Summe ${formatReadableDistance(distance)}` }
        });
      }

      map.getSource("measure").setData({
        type: "FeatureCollection",
        features: [...lineFeatures, ...linePointFeatures, ...lineLabelFeatures]
      });

      const areaPointFeatures = areaPoints.map((coord, index) => ({
        type: "Feature",
        geometry: { type: "Point", coordinates: coord },
        properties: { index, tool: "area", kind: "vertex" }
      }));
      const areaFeatures = [];
      const areaLabelFeatures = [];
      const areaSegmentLabelFeatures = [];
      const areaTotalLabelFeatures = [];
      if (areaPoints.length >= 2) {
        const openLine = areaPoints.length >= 3 ? [...areaPoints, areaPoints[0]] : areaPoints;
        areaFeatures.push({
          type: "Feature",
          geometry: { type: "LineString", coordinates: openLine },
          properties: { kind: "outline" },
        });
        let cumulativeDistance = 0;
        const cumulativeSegmentCount = areaPoints.length - 1;
        for (let i = 0; i < cumulativeSegmentCount; i++) {
          const a = areaPoints[i];
          const b = areaPoints[i + 1];
          cumulativeDistance += haversine(a, b);
        }
        areaTotalLabelFeatures.push({
          type: "Feature",
          geometry: { type: "Point", coordinates: areaPoints[areaPoints.length - 1] },
          properties: { kind: "area-total-label", label: `Σ=${formatReadableDistance(cumulativeDistance)}` },
        });
      }
      if (areaPoints.length >= 3) {
        areaFeatures.push({
          type: "Feature",
          geometry: { type: "Polygon", coordinates: [[...areaPoints, areaPoints[0]]] },
          properties: { kind: "area" },
        });
        areaLabelFeatures.push({
          type: "Feature",
          geometry: { type: "Point", coordinates: polygonLabelPoint(areaPoints) },
          properties: { kind: "area-label", label: `A=${formatMeasuredArea(geodesicArea(areaPoints))}` },
        });
      }
      map.getSource("measure-area").setData({
        type: "FeatureCollection",
        features: [...areaPointFeatures, ...areaFeatures, ...areaLabelFeatures, ...areaSegmentLabelFeatures, ...areaTotalLabelFeatures]
      });

      const radiusFeatures = [];
      if (radiusPoints.length >= 2) {
        const [center, edge] = radiusPoints;
        const ring = buildCirclePoints(center, edge, 64);
        radiusFeatures.push({
          type: "Feature",
          geometry: { type: "Polygon", coordinates: [ring] },
          properties: { kind: "radius" },
        });
        radiusFeatures.push({
          type: "Feature",
          geometry: { type: "LineString", coordinates: [center, edge] },
          properties: { kind: "radius-line" },
        });
      }
      if (isMapReady()) {
        map.getSource("measure-radius").setData({
          type: "FeatureCollection",
          features: radiusFeatures
        });
      }
    }

    function addMeasurePoint(coord) {
      if (activeTool === "measureLine") {
        measurePoints.push(coord);
      } else if (activeTool === "measureArea") {
        areaPoints.push(coord);
      } else if (activeTool === "measureRadius") {
        radiusPoints.push(coord);
        if (radiusPoints.length > 2) {
          radiusPoints = [coord];
        }
      } else {
        return;
      }
      applyMeasureLayers();
      setMeasureStatus();
    }

    function haversine(a, b) {
      const toRad = (degree) => degree * Math.PI / 180;
      const R = 6371000;
      const lat1 = toRad(a[1]);
      const lat2 = toRad(b[1]);
      const dLat = toRad(b[1] - a[1]);
      const dLon = toRad(b[0] - a[0]);
      const s = Math.sin(dLat / 2) * Math.sin(dLat / 2)
        + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLon / 2) * Math.sin(dLon / 2);
      return 2 * R * Math.asin(Math.sqrt(Math.min(1, s)));
    }

    function updateScaleInfo() {
      if (!map) return;
      const scale = mapScaleData();
      if (!scale) return;
      statusLeft.textContent = `1:${scale.denominator.toLocaleString("de-DE")}`;
      zoomReadout.textContent = `Zoom ${map.getZoom().toFixed(2)}`;
    }


    function updateCursorInfo(event) {
      const lon = event.lngLat.lng;
      const lat = event.lngLat.lat;
      lastCursorLngLat = [lon, lat];
      statusCenter.textContent = `${formatCoordinate(lat, "lat")} · ${formatCoordinate(lon, "lon")}`;
      statusEl.dataset.ready = "false";
      if (statusLeft.textContent) statusEl.style.display = "none";
      clearTimeout(window.__okStatusTimer);
      window.__okStatusTimer = setTimeout(() => { statusEl.dataset.ready = "true"; }, 900);
    }


    function selectedFeatureBounds(items) {
      if (!map) return null;
      let bounds = null;
      for (const item of items) {
        if (item?.bbox && item.bbox.length === 4) {
          const next = new maplibregl.LngLatBounds([item.bbox[0], item.bbox[1]], [item.bbox[2], item.bbox[3]]);
          if (!bounds) {
            bounds = next;
          } else {
            bounds.extend(next.getSouthWest());
            bounds.extend(next.getNorthEast());
          }
          continue;
        }
        const center = item?.center || [];
        const lon = Number(center[0]);
        const lat = Number(center[1]);
        if (Number.isFinite(lon) && Number.isFinite(lat)) {
          if (!bounds) {
            bounds = new maplibregl.LngLatBounds([lon, lat], [lon, lat]);
          } else {
            bounds.extend([lon, lat]);
          }
        }
      }
      return bounds;
    }

    function copySelectionAsReport() {
      const parcels = [...selectedParcels.values()];
      const buildings = [...selectedBuildings.values()];
      const center = map.getCenter();
      const bounds = map.getBounds();
      const scale = mapScaleData();
      return {
        createdAt: new Date().toISOString(),
        map: {
          center: [center.lng, center.lat],
          zoom: map.getZoom(),
          bounds: [bounds.getWest(), bounds.getSouth(), bounds.getEast(), bounds.getNorth()],
          scaleDenominator: scale?.denominator || null,
          metersPerPixel: scale ? scale.metersPerPixel : null,
        },
        states: visibleStateEntries().map((state) => ({
          slug: state.slug,
          name: state.name,
          metadata: stateMetadata.get(state.slug) || null,
        })),
        selection: {
          parcels,
          buildings,
          parcelCount: parcels.length,
          buildingCount: buildings.length,
        },
        metadata: {
          pins: pinnedPoints,
          copiedAt: new Date().toISOString(),
        },
      };
    }

      function selectionReportText(payload) {
      const date = new Date(payload.createdAt).toLocaleString("de-DE");
      const rows = [];
      rows.push("Exposé-Kurzbericht");
      rows.push(`Erstellt: ${date}`);
      rows.push(`Ausschnitt: Zoom ${payload.map.zoom.toFixed(2)} · ${payload.map.scaleDenominator ? `1:${payload.map.scaleDenominator.toLocaleString("de-DE")}` : "Maßstab unbekannt"}`);
      if (payload.selection.parcelCount || payload.selection.buildingCount) {
        rows.push(`Auswahl: ${payload.selection.parcelCount} Flurstück(e), ${payload.selection.buildingCount} Gebäude`);
      }
      rows.push("Nutzungslizenzen/Quellen:");
      for (const state of payload.states) {
        const meta = state.metadata || {};
        rows.push(`- ${state.name}`);
        if (meta.quellenvermerk) rows.push(`  • Quelle: ${meta.quellenvermerk}`);
        if (meta.datenstand) rows.push(`  • Stand: ${meta.datenstand}`);
        if (meta.datenjahr) rows.push(`  • Datenjahr: ${meta.datenjahr}`);
        if (meta.lizenz) rows.push(`  • Lizenz: ${meta.lizenz}`);
        if (meta.aktualisiert_am) rows.push(`  • Aktualisiert: ${meta.aktualisiert_am}`);
      }
      return rows.join("\\n");
    }

    function zoomToSelection() {
      const bounds = selectedFeatureBounds([...selectedParcels.values(), ...selectedBuildings.values()]);
      if (!bounds) {
        setStatus("Keine Auswahl zum Fokussieren vorhanden.");
        return false;
      }
      map.fitBounds(bounds, { padding: 80, maxZoom: 18.5, duration: 500 });
      return true;
    }

    function visibleStateEntries() {
      if (!map) return [];
      const bounds = map.getBounds();
      const center = map.getCenter();
      const mapStateSlugs = activeStateSlugs.size
        ? [...activeStateSlugs]
        : Object.keys(STATE_CENTERS);
      const visible = [];
      for (const [slug, state] of Object.entries(STATE_CENTERS)) {
        if (!mapStateSlugs.includes(slug)) continue;
        const inside = state.lon >= bounds.getWest() && state.lon <= bounds.getEast()
          && state.lat >= bounds.getSouth() && state.lat <= bounds.getNorth();
        if (inside) visible.push({ slug, ...state });
      }
      const candidates = (visible.length ? visible : Object.entries(STATE_CENTERS)
        .filter(([slug]) => mapStateSlugs.includes(slug))
        .map(([slug, state]) => ({ slug, ...state })))
        .map((state) => ({
          ...state,
          distance: haversine([center.lng, center.lat], [state.lon, state.lat]),
        }))
        .sort((a, b) => a.distance - b.distance);
      const limit = map.getZoom() < 7 ? 3 : 1;
      return candidates.slice(0, limit);
    }
    function refreshSourceInfo() {
      const visible = visibleStateEntries();
      const states = visible.length
        ? visible
        : [...activeStateSlugs].map((slug) => {
          const state = STATE_CENTERS[slug] || {name: slug};
          return { slug, name: state.name || slug };
        });
      if (!states.length) {
        if (sourceTitle) sourceTitle.textContent = "";
        sourceList.innerHTML = '<li class="source-item"><div class="source-line"><a href="https://maplibre.org/" target="_blank" rel="noopener noreferrer">© MapLibre</a></div></li>';
        return;
      }
      if (sourceTitle) sourceTitle.textContent = "";
      const mapLibre = '<li class="source-item"><div class="source-line"><a href="https://maplibre.org/" target="_blank" rel="noopener noreferrer">© MapLibre</a></div></li>';
      sourceList.innerHTML = mapLibre + states
        .map((state) => {
          const lines = formatMetadataInfo(state).map((line) => escapeHtml(line));
          return `<li class="source-item"><div class="source-line">${lines.join("")}</div></li>`;
        })
        .join("");
    }
    function formatResultList(results) {
      return results
        .map((result, index) => `
          <button class=\"search-result\" type=\"button\" data-index=\"${index}\">
            <div class=\"search-title\">${escapeHtml(result.label || \"Treffer\")}</div>
            <div class=\"search-meta\">${escapeHtml([result.subtitle, result.state_label || result.state].filter(Boolean).join(\" · \"))}</div>
          </button>
        `)
        .join(\"\");
    }

    function lonLatToTile(lon, lat, zoom) {
      const clampedLat = Math.max(-85.05112878, Math.min(85.05112878, Number(lat)));
      const scale = 2 ** zoom;
      const x = Math.floor(((Number(lon) + 180) / 360) * scale);
      const rad = clampedLat * Math.PI / 180;
      const y = Math.floor((1 - Math.log(Math.tan(rad) + 1 / Math.cos(rad)) / Math.PI) / 2 * scale);
      return { x: Math.max(0, Math.min(scale - 1, x)), y: Math.max(0, Math.min(scale - 1, y)), z: zoom };
    }

    function tileUrlFromTemplate(template, tile) {
      return String(template || "")
        .replace("{z}", String(tile.z))
        .replace("{x}", String(tile.x))
        .replace("{y}", String(tile.y));
    }

    function resultPrefetchPoints(result) {
      const points = [];
      if (result.center?.length === 2) {
        points.push([Number(result.center[0]), Number(result.center[1])]);
      }
      if (result.bbox?.length === 4) {
        const [minLon, minLat, maxLon, maxLat] = result.bbox.map(Number);
        if ([minLon, minLat, maxLon, maxLat].every(Number.isFinite)) {
          points.push(
            [(minLon + maxLon) / 2, (minLat + maxLat) / 2],
            [minLon, minLat],
            [minLon, maxLat],
            [maxLon, minLat],
            [maxLon, maxLat]
          );
        }
      }
      return points.filter(([lon, lat]) => Number.isFinite(lon) && Number.isFinite(lat));
    }

    async function prefetchResultTiles(result) {
      const template = window.__okTilejson?.tiles?.[0];
      if (!template || !result || !layerSettings.alkis) return;
      const points = resultPrefetchPoints(result);
      if (!points.length) return;
      const zooms = result.result_type === "street" ? [17] : [17, 18];
      const urls = [];
      const seen = new Set();
      for (const zoom of zooms) {
        for (const [lon, lat] of points) {
          const tile = lonLatToTile(lon, lat, zoom);
          for (let dx = -1; dx <= 1; dx += 1) {
            for (let dy = -1; dy <= 1; dy += 1) {
              const neighbor = { z: tile.z, x: tile.x + dx, y: tile.y + dy };
              const max = 2 ** neighbor.z;
              if (neighbor.x < 0 || neighbor.y < 0 || neighbor.x >= max || neighbor.y >= max) continue;
              const key = `${neighbor.z}/${neighbor.x}/${neighbor.y}`;
              if (seen.has(key)) continue;
              seen.add(key);
              urls.push(tileUrlFromTemplate(template, neighbor));
              if (urls.length >= 20) break;
            }
            if (urls.length >= 20) break;
          }
          if (urls.length >= 20) break;
        }
        if (urls.length >= 20) break;
      }
      if (!urls.length) return;
      const fetches = urls.map((url) => fetch(url, { cache: "force-cache", credentials: "same-origin" }).catch(() => null));
      await Promise.race([
        Promise.allSettled(fetches),
        new Promise((resolve) => window.setTimeout(resolve, 900)),
      ]);
    }

    async function zoomToResult(result) {
      await prefetchResultTiles(result);
      if (result.result_type === "street" && result.center && result.center.length === 2) {
        const targetZoom = Number.isFinite(Number(result.zoom)) ? Number(result.zoom) : 17.4;
        map.flyTo({ center: result.center, zoom: Math.max(targetZoom, 17.2), duration: 450 });
        return;
      }
      if (result.bbox && result.bbox.length === 4) {
        map.fitBounds(
          [[result.bbox[0], result.bbox[1]], [result.bbox[2], result.bbox[3]]],
          { padding: 80, maxZoom: 18.5, duration: 500 }
        );
        return;
      }
      if (result.center && result.center.length === 2) {
        const targetZoom = Number.isFinite(Number(result.zoom)) ? Number(result.zoom) : Math.max(map.getZoom(), 17.5);
        map.flyTo({ center: result.center, zoom: targetZoom, duration: 450 });
      }
    }

    function startupSearchQueryFromUrl() {
      const params = new URLSearchParams(window.location.search);
      if (params.get("okSearchOpen") !== "1") return "";
      const mode = params.get("okSearchMode") || "address";
      if (mode === "parcel") {
        const parts = [
          params.get("okGemarkung") ? `Gemarkung ${params.get("okGemarkung")}` : "",
          params.get("okFlur") ? `Flur ${params.get("okFlur")}` : "",
          params.get("okFlurstueck") ? `Flurstück ${params.get("okFlurstueck")}` : "",
        ].filter(Boolean);
        return parts.join(" ");
      }
      const streetLine = [params.get("okStreet"), params.get("okHouseNumber")].filter(Boolean).join(" ");
      const placeLine = [params.get("okPostcode"), params.get("okPlace")].filter(Boolean).join(" ");
      return [streetLine, placeLine].filter(Boolean).join(", ");
    }

    function applyStartupSearchFromUrl() {
      const query = startupSearchQueryFromUrl();
      if (!query || !searchInput) return;
      searchInput.value = query;
      searchInput.dataset.selectedSearchValue = "";
      searchInput.focus({ preventScroll: true });
      performSearch(query).catch((error) => {
        if (error.name === "AbortError") return;
        console.error(error);
        setStatus(`Suche konnte nicht geladen werden: ${error.message}`);
      });
    }

    async function performSearch(query) {
      const value = query.trim();
      if (value.length < 2) {
        searchResults.hidden = true;
        return;
      }
      const cacheKey = value.normalize("NFKC").toLocaleLowerCase("de-DE");
      let data = searchResponseCache.get(cacheKey);
      if (!data) {
        if (searchAbort) searchAbort.abort();
        searchAbort = new AbortController();
        const response = await fetch(`${searchUrl}&q=${encodeURIComponent(value)}&limit=12`, { signal: searchAbort.signal });
        if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
        data = await response.json();
        if (searchResponseCache.size >= 80) {
          searchResponseCache.delete(searchResponseCache.keys().next().value);
        }
        searchResponseCache.set(cacheKey, data);
      }
      const results = data.results || [];
      if (!results.length) {
        searchResults.innerHTML = `<div class=\"search-meta\" style=\"padding: 8px 10px\">Keine Treffer</div>`;
        searchResults.hidden = false;
        return;
      }
      searchResults.innerHTML = formatResultList(results);
      searchResults.hidden = false;

      for (const button of searchResults.querySelectorAll(\".search-result\")) {
        button.addEventListener(\"click\", async () => {
          const result = results[Number(button.dataset.index)];
          searchResults.hidden = true;
          if (!result) return;
          const selectedLabel = result.address?.label || result.label || searchInput.value;
          if (selectedLabel) {
            searchInput.value = selectedLabel;
            searchInput.dataset.selectedSearchValue = selectedLabel;
          }
          const shouldSelectFeature = result.result_type === "address" || result.kind === "parcel" || result.kind === "building";
          if (!shouldSelectFeature) {
            clearSelection();
            zoomToResult(result);
            return;
          }
          if (result.kind === \"parcel\" || result.kind === \"building\") {
            clearSelection();
            selectResult(result);
          }
          zoomToResult(result);
          if (result.center?.length === 2) {
            const preferredKind = result.kind === "parcel" ? "parcel" : result.kind === "building" || result.kind === "address" ? "building" : null;
            queryFeaturesAt(result.center[0], result.center[1], false, preferredKind).catch((error) => {
              console.error(error);
              setStatus(`Auswahl konnte nicht geladen werden: ${error.message}`);
            });
          };
        });
      }
    }

    function selectResult(result) {
      if (!result || !result.feature) return;
      if (result.kind === \"parcel\") selectedParcels.set(itemKey(result.feature), result.feature);
      if (result.kind === \"building\") selectedBuildings.set(itemKey(result.feature), result.feature);
      renderSelection();
    }

    function preferredSelectionKind(event) {
      if (!map || !event?.point) return null;
      try {
        const buildingLayers = ["building-fill", "building-outline"].filter((id) => map.getLayer(id));
        if (buildingLayers.length && map.queryRenderedFeatures(event.point, { layers: buildingLayers }).length) return "building";
        const parcelLayers = ["parcel-fill", "parcel-outline"].filter((id) => map.getLayer(id));
        if (parcelLayers.length && map.queryRenderedFeatures(event.point, { layers: parcelLayers }).length) return "parcel";
      } catch (error) {
        console.warn("Auswahltyp konnte nicht über Kartenlayer bestimmt werden", error);
      }
      return null;
    }

    async function queryFeaturesAt(lng, lat, additive, preferredKind = null) {
      const url = `${featureUrl}&lon=${encodeURIComponent(lng)}&lat=${encodeURIComponent(lat)}`;
      const data = await loadJson(url);
      const resolvedKind = preferredKind || ((data.buildings || []).length ? "building" : (data.parcels || []).length ? "parcel" : null);
      if (!additive) clearSelection();
      if (!resolvedKind || resolvedKind === "parcel") {
        for (const parcel of data.parcels || []) {
          const key = itemKey(parcel);
          if (additive && selectedParcels.has(key)) selectedParcels.delete(key);
          else selectedParcels.set(key, parcel);
        }
      }
      if (!resolvedKind || resolvedKind === "building") {
        for (const building of data.buildings || []) {
          const key = itemKey(building);
          if (additive && selectedBuildings.has(key)) selectedBuildings.delete(key);
          else selectedBuildings.set(key, building);
        }
      }
      renderSelection();
    }

    function startMeasureVertexDrag(event, tool) {
      if (activeTool === "erase") return;
      const feature = event.features && event.features[0];
      if (!feature) return;
      dragMeasureVertex = { tool, index: Number(feature.properties.index) };
      suppressNextMapClick = true;
      if (event.preventDefault) event.preventDefault();
      if (event.originalEvent?.preventDefault) event.originalEvent.preventDefault();
      if (map.dragPan?.disable) map.dragPan.disable();
      if (map.touchZoomRotate?.disable) map.touchZoomRotate.disable();
      map.getCanvas().style.cursor = "grabbing";
    }

    function updateMeasureVertexDrag(event) {
      if (!dragMeasureVertex) return;
      if (!event.lngLat) return;
      const coord = nearestSnapCoordinate(event, [event.lngLat.lng, event.lngLat.lat]);
      if (dragMeasureVertex.tool === "line" && measurePoints[dragMeasureVertex.index]) {
        measurePoints[dragMeasureVertex.index] = coord;
      }
      if (dragMeasureVertex.tool === "area" && areaPoints[dragMeasureVertex.index]) {
        areaPoints[dragMeasureVertex.index] = coord;
      }
      applyMeasureLayers();
    }

    function stopMeasureVertexDrag() {
      if (!dragMeasureVertex) return;
      dragMeasureVertex = null;
      if (map.dragPan?.enable) map.dragPan.enable();
      if (map.touchZoomRotate?.enable) map.touchZoomRotate.enable();
      map.getCanvas().style.cursor = activeTool === "measureLine" || activeTool === "measureArea" ? "crosshair" : "";
      setMeasureStatus();
    }

    function startPinDrag(event) {
      if (activeTool === "erase") return;
      if (!pinnedPoints.length) return;
      const feature = event.features && event.features[0];
      const pinIndex = Number(feature?.properties?.pinIndex);
      if (!Number.isInteger(pinIndex) || !pinnedPoints[pinIndex]) return;
      dragPin = { index: pinIndex };
      pinMoved = false;
      suppressNextMapClick = true;
      if (event.preventDefault) event.preventDefault();
      if (event.originalEvent?.preventDefault) event.originalEvent.preventDefault();
      if (map.dragPan?.disable) map.dragPan.disable();
      if (map.touchZoomRotate?.disable) map.touchZoomRotate.disable();
      map.getCanvas().style.cursor = "grabbing";
    }

    function updatePinDrag(event) {
      if (!dragPin || !event.lngLat) return;
      pinMoved = true;
      pinnedPoints[dragPin.index] = nearestSnapCoordinate(event, [event.lngLat.lng, event.lngLat.lat]);
      setPinSource();
    }

    function stopPinDrag() {
      if (!dragPin) return;
      dragPin = null;
      if (pinMoved) {
        suppressNextMapClick = true;
      }
      pinMoved = false;
      if (map.dragPan?.enable) map.dragPan.enable();
      if (map.touchZoomRotate?.enable) map.touchZoomRotate.enable();
      setSnapIndicator(null);
      map.getCanvas().style.cursor = activeTool === "pin" ? "crosshair" : "";
    }

    function startAnnotationVertexDrag(event) {
      if (activeTool === "erase") return;
      const feature = event.features && event.features[0];
      if (!feature || (feature.properties.annotationIndex === undefined && String(feature.properties.draft) !== "true")) return;
      dragAnnotationVertex = {
        draft: String(feature.properties.draft) === "true",
        annotationIndex: feature.properties.annotationIndex === undefined ? null : Number(feature.properties.annotationIndex),
        vertexIndex: Number(feature.properties.vertexIndex),
      };
      suppressNextMapClick = true;
      if (event.preventDefault) event.preventDefault();
      if (event.originalEvent?.preventDefault) event.originalEvent.preventDefault();
      if (map.dragPan?.disable) map.dragPan.disable();
      if (map.touchZoomRotate?.disable) map.touchZoomRotate.disable();
      map.getCanvas().style.cursor = "grabbing";
    }

    function updateAnnotationVertexDrag(event) {
      if (!dragAnnotationVertex || !event.lngLat) return;
      const nextPoint = nearestSnapCoordinate(event, [event.lngLat.lng, event.lngLat.lat]);
      if (dragAnnotationVertex.draft) {
        if (!annotationPoints[dragAnnotationVertex.vertexIndex]) return;
        annotationPoints[dragAnnotationVertex.vertexIndex] = nextPoint;
      } else {
        const annotation = annotations[dragAnnotationVertex.annotationIndex];
        if (!annotation || !annotation.points[dragAnnotationVertex.vertexIndex]) return;
        annotation.points[dragAnnotationVertex.vertexIndex] = nextPoint;
      }
      applyAnnotationLayers();
    }

    function stopAnnotationVertexDrag() {
      if (!dragAnnotationVertex) return;
      dragAnnotationVertex = null;
      if (map.dragPan?.enable) map.dragPan.enable();
      if (map.touchZoomRotate?.enable) map.touchZoomRotate.enable();
      setSnapIndicator(null);
      map.getCanvas().style.cursor = activeTool === "drawLine" || activeTool === "drawPolygon" ? "crosshair" : "";
    }

    function drawingToolActive() {
      return ["measureLine", "measureArea", "measureRadius", "drawLine", "drawPolygon", "pin", "erase"].includes(activeTool);
    }

    function startSpacePan(event) {
      if (event.code !== "Space" || spacePanActive || !drawingToolActive()) return;
      const target = event.target;
      if (target && ["INPUT", "TEXTAREA", "SELECT", "BUTTON"].includes(target.tagName)) return;
      event.preventDefault();
      spacePanActive = true;
      if (map?.dragPan?.enable) map.dragPan.enable();
      if (map) map.getCanvas().style.cursor = "grab";
    }

    function stopSpacePan(event) {
      if (event.code !== "Space" || !spacePanActive) return;
      event.preventDefault();
      spacePanActive = false;
      if (map) {
        map.getCanvas().style.cursor = drawingToolActive()
          ? (activeTool === "erase" ? ERASER_CURSOR : "crosshair")
          : "";
      }
      setEraserInteractionState();
    }

    function finishActiveDrawing() {
      if (activeTool === "drawLine" || activeTool === "drawPolygon") {
        dropDuplicateAnnotationEndpoint();
        commitAnnotation();
        setSnapIndicator(null);
        return true;
      }
      return false;
    }

    function handleEscapeFinish(event) {
      if (event.key !== "Escape") return;
      if (finishActiveDrawing()) {
        event.preventDefault();
      }
    }

    function cancelLongPress() {
      if (longPressTimer) {
        clearTimeout(longPressTimer);
        longPressTimer = 0;
      }
      longPressStart = null;
    }

    function startLongPressSelection(event) {
      if (activeTool !== "none" || dragMeasureVertex || !event.lngLat) return;
      const touches = event.originalEvent?.touches;
      if (touches && touches.length > 1) return;
      cancelLongPress();
      const startPoint = event.point;
      const lngLat = [event.lngLat.lng, event.lngLat.lat];
      longPressStart = startPoint;
      const preferredKind = preferredSelectionKind(event);
      longPressTimer = window.setTimeout(() => {
        suppressNextMapClick = true;
        longPressTimer = 0;
        if (navigator?.vibrate) navigator.vibrate(18);
        queryFeaturesAt(lngLat[0], lngLat[1], true, preferredKind).catch((error) => {
          console.error(error);
          setStatus(`Auswahl konnte nicht geladen werden: ${error.message}`);
        });
      }, 520);
    }

    function moveLongPressSelection(event) {
      if (!longPressTimer || !longPressStart || !event.point) return;
      const dx = event.point.x - longPressStart.x;
      const dy = event.point.y - longPressStart.y;
      if (Math.sqrt(dx * dx + dy * dy) > 12) {
        cancelLongPress();
      }
    }

    function attachMapEvents() {
      map.on(\"click\", (event) => {
        if (spacePanActive) return;
        if (exportCropMode) return;
        if (suppressNextMapClick) {
          suppressNextMapClick = false;
          return;
        }
        if (activeTool === "erase") {
          eraseItemAt(event);
          return;
        }
        if (activeTool === "measureLine" || activeTool === "measureArea" || activeTool === "measureRadius") {
          addMeasurePoint(nearestSnapCoordinate(event, [event.lngLat.lng, event.lngLat.lat]));
          return;
        }
        if (activeTool === "drawLine" || activeTool === "drawPolygon") {
          addAnnotationPoint(nearestSnapCoordinate(event, [event.lngLat.lng, event.lngLat.lat]));
          return;
        }
        if (activeTool === "pin") {
          const pin = nearestSnapCoordinate(event, [event.lngLat.lng, event.lngLat.lat]);
          pinnedPoints.push(pin);
          setPinSource();
          setSnapIndicator(null);
          return;
        }
        const additive = Boolean(event.originalEvent?.metaKey || event.originalEvent?.ctrlKey);
        queryFeaturesAt(event.lngLat.lng, event.lngLat.lat, additive, preferredSelectionKind(event)).catch((error) => {
          console.error(error);
          setStatus(`Auswahl konnte nicht geladen werden: ${error.message}`);
        });
      });
      map.on("dblclick", (event) => {
        if (spacePanActive) return;
        if (activeTool === "drawLine" || activeTool === "drawPolygon") {
          if (event.preventDefault) event.preventDefault();
          if (event.originalEvent?.preventDefault) event.originalEvent.preventDefault();
          finishActiveDrawing();
        }
      });
      map.on("contextmenu", (event) => {
        if (finishActiveDrawing()) {
          if (event.preventDefault) event.preventDefault();
          if (event.originalEvent?.preventDefault) event.originalEvent.preventDefault();
        }
      });
      map.on("mousedown", startExportCrop);
      map.on("touchstart", startExportCrop);
      map.on("mousedown", startEraseBrush);
      map.on("touchstart", startEraseBrush);
      map.on("mousedown", "measure-point", (event) => startMeasureVertexDrag(event, "line"));
      map.on("mousedown", "measure-area-point", (event) => startMeasureVertexDrag(event, "area"));
      map.on("touchstart", "measure-point", (event) => startMeasureVertexDrag(event, "line"));
      map.on("touchstart", "measure-area-point", (event) => startMeasureVertexDrag(event, "area"));
      map.on("mousedown", "annotations-point", startAnnotationVertexDrag);
      map.on("touchstart", "annotations-point", startAnnotationVertexDrag);
      map.on("mousedown", "pins-fill", startPinDrag);
      map.on("touchstart", "pins-fill", startPinDrag);
      map.on("touchstart", startLongPressSelection);
      map.on("touchmove", moveLongPressSelection);
      map.on("touchend", cancelLongPress);
      map.on("touchcancel", () => {
        stopExportCrop();
        stopEraseBrush();
        cancelLongPress();
      });
      map.on("mouseenter", "measure-point", () => { map.getCanvas().style.cursor = "grab"; });
      map.on("mouseenter", "measure-area-point", () => { map.getCanvas().style.cursor = "grab"; });
      map.on("mouseenter", "annotations-point", () => { map.getCanvas().style.cursor = "grab"; });
      map.on("mouseenter", "pins-fill", () => { map.getCanvas().style.cursor = activeTool === "pin" ? "crosshair" : "grab"; });
      map.on("mouseleave", "measure-point", () => {
        if (!dragMeasureVertex) map.getCanvas().style.cursor = activeTool === "measureLine" || activeTool === "measureArea" ? "crosshair" : "";
      });
      map.on("mouseleave", "measure-area-point", () => {
        if (!dragMeasureVertex) map.getCanvas().style.cursor = activeTool === "measureLine" || activeTool === "measureArea" ? "crosshair" : "";
      });
      map.on("mouseleave", "annotations-point", () => {
        if (!dragAnnotationVertex) map.getCanvas().style.cursor = activeTool === "drawLine" || activeTool === "drawPolygon" ? "crosshair" : "";
      });
      map.on("mouseleave", "pins-fill", () => {
        if (!dragPin) map.getCanvas().style.cursor = activeTool === "pin" ? "crosshair" : "";
      });
      map.on(\"mousemove\", (event) => {
        updateCursorInfo(event);
        updateExportCrop(event);
        updateEraseBrush(event);
        updateMeasureVertexDrag(event);
        updateAnnotationVertexDrag(event);
        updatePinDrag(event);
        if (activeTool === "pin" && !dragPin && !spacePanActive) {
          map.getCanvas().style.cursor = "crosshair";
        }
      });
      map.on("touchmove", (event) => {
        updateExportCrop(event);
        updateEraseBrush(event);
        updateMeasureVertexDrag(event);
        updateAnnotationVertexDrag(event);
        updatePinDrag(event);
      });
      map.on("mouseup", () => {
        stopExportCrop();
        stopEraseBrush();
        stopMeasureVertexDrag();
        stopAnnotationVertexDrag();
        stopPinDrag();
      });
      map.on("touchend", () => {
        stopExportCrop();
        stopEraseBrush();
        stopMeasureVertexDrag();
        stopAnnotationVertexDrag();
        stopPinDrag();
      });
      map.on("touchcancel", () => {
        stopExportCrop();
        stopMeasureVertexDrag();
        stopAnnotationVertexDrag();
        stopPinDrag();
      });
      map.on(\"mouseout\", () => {
        statusEl.textContent = \"Karte wird geladen ...\";
        stopMeasureVertexDrag();
        stopAnnotationVertexDrag();
        stopPinDrag();
      });
      map.on(\"move\", updateScaleInfo);
      map.on(\"zoom\", updateScaleInfo);
      map.on(\"moveend\", () => {
      updateBootstrapBackdrop();
        refreshSourceInfo();
        if (layerSettings.aerial) updateLuftbildLayers();
      });
    }

    async function initializeMeta() {
      const [datasetsPayload, statePayload] = await Promise.all([
        loadJson(datasetsUrl).catch(() => ({ datasets: [] })),
        loadJson(metadataUrl).catch(() => ({ states: [] }))
      ]);
      const datasetInfo = (datasetsPayload.datasets || []).find((item) => item.id === \"__DATASET__\");
      if (datasetInfo && Array.isArray(datasetInfo.sources)) {
        for (const source of datasetInfo.sources) {
          const match = String(source).match(/^([a-z0-9_-]+)_(?:detail|overview)\\.pmtiles$/);
          if (!match) continue;
          activeStateSlugs.add(match[1]);
        }
      }
      if (!activeStateSlugs.size && \"__DATASET__\" !== \"deutschland\") {
        activeStateSlugs.add(stateSlug(\"__DATASET__\"));
      }
      for (const row of statePayload.states || []) {
        if (row && row.bundesland) {
          stateMetadata.set(stateSlug(row.bundesland), row);
        }
      }
      refreshSourceInfo();
    }

    async function main() {
      const [style, tilejson] = await Promise.all([
        loadJson(__STYLE_URL__),
        loadJson(__TILEJSON_URL__)
      ]);
      window.__okTilejson = tilejson;

      const bounds = tilejson.bounds || [5.5, 47.0, 15.5, 55.5];
      const webMinZoom = Math.min(tilejson.minzoom ?? 4, 4);
      const center = tilejson.center || [
        (bounds[0] + bounds[2]) / 2,
        (bounds[1] + bounds[3]) / 2,
        Math.max(tilejson.minzoom || webMinZoom, webMinZoom),
      ];

      map = new maplibregl.Map({
        container: \"map\",
        preserveDrawingBuffer: !isTouchLikeDevice,
        style,
        center: [center[0], center[1]],
        zoom: center[2],
        minZoom: webMinZoom,
        maxZoom: Math.max(tilejson.maxzoom || 20, 20),
        attributionControl: false,
        dragRotate: false,
        pitchWithRotate: false,
        pitch: 0,
        bearing: 0,
        hash: true,
      });

      window.__okMap = map;\n      map.addControl(new maplibregl.NavigationControl({ showCompass: false, showZoom: true }), \"bottom-left\");
      map.dragRotate.disable();
      map.touchZoomRotate.disableRotation();

      map.once(\"load\", async () => {
        addSelectionLayers();
        attachMapEvents();
        map.setBearing(0);
        map.setPitch(0);
        if (map.getLayer("state-outlines")) map.moveLayer("state-outlines");
        if (map.getLayer("state-labels")) map.moveLayer("state-labels");
        normalizeStreetLabelWrapping();
        syncLayerSettingsUi();\n        applyLayerSettings();
        updateScaleInfo();
        setStatus(\"Karte bereit.\");
        statusEl.dataset.ready = \"true\";
        statusLeft.textContent = \"1:–\";
        statusCenter.textContent = \"–\";
        statusRight.textContent = \"\";
        await initializeMeta();
        const boundsObj = map.getBounds();
        window.__okGermanyBounds = new maplibregl.LngLatBounds(
          boundsObj.getSouthWest(),
          boundsObj.getNorthEast()
        );
        captureStableFrame();
        applyStartupSearchFromUrl();
      });

      map.on("movestart", () => showFrameFallback());
      map.on("zoomstart", () => showFrameFallback());
      map.on("dragstart", () => showFrameFallback());
      map.on("idle", () => {
        captureStableFrame();
        hideFrameFallbackSoon();
      });
      map.on("render", () => {
        if (map.loaded()) captureStableFrame();
      });
      map.on("rotate", () => {
        if (map.getBearing() !== 0) map.setBearing(0);
      });
      map.on("pitch", () => {
        if (map.getPitch() !== 0) map.setPitch(0);
      });

      if (!window.location.hash || !/^#\\d/.test(window.location.hash)) {
        map.fitBounds(
          [[bounds[0], bounds[1]], [bounds[2], bounds[3]]],
          { padding: 40, duration: 0, maxZoom: 10 }
        );
      }

      searchForm.addEventListener(\"submit\", (event) => {
        event.preventDefault();
        performSearch(searchInput.value).catch((error) => {
          if (error.name === \"AbortError\") return;
          console.error(error);
          setStatus(`Suche konnte nicht geladen werden: ${error.message}`);
        });
      });

      searchInput.addEventListener(\"input\", () => {
        window.clearTimeout(searchTimer);
        const value = searchInput.value.trim();
        delete searchInput.dataset.selectedSearchValue;
        if (value.length < 3) {
          searchResults.hidden = true;
          return;
        }
        searchTimer = window.setTimeout(() => {
          performSearch(value).catch((error) => {
            if (error.name === \"AbortError\") return;
            console.error(error);
            setStatus(`Suche konnte nicht geladen werden: ${error.message}`);
          });
        }, 120);
      });

      searchInput.addEventListener("focus", () => {
        const value = searchInput.value.trim();
        if (value.length < 3 || value === searchInput.dataset.selectedSearchValue) return;
        window.clearTimeout(searchTimer);
        searchTimer = window.setTimeout(() => {
          performSearch(value).catch((error) => {
            if (error.name === "AbortError") return;
            console.error(error);
            setStatus(`Suche konnte nicht geladen werden: ${error.message}`);
          });
        }, 80);
      });

      selectionClose.addEventListener(\"click\", () => {
        clearSelection();
      });

      sourceToggle.addEventListener(\"click\", () => {
        const nextOpen = sourceDetails.hidden;
        sourcePanel.dataset.open = nextOpen ? \"true\" : \"false\";
        sourceDetails.hidden = !nextOpen;
        sourceToggle.setAttribute(\"aria-expanded\", nextOpen ? \"true\" : \"false\");
      });

      toolDockToggle.addEventListener(\"click\", () => {
        const nextOpen = toolDock.dataset.open !== \"true\";
        toolDock.dataset.open = nextOpen ? \"true\" : \"false\";
        toolDockToggle.setAttribute(\"aria-expanded\", nextOpen ? \"true\" : \"false\");
      });

      layerToggle.addEventListener("click", () => {
        const nextOpen = layerMenu.hidden;
        layerMenu.hidden = !nextOpen;
        layerToggle.setAttribute("aria-expanded", nextOpen ? "true" : "false");
      });
      for (const input of layerGroupInputs) {
        input.addEventListener("click", (event) => event.stopPropagation());
        input.addEventListener("change", () => {
          const keys = layerSettingGroups[input.dataset.layerGroup] || [];
          for (const key of keys) layerSettings[key] = input.checked;
          syncLayerSettingsUi();
          applyLayerSettings();
        });
      }
      for (const input of layerSettingInputs) {
        input.addEventListener("change", () => {
          const key = input.dataset.layerSetting;
          if (Object.prototype.hasOwnProperty.call(layerSettings, key)) {
            layerSettings[key] = input.checked;
            syncLayerSettingsUi();
            applyLayerSettings();
          }
        });
      }
      document.addEventListener("click", (event) => {
        if (!event.target.closest("#layerSwitch")) {
          layerMenu.hidden = true;
          layerToggle.setAttribute("aria-expanded", "false");
        }
      });
      document.addEventListener("keydown", startSpacePan);
      document.addEventListener("keydown", handleEscapeFinish);
      document.addEventListener("keyup", stopSpacePan);

      annotationColorPicker.addEventListener("input", () => {
        annotationColor = annotationColorPicker.value || "#f86d14";
        applyAnnotationLayers();
      });

      toolSelect.addEventListener(\"click\", () => {
        setToolMode(\"none\");
      });

      toolMeasureLine.addEventListener(\"click\", () => {
        setToolMode(\"measureLine\");
      });

      toolMeasureArea.addEventListener(\"click\", () => {
        setToolMode(\"measureArea\");
      });

      toolDrawLine.addEventListener("click", () => {
        setToolMode("drawLine");
      });

      toolDrawPolygon.addEventListener("click", () => {
        setToolMode("drawPolygon");
      });

      toolErase.addEventListener("click", () => {
        setToolMode("erase");
      });

      toolMeasureRadius.addEventListener(\"click\", () => {
        setToolMode(\"measureRadius\");
      });

      toolMeasureUndo.addEventListener(\"click\", () => {
        if (activeTool === \"measureLine\" || activeTool === \"measureArea\" || activeTool === \"measureRadius\") {
          removeLastMeasurePoint();
        } else {
          setStatus(\"Messwerkzeug aktivieren, dann letzten Punkt entfernen.\");
        }
      });

      toolPin.addEventListener(\"click\", () => {
        setToolMode(\"pin\");
      });

      toolMapExport.addEventListener("click", () => {
        setToolMode("none");
        const willOpen = exportPanel.hidden;
        exportPanel.hidden = !willOpen;
        toolMapExport.classList.toggle("active", willOpen);
        toolMapExport.dataset.state = willOpen ? "active" : "off";
        toolMapExport.setAttribute("aria-pressed", willOpen ? "true" : "false");
        if (willOpen) {
          updateExportSummary();
          setExportSettingsOpen(false);
          setExportCropMode(true);
        } else {
          setExportCropMode(false);
          clearExportCropRect();
        }
      });

      exportClose.addEventListener("click", () => {
        exportPanel.hidden = true;
        toolMapExport.classList.remove("active");
        toolMapExport.dataset.state = "off";
        toolMapExport.setAttribute("aria-pressed", "false");
        setExportCropMode(false);
        setExportSettingsOpen(false);
        clearExportCropRect();
      });

      exportSelectArea?.addEventListener("click", () => {
        setToolMode("none");
        setExportCropMode(true);
      });

      exportUseView?.addEventListener("click", () => {
        setExportCropMode(false);
        clearExportCropRect();
      });

      [exportPaper, exportOrientation, exportScale].forEach((control) => {
        control?.addEventListener("change", () => {
          updateExportSummary();
          refitExportCropRectToOrientation();
          if (exportCropRect) setExportStatus("Rahmen an Format und Ausrichtung angepasst.");
        });
      });
      exportOutput?.addEventListener("change", updateExportSummary);

      exportSettingsToggle?.addEventListener("click", () => {
        const nextOpen = exportPanel.dataset.settingsOpen !== "true";
        setExportSettingsOpen(nextOpen);
      });

      map.on("move", () => {
        if (!exportPanel.hidden && exportCropRect) refitExportCropRectToOrientation();
      });
      map.on("zoom", () => {
        if (!exportPanel.hidden && exportCropRect) refitExportCropRectToOrientation();
      });

      exportDownloadPng.addEventListener("click", () => {
        setExportCropMode(false);
        exportMapFile();
      });

      toolCopyCoords.addEventListener(\"click\", async () => {
        const center = map.getCenter();
        const text = [
          `Kartenmitte (WGS84, Dezimal): ${formatCoordinate(center.lat, "lat")}, ${formatCoordinate(center.lng, "lon")}`,
          `Kartenmitte (WGS84, DMS): ${formatCoordinateDms(center.lat, "lat")}, ${formatCoordinateDms(center.lng, "lon")}`,
        ].join(\"\\n\");
        try {
          await copyText(text);
          setStatus(\"Koordinaten in die Zwischenablage kopiert.\");
        } catch (error) {
          setStatus(\"Koordinaten konnten nicht kopiert werden.\");
        }
      });

      toolCopyCursor.addEventListener(\"click\", async () => {
        if (!lastCursorLngLat) {
          setStatus(\"Bitte zuerst die Karte mit der Maus berühren.\");
          return;
        }
        const [lng, lat] = lastCursorLngLat;
        const text = [
          `Maus (WGS84, Dezimal): ${formatCoordinate(lat, "lat")}, ${formatCoordinate(lng, "lon")}`,
          `Maus (WGS84, DMS): ${formatCoordinateDms(lat, \"lat\")}, ${formatCoordinateDms(lng, \"lon\")}`,
        ].join(\"\\n\");
        try {
          await copyText(text);
          setStatus(\"Maus-Position in die Zwischenablage kopiert.\");
        } catch (error) {
          setStatus(\"Koordinaten konnten nicht kopiert werden.\");
        }
      });

      toolExport.addEventListener(\"click\", async () => {
        const parcels = [...selectedParcels.values()];
        const buildings = [...selectedBuildings.values()];
        const payload = {
          createdAt: new Date().toISOString(),
          parcels,
          buildings,
          parcelCount: parcels.length,
          buildingCount: buildings.length,
        };
        if (!parcels.length && !buildings.length) {
          setStatus(\"Keine Auswahl zum Exportieren vorhanden.\");
          return;
        }
        try {
          await copyText(JSON.stringify(payload, null, 2));
          setStatus(\"Auswahl als JSON kopiert.\");
        } catch (error) {
          setStatus(\"Export konnte nicht in die Zwischenablage kopiert werden.\");
        }
      });

      toolExportCsv.addEventListener(\"click\", async () => {
        if (!selectedParcels.size && !selectedBuildings.size) {
          setStatus(\"Keine Auswahl zum CSV-Export vorhanden.\");
          return;
        }
        try {
          await copyText(exportSelectionAsCsv());
          setStatus(\"Auswahl als CSV kopiert.\");
        } catch (error) {
          setStatus(\"CSV-Export konnte nicht in die Zwischenablage kopiert werden.\");
        }
      });

      toolExportView.addEventListener(\"click\", async () => {
        const center = map.getCenter();
        const state = `#${center.lng.toFixed(6)},${center.lat.toFixed(6)},${map.getZoom().toFixed(2)}`;
        const url = `${window.location.origin}${window.location.pathname}${window.location.search}${state}`;
        try {
          await copyText(url);
          setStatus(\"Ansicht wurde als Link kopiert.\");
        } catch (error) {
          setStatus(\"Ansicht-Link konnte nicht kopiert werden.\");
        }
      });

      toolCopyViewport.addEventListener(\"click\", async () => {
        const payload = copyViewportMetadata();
        if (!payload) {
          setStatus(\"Ausschnitt konnte nicht erfasst werden.\");
          return;
        }
        try {
          await copyText(JSON.stringify(payload, null, 2));
          setStatus(\"Ausschnittdaten kopiert.\");
        } catch (error) {
          setStatus(\"Ausschnitt konnte nicht kopiert werden.\");
        }
      });

      toolSelectionReport.addEventListener(\"click\", async () => {
        const payload = copySelectionAsReport();
        if (!payload.selection.parcelCount && !payload.selection.buildingCount) {
          setStatus(\"Keine Auswahl für den Exposé-Export vorhanden.\");
          return;
        }
        try {
          await copyText(selectionReportText(payload));
          setStatus(\"Exposé-Zusammenfassung kopiert.\");
        } catch (error) {
          setStatus(\"Exposé-Export konnte nicht kopiert werden.\");
        }
      });

      toolZoomSelection.addEventListener(\"click\", () => {
        if (zoomToSelection()) {
          setStatus(\"Auswahl in den Fokus gesetzt.\");
        }
      });

      toolClear.addEventListener(\"click\", () => {
        clearSelection();
        clearMeasure();
        pinnedPoints = [];
        if (isMapReady()) {
          setPinSource();
        }
        setToolMode(\"none\");
      });

      toolHome.addEventListener(\"click\", () => {
        map.fitBounds(
          [[bounds[0], bounds[1]], [bounds[2], bounds[3]]],
          { padding: 40, duration: 200 }
        );
      });

      toolCopy.addEventListener(\"click\", async () => {
        const center = map.getCenter();
        const state = `#${Math.round(center.lng * 1000000) / 1000000},${Math.round(center.lat * 1000000) / 1000000},${map.getZoom().toFixed(2)}`;
        const url = `${window.location.origin}${window.location.pathname}${window.location.search}${state}`;
        try {
          await copyText(url);
          setStatus(\"Ansicht-Link kopiert.\");
        } catch (error) {
          setStatus(\"Link konnte nicht in die Zwischenablage kopiert werden.\");
        }
      });
    }

    main().catch((error) => {
      console.error(error);
      setStatus(`Karte konnte nicht geladen werden: ${error.message}`);
    });
  </script>
</body>
    </html>"""
    state_centers = {slug: {"name": name, "lon": lon, "lat": lat} for slug, (name, lon, lat) in STATE_LABEL_POINTS.items()}
    return (
        template
        .replace("__STYLE_URL__", json.dumps(style_url))
        .replace("__TILEJSON_URL__", json.dumps(tilejson_url))
        .replace("__FEATURE_URL__", json.dumps(feature_url))
        .replace("__SEARCH_URL__", json.dumps(search_url))
        .replace("__DATASETS_URL__", json.dumps(datasets_url))
        .replace("__METADATA_URL__", json.dumps(metadata_url))
        .replace("__STATE_CENTERS__", json.dumps(state_centers))
        .replace("__DATASET__", dataset)
    )



def _safe_version_name(version: str) -> str:
    value = version.strip()
    if not VERSION_RE.match(value) or "/" in value or ".." in value:
        raise HTTPException(status_code=400, detail="invalid tile version")
    return value


def _volume_versions_root() -> Path:
    return ACTIVE_VOLUME_ROOT / "versions"


def _volume_active_root() -> Path:
    return ACTIVE_VOLUME_ROOT / "active"


def _volume_incoming_root() -> Path:
    return ACTIVE_VOLUME_ROOT / ".incoming"


def _volume_version_dir(state_slug: str, version_name: str) -> Path:
    if not DATASET_RE.match(state_slug):
        raise HTTPException(status_code=400, detail="invalid state slug")
    return _volume_versions_root() / state_slug / _safe_version_name(version_name)


def _volume_upload_dir(state_slug: str, version_name: str) -> Path:
    if not DATASET_RE.match(state_slug):
        raise HTTPException(status_code=400, detail="invalid state slug")
    return _volume_incoming_root() / state_slug / _safe_version_name(version_name)


def _validate_volume_filenames(files: list[dict]) -> list[dict]:
    names = [os.path.basename(str(item.get("filename") or "")) for item in files]
    incoming = set(names)
    missing = sorted(VOLUME_REQUIRED_FILES - incoming)
    unexpected = sorted(incoming - VOLUME_REQUIRED_FILES)
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if missing or unexpected or duplicates:
        raise HTTPException(
            status_code=400,
            detail=(
                "Exactly these 3 standard tile state files are required: "
                f"{', '.join(sorted(VOLUME_REQUIRED_FILES))}. "
                f"missing: {', '.join(missing)} unexpected: {', '.join(unexpected)} duplicates: {', '.join(duplicates)}"
            ),
        )
    result = []
    for item in files:
        filename = os.path.basename(str(item.get("filename") or ""))
        size_bytes = int(item.get("size_bytes") or 0)
        if size_bytes <= 0:
            raise HTTPException(status_code=400, detail=f"empty upload file: {filename}")
        result.append({"filename": filename, "size_bytes": size_bytes})
    return sorted(result, key=lambda item: item["filename"])


def _volume_upload_file_status(upload_dir: Path, item: dict) -> dict:
    filename = item["filename"]
    size_bytes = int(item["size_bytes"])
    partial_path = upload_dir / f"{filename}.partial"
    final_path = upload_dir / filename
    partial_bytes = partial_path.stat().st_size if partial_path.is_file() else 0
    final_bytes = final_path.stat().st_size if final_path.is_file() else 0
    uploaded_bytes = final_bytes if final_bytes else partial_bytes
    if uploaded_bytes > size_bytes:
        raise HTTPException(
            status_code=409,
            detail=f"existing upload for {filename} is larger than selected file: {uploaded_bytes} > {size_bytes}",
        )
    return {
        "filename": filename,
        "size_bytes": size_bytes,
        "part_size": VOLUME_UPLOAD_PART_BYTES,
        "uploaded_bytes": uploaded_bytes,
        "partial_bytes": partial_bytes,
        "complete": final_bytes == size_bytes,
    }


def _volume_upload_sessions(state_slug: str) -> list[dict]:
    if not DATASET_RE.match(state_slug):
        raise HTTPException(status_code=400, detail="invalid state slug")
    state_dir = _volume_incoming_root() / state_slug
    if not state_dir.is_dir():
        return []
    sessions = []
    for upload_dir in sorted(state_dir.iterdir(), key=lambda path: path.stat().st_mtime, reverse=True):
        if not upload_dir.is_dir():
            continue
        files = []
        uploaded_total = 0
        for filename in sorted(VOLUME_REQUIRED_FILES):
            partial_path = upload_dir / f"{filename}.partial"
            final_path = upload_dir / filename
            partial_bytes = partial_path.stat().st_size if partial_path.is_file() else 0
            final_bytes = final_path.stat().st_size if final_path.is_file() else 0
            uploaded_bytes = final_bytes if final_bytes else partial_bytes
            uploaded_total += uploaded_bytes
            files.append({
                "filename": filename,
                "uploaded_bytes": uploaded_bytes,
                "partial_bytes": partial_bytes,
                "final_bytes": final_bytes,
                "complete": final_bytes > 0 and partial_bytes == 0,
            })
        if uploaded_total <= 0:
            continue
        stat = upload_dir.stat()
        sessions.append({
            "version_name": upload_dir.name,
            "uploaded_bytes": uploaded_total,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(stat.st_mtime)),
            "files": files,
        })
    return sessions


def _validate_volume_state_dir(path: Path) -> dict:
    for filename in VOLUME_REQUIRED_FILES:
        if not (path / filename).is_file():
            raise HTTPException(status_code=400, detail=f"missing uploaded file: {filename}")
    for sqlite_name in ("features.sqlite", "search.sqlite"):
        with (path / sqlite_name).open("rb") as handle:
            if handle.read(16) != b"SQLite format 3\x00":
                raise HTTPException(status_code=400, detail=f"{sqlite_name} is not a SQLite database")
    pmtiles_path = path / "alkis.pmtiles"
    with pmtiles_path.open("rb") as handle:
        if pmtiles_path.stat().st_size < 127 or handle.read(7) != b"PMTiles":
            raise HTTPException(status_code=400, detail="alkis.pmtiles is not a PMTiles file")
    total = sum((path / filename).stat().st_size for filename in VOLUME_REQUIRED_FILES)
    return {"files": len(VOLUME_REQUIRED_FILES), "bytes_total": total}


def _write_volume_upload_manifest(path: Path, *, state_slug: str, bundesland: str, version_name: str, files: list[dict]) -> None:
    manifest = {
        "format": "openkataster-tile-state-folder",
        "bundesland": bundesland,
        "state_slug": state_slug,
        "version_name": version_name,
        "uploaded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "files": len(files),
        "bytes_total": sum(int(item["size_bytes"]) for item in files),
        "upload_contract": "standard-maplibre-pmtiles-features-search-v1",
    }
    (path / "state_upload_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _active_volume_state_dir(state_slug: str) -> Path:
    if not DATASET_RE.match(state_slug):
        raise HTTPException(status_code=404, detail="active state not found")
    active_root = (ACTIVE_VOLUME_ROOT / "active").resolve()
    versions_root = (ACTIVE_VOLUME_ROOT / "versions").resolve()
    path = (active_root / state_slug).resolve()
    is_active_child = active_root in path.parents or path == active_root
    is_version_child = versions_root in path.parents or path == versions_root
    if not is_active_child and not is_version_child:
        raise HTTPException(status_code=404, detail="active state not found")
    if not path.exists() or not path.is_dir():
        raise HTTPException(status_code=404, detail="active state not found")
    return path



app = FastAPI(title="OpenKataster Tiles", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "HEAD", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


@app.api_route("/health", methods=["GET", "HEAD"])
def health() -> dict:
    return {"status": "ok"}


@app.on_event("startup")
def warm_search_indexes() -> None:
    try:
        states = set(active_bucket_state_keys())
        exact_place_context_index(gn250_places_signature())
        for entry in search_db_entries_for_states(tuple(sorted(states))):
            try:
                con = search_db_connection(entry.path)
                con.execute("SELECT 1 FROM address_lookup LIMIT 1").fetchone()
                con.execute("SELECT 1 FROM street_lookup LIMIT 1").fetchone()
                con.execute("SELECT 1 FROM parcel_lookup LIMIT 1").fetchone()
            except sqlite3.Error:
                continue
        feature_db_entries_for_dataset("deutschland")
        search_places_for_dataset(VIRTUAL_GERMANY_DATASET, "Freden", 5)
        search_places_for_dataset(VIRTUAL_GERMANY_DATASET, "Hamburg", 5)
        search_direct_geocoder_for_dataset("feldstraße hildesheim", 5, states)
        search_direct_geocoder_for_dataset("feldstraße 18 hildesheim", 5, states)
        search_direct_geocoder_for_dataset("Glasewitzer Str. 3", 5, states)
        search_fast_cadastre_parcels_for_dataset("Könnigde", "1", "66/4", 5, {"sachsen-anhalt"})
    except Exception as exc:
        print(f"search warmup failed: {exc}")


@app.post("/admin/rebuild-geocoder")
def rebuild_geocoder(_: Annotated[str, Depends(require_admin_key)]) -> dict:
    raise HTTPException(status_code=410, detail="legacy geocoder rebuild removed; search.sqlite is built per tile version")


@app.api_route("/datasets", methods=["GET", "HEAD"])
def datasets(_: Annotated[str, Depends(require_api_key)]) -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    items = []
    for path in sorted(DATA_DIR.glob("*.pmtiles")):
        items.append({"id": path.stem, "bytes": path.stat().st_size})
    if mosaic_entries():
        metadata = mosaic_metadata()
        items.insert(
            0,
            {
                "id": VIRTUAL_GERMANY_DATASET,
                "bytes": sum(entry.dataset.size for entry in mosaic_entries()),
                "virtual": True,
                "sources": [str(entry.path) for entry in mosaic_entries()],
                "bounds": metadata["bounds"],
                "minzoom": metadata["minzoom"],
                "maxzoom": metadata["maxzoom"],
            },
        )
    return {
        "datasets": items,
        "feature_indexes": [
            {"id": entry.name, "file": entry.path.name, "bytes": entry.path.stat().st_size}
            for entry in feature_db_entries()
        ],
    }


@app.api_route("/tilejson/{dataset}.json", methods=["GET", "HEAD"])
def tilejson(
    request: Request,
    dataset: str,
    key_value: Annotated[str, Depends(require_api_key)],
) -> dict:
    if is_virtual_germany_dataset(dataset):
        return mosaic_tilejson_for(request, key_value)
    ds = get_dataset(dataset)
    return tilejson_for(request, dataset, ds, key_value)


@app.api_route("/styles/{dataset}.json", methods=["GET", "HEAD"])
def style(
    request: Request,
    dataset: str,
    key_value: Annotated[str, Depends(require_api_key)],
) -> dict:
    if is_virtual_germany_dataset(dataset):
        return mosaic_style_for(request, key_value)
    ds = get_dataset(dataset)
    return style_for(request, dataset, ds, key_value)


@app.api_route("/overview-raster/{filename}", methods=["GET", "HEAD"])
def overview_raster_png(
    filename: str,
    _: Annotated[str, Depends(require_api_key)],
) -> FileResponse:
    if not re.match(r"^[a-z0-9][a-z0-9_-]{0,80}\.png$", filename):
        raise HTTPException(status_code=404, detail="overview raster not found")
    path = DATA_DIR / "overview_raster" / filename
    if not path.exists() or path.parent != DATA_DIR / "overview_raster":
        raise HTTPException(status_code=404, detail="overview raster not found")
    return FileResponse(
        path,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400, immutable"},
    )


@app.api_route("/assets/{asset_name}", methods=["GET", "HEAD"])
def asset(
    asset_name: str,
    _: Annotated[str, Depends(require_api_key)],
) -> FileResponse:
    return FileResponse(asset_path(asset_name), media_type="application/geo+json")


@app.api_route("/glyphs/{fontstack}/{glyph_range}", methods=["GET", "HEAD"])
def glyph(fontstack: str, glyph_range: str) -> FileResponse:
    return FileResponse(glyph_path(fontstack, glyph_range), media_type="application/x-protobuf")


@app.api_route("/api/features/point", methods=["GET", "HEAD"])
def global_features_at_point(
    key_value: Annotated[str, Depends(require_api_key)],
    lon: Annotated[float, Query(ge=-180, le=180)],
    lat: Annotated[float, Query(ge=-90, le=90)],
) -> dict:
    del key_value
    return features_at_point_for_dataset(VIRTUAL_GERMANY_DATASET, lon, lat)


@app.api_route("/api/features/{dataset}/point", methods=["GET", "HEAD"])
def dataset_features_at_point(
    dataset: str,
    key_value: Annotated[str, Depends(require_api_key)],
    lon: Annotated[float, Query(ge=-180, le=180)],
    lat: Annotated[float, Query(ge=-90, le=90)],
) -> dict:
    del key_value
    if not is_virtual_germany_dataset(dataset):
        try:
            get_dataset(dataset)
        except HTTPException:
            if not feature_db_entries_for_dataset(dataset):
                raise
    return features_at_point_for_dataset(dataset, lon, lat)


@app.api_route("/api/search", methods=["GET", "HEAD"])
def global_search(
    key_value: Annotated[str, Depends(require_api_key)],
    q: Annotated[str, Query(min_length=2, max_length=120)],
    limit: Annotated[int, Query(ge=1, le=30)] = 12,
    mode: str = "mixed",
    state: str = "",
    gemarkung: str = "",
    flur: str = "",
    flurstueck: str = "",
) -> dict:
    del key_value
    return cached_search_features_for_dataset(
        VIRTUAL_GERMANY_DATASET,
        q,
        limit,
        mode,
        state=state,
        gemarkung=gemarkung,
        flur=flur,
        flurstueck=flurstueck,
    )


@app.api_route("/api/search/{dataset}", methods=["GET", "HEAD"])
def dataset_search(
    dataset: str,
    key_value: Annotated[str, Depends(require_api_key)],
    q: Annotated[str, Query(min_length=2, max_length=120)],
    limit: Annotated[int, Query(ge=1, le=30)] = 12,
    mode: str = "mixed",
    state: str = "",
    gemarkung: str = "",
    flur: str = "",
    flurstueck: str = "",
) -> dict:
    del key_value
    if not is_virtual_germany_dataset(dataset):
        get_dataset(dataset)
    return cached_search_features_for_dataset(
        dataset,
        q,
        limit,
        mode,
        state=state,
        gemarkung=gemarkung,
        flur=flur,
        flurstueck=flurstueck,
    )


@app.api_route("/api/cadastre/gemarkungen", methods=["GET", "HEAD"])
def global_cadastre_gemarkungen(
    key_value: Annotated[str, Depends(require_api_key)],
    q: str = "",
    limit: Annotated[int, Query(ge=1, le=50)] = 12,
    state: str = "",
) -> dict:
    del key_value
    return search_cadastre_gemarkungen_for_dataset(
        VIRTUAL_GERMANY_DATASET,
        q,
        limit,
        state=state,
    )


@app.api_route("/api/cadastre/gemarkungen/{dataset}", methods=["GET", "HEAD"])
def dataset_cadastre_gemarkungen(
    dataset: str,
    key_value: Annotated[str, Depends(require_api_key)],
    q: str = "",
    limit: Annotated[int, Query(ge=1, le=50)] = 12,
    state: str = "",
) -> dict:
    del key_value
    if not is_virtual_germany_dataset(dataset):
        get_dataset(dataset)
    return search_cadastre_gemarkungen_for_dataset(dataset, q, limit, state=state)


@app.api_route("/api/suggest/places/{dataset}", methods=["GET", "HEAD"])
def dataset_place_suggestions(
    dataset: str,
    key_value: Annotated[str, Depends(require_api_key)],
    q: Annotated[str, Query(min_length=2, max_length=80)],
    limit: Annotated[int, Query(ge=1, le=20)] = 10,
    state: str = "",
) -> dict:
    del key_value
    if not is_virtual_germany_dataset(dataset):
        get_dataset(dataset)
    return search_place_suggestions_for_dataset(dataset, q, limit, state=state)


@app.api_route("/api/suggest/streets/{dataset}", methods=["GET", "HEAD"])
def dataset_street_suggestions(
    dataset: str,
    key_value: Annotated[str, Depends(require_api_key)],
    q: Annotated[str, Query(min_length=2, max_length=80)],
    place: Annotated[str, Query(min_length=2, max_length=80)],
    limit: Annotated[int, Query(ge=1, le=20)] = 10,
    state: str = "",
) -> dict:
    del key_value
    return search_street_suggestions_for_dataset(dataset, place, q, limit, state=state)


@app.api_route("/api/suggest/gemarkungen/{dataset}", methods=["GET", "HEAD"])
def dataset_gemarkung_suggestions(
    dataset: str,
    key_value: Annotated[str, Depends(require_api_key)],
    q: Annotated[str, Query(min_length=2, max_length=80)],
    limit: Annotated[int, Query(ge=1, le=20)] = 10,
    state: str = "",
) -> dict:
    del key_value
    return search_gemarkung_suggestions_for_dataset(dataset, q, limit, state=state)


@app.api_route("/api/state-metadata", methods=["GET", "HEAD"])
def api_state_metadata(key_value: Annotated[str, Depends(require_api_key)]) -> dict:
    del key_value
    return {"states": _state_metadata_cache()}




def _active_volume_state_dir(state_slug: str) -> Path:
    if not DATASET_RE.match(state_slug):
        raise HTTPException(status_code=404, detail="active state not found")
    active_root = (ACTIVE_VOLUME_ROOT / "active").resolve()
    versions_root = (ACTIVE_VOLUME_ROOT / "versions").resolve()
    path = (active_root / state_slug).resolve()
    is_active_child = active_root in path.parents or path == active_root
    is_version_child = versions_root in path.parents or path == versions_root
    if not is_active_child and not is_version_child:
        raise HTTPException(status_code=404, detail="active state not found")
    if not path.exists() or not path.is_dir():
        raise HTTPException(status_code=404, detail="active state not found")
    return path


def _active_volume_asset_path(state_slug: str, asset_path: str) -> Path:
    state_dir = _active_volume_state_dir(state_slug)
    root = state_dir.resolve()
    path = (root / asset_path).resolve()
    if root not in path.parents and path != root:
        raise HTTPException(status_code=404, detail="asset not found")
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="asset not found")
    return path




def _api_v1_state_rows() -> list[dict]:
    metadata_by_slug = {
        _state_metadata_slug(str(row.get("bundesland") or row.get("state") or row.get("name") or "")): row
        for row in _state_metadata_cache()
        if isinstance(row, dict)
    }
    rows: list[dict] = []
    for slug in active_bucket_state_keys():
        name, lon, lat = STATE_LABEL_POINTS.get(slug, (slug.replace("-", " ").title(), 0, 0))
        meta = metadata_by_slug.get(slug, {})
        rows.append(
            {
                "slug": slug,
                "name": str(meta.get("bundesland") or meta.get("name") or name),
                "center": {"lon": lon, "lat": lat},
                "datenstand": meta.get("datenstand"),
                "datenjahr": meta.get("datenjahr"),
                "quellenvermerk": meta.get("quellenvermerk"),
                "lizenz": meta.get("lizenz"),
                "active": True,
            }
        )
    return rows


def _api_v1_search_query_from_parts(*parts: str) -> str:
    return " ".join(part.strip() for part in parts if part and part.strip()).strip()


@app.api_route("/api/v1", methods=["GET", "HEAD"])
def api_v1_contract() -> dict:
    return {
        "name": "OpenKataster Tiles API",
        "version": "v1",
        "status": "preview",
        "auth": {
            "free": {
                "description": "No token required for public map, basic search and public geometry previews.",
                "scopes": ["map:view", "search:basic", "layers:basic"],
            },
            "pro": {
                "description": "Use token query parameter or Bearer token for feature details and pro tools.",
                "scopes": ["feature:read", "measure", "export:map", "export:cadastre"],
            },
        },
        "endpoints": {
            "session": "GET /api/v1/session",
            "states": "GET /api/v1/states",
            "sources": "GET /api/v1/sources",
            "datasets": "GET /api/v1/datasets",
            "tilejson": "GET /api/v1/tilejson/{state}.json",
            "tiles": "GET /api/v1/tiles/{state}/{z}/{x}/{y}.mvt",
            "search_address": "GET /api/v1/search/address?place=&street=&house_number=",
            "search_parcel": "GET /api/v1/search/parcel?gemarkung=&flur=&flurstueck=",
            "search_dataset": "GET /api/v1/search/{dataset}?q=&mode=",
            "feature_point": "GET /api/v1/features/point?lon=&lat=",
            "feature_geometry": "GET /api/v1/features/geometry?state=&source_db=&gml_id=&kind=",
            "onoffice_selection_payload": "POST /api/v1/integrations/onoffice/selection-payload",
        },
        "notes": [
            "The iframe viewer should use only /api/v1 endpoints.",
            "Object details require pro access; geometry previews remain public where explicitly exposed.",
            "The onOffice endpoint is a payload adapter only. It does not write to onOffice yet.",
        ],
    }


@app.api_route("/api/v1/states", methods=["GET", "HEAD"])
def api_v1_states() -> dict:
    states = _api_v1_state_rows()
    return {
        "dataset": VIRTUAL_GERMANY_DATASET,
        "states": states,
        "count": len(states),
    }


@app.api_route("/api/v1/sources", methods=["GET", "HEAD"])
def api_v1_sources() -> dict:
    states = _api_v1_state_rows()
    return {
        "dataset": VIRTUAL_GERMANY_DATASET,
        "states": states,
        "sources": [
            {
                "state": row["slug"],
                "name": row["name"],
                "datenstand": row.get("datenstand"),
                "quellenvermerk": row.get("quellenvermerk"),
                "lizenz": row.get("lizenz"),
            }
            for row in states
        ],
    }


@app.api_route("/api/v1/datasets", methods=["GET", "HEAD"])
def api_v1_datasets() -> dict:
    payload = datasets("api-v1")
    payload["api_version"] = "v1"
    return payload



@app.get("/api/v1/tilejson/{state}.json")
async def api_v1_tilejson(state: str, request: Request):
    state_key = normalize_state_key(state)
    base_url = str(request.base_url).rstrip("/")
    maxzoom = style_source_maxzoom(20)
    return {
        "tilejson": "3.0.0",
        "name": f"OpenKataster ALKIS {state_key}",
        "version": "1.0.0",
        "scheme": "xyz",
        "tiles": [f"{base_url}/api/v1/tiles/{state_key}/{{z}}/{{x}}/{{y}}.mvt?client=viewer"],
        "minzoom": 0,
        "maxzoom": maxzoom,
        "bounds": [5.5, 47.0, 15.5, 55.5],
        "vector_layers": [
            {"id": "surfaces", "fields": {}},
            {"id": "building_fills", "fields": {}},
            {"id": "building_lines", "fields": {}},
            {"id": "lines", "fields": {}},
            {"id": "parcel_outline_lines", "fields": {}},
            {"id": "parcel_number_lines", "fields": {}},
            {"id": "point_symbol_fills_simplified", "fields": {}},
            {"id": "labels", "fields": {}},
        ],
        "attribution": "ALKIS / OpenKataster",
    }

@app.api_route("/api/v1/tiles/{state}/{z}/{x}/{y}.mvt", methods=["GET", "HEAD"])
def api_v1_tile_mvt(state: str, z: int, x: int, y: int) -> Response:
    dataset = state.strip().lower()
    if dataset != VIRTUAL_GERMANY_DATASET and dataset in active_bucket_state_keys() and not (DATA_DIR / f"{dataset}.pmtiles").exists():
        dataset = VIRTUAL_GERMANY_DATASET
    return tile_response(dataset, z, x, y)


@app.api_route("/api/v1/search/address", methods=["GET", "HEAD"])
def api_v1_search_address(
    q: str = "",
    place: str = "",
    street: str = "",
    house_number: str = "",
    limit: Annotated[int, Query(ge=1, le=30)] = 12,
    state: str = "",
) -> dict:
    query = q.strip() or _api_v1_search_query_from_parts(street, house_number, place)
    if len(query) < 2:
        return {"query": query, "results": []}
    state_key = state
    if not state_key.strip() and place.strip():
        inferred_states = states_for_place_context(place, set(active_bucket_state_keys()))
        if len(inferred_states) == 1:
            state_key = inferred_states[0]
    mode = "street" if street.strip() and not house_number.strip() else "address"
    return cached_search_features_for_dataset(
        VIRTUAL_GERMANY_DATASET,
        query,
        limit,
        mode,
        state=state_key,
    )


@app.api_route("/api/v1/search/parcel", methods=["GET", "HEAD"])
def api_v1_search_parcel(
    gemarkung: str,
    flur: str,
    flurstueck: str,
    limit: Annotated[int, Query(ge=1, le=30)] = 12,
    state: str = "",
) -> dict:
    query = _api_v1_search_query_from_parts(gemarkung, flur, flurstueck)
    if len(gemarkung.strip()) < 2 or not flur.strip() or not flurstueck.strip():
        return {"query": query, "results": []}
    return cached_search_features_for_dataset(
        VIRTUAL_GERMANY_DATASET,
        gemarkung.strip(),
        limit,
        "parcel",
        state=state,
        gemarkung=gemarkung,
        flur=flur,
        flurstueck=flurstueck,
    )


@app.api_route("/api/v1/search/{dataset}", methods=["GET", "HEAD"])
def api_v1_dataset_search(
    dataset: str,
    q: str = "",
    limit: Annotated[int, Query(ge=1, le=30)] = 12,
    mode: str = "mixed",
    state: str = "",
    gemarkung: str = "",
    flur: str = "",
    flurstueck: str = "",
) -> dict:
    if not is_virtual_germany_dataset(dataset):
        get_dataset(dataset)
    query = q.strip() or gemarkung.strip() or flurstueck.strip()
    if len(query) < 2:
        return {"query": query, "results": []}
    return cached_search_features_for_dataset(
        dataset,
        query,
        limit,
        mode,
        state=state,
        gemarkung=gemarkung,
        flur=flur,
        flurstueck=flurstueck,
    )




def feature_geometry_entries_for_state(state: str) -> tuple[FeatureDbEntry, ...]:
    state_key = normalize_state_key(state)
    if state_key:
        direct_path = DATA_DIR / f"{state_key}{FEATURE_DB_SUFFIX}"
        if direct_path.exists():
            return (FeatureDbEntry(name=state_key, path=direct_path),)
    if state_key and is_virtual_germany_dataset(state_key):
        return feature_db_entries_for_dataset(VIRTUAL_GERMANY_DATASET)
    return tuple(entry for entry in feature_db_entries_for_dataset(VIRTUAL_GERMANY_DATASET) if entry.name == state_key)


def feature_geometry_only_for_id(
    state: str,
    source_db: str,
    gml_id: str,
    kind: str = "",
) -> dict:
    state_key = normalize_state_key(state)
    source_db = (source_db or "").strip()
    gml_id = (gml_id or "").strip()
    kind = (kind or "").strip().lower()
    if not source_db or not gml_id:
        raise HTTPException(status_code=400, detail="source_db and gml_id required")
    entries = feature_geometry_entries_for_state(state_key)
    if not entries and state_key:
        candidate = DATA_DIR / f"{state_key}{FEATURE_DB_SUFFIX}"
        if candidate.exists():
            entries = (FeatureDbEntry(name=state_key, path=candidate),)
    if not entries:
        raise HTTPException(status_code=404, detail="feature index not found")
    for entry in entries:
        with sqlite_feature_connection(entry.path) as con:
            clauses = ["source_db = ?", "gml_id = ?"]
            params: list[str] = [source_db, gml_id]
            if kind in {"parcel", "building"}:
                clauses.insert(0, "kind = ?")
                params.insert(0, kind)
            row = con.execute(
                f"""
                SELECT kind, source_db, gml_id, geometry_wkb, min_lon, min_lat, max_lon, max_lat
                FROM features
                WHERE {' AND '.join(clauses)}
                LIMIT 1
                """,
                tuple(params),
            ).fetchone()
            if not row:
                row = con.execute(
                    """
                    SELECT kind, source_db, gml_id, geometry_wkb, min_lon, min_lat, max_lon, max_lat
                    FROM features
                    WHERE source_db = ? AND gml_id = ?
                    LIMIT 1
                    """,
                    (source_db, gml_id),
                ).fetchone()
            if not row:
                continue
            try:
                geom = wkb.loads(bytes(row["geometry_wkb"]))
            except (GEOSException, TypeError, ValueError):
                raise HTTPException(status_code=422, detail="feature geometry invalid")
            point = geom.representative_point()
            bounds = list(geom.bounds)
            return {
                "access": "public-geometry",
                "state": state_key or entry.name,
                "kind": str(row["kind"] or kind),
                "source_db": str(row["source_db"] or source_db),
                "gml_id": str(row["gml_id"] or gml_id),
                "center": [point.x, point.y],
                "bbox": bounds,
                "geometry": mapping(geom),
            }
    raise HTTPException(status_code=404, detail="feature geometry not found")


def feature_detail_for_id(
    state: str,
    source_db: str,
    gml_id: str,
    kind: str = "",
) -> dict | None:
    state_key = normalize_state_key(state)
    source_db = (source_db or "").strip()
    gml_id = (gml_id or "").strip()
    kind = (kind or "").strip().lower()
    if not source_db or not gml_id:
        return None
    entries = feature_geometry_entries_for_state(state_key)
    if not entries:
        return None
    for entry in entries:
        with sqlite_feature_connection(entry.path) as con:
            if compact_feature_schema(con):
                clauses = ["kind = ?"]
                params: list[object] = [kind] if kind in {"parcel", "building"} else [""]
                if kind not in {"parcel", "building"}:
                    clauses = ["kind IN ('parcel', 'building')"]
                    params = []
                clauses.append(
                    """
                    (
                      id = ?
                      OR json_extract(properties_json, '$.gml_id') = ?
                      OR (
                        json_extract(properties_json, '$.source_db') = ?
                        AND json_extract(properties_json, '$.gml_id') = ?
                      )
                    )
                    """
                )
                params.extend([gml_id, gml_id, source_db, gml_id])
                row = con.execute(
                    f"""
                    SELECT *
                    FROM features
                    WHERE {' AND '.join(clauses)}
                    LIMIT 1
                    """,
                    tuple(params),
                ).fetchone()
                if not row:
                    continue
                result = result_from_compact_feature(row)
                feature = result["feature"]
                feature["source_db"] = feature.get("source_db") or source_db
                if row["kind"] == "building" and sqlite_table_exists(con, "feature_addresses"):
                    address_rows = con.execute(
                        """
                        SELECT
                          address AS compact_address,
                          street_house AS compact_street_house,
                          parcel_id AS compact_address_parcel_id,
                          lon AS compact_address_lon,
                          lat AS compact_address_lat,
                          source AS compact_address_source
                        FROM feature_addresses
                        WHERE feature_id = ?
                        LIMIT 25
                        """,
                        (row["id"],),
                    ).fetchall()
                    feature["addresses"] = enrich_addresses_with_postcode(
                        [compact_address_properties(address_row) for address_row in address_rows],
                        result["center"][0],
                        result["center"][1],
                        state_key,
                    )
                    feature["address"] = feature["addresses"][0]["label"] if feature["addresses"] else feature.get("address", "")
                if row["kind"] == "parcel":
                    area_m2 = compact_feature_area_m2(con, row["id"])
                    if area_m2 is not None:
                        feature["amtliche_flaeche_m2"] = area_m2
                result["state"] = state_key or entry.name
                return result

            clauses = ["source_db = ?", "gml_id = ?"]
            params = [source_db, gml_id]
            if kind in {"parcel", "building"}:
                clauses.insert(0, "kind = ?")
                params.insert(0, kind)
            row = con.execute(
                f"""
                SELECT *
                FROM features
                WHERE {' AND '.join(clauses)}
                LIMIT 1
                """,
                tuple(params),
            ).fetchone()
            if not row:
                continue
            try:
                result = result_from_feature(row)
            except (GEOSException, TypeError, ValueError):
                return None
            feature = result["feature"]
            if row["kind"] == "parcel":
                enrich_gemarkung_from_lookup(entry.path, feature)
            try:
                geom = wkb.loads(bytes(row["geometry_wkb"]))
                feature["addresses"] = enrich_addresses_with_postcode(
                    addresses_for_feature(con, dict(row), geom),
                    result["center"][0],
                    result["center"][1],
                    state_key,
                )
                feature["address"] = feature["addresses"][0]["label"] if feature["addresses"] else feature.get("address", "")
            except (GEOSException, TypeError, ValueError):
                pass
            result["state"] = state_key or entry.name
            return result
    return None


def _onoffice_feature_reference(raw: dict) -> dict:
    return {
        "state": normalize_state_key(str(raw.get("state") or raw.get("bundesland") or "")),
        "kind": str(raw.get("kind") or "").strip().lower(),
        "source_db": str(raw.get("source_db") or "").strip(),
        "gml_id": str(raw.get("gml_id") or raw.get("id") or "").strip(),
    }


def _onoffice_address_labels(features: list[dict]) -> list[str]:
    labels: list[str] = []
    seen: set[str] = set()
    for result in features:
        feature = result.get("feature") if isinstance(result.get("feature"), dict) else {}
        addresses = feature.get("addresses") if isinstance(feature.get("addresses"), list) else []
        candidates = [address.get("label") for address in addresses if isinstance(address, dict)]
        candidates.append(feature.get("address"))
        for value in candidates:
            label = str(value or "").strip()
            if not label or label.casefold() in seen:
                continue
            seen.add(label.casefold())
            labels.append(label)
    return labels


def _onoffice_parcel_label(feature: dict) -> str:
    parts = [
        f"Gemarkung {feature.get('gemarkung')}" if feature.get("gemarkung") else "",
        f"Flur {feature.get('flur')}" if feature.get("flur") else "",
        f"Flurstück {feature.get('flurstueck') or feature.get('label')}" if feature.get("flurstueck") or feature.get("label") else "",
    ]
    return ", ".join(part for part in parts if part)


def build_onoffice_selection_payload(features: list[dict]) -> dict:
    parcels = [item for item in features if item.get("kind") == "parcel"]
    buildings = [item for item in features if item.get("kind") == "building"]
    addresses = _onoffice_address_labels(buildings) or _onoffice_address_labels(parcels)
    parcel_labels = [_onoffice_parcel_label(item.get("feature") or {}) for item in parcels]
    parcel_labels = [label for label in parcel_labels if label]
    parcel_areas = [
        item.get("feature", {}).get("amtliche_flaeche_m2")
        for item in parcels
        if item.get("feature", {}).get("amtliche_flaeche_m2") is not None
    ]
    suggested_fields = {
        "openkataster_adresse": "; ".join(addresses),
        "openkataster_flurstuecke": "; ".join(parcel_labels),
        "openkataster_amtliche_flaeche_m2": sum(float(area) for area in parcel_areas) if parcel_areas else None,
    }
    return {
        "integration": "onoffice",
        "mode": "selection-payload-preview",
        "write_enabled": False,
        "summary": {
            "parcel_count": len(parcels),
            "building_count": len(buildings),
            "address_count": len(addresses),
        },
        "suggested_fields": suggested_fields,
        "features": [
            {
                "state": item.get("state"),
                "kind": item.get("kind"),
                "source_db": item.get("source_db"),
                "gml_id": item.get("gml_id"),
                "label": item.get("label"),
                "subtitle": item.get("subtitle"),
                "center": item.get("center"),
                "bbox": item.get("bbox"),
                "properties": item.get("feature") or {},
            }
            for item in features
        ],
        "warnings": [
            "This endpoint only prepares an onOffice payload. Writing to onOffice requires an authenticated onOffice adapter.",
        ],
    }




@app.api_route("/api/v1/features/geometry", methods=["GET", "HEAD"])
def api_v1_feature_geometry(
    state: str,
    source_db: str,
    gml_id: str,
    kind: str = "",
) -> dict:
    return feature_geometry_only_for_id(state, source_db, gml_id, kind)


@app.api_route("/api/v1/session", methods=["GET", "HEAD"])
def api_v1_session(
    access: Annotated[ApiAccessContext, Depends(api_access_context)],
) -> dict:
    return _public_access_claims(access)


@app.post("/api/v1/integrations/onoffice/selection-payload")
def api_v1_onoffice_selection_payload(
    access: Annotated[ApiAccessContext, Depends(api_access_context)],
    payload: Annotated[dict, Body()],
) -> dict:
    if not access.is_pro:
        raise HTTPException(status_code=403, detail="pro access required")
    raw_features = payload.get("features") or payload.get("selection") or []
    if not isinstance(raw_features, list):
        raise HTTPException(status_code=422, detail="features must be a list")
    if len(raw_features) > 50:
        raise HTTPException(status_code=422, detail="selection is too large")

    features: list[dict] = []
    missing: list[dict] = []
    for raw in raw_features:
        if not isinstance(raw, dict):
            continue
        ref = _onoffice_feature_reference(raw)
        if not ref["state"] or not ref["source_db"] or not ref["gml_id"]:
            missing.append({**ref, "reason": "incomplete reference"})
            continue
        detail = feature_detail_for_id(ref["state"], ref["source_db"], ref["gml_id"], ref["kind"])
        if not detail:
            missing.append({**ref, "reason": "feature not found"})
            continue
        features.append(detail)

    response = build_onoffice_selection_payload(features)
    response["missing"] = missing
    return response


@app.api_route("/api/v1/features/point", methods=["GET", "HEAD"])
def api_v1_features_at_point(
    access: Annotated[ApiAccessContext, Depends(api_access_context)],
    lon: Annotated[float, Query(ge=-180, le=180)],
    lat: Annotated[float, Query(ge=-90, le=90)],
    dataset: str = VIRTUAL_GERMANY_DATASET,
) -> dict:
    if not access.is_pro:
        return {
            "access": "free",
            "pro_required": True,
            "parcels": [],
            "buildings": [],
        }
    if not is_virtual_germany_dataset(dataset):
        try:
            get_dataset(dataset)
        except HTTPException:
            if not feature_db_entries_for_dataset(dataset):
                raise
    payload = features_at_point_for_dataset(dataset, lon, lat)
    payload["access"] = access.mode
    return payload


@app.api_route("/active/{state_slug}/{asset_path:path}", methods=["GET", "HEAD"])
def active_volume_asset(state_slug: str, asset_path: str) -> FileResponse:
    path = _active_volume_asset_path(state_slug, asset_path)
    suffix = path.suffix.lower()
    media_type = {
        ".html": "text/html; charset=utf-8",
        ".js": "text/javascript; charset=utf-8",
        ".json": "application/json",
        ".wasm": "application/wasm",
        ".alkbin": "application/octet-stream",
        ".pmtiles": "application/octet-stream",
        ".gz": "application/gzip",
        ".png": "image/png",
        ".webp": "image/webp",
        ".css": "text/css; charset=utf-8",
    }.get(suffix)
    return FileResponse(
        path,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.get("/bootstrap/{state}.webp")
def bootstrap_backdrop(state: str) -> FileResponse:
    if not DATASET_RE.match(state):
        raise HTTPException(status_code=400, detail="Invalid state")
    path = DATA_DIR / f"{state}_bootstrap.webp"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Bootstrap backdrop not found")
    return FileResponse(path, media_type="image/webp")


@app.api_route("/luftbild/{state_slug}/{z}/{x}/{y}.png", methods=["GET", "HEAD"])
def luftbild_tile(state_slug: str, z: int, x: int, y: int) -> Response:
    if state_slug not in LUFTBILD_WMS_CONFIGS:
        raise HTTPException(status_code=404, detail="Luftbild not configured")
    if z < 0 or z > 22 or x < 0 or y < 0 or x >= 2**z or y >= 2**z:
        raise HTTPException(status_code=400, detail="Invalid tile coordinate")

    config = {**LUFTBILD_WMS_CONFIGS[state_slug], "crs": "EPSG:3857"}
    layer = config.get("layer")
    if not layer:
        raise HTTPException(status_code=404, detail="Luftbild layer not configured")

    cache_path = _luftbild_cache_path(state_slug, str(layer), str(config["crs"]), z, x, y)
    if cache_path.exists():
        return FileResponse(
            cache_path,
            media_type="image/png",
            headers={"Cache-Control": "public, max-age=604800, immutable"},
        )

    bbox, _center_lat = _luftbild_wms_bbox(config, z, x, y)
    params = {
        "SERVICE": "WMS",
        "REQUEST": "GetMap",
        "VERSION": config.get("version", "1.3.0"),
        "LAYERS": layer,
        "STYLES": "",
        "FORMAT": config.get("format", "image/png"),
        "TRANSPARENT": "false",
        "CRS": config["crs"],
        "BBOX": ",".join(f"{value:.3f}" for value in bbox),
        "WIDTH": str(LUFTBILD_TILE_SIZE),
        "HEIGHT": str(LUFTBILD_TILE_SIZE),
    }
    url = f"{config['url']}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": "OpenKataster/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            content_type = response.headers.get("Content-Type", "")
            data = response.read()
    except urllib.error.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Luftbild WMS error: {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise HTTPException(status_code=502, detail="Luftbild WMS unavailable") from exc

    if not data or ("image/" not in content_type.lower() and not data.startswith((b"\x89PNG", b"\xff\xd8", b"GIF"))):
        raise HTTPException(status_code=502, detail="Luftbild WMS returned no image")

    _write_luftbild_cache(cache_path, data)
    return Response(
        content=data,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=604800, immutable"},
    )



def _viewer_asset_file(viewer_version: str, asset_path: str) -> Path:
    if not VIEWER_VERSION_RE.match(viewer_version):
        raise HTTPException(status_code=404, detail="viewer asset not found")
    parts = [part for part in asset_path.split("/") if part]
    if not parts or any(part in {".", ".."} or not VIEWER_ASSET_RE.match(part) for part in parts):
        raise HTTPException(status_code=404, detail="viewer asset not found")
    root = (VIEWER_ROOT / viewer_version).resolve()
    path = root.joinpath(*parts).resolve()
    if root not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="viewer asset not found")
    return path


def _viewer_media_type(path: Path) -> str | None:
    return {
        ".html": "text/html; charset=utf-8",
        ".js": "text/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".json": "application/json",
        ".svg": "image/svg+xml",
        ".png": "image/png",
        ".webp": "image/webp",
        ".wasm": "application/wasm",
    }.get(path.suffix.lower())


@app.api_route("/viewer-assets/{viewer_version}/{asset_path:path}", methods=["GET", "HEAD"])
def viewer_asset(viewer_version: str, asset_path: str) -> FileResponse:
    path = _viewer_asset_file(viewer_version, asset_path)
    return FileResponse(
        path,
        media_type=_viewer_media_type(path),
        headers={"Cache-Control": "public, max-age=300"},
    )

@app.api_route("/viewer/{dataset}", methods=["GET", "HEAD"], response_class=HTMLResponse)
def viewer(
    request: Request,
    dataset: str,
    key: Annotated[str | None, Query()] = None,
):
    if not is_virtual_germany_dataset(dataset):
        get_dataset(dataset)
    static_index = VIEWER_ROOT / f"{dataset}-v2" / "index.html"
    if static_index.is_file():
        return FileResponse(
            static_index,
            media_type="text/html; charset=utf-8",
            headers={"Cache-Control": "no-cache"},
        )
    key_value = viewer_key(key)
    return HTMLResponse(viewer_html(request, dataset, key_value))



@app.post("/admin/volume-upload-session/{state_slug}")
async def create_volume_upload_session(
    state_slug: str,
    _: Annotated[str, Depends(require_openkataster_admin_token)],
    request: Request,
    version: Annotated[str, Query(min_length=1)],
    bundesland: Annotated[str | None, Query()] = None,
) -> dict:
    if not DATASET_RE.match(state_slug):
        raise HTTPException(status_code=400, detail="invalid state slug")
    version_name = _safe_version_name(version)
    payload = await request.json()
    files = _validate_volume_filenames(list(payload.get("files") or []))
    upload_dir = _volume_upload_dir(state_slug, version_name)
    upload_dir.mkdir(parents=True, exist_ok=True)
    session_files = [_volume_upload_file_status(upload_dir, item) for item in files]
    return {
        "status": "success",
        "target": "tile-volume",
        "state_slug": state_slug,
        "bundesland": bundesland or state_slug.replace("-", " ").title(),
        "version_name": version_name,
        "part_size": VOLUME_UPLOAD_PART_BYTES,
        "resume": any(int(item.get("uploaded_bytes") or 0) > 0 for item in session_files),
        "files": session_files,
    }


@app.get("/admin/volume-upload-sessions/{state_slug}")
def list_volume_upload_sessions(
    state_slug: str,
    _: Annotated[str, Depends(require_openkataster_admin_token)],
) -> dict:
    return {
        "status": "success",
        "target": "tile-volume",
        "state_slug": state_slug,
        "sessions": _volume_upload_sessions(state_slug),
    }


@app.put("/admin/volume-upload-part/{state_slug}")
async def upload_volume_part(
    request: Request,
    state_slug: str,
    _: Annotated[str, Depends(require_openkataster_admin_token)],
    version: Annotated[str, Query(min_length=1)],
    filename: Annotated[str, Query(min_length=1)],
    start: Annotated[int, Query(ge=0)],
    end: Annotated[int, Query(gt=0)],
    total_size: Annotated[int, Query(gt=0)],
) -> dict:
    if not DATASET_RE.match(state_slug):
        raise HTTPException(status_code=400, detail="invalid state slug")
    version_name = _safe_version_name(version)
    safe_filename = os.path.basename(filename)
    if safe_filename not in VOLUME_REQUIRED_FILES:
        raise HTTPException(status_code=400, detail=f"unexpected tile file: {filename}")
    if end <= start or end > total_size:
        raise HTTPException(status_code=400, detail="invalid upload byte range")
    expected_chunk_size = end - start
    if expected_chunk_size > VOLUME_UPLOAD_MAX_PART_BYTES:
        raise HTTPException(status_code=413, detail="volume upload part too large")

    upload_dir = _volume_upload_dir(state_slug, version_name)
    upload_dir.mkdir(parents=True, exist_ok=True)
    target_path = upload_dir / f"{safe_filename}.partial"
    current_size = target_path.stat().st_size if target_path.exists() else 0
    if current_size >= end:
        return {"status": "success", "already_uploaded": True, "uploaded_bytes": current_size, "size_bytes": 0}
    if current_size != start:
        raise HTTPException(status_code=409, detail=f"upload offset mismatch for {safe_filename}: expected start {current_size}, got {start}")

    written = 0
    try:
        with target_path.open("ab") as handle:
            async for chunk in request.stream():
                if not chunk:
                    continue
                written += len(chunk)
                if written > expected_chunk_size:
                    handle.truncate(start)
                    raise HTTPException(status_code=413, detail="upload part exceeded declared range")
                await asyncio.to_thread(handle.write, chunk)
        if written != expected_chunk_size:
            with target_path.open("ab") as handle:
                handle.truncate(start)
            raise HTTPException(status_code=400, detail=f"incomplete upload part: expected {expected_chunk_size}, got {written}")
    except HTTPException:
        raise
    except Exception as exc:
        try:
            with target_path.open("ab") as handle:
                handle.truncate(start)
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=f"could not write upload part: {exc}") from exc

    return {
        "status": "success",
        "target": "tile-volume",
        "filename": safe_filename,
        "start": start,
        "end": end,
        "size_bytes": written,
        "uploaded_bytes": target_path.stat().st_size,
    }


@app.post("/admin/complete-volume-upload/{state_slug}")
async def complete_volume_upload(
    state_slug: str,
    _: Annotated[str, Depends(require_openkataster_admin_token)],
    request: Request,
    version: Annotated[str, Query(min_length=1)],
    bundesland: Annotated[str | None, Query()] = None,
) -> dict:
    if not DATASET_RE.match(state_slug):
        raise HTTPException(status_code=400, detail="invalid state slug")
    version_name = _safe_version_name(version)
    payload = await request.json()
    files = _validate_volume_filenames(list(payload.get("files") or []))
    upload_dir = _volume_upload_dir(state_slug, version_name)
    if not upload_dir.is_dir():
        raise HTTPException(status_code=400, detail="upload session not found")

    for item in files:
        filename = item["filename"]
        partial_path = upload_dir / f"{filename}.partial"
        final_path = upload_dir / filename
        if partial_path.is_file():
            if partial_path.stat().st_size != int(item["size_bytes"]):
                raise HTTPException(status_code=400, detail=f"size mismatch for {filename}: expected {item['size_bytes']}, got {partial_path.stat().st_size}")
            final_path.unlink(missing_ok=True)
            partial_path.rename(final_path)
        if not final_path.is_file():
            raise HTTPException(status_code=400, detail=f"missing uploaded file: {filename}")
        if final_path.stat().st_size != int(item["size_bytes"]):
            raise HTTPException(status_code=400, detail=f"size mismatch for {filename}: expected {item['size_bytes']}, got {final_path.stat().st_size}")

    validation = _validate_volume_state_dir(upload_dir)
    display_name = bundesland or state_slug.replace("-", " ").title()
    _write_volume_upload_manifest(upload_dir, state_slug=state_slug, bundesland=display_name, version_name=version_name, files=files)

    version_dir = _volume_version_dir(state_slug, version_name)
    version_dir.parent.mkdir(parents=True, exist_ok=True)
    tmp_version_dir = version_dir.with_name(f".{version_dir.name}.{os.getpid()}.tmp")
    shutil.rmtree(tmp_version_dir, ignore_errors=True)
    os.replace(upload_dir, tmp_version_dir)
    shutil.rmtree(version_dir, ignore_errors=True)
    os.replace(tmp_version_dir, version_dir)
    _clear_data_caches()
    return {
        "status": "success",
        "target": "tile-volume",
        "state_slug": state_slug,
        "bundesland": display_name,
        "version_name": version_name,
        "active": False,
        "validation": validation,
        "remote_version_path": str(version_dir),
    }


@app.post("/admin/sync-bucket/{state_slug}")
def sync_bucket_state(
    state_slug: str,
    _: Annotated[str, Depends(require_admin_key)],
    version: Annotated[str | None, Query()] = None,
) -> dict:
    return sync_bucket_tiles(state_slug, version)


def tile_response(dataset: str, z: int, x: int, y: int) -> Response:
    if is_virtual_germany_dataset(dataset):
        data = mosaic_tile(z, x, y)
        if data is None:
            return Response(status_code=204, headers={"Cache-Control": "public, max-age=86400, immutable"})
        return Response(
            content=data,
            media_type="application/vnd.mapbox-vector-tile",
            headers={
                "Cache-Control": "public, max-age=86400, immutable",
                "Content-Encoding": "gzip",
            },
        )

    shard_names = _direct_shard_names(dataset)
    if shard_names:
        data = direct_shard_tile(dataset, z, x, y)
        if data is None:
            return Response(status_code=204, headers={"Cache-Control": "public, max-age=86400, immutable"})
        return Response(
            content=data,
            media_type="application/vnd.mapbox-vector-tile",
            headers={
                "Cache-Control": "public, max-age=86400, immutable",
                "Content-Encoding": "gzip",
            },
        )

    ds = get_dataset(dataset)
    data = ds.tile(z, x, y)
    if data is None:
        return Response(status_code=204, headers={"Cache-Control": "public, max-age=86400, immutable"})

    headers = {
        "Cache-Control": "public, max-age=86400, immutable",
        **compression_header(ds),
    }
    return Response(
        content=data,
        media_type="application/vnd.mapbox-vector-tile",
        headers=headers,
    )


def raster_tile_response(dataset: str, z: int, x: int, y: int) -> Response:
    if not is_virtual_germany_dataset(dataset):
        raise HTTPException(status_code=404, detail="raster tile not found")
    data = mosaic_raster_tile(z, x, y)
    if data is None:
        raise HTTPException(status_code=404, detail="raster tile not found")
    return Response(
        content=data,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400, immutable"},
    )


@app.api_route("/tiles/{dataset}/{z}/{x}/{y}.mvt", methods=["GET", "HEAD"])
def tile_mvt(
    dataset: str,
    z: int,
    x: int,
    y: int,
    _: Annotated[str, Depends(require_api_key)],
) -> Response:
    return tile_response(dataset, z, x, y)


@app.api_route("/tiles/{dataset}/{z}/{x}/{y}.png", methods=["GET", "HEAD"])
def tile_png(
    dataset: str,
    z: int,
    x: int,
    y: int,
    _: Annotated[str, Depends(require_api_key)],
) -> Response:
    return raster_tile_response(dataset, z, x, y)


@app.api_route("/tiles/{dataset}/{z}/{x}/{y}.pbf", methods=["GET", "HEAD"])
def tile_pbf(
    dataset: str,
    z: int,
    x: int,
    y: int,
    _: Annotated[str, Depends(require_api_key)],
) -> Response:
    return tile_response(dataset, z, x, y)


def normalize_slug(value: str | None) -> str:
    value_text = str(value or '').strip().casefold()
    return re.sub(r'[^0-9a-z-]', '-', value_text)
