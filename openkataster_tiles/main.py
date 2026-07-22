from __future__ import annotations

import asyncio
import base64
import fcntl
import gzip
import hashlib
import hmac
import http.client
import json
import math
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import threading
import traceback
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Iterable

import mapbox_vector_tile
from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query, Request, Response, Security
from fastapi.openapi.utils import get_openapi
from fastapi.responses import FileResponse
from fastapi.responses import HTMLResponse
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pmtiles.reader import Compression, MmapSource, Reader
from pydantic import BaseModel, Field
from shapely import wkb
from shapely.geometry import Point, mapping, shape
from shapely.errors import GEOSException

from openkataster_tiles.search_analytics import (
    SearchAnalytics,
    install_queryless_uvicorn_access_logging,
    valid_analytics_marker,
)
from openkataster_tiles.poi_search import (
    poi_index_available,
    poi_index_metadata,
    search_poi_by_id,
    search_poi_suggestions,
)

try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from cryptography.hazmat.primitives.serialization import load_pem_public_key
except Exception:  # pragma: no cover - optional deployment dependency
    Ed25519PublicKey = None  # type: ignore[assignment]
    load_pem_public_key = None  # type: ignore[assignment]


DATA_DIR = Path(os.environ.get("OPENKATASTER_TILE_DATA_DIR", "/srv/openkataster-tiles/data"))
SEARCH_ANALYTICS = SearchAnalytics.from_environment(DATA_DIR)
install_queryless_uvicorn_access_logging()
VIEWER_ROOT = Path(os.environ.get("OPENKATASTER_VIEWER_ROOT", "/srv/openkataster-tiles/live-viewer"))
GN250_PLACES_DB = Path(os.environ.get("OPENKATASTER_GN250_PLACES_DB", str(DATA_DIR / "places.sqlite")))
POSTCODE_AREAS_DB = Path(os.environ.get("OPENKATASTER_POSTCODE_AREAS_DB", "/srv/openkataster-tiles/plz/postcode_areas.sqlite"))
OPENPLZ_DB = Path(os.environ.get("OPENKATASTER_OPENPLZ_DB", "/srv/openkataster-tiles/plz/openplz.sqlite"))
API_KEY_STORE_PATH = Path(os.environ.get("OPENKATASTER_API_KEY_STORE", str(DATA_DIR / "api_keys.json")))
API_USAGE_DB = Path(os.environ.get("OPENKATASTER_API_USAGE_DB", str(DATA_DIR / "api_usage.sqlite")))
EMBED_SESSION_TTL_SECONDS = max(60, min(3600, int(os.environ.get("OPENKATASTER_EMBED_SESSION_TTL_SECONDS", "900"))))
PUBLIC_BASE_URL = os.environ.get("OPENKATASTER_TILE_PUBLIC_BASE_URL", "").rstrip("/")
CORS_ORIGINS = [
    value.strip().rstrip("/")
    for value in os.environ.get(
        "OPENKATASTER_CORS_ORIGINS",
        "https://tiles.openkataster.de,https://openkataster.de,https://www.openkataster.de",
    ).split(",")
    if value.strip()
]
ADMIN_API_BASE_URL = os.environ.get("OPENKATASTER_ADMIN_API_BASE_URL", "http://openkataster-api:8000").rstrip("/")
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
    "bayern": {
        "url": "https://geoservices.bayern.de/od/wms/dop/v1/dop20",
        "layer": "by_dop20c",
        "crs": "EPSG:3857",
        "format": "image/jpeg",
        "version": "1.3.0",
        "tile_size": 512,
        "map_tile_size": 512,
        "timeout": 12,
        "attempts": 2,
        "revision": "by-dop20c-512-jpeg-v1",
        "attribution": "Bayerische Vermessungsverwaltung – www.geodaten.bayern.de · CC BY 4.0",
    },
    "berlin": {"url": "https://isk.geobasis-bb.de/mapproxy/dop20c/service/wms", "layer": "bebb_dop20c", "crs": "EPSG:25833", "format": "image/png"},
    "brandenburg": {"url": "https://isk.geobasis-bb.de/mapproxy/dop20c/service/wms", "layer": "bebb_dop20c", "crs": "EPSG:25833", "format": "image/png"},
    "bremen": {"url": "https://geodienste.bremen.de/wms_dop20_2023", "layer": "DOP20_2023_HB", "layer_alt": "DOP20_2023_BHV", "crs": "EPSG:25832", "format": "image/png"},
    "hamburg": {"url": "https://geodienste.hamburg.de/wms_dop_zeitreihe_belaubt", "layer": "dop_zeitreihe_belaubt", "crs": "EPSG:25832", "format": "image/png"},
    "hessen": {"url": "https://www.gds-srv.hessen.de/cgi-bin/lika-services/ogc-free-images.ows", "layer": "he_dop20_rgb", "crs": "EPSG:25832", "format": "image/png"},
    "mecklenburg-vorpommern": {"url": "https://www.geodaten-mv.de/dienste/adv_dop", "layer": "mv_dop", "crs": "EPSG:25833", "format": "image/png"},
    "niedersachsen": {
        "url": "https://opendata.geoservices.lgln.niedersachsen.de/dop_wms",
        "layer": "ni_dop",
        "crs": "EPSG:3857",
        "format": "image/jpeg",
        "tile_size": 512,
        "timeout": 8,
        "attempts": 2,
        "map_tile_size": 512,
        "revision": "ni-dop-512-jpeg-direct-wms1",
    },
    "nordrhein-westfalen": {"url": "https://www.wms.nrw.de/geobasis/wms_nw_dop", "layer": "nw_dop_rgb", "crs": "EPSG:25832", "format": "image/png"},
    "rheinland-pfalz": {"url": "https://geo4.service24.rlp.de/wms/rp_dop20.fcgi", "layer": "rp_dop20", "crs": "EPSG:25832", "format": "image/png"},
    "saarland": {"url": "https://geoportal.saarland.de/freewms/dop2020", "layer": "sl_dop2020", "crs": "EPSG:25832", "format": "image/png"},
    "sachsen": {"url": "https://geodienste.sachsen.de/wms_geosn_dop-rgb/guest", "layer": "sn_dop_020", "crs": "EPSG:25833", "format": "image/png"},
    "sachsen-anhalt": {
        "url": "https://www.geodatenportal.sachsen-anhalt.de/wss/service/ST_LVermGeo_DOP_WMS_OpenData/guest",
        "layer": "lsa_lvermgeo_dop20_2",
        "crs": "EPSG:25832",
        "format": "image/png",
        "map_tile_size": 512,
        "revision": "st-dop20-open-data-v1",
        "attribution": "© GeoBasis-DE / LVermGeo ST · Datenlizenz Deutschland – Namensnennung – Version 2.0",
    },
    "schleswig-holstein": {"url": "https://dienste.gdi-sh.de/WMS_SH_DOP20col_OpenGBD", "layer": "sh_dop20_rgb", "crs": "EPSG:25832", "format": "image/png"},
    "thueringen": {"url": "https://www.geoproxy.geoportal-th.de/geoproxy/services/DOP20", "layer": "th_dop", "crs": "EPSG:25832", "format": "image/png"},
    "thuringen": {"url": "https://www.geoproxy.geoportal-th.de/geoproxy/services/DOP20", "layer": "th_dop", "crs": "EPSG:25832", "format": "image/png"},
}

# Some states publish the authoritative cadastral presentation only (Bavaria)
# or most reliably (Saxony-Anhalt) as a WMS.  OpenKataster keeps its local
# vector/SQLite artefacts for search, hit-testing and export, while this raster
# is used purely as the visible, official cartographic presentation.
KATASTER_WMS_CONFIGS = {
    "bayern": {
        "url": "https://geoservices.bayern.de/od/wms/alkis/v1/parzellarkarte",
        "layer": "by_alkis_parzellarkarte_farbe",
        "styles": "Farbe",
        "crs": "EPSG:3857",
        "format": "image/png",
        "version": "1.3.0",
        "transparent": True,
        "tile_size": 512,
        "dpi": 120,
        "timeout": 20,
        "attempts": 2,
        "minzoom": 17,
        "maxzoom": 22,
        "revision": "by-parzellarkarte-farbe-screen-dpi120-v2",
        "attribution": "Bayerische Vermessungsverwaltung – www.geodaten.bayern.de · CC BY 4.0",
        "cache_ttl_seconds": 24 * 60 * 60,
        "cache_control": "public, max-age=86400, stale-while-revalidate=3600",
    },
    "sachsen-anhalt": {
        "url": "https://www.geodatenportal.sachsen-anhalt.de/wss/service/ST_LVermGeo_ALKIS_WMS_AdV_konform_App/guest",
        "layer": ",".join(
            (
                "adv_alkis_tatsaechliche_nutzung",
                "adv_alkis_gesetzl_festlegungen",
                "adv_alkis_weiteres",
                "adv_alkis_gebaeude",
                "adv_alkis_flurstuecke",
            )
        ),
        "styles": ",".join(("Farbe",) * 5),
        "crs": "EPSG:3857",
        "format": "image/png",
        "version": "1.3.0",
        "transparent": True,
        "tile_size": 512,
        "dpi": 120,
        "timeout": 20,
        "attempts": 2,
        "minzoom": 17,
        "maxzoom": 22,
        "revision": "st-adv-alkis-farbe-screen-dpi120-v2",
        "attribution": "© GeoBasis-DE / LVermGeo ST · Datenlizenz Deutschland – Namensnennung – Version 2.0",
        "cache_ttl_seconds": 24 * 60 * 60,
        "cache_control": "public, max-age=86400, stale-while-revalidate=3600",
    },
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


def _luftbild_media_type(image_format: str) -> str:
    normalized = str(image_format or "").split(";", 1)[0].strip().lower()
    if normalized in {"image/jpeg", "image/jpg"}:
        return "image/jpeg"
    return "image/png"


def _luftbild_cache_path(
    state_slug: str,
    layer: str,
    crs: str,
    z: int,
    x: int,
    y: int,
    *,
    tile_size: int = LUFTBILD_TILE_SIZE,
    image_format: str = "image/png",
) -> Path:
    safe_layer = re.sub(r"[^a-zA-Z0-9_.-]+", "_", layer).strip("_") or "layer"
    safe_crs = re.sub(r"[^a-zA-Z0-9_.-]+", "_", crs).strip("_") or "crs"
    suffix = ".jpg" if _luftbild_media_type(image_format) == "image/jpeg" else ".png"
    return LUFTBILD_CACHE_DIR / str(tile_size) / state_slug / safe_layer / safe_crs / str(z) / str(x) / f"{y}{suffix}"


def _luftbild_cache_usage() -> tuple[int, list[tuple[float, int, Path]]]:
    total = 0
    files: list[tuple[float, int, Path]] = []
    if not LUFTBILD_CACHE_DIR.exists():
        return total, files
    for pattern in ("*.png", "*.jpg", "*.jpeg"):
        for path in LUFTBILD_CACHE_DIR.rglob(pattern):
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
STATE_METADATA_ENDPOINT = os.environ.get("OPENKATASTER_TILE_STATE_METADATA_ENDPOINT", "http://openkataster-api:8000/v1/metadata").rstrip("/")
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
VOLUME_UPLOAD_SESSION_TTL_SECONDS = int(os.environ.get("OPENKATASTER_TILE_VOLUME_UPLOAD_SESSION_TTL_SECONDS", str(7 * 24 * 60 * 60)))
VOLUME_REQUIRED_FILES = {"alkis.pmtiles", "features.sqlite", "search.sqlite"}
VOLUME_UPLOAD_SESSION_MANIFEST = ".upload-session.json"
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
    "baden-wurttemberg": {
        "bundesland": "Baden-Württemberg",
        "quellenvermerk": "Datenquelle: LGL, www.lgl-bw.de, dl-de/by-2-0",
        "lizenz": "dl-de/by-2-0",
    },
    "bayern": {
        "bundesland": "Bayern",
        "datenstand": "14.07.2026",
        "datenjahr": 2026,
        "quellenvermerk": "Bayerische Vermessungsverwaltung – www.geodaten.bayern.de",
        "lizenz": "CC BY 4.0",
    },
    "sachsen-anhalt": {
        "bundesland": "Sachsen-Anhalt",
        "quellenvermerk": "© GeoBasis-DE / LVermGeo ST",
        "lizenz": "dl-de/by-2-0",
    },
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


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _embed_session_secret() -> bytes:
    raw = os.environ.get("OPENKATASTER_EMBED_SESSION_SECRET", "").strip()
    if len(raw) < 32:
        raise HTTPException(status_code=503, detail="embed sessions are not configured")
    return raw.encode("utf-8")


def _normalize_origin(value: str) -> str | None:
    raw = (value or "").strip()
    if not raw:
        return None
    if "://" not in raw:
        raw = f"https://{raw}"
    try:
        parsed = urllib.parse.urlsplit(raw)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    if parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        return None
    host = parsed.hostname.lower().rstrip(".")
    if not re.fullmatch(r"(?:\*\.)?[a-z0-9.-]+|localhost|\[[0-9a-f:]+\]", host):
        return None
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{host}{port}"


def _origin_is_allowed(origin: str, allowed_origins: list[str]) -> bool:
    normalized = _normalize_origin(origin)
    if not normalized:
        return False
    requested = urllib.parse.urlsplit(normalized)
    for configured in allowed_origins:
        candidate = _normalize_origin(str(configured))
        if not candidate:
            continue
        parsed = urllib.parse.urlsplit(candidate)
        if parsed.scheme != requested.scheme or parsed.port != requested.port:
            continue
        configured_host = parsed.hostname or ""
        requested_host = requested.hostname or ""
        if configured_host.startswith("*."):
            suffix = configured_host[1:]
            if requested_host.endswith(suffix) and requested_host != configured_host[2:]:
                return True
        elif configured_host == requested_host:
            return True
    return False


def _sign_embed_claims(claims: dict) -> str:
    header = {"alg": "HS256", "typ": "JWT", "kid": "embed-v1"}
    encoded_header = _base64url_encode(json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    encoded_claims = _base64url_encode(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signing_input = f"{encoded_header}.{encoded_claims}".encode("ascii")
    signature = hmac.new(_embed_session_secret(), signing_input, hashlib.sha256).digest()
    return f"{encoded_header}.{encoded_claims}.{_base64url_encode(signature)}"


def _verify_embed_session(token: str) -> dict | None:
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        header = json.loads(_base64url_decode(parts[0]).decode("utf-8"))
        if header.get("alg") != "HS256" or header.get("kid") != "embed-v1":
            return None
        signing_input = f"{parts[0]}.{parts[1]}".encode("ascii")
        supplied = _base64url_decode(parts[2])
        expected = hmac.new(_embed_session_secret(), signing_input, hashlib.sha256).digest()
        if not hmac.compare_digest(supplied, expected):
            return None
        claims = json.loads(_base64url_decode(parts[1]).decode("utf-8"))
        now = int(time.time())
        if claims.get("typ") not in {"embed", "viewer"}:
            return None
        if claims.get("aud") != "openkataster-embed":
            return None
        if int(claims.get("nbf", 0)) > now or int(claims.get("exp", 0)) < now:
            return None
        return claims if isinstance(claims, dict) else None
    except (HTTPException, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _new_viewer_session(
    *,
    pro: bool = False,
    subject: str = "public-viewer",
    name: str | None = None,
    allow_export: bool = False,
) -> str:
    now = int(time.time())
    scopes = ["map:view", "search:basic", "layers:basic", "feature:preview"]
    if allow_export:
        scopes.extend(["export:map", "export:cadastre"])
    if pro:
        scopes.extend(["layers:advanced", "feature:read", "measure"])
    return _sign_embed_claims(
        {
            "typ": "viewer",
            "aud": "openkataster-embed",
            "sub": subject[:120],
            "name": name[:255] if name else None,
            "plan": "pro" if pro else "free",
            "integration": "viewer",
            "scopes": scopes,
            "iat": now,
            "nbf": now - 5,
            "exp": now + min(3600, EMBED_SESSION_TTL_SECONDS * 4),
            "jti": secrets.token_urlsafe(12),
        }
    )


def _claims_grant_pro_access(claims: dict) -> bool:
    scopes = claims.get("scopes")
    if isinstance(scopes, list) and ("feature:read" in scopes or "measure" in scopes):
        return True
    plan = str(claims.get("plan") or "").lower()
    return plan in {"pro", "onoffice_pro", "professional", "starter", "beta", "api_beta", "enterprise", "partner"}


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
    normalized = normalized.strip("-")
    return {
        # The first BW upload predates the umlaut-safe slug convention.
        "baden-wuerttemberg": "baden-wurttemberg",
    }.get(normalized, normalized)


def _merge_local_state_metadata(states: list[dict]) -> list[dict]:
    merged = list(states)
    existing = {
        _state_metadata_slug(str(state.get("bundesland") or state.get("state") or state.get("name") or "")): state
        for state in merged
    }
    for slug, metadata in LOCAL_STATE_METADATA.items():
        state = existing.get(slug)
        if state is None:
            merged.append(dict(metadata))
            continue
        for field, value in metadata.items():
            if not state.get(field):
                state[field] = value
    return merged


def _state_metadata_cache() -> list[dict]:
    now = time.time()
    if _STATE_METADATA_CACHE["expires_at"] >= now:
        return _merge_local_state_metadata(list(_STATE_METADATA_CACHE["states"]))  # type: ignore[arg-type]

    request = urllib.request.Request(
        STATE_METADATA_ENDPOINT,
        headers={"Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            if response.status != 200:
                return _merge_local_state_metadata(list(_STATE_METADATA_CACHE["states"]))  # type: ignore[arg-type]
            payload = json.loads(response.read().decode("utf-8"))
            if isinstance(payload, dict):
                states = payload.get("states", [])
            else:
                states = payload
            if not isinstance(states, list):
                return _merge_local_state_metadata(list(_STATE_METADATA_CACHE["states"]))  # type: ignore[arg-type]
            states = [state for state in states if isinstance(state, dict)]
            if not states:
                return _merge_local_state_metadata(list(_STATE_METADATA_CACHE["states"]))  # type: ignore[arg-type]
            states = _merge_local_state_metadata(states)
            _STATE_METADATA_CACHE["states"] = states
            _STATE_METADATA_CACHE["expires_at"] = now + STATE_METADATA_CACHE_SECONDS
            return states
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError):
        return _merge_local_state_metadata(list(_STATE_METADATA_CACHE["states"]))  # type: ignore[arg-type]


def require_api_key(
    key: Annotated[str | None, Query()] = None,
    api_key: Annotated[str | None, Query()] = None,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> str:
    allowed = _configured_keys()
    provided = key or api_key or x_api_key or _extract_bearer(authorization)
    api_key_record = _api_key_record_for_token(provided)
    if api_key_record:
        _check_and_record_api_key_usage(api_key_record)
        return provided
    if not allowed:
        raise HTTPException(status_code=503, detail="tile service has no API keys configured")

    if not provided or provided not in allowed:
        raise HTTPException(status_code=401, detail="invalid API key")
    return provided


_API_KEY_STORE_CACHE: dict[str, object] = {"mtime": None, "records": {}}


def _api_key_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _api_key_store_records() -> dict[str, dict]:
    try:
        stat = API_KEY_STORE_PATH.stat()
    except OSError:
        _API_KEY_STORE_CACHE["mtime"] = None
        _API_KEY_STORE_CACHE["records"] = {}
        return {}

    mtime = stat.st_mtime_ns
    if _API_KEY_STORE_CACHE.get("mtime") == mtime:
        cached = _API_KEY_STORE_CACHE.get("records")
        return cached if isinstance(cached, dict) else {}

    try:
        payload = json.loads(API_KEY_STORE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        _API_KEY_STORE_CACHE["mtime"] = mtime
        _API_KEY_STORE_CACHE["records"] = {}
        return {}

    records: dict[str, dict] = {}
    for record in payload.get("keys", []) if isinstance(payload, dict) else []:
        if not isinstance(record, dict):
            continue
        token_hash = str(record.get("token_hash") or "").strip().lower()
        if not re.fullmatch(r"[a-f0-9]{64}", token_hash):
            continue
        if str(record.get("status") or "active").lower() != "active":
            continue
        records[token_hash] = record

    _API_KEY_STORE_CACHE["mtime"] = mtime
    _API_KEY_STORE_CACHE["records"] = records
    return records


def _api_key_record_for_token(token: str | None) -> dict | None:
    if not token:
        return None
    return _api_key_store_records().get(_api_key_hash(token))


def _api_key_claims(record: dict) -> dict:
    plan = str(record.get("plan") or "free").lower()
    scopes = record.get("scopes")
    if not isinstance(scopes, list):
        scopes = ["map:view", "search:basic", "layers:basic"]
        if plan in {"beta", "enterprise", "partner"}:
            scopes += ["feature:read", "measure", "export:map", "export:cadastre"]
    return {
        "sub": record.get("user_id"),
        "name": record.get("project_name"),
        "plan": "api_beta" if plan == "beta" else plan,
        "integration": "api_key",
        "scopes": scopes,
        "allowed_origins": record.get("allowed_origins") if isinstance(record.get("allowed_origins"), list) else [],
        "monthly_limit": record.get("monthly_limit"),
    }


def _api_usage_month() -> str:
    return time.strftime("%Y-%m", time.gmtime())


def _api_usage_subject(record: dict) -> str | None:
    raw = str(record.get("usage_subject") or "").strip()
    if raw:
        return raw[:180]
    project_id = str(record.get("project_id") or "").strip()
    if project_id:
        return f"project:{project_id[:120]}"
    user_id = str(record.get("user_id") or "").strip()
    if user_id:
        return f"user:{user_id[:120]}"
    token_hash = str(record.get("token_hash") or "").strip().lower()
    if re.fullmatch(r"[a-f0-9]{64}", token_hash):
        return f"key:{token_hash}"
    return None


def _ensure_api_usage_schema(con: sqlite3.Connection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS api_usage (
            token_hash TEXT NOT NULL,
            month TEXT NOT NULL,
            count INTEGER NOT NULL DEFAULT 0,
            updated_at INTEGER NOT NULL,
            PRIMARY KEY (token_hash, month)
        )
        """
    )


def _record_api_key_usage(record: dict) -> None:
    subject = _api_usage_subject(record)
    if not subject:
        return
    month = _api_usage_month()
    try:
        API_USAGE_DB.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(API_USAGE_DB, timeout=1.5) as con:
            _ensure_api_usage_schema(con)
            con.execute(
                """
                INSERT INTO api_usage (token_hash, month, count, updated_at)
                VALUES (?, ?, 1, ?)
                ON CONFLICT(token_hash, month)
                DO UPDATE SET count = count + 1, updated_at = excluded.updated_at
                """,
                (subject, month, int(time.time())),
            )
    except sqlite3.Error:
        return


def _api_key_usage_count(record: dict, month: str | None = None) -> int:
    subject = _api_usage_subject(record)
    token_hash = str(record.get("token_hash") or "").strip().lower()
    if not API_USAGE_DB.exists() or not subject:
        return 0
    identifiers = [subject]
    if re.fullmatch(r"[a-f0-9]{64}", token_hash):
        # Backward compatibility for usage counted before project-based usage.
        identifiers.append(token_hash)
        identifiers.append(f"key:{token_hash}")
    try:
        with sqlite3.connect(API_USAGE_DB, timeout=1.5) as con:
            _ensure_api_usage_schema(con)
            rows = con.execute(
                f"SELECT COALESCE(SUM(count), 0) FROM api_usage WHERE token_hash IN ({','.join(['?'] * len(identifiers))}) AND month = ?",
                (*identifiers, month or _api_usage_month()),
            ).fetchone()
            return int(rows[0]) if rows else 0
    except sqlite3.Error:
        return 0


def _check_and_record_api_key_usage(record: dict) -> None:
    subject = _api_usage_subject(record)
    if not subject:
        raise HTTPException(status_code=503, detail="API usage subject is not configured")
    monthly_limit = int(record.get("monthly_limit") or 0)
    month = _api_usage_month()
    token_hash = str(record.get("token_hash") or "").strip().lower()
    identifiers = [subject]
    if re.fullmatch(r"[a-f0-9]{64}", token_hash):
        identifiers.extend([token_hash, f"key:{token_hash}"])
    try:
        API_USAGE_DB.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(API_USAGE_DB, timeout=3.0, isolation_level=None) as con:
            _ensure_api_usage_schema(con)
            con.execute("BEGIN IMMEDIATE")
            count = con.execute(
                f"SELECT COALESCE(SUM(count), 0) FROM api_usage WHERE token_hash IN ({','.join(['?'] * len(identifiers))}) AND month = ?",
                (*identifiers, month),
            ).fetchone()
            if monthly_limit > 0 and int(count[0] if count else 0) >= monthly_limit:
                con.execute("ROLLBACK")
                raise HTTPException(status_code=429, detail="monthly API request limit exceeded")
            con.execute(
                """
                INSERT INTO api_usage (token_hash, month, count, updated_at)
                VALUES (?, ?, 1, ?)
                ON CONFLICT(token_hash, month)
                DO UPDATE SET count = count + 1, updated_at = excluded.updated_at
                """,
                (subject, month, int(time.time())),
            )
            con.execute("COMMIT")
    except HTTPException:
        raise
    except sqlite3.Error as exc:
        raise HTTPException(status_code=503, detail="API usage accounting unavailable") from exc


def _sanitize_api_key_record(record: dict) -> dict | None:
    token_hash = str(record.get("token_hash") or "").strip().lower()
    if not re.fullmatch(r"[a-f0-9]{64}", token_hash):
        return None
    plan = str(record.get("plan") or "free").strip().lower()
    if plan not in {"free", "beta", "enterprise", "partner"}:
        plan = "free"
    status = str(record.get("status") or "active").strip().lower()
    if status not in {"active", "disabled"}:
        status = "disabled"
    scopes = record.get("scopes")
    if not isinstance(scopes, list):
        scopes = []
    allowed_origins = record.get("allowed_origins")
    if not isinstance(allowed_origins, list):
        allowed_origins = []
    monthly_limit = record.get("monthly_limit")
    try:
        monthly_limit = int(monthly_limit or (100000 if plan != "free" else 1000))
    except (TypeError, ValueError):
        monthly_limit = 100000 if plan != "free" else 1000
    return {
        "token_hash": token_hash,
        "token_preview": str(record.get("token_preview") or "")[:80],
        "status": status,
        "plan": plan,
        "user_id": str(record.get("user_id") or "")[:100],
        "project_id": str(record.get("project_id") or record.get("user_id") or "")[:100],
        "usage_subject": str(record.get("usage_subject") or f"project:{record.get('project_id') or record.get('user_id') or token_hash}")[:180],
        "project_name": str(record.get("project_name") or "OpenKataster API")[:255],
        "allowed_origins": [str(origin).strip() for origin in allowed_origins if str(origin).strip()][:50],
        "scopes": [str(scope).strip() for scope in scopes if str(scope).strip()][:50],
        "monthly_limit": max(0, monthly_limit),
    }




@dataclass(frozen=True)
class ApiAccessContext:
    mode: str
    token: str | None = None
    claims: dict | None = None

    @property
    def is_pro(self) -> bool:
        return self.mode in {"pro", "partner"}

    @property
    def scopes(self) -> set[str]:
        values = (self.claims or {}).get("scopes")
        if isinstance(values, list):
            return {str(value) for value in values}
        if self.is_pro:
            return {"map:view", "search:basic", "layers:basic", "feature:preview", "feature:read", "measure", "export:map", "export:cadastre", "embed:pro"}
        return set()


api_key_bearer = HTTPBearer(
    auto_error=False,
    scheme_name="OpenKatasterApiKey",
    description="Projekt-Key aus dem Developer-Bereich. Nur serverseitig verwenden.",
)


def _configured_pro_tokens() -> set[str]:
    raw = os.environ.get("OPENKATASTER_TILE_PRO_TOKENS", "")
    tokens = {part.strip() for part in raw.split(",") if part.strip()}
    return tokens or _configured_keys()


def api_access_context(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Security(api_key_bearer)] = None,
    session: Annotated[str | None, Query(description="Kurzlebiges Embed-Session-Token")] = None,
    token: Annotated[str | None, Query()] = None,
    api_key: Annotated[str | None, Query()] = None,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> ApiAccessContext:
    bearer = credentials.credentials if credentials and credentials.scheme.lower() == "bearer" else None
    provided = session or token or api_key or x_api_key or bearer
    if provided and provided in _configured_pro_tokens():
        return ApiAccessContext(mode="pro", token=provided)
    api_key_record = _api_key_record_for_token(provided)
    if api_key_record:
        _check_and_record_api_key_usage(api_key_record)
        claims = _api_key_claims(api_key_record)
        mode = "partner" if _claims_grant_pro_access(claims) else "free"
        return ApiAccessContext(mode=mode, token=provided, claims=claims)
    if provided:
        claims = _verify_embed_session(provided) or _verify_viewer_token(provided)
        if claims:
            grants_pro = _claims_grant_pro_access(claims)
            mode = "partner" if grants_pro and claims.get("integration") == "embed" else "pro" if grants_pro else "free"
            return ApiAccessContext(mode=mode, token=provided, claims=claims)
    return ApiAccessContext(mode="free")


def require_api_key_access(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Security(api_key_bearer)] = None,
) -> ApiAccessContext:
    provided = credentials.credentials if credentials and credentials.scheme.lower() == "bearer" else None
    record = _api_key_record_for_token(provided)
    if not record:
        raise HTTPException(
            status_code=401,
            detail="missing or invalid API key",
            headers={"WWW-Authenticate": "Bearer"},
        )
    _check_and_record_api_key_usage(record)
    claims = _api_key_claims(record)
    mode = "partner" if _claims_grant_pro_access(claims) else "free"
    return ApiAccessContext(mode=mode, token=provided, claims=claims)


class RequireScopes:
    def __init__(self, *scopes: str):
        self.required = set(scopes)

    def __call__(
        self,
        access: ApiAccessContext = Depends(api_access_context),
    ) -> ApiAccessContext:
        if not access.token:
            raise HTTPException(
                status_code=401,
                detail="authentication required",
                headers={"WWW-Authenticate": "Bearer"},
            )
        if not self.required.issubset(access.scopes):
            raise HTTPException(status_code=403, detail=f"required scope: {', '.join(sorted(self.required))}")
        return access

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


FEATURE_ADDRESS_RELATION_LIMIT = 25


@dataclass(frozen=True)
class FeatureAddressRelations:
    addresses: list[dict]
    total: int = 0
    limit: int = FEATURE_ADDRESS_RELATION_LIMIT

    @property
    def truncated(self) -> bool:
        return self.total > self.limit


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
_SEARCH_DB_QUERY_LOCKS: dict[str, threading.RLock] = {}
_SEARCH_DB_QUERY_LOCKS_LOCK = threading.Lock()


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


def search_db_query_lock(path: Path) -> threading.RLock:
    key = str(path)
    with _SEARCH_DB_QUERY_LOCKS_LOCK:
        lock = _SEARCH_DB_QUERY_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _SEARCH_DB_QUERY_LOCKS[key] = lock
        return lock


def search_db_fetchall(
    path: Path,
    query: str,
    parameters: Iterable[object] = (),
) -> list[sqlite3.Row]:
    # sqlite3 connections/cursors cannot be used concurrently even when the
    # connection was opened with check_same_thread=False.  FastAPI executes
    # synchronous search routes in a thread pool, so guard only the actual
    # execute+fetch window per state DB and leave normalization outside it.
    try:
        with search_db_query_lock(path):
            return search_db_connection(path).execute(query, parameters).fetchall()
    except sqlite3.Error as exc:
        raise search_database_unavailable(path, exc) from exc


def search_db_fetchone(
    path: Path,
    query: str,
    parameters: Iterable[object] = (),
) -> sqlite3.Row | None:
    try:
        with search_db_query_lock(path):
            return search_db_connection(path).execute(query, parameters).fetchone()
    except sqlite3.Error as exc:
        raise search_database_unavailable(path, exc) from exc


def search_database_unavailable(path: Path, error: sqlite3.Error) -> HTTPException:
    request_id = f"search-{secrets.token_hex(8)}"
    print(
        "search database unavailable "
        f"request_id={request_id} database={path.name} "
        f"error={type(error).__name__}: {error}"
    )
    return HTTPException(
        status_code=503,
        detail={
            "code": "search_database_unavailable",
            "message": "Die Suche ist vorübergehend nicht verfügbar.",
            "request_id": request_id,
        },
        headers={"X-Request-ID": request_id},
    )


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
    postcode_area_lookup.cache_clear()


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
    forwarded_host = request.headers.get("x-forwarded-host")
    forwarded_proto = request.headers.get("x-forwarded-proto")
    host = forwarded_host or request.headers.get("host") or request.url.netloc
    proto = forwarded_proto or request.url.scheme
    if host.split(":", 1)[0] == "tiles.openkataster.de":
        proto = "https"
    return f"{proto}://{host}".rstrip("/")



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


_SACHSEN_ANHALT_COMMON_FEATURE_FIELDS = frozenset(
    {
        # Stable references and trusted runtime geometry. These fields are not
        # rendered as table columns, but are required for selection restore,
        # geometry downloads and onOffice hand-off.
        "id",
        "source_db",
        "gml_id",
        "geometry",
        "bbox",
        "center",
        "address",
        "addresses",
        "address_relation_count",
        "address_relation_limit",
        "address_relations_truncated",
    }
)

_SACHSEN_ANHALT_LOCATION_DISPLAY_MAX_LENGTH = 240

_BAYERN_LOD2_BUILDING_FEATURE_FIELDS = _SACHSEN_ANHALT_COMMON_FEATURE_FIELDS | frozenset(
    {
        # Keep the useful LoD2 facts in the object table. Raw CityGML IDs,
        # source tiles, EPSG values, codelist codes and acquisition-method
        # fields remain in features.sqlite for auditability, but are not
        # meaningful columns for map users.
        "gebaeudefunktion_text",
        "name",
        "geschosse_oberirdisch",
        "dachform_text",
        "geometrische_flaeche_m2",
    }
)

_SACHSEN_ANHALT_BUILDING_FEATURE_FIELDS = _SACHSEN_ANHALT_COMMON_FEATURE_FIELDS | frozenset(
    {
        "gebaeudefunktion",
        "gebaeudefunktion_text",
        "gebaeudekennzeichen",
        "name",
        "geschosse_oberirdisch",
        "geschosse_unterirdisch",
        "dachform",
        "dachform_text",
        "dachart",
        "dachgeschossausbau",
        "dachgeschossausbau_text",
        "bauweise",
        "bauweise_text",
        "baujahr",
        "umbauter_raum_m3",
        "objekthoehe_m",
        "lage_zur_erdoberflaeche",
        "lage_zur_erdoberflaeche_text",
        "hochhaus",
        "weitere_gebaeudefunktion",
        "weitere_gebaeudefunktion_text",
        "zustand",
        "zustand_text",
        "geschossflaeche_m2",
        "grundflaeche_m2",
        "amtliche_flaeche_m2",
        "geometrische_flaeche_m2",
    }
)

_SACHSEN_ANHALT_PARCEL_FEATURE_FIELDS = _SACHSEN_ANHALT_COMMON_FEATURE_FIELDS | frozenset(
    {
        "gemarkungsschluessel",
        "gemarkung_key",
        "gemarkung",
        "gemarkungsnummer",
        "flur",
        "flurstueck",
        "flurstueckskennzeichen",
        "zaehler",
        "nenner",
        "nutzungen",
        "nutzung_haupt",
        "nutzung",
        "tatsaechliche_nutzung",
        "thema",
        "lage",
        "gemeindeteil",
        "abweichender_rechtszustand",
        "rechtsbehelfsverfahren",
        "zweifelhafter_flurstuecksnachweis",
        "zeitpunkt_der_entstehung",
        "amtliche_flaeche_m2",
    }
)


def _sachsen_anhalt_usage_detail_values(raw: str) -> list[str]:
    """Return useful ALKIS qualifiers while dropping technical ``null`` values."""
    matches = list(re.finditer(r"(?:^|,)\s*([\wäöüÄÖÜß]+)\s*:\s*", raw))
    values: list[str] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(raw)
        value = raw[match.end() : end].strip(" ,")
        if not value or value.casefold() == "null":
            continue
        if value not in values:
            values.append(value)
    return values


def normalize_sachsen_anhalt_usages(raw: object) -> list[dict]:
    """Convert the legacy ``Theme(details);area|...`` value into UI data."""
    if not isinstance(raw, str) or not raw.strip():
        return []
    usages: list[dict] = []
    for component in raw.split("|"):
        component = component.strip()
        if not component:
            continue
        descriptor, separator, raw_area = component.rpartition(";")
        if not separator:
            descriptor, raw_area = component, ""
        descriptor = descriptor.strip()
        match = re.fullmatch(r"(.*?)\((.*)\)", descriptor)
        if match:
            theme = match.group(1).strip()
            details = _sachsen_anhalt_usage_detail_values(match.group(2))
        else:
            theme = descriptor
            details = []
        if not theme:
            continue
        label = f"{theme} ({', '.join(details)})" if details else theme
        entry: dict[str, object] = {"thema": label}
        try:
            area = float(raw_area.strip())
        except (TypeError, ValueError):
            area = 0.0
        if area > 0:
            entry["flaeche_m2"] = int(area) if area.is_integer() else area
        usages.append(entry)

    usages.sort(key=lambda entry: (-float(entry.get("flaeche_m2") or 0), str(entry["thema"])))
    total_area = sum(float(entry.get("flaeche_m2") or 0) for entry in usages)
    if total_area > 0:
        for entry in usages:
            area = float(entry.get("flaeche_m2") or 0)
            if area > 0:
                entry["anteil"] = area / total_area
    return usages


def _sachsen_anhalt_address_display_values(properties: dict) -> list[str]:
    values: list[str] = []
    raw_addresses = properties.get("addresses")
    addresses = raw_addresses if isinstance(raw_addresses, list) else []
    if properties.get("address"):
        addresses = [*addresses, properties["address"]]
    for address in addresses:
        if isinstance(address, str):
            candidates = (address, address.split(",", 1)[0])
        elif isinstance(address, dict):
            street_house = str(address.get("street_house") or "").strip()
            if not street_house:
                street_house = " ".join(
                    part
                    for part in (
                        str(address.get("street") or "").strip(),
                        str(address.get("house_number") or "").strip(),
                    )
                    if part
                )
            label = str(address.get("label") or "").strip()
            candidates = (street_house, label, label.split(",", 1)[0])
        else:
            continue
        for candidate in candidates:
            candidate = str(candidate or "").strip()
            if candidate and candidate not in values:
                values.append(candidate)
    return values


def _hide_redundant_sachsen_anhalt_location(properties: dict) -> None:
    """Keep useful cadastral locations, but suppress address copies and raw lists."""
    raw_location = properties.get("lage")
    if not isinstance(raw_location, (str, int, float)):
        properties.pop("lage", None)
        return
    location = str(raw_location).strip()
    if not location or len(location) > _SACHSEN_ANHALT_LOCATION_DISPLAY_MAX_LENGTH:
        properties.pop("lage", None)
        return
    location_key = fast_compact_norm(location)
    if location_key and any(
        fast_compact_norm(candidate) == location_key
        for candidate in _sachsen_anhalt_address_display_values(properties)
    ):
        properties.pop("lage", None)
        return
    properties["lage"] = location


def normalize_feature_properties_for_response(state: str, kind: str, properties: dict) -> dict:
    """Expose stable, user-facing contracts for hybrid cadastral sources.

    The first Sachsen-Anhalt runtime was assembled from presentation-oriented
    intermediate data. Its JSON therefore contains renderer colors, duplicated
    aliases and a compact usage string. Keep that source untouched, but never
    leak those implementation fields into the object-information table. The
    Bavarian LoD2 adapter similarly retains audit/provenance fields in SQLite
    while exposing only meaningful building facts to the UI.
    """
    props = dict(properties or {})
    state_key = normalize_state_key(state)
    source_key = normalize_state_key(str(props.get("source_db") or ""))
    kind = str(kind or props.get("type") or "").strip().lower()

    if source_key == "bayern-lod2" and kind == "building":
        return {
            key: value
            for key, value in props.items()
            if key in _BAYERN_LOD2_BUILDING_FEATURE_FIELDS
            and value is not None
            and value != ""
            and value != []
            and value != {}
        }

    if state_key != "sachsen-anhalt" and source_key != "sachsen-anhalt":
        return props

    if kind == "building":
        function = props.get("gebaeudefunktion_text") or props.get("funktion")
        if function:
            props["gebaeudefunktion_text"] = function
        relative_location = str(props.get("rellage") or "").strip()
        if relative_location and not props.get("lage_zur_erdoberflaeche_text"):
            props["lage_zur_erdoberflaeche_text"] = relative_location
        elif props.get("underground") is True and not props.get("lage_zur_erdoberflaeche_text"):
            props["lage_zur_erdoberflaeche_text"] = "Unter der Erdoberfläche"
        allowed = _SACHSEN_ANHALT_BUILDING_FEATURE_FIELDS
    elif kind == "parcel":
        props["gemarkungsschluessel"] = props.get("gemarkungsschluessel") or props.get("gemaschl")
        usages = props.get("nutzungen")
        if not isinstance(usages, list):
            usages = normalize_sachsen_anhalt_usages(props.get("usage"))
        if usages:
            props["nutzungen"] = usages
            props["nutzung_haupt"] = props.get("nutzung_haupt") or usages[0].get("thema")
        _hide_redundant_sachsen_anhalt_location(props)
        allowed = _SACHSEN_ANHALT_PARCEL_FEATURE_FIELDS
    else:
        return props

    return {
        key: value
        for key, value in props.items()
        if key in allowed and value is not None and value != "" and value != [] and value != {}
    }


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




def postcode_areas_signature() -> tuple[int, int]:
    return sqlite_file_signature(POSTCODE_AREAS_DB)


def openplz_signature() -> tuple[int, int]:
    return sqlite_file_signature(OPENPLZ_DB)






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








def enrich_address_postcode(address: dict, lon: float, lat: float) -> None:
    # Do not infer postal codes at request time. Postal codes must come from
    # the built features/search SQLite data so display and search stay
    # consistent for states that have not been rebuilt with PLZ data yet.
    address.setdefault("country", "Deutschland")


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


def place_input_context_variants(value: str | None) -> tuple[str, ...]:
    """Return explicit place components without guessing arbitrary substrings.

    Address sources commonly spell a municipality and district together as
    ``Kindelbrück OT Düppel``.  Both components are useful, but ``OT`` is a
    structural separator rather than part of either official GN250 name.
    """
    text = str(value or "").strip()
    if not text:
        return tuple()
    variants = [text]
    parts = re.split(
        r"\s*,?\s+(?:OT|Ortsteil)\s+",
        text,
        maxsplit=1,
        flags=re.IGNORECASE,
    )
    if len(parts) == 2:
        for part in parts:
            candidate = part.strip(" ,")
            if candidate and candidate not in variants:
                variants.append(candidate)
    return tuple(variants)


def gn250_place_name_aliases(value: str | None, state: str | None) -> tuple[str, ...]:
    """Return safe aliases derived from an official GN250 place name.

    A slash suffix is removed only when it is the place's own state name, as
    in ``Mühlhausen/Thüringen``.  Other slash-bearing place names remain
    untouched.
    """
    text = str(value or "").strip()
    if not text:
        return tuple()
    aliases = [text]
    parenthetical_base = re.sub(r"\s*\([^)]*\)\s*$", "", text).strip()
    if parenthetical_base and parenthetical_base != text:
        aliases.append(parenthetical_base)
    state_key = normalize_state_key(state)
    if "/" in text and state_key:
        base, suffix = (part.strip() for part in text.rsplit("/", 1))
        state_names = {
            normalize_place_search_text(state_display_name(state_key)),
            normalize_place_search_text(state_key.replace("-", " ")),
        }
        if base and normalize_place_search_text(suffix) in state_names:
            aliases.append(base)
    return tuple(dict.fromkeys(alias for alias in aliases if alias))


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
            for alias in gn250_place_name_aliases(value, state):
                for key in exact_place_key_variants(alias):
                    if key:
                        index.setdefault(key, []).append(context)
    return {key: tuple(value) for key, value in index.items()}


@lru_cache(maxsize=4096)
def _exact_place_context_cached(value: str, allowed_states_key: tuple[str, ...], signature: tuple[int, int]) -> dict | None:
    allowed_states = set(allowed_states_key)
    index = exact_place_context_index(signature)
    seen: set[tuple[str, str, str]] = set()
    for candidate in place_input_context_variants(value):
        for key in exact_place_key_variants(candidate):
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
    for candidate in place_input_context_variants(place):
        for key in exact_place_key_variants(candidate):
            for context in index.get(key, tuple()):
                state = normalize_state_key(str(context.get("state") or ""))
                if state in allowed_states:
                    matches.add(state)
    return tuple(sorted(matches))


@lru_cache(maxsize=4096)
def gn250_place_bboxes_for_state_context(
    place: str,
    state: str,
    signature: tuple[int, int],
) -> tuple[tuple[float, float, float, float], ...]:
    """Return exact GN250 place extents for a place within one state.

    The fallback deliberately accepts only exact place aliases from the
    central GN250 index.  Larger municipality extents sort first and subsume
    contained locality extents, which keeps the SQL predicate both complete
    and bounded for municipalities with many districts.
    """
    state_key = normalize_state_key(state)
    if not state_key or len(str(place or "").strip()) < 2:
        return tuple()
    index = exact_place_context_index(signature)
    raw_bboxes: set[tuple[float, float, float, float]] = set()
    for candidate in place_input_context_variants(place):
        for key in exact_place_key_variants(candidate):
            for context in index.get(key, tuple()):
                if normalize_state_key(str(context.get("state") or "")) != state_key:
                    continue
                bbox = context.get("bbox")
                if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
                    continue
                try:
                    parsed = tuple(float(value) for value in bbox)
                except (TypeError, ValueError):
                    continue
                if not all(math.isfinite(value) for value in parsed):
                    continue
                min_lon, min_lat, max_lon, max_lat = parsed
                if min_lon > max_lon or min_lat > max_lat:
                    continue
                raw_bboxes.add((min_lon, min_lat, max_lon, max_lat))

    ordered = sorted(
        raw_bboxes,
        key=lambda bbox: (
            -((bbox[2] - bbox[0]) * (bbox[3] - bbox[1])),
            bbox,
        ),
    )
    selected: list[tuple[float, float, float, float]] = []
    for bbox in ordered:
        min_lon, min_lat, max_lon, max_lat = bbox
        if any(
            outer_min_lon <= min_lon
            and outer_min_lat <= min_lat
            and outer_max_lon >= max_lon
            and outer_max_lat >= max_lat
            for outer_min_lon, outer_min_lat, outer_max_lon, outer_max_lat in selected
        ):
            continue
        selected.append(bbox)
        if len(selected) >= 32:
            break
    return tuple(selected)


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
    # Point lookups should not materialize and normalize the complete GN250
    # catalogue.  The state prefix index reduces this to a few thousand cheap
    # bbox comparisons and preserves the exact ranking used by the in-memory
    # fallback below.
    if GN250_PLACES_DB.exists():
        try:
            con = sqlite3.connect(
                f"file:{GN250_PLACES_DB}?mode=ro",
                uri=True,
            )
            con.row_factory = sqlite3.Row
            try:
                rows = con.execute(
                    """
                    SELECT
                      class, name, municipality,
                      min_lon, min_lat, max_lon, max_lat
                    FROM places
                    WHERE state_key = ?
                      AND min_lon <= ? AND max_lon >= ?
                      AND min_lat <= ? AND max_lat >= ?
                    """,
                    [
                        gn250_storage_state_key(state),
                        float(lon),
                        float(lon),
                        float(lat),
                        float(lat),
                    ],
                ).fetchall()
            finally:
                con.close()
        except sqlite3.Error:
            rows = None
        if rows is not None:
            matches = []
            for row in rows:
                min_lon = fast_float(row["min_lon"])
                min_lat = fast_float(row["min_lat"])
                max_lon = fast_float(row["max_lon"])
                max_lat = fast_float(row["max_lat"])
                area = max(0.0, max_lon - min_lon) * max(
                    0.0,
                    max_lat - min_lat,
                )
                place_class = str(row["class"] or "")
                municipality = str(row["municipality"] or "").strip()
                name = str(row["name"] or "").strip()
                city = municipality or name
                if not city:
                    continue
                class_rank = 0 if place_class == "Gemeinde" else 1
                matches.append((class_rank, area, city))
            if not matches:
                return None
            _, _, name = min(matches, key=lambda item: item[:2])
            return {
                "name": name,
                "folded": normalize_place_search_text(name),
            }

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
            properties.get("address") or "",
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


def compact_feature_relation_addresses(
    con: sqlite3.Connection,
    row: sqlite3.Row,
) -> FeatureAddressRelations:
    if not sqlite_table_exists(con, "feature_addresses"):
        return FeatureAddressRelations([])
    required_columns = {
        "feature_id",
        "parcel_id",
        "address",
        "street_house",
        "lon",
        "lat",
        "source",
    }
    available_columns = {
        str(column["name"]) for column in con.execute("PRAGMA table_info(feature_addresses)")
    }
    if not required_columns.issubset(available_columns):
        return FeatureAddressRelations([])

    kind = str(row["kind"] or "")
    if kind == "building":
        relation_column = "feature_id"
    elif kind == "parcel":
        relation_column = "parcel_id"
    else:
        return FeatureAddressRelations([])

    count_row = con.execute(
        f"SELECT COUNT(*) AS relation_count FROM feature_addresses WHERE {relation_column} = ?",
        (row["id"],),
    ).fetchone()
    relation_count = int(count_row["relation_count"] or 0) if count_row else 0
    address_rows = con.execute(
        f"""
        SELECT
          address AS compact_address,
          street_house AS compact_street_house,
          parcel_id AS compact_address_parcel_id,
          lon AS compact_address_lon,
          lat AS compact_address_lat,
          source AS compact_address_source
        FROM feature_addresses
        WHERE {relation_column} = ?
        ORDER BY address, street_house, rowid
        LIMIT ?
        """,
        (row["id"], FEATURE_ADDRESS_RELATION_LIMIT),
    ).fetchall()
    return FeatureAddressRelations(
        [compact_address_properties(address_row) for address_row in address_rows],
        total=relation_count,
    )










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


def sachsen_anhalt_city_from_address_label(address: dict) -> str:
    """Read the municipality carried by the official building-reference label.

    Sachsen-Anhalt's source labels use either
    ``street house, municipality, Sachsen-Anhalt`` or
    ``street house, municipality, municipality type, Sachsen-Anhalt``.  The
    municipality is source data and must win over a GN250 bbox approximation,
    especially close to municipal boundaries.
    """
    label = str(address.get("label") or "").strip()
    parts = [part.strip() for part in label.split(",")]
    if len(parts) < 3 or normalize_state_key(parts[-1]) != "sachsen-anhalt":
        return ""

    street_house = str(address.get("street_house") or "").strip()
    if street_house and fast_compact_norm(parts[0]) != fast_compact_norm(street_house):
        return ""
    return parts[1]


def group_addresses_for_display(addresses: list[dict]) -> list[dict]:
    grouped: dict[tuple[str, str, str], dict] = {}
    passthrough: list[dict] = []
    for address in addresses:
        street, house_number = address_display_parts(address)
        postcode = str(address.get("post_code") or address.get("postal_code") or "").strip()
        city = str(address.get("city") or address.get("municipality") or address.get("locality") or "").strip()
        normalized_address = {
            **address,
            "street": street,
            "city": city,
        }
        if postcode:
            normalized_address["post_code"] = postcode
            normalized_address["postal_code"] = postcode
        else:
            normalized_address.pop("post_code", None)
            normalized_address.pop("postal_code", None)
        if not street or not house_number:
            # Official building references may deliberately have no separate
            # house number (for example Bavarian ``o.Nr.`` labels). Preserve
            # those facts as an ungrouped address instead of hiding them from
            # the object table or inventing a number.
            if street or city or str(address.get("label") or "").strip():
                normalized_address["house_number"] = house_number
                normalized_address["street_house"] = " ".join(
                    part for part in (street, house_number) if part
                ).strip()
                normalized_address["label"] = canonical_address_label(
                    street,
                    [house_number] if house_number else [],
                    postcode,
                    city,
                    str(address.get("label") or ""),
                )
                passthrough.append(normalized_address)
            continue
        key = (street.casefold(), postcode, city.casefold())
        normalized_address["_house_numbers"] = []
        group = grouped.setdefault(key, normalized_address)
        if house_number not in group["_house_numbers"]:
            group["_house_numbers"].append(house_number)

    results: list[dict] = []
    for group in grouped.values():
        house_numbers = sorted(group.pop("_house_numbers", []), key=house_number_sort_key)
        group["house_number"] = "/".join(house_numbers)
        group["street_house"] = " ".join(
            part for part in (str(group.get("street") or ""), group["house_number"]) if part
        ).strip()
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
        if normalize_state_key(state) == "sachsen-anhalt" and not str(address.get("city") or "").strip():
            source_city = sachsen_anhalt_city_from_address_label(address)
            if source_city:
                address["city"] = source_city
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


def feature_relation_addresses(
    con: sqlite3.Connection,
    source_db: str,
    kind: str,
    gml_id: str,
) -> FeatureAddressRelations:
    count_row = con.execute(
        """
        SELECT COUNT(*) AS relation_count
        FROM feature_addresses
        WHERE source_db = ? AND kind = ? AND gml_id = ?
        """,
        (source_db, kind, gml_id),
    ).fetchone()
    relation_count = int(count_row["relation_count"] or 0) if count_row else 0
    rows = con.execute(
        """
        SELECT properties_json
        FROM feature_addresses
        WHERE source_db = ? AND kind = ? AND gml_id = ?
        ORDER BY rowid
        LIMIT ?
        """,
        (source_db, kind, gml_id, FEATURE_ADDRESS_RELATION_LIMIT),
    ).fetchall()
    return FeatureAddressRelations(
        [load_properties(row["properties_json"]) for row in rows],
        total=relation_count,
    )


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


def addresses_for_feature(con: sqlite3.Connection, feature: dict, geom) -> FeatureAddressRelations:
    source_db = feature.get("source_db") or ""
    kind = feature.get("kind") or ""
    gml_id = feature.get("gml_id") or ""
    relations = FeatureAddressRelations([])
    if kind in {"building", "parcel"}:
        relations = feature_relation_addresses(con, source_db, kind, gml_id)
    addresses = list(relations.addresses)
    addresses.extend(feature_spatial_addresses(con, source_db, geom))
    return FeatureAddressRelations(
        dedupe_addresses(addresses)[:FEATURE_ADDRESS_RELATION_LIMIT],
        total=relations.total,
        limit=relations.limit,
    )


def apply_feature_address_relation_metadata(
    properties: dict,
    relations: FeatureAddressRelations,
) -> None:
    for key in (
        "address_relation_count",
        "address_relation_limit",
        "address_relations_truncated",
    ):
        properties.pop(key, None)
    if not relations.truncated:
        return
    properties["address_relation_count"] = relations.total
    properties["address_relation_limit"] = relations.limit
    properties["address_relations_truncated"] = True


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
                    address_relations = compact_feature_relation_addresses(con, row)
                    if address_relations.addresses:
                        properties["addresses"] = enrich_addresses_with_postcode(
                            address_relations.addresses,
                            properties.get("center", [None, None])[0],
                            properties.get("center", [None, None])[1],
                            state_key,
                        )
                        properties["address"] = properties["addresses"][0]["label"] if properties["addresses"] else properties.get("address", "")
                    apply_feature_address_relation_metadata(properties, address_relations)
                    if row["kind"] == "parcel":
                        area_m2 = compact_feature_area_m2(con, row["id"])
                        if area_m2 is not None:
                            properties["amtliche_flaeche_m2"] = area_m2
                    properties = normalize_feature_properties_for_response(
                        state_key,
                        str(row["kind"] or ""),
                        properties,
                    )
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
                address_relations = compact_feature_relation_addresses(con, row)
                if address_relations.addresses:
                    properties["addresses"] = address_relations.addresses
                    properties["address"] = properties["addresses"][0]["label"] if properties["addresses"] else properties.get("address", "")
                apply_feature_address_relation_metadata(properties, address_relations)
                if row["kind"] == "parcel":
                    area_m2 = compact_feature_area_m2(con, row["id"])
                    if area_m2 is not None:
                        properties["amtliche_flaeche_m2"] = area_m2
                properties = normalize_feature_properties_for_response(
                    state_key,
                    str(row["kind"] or ""),
                    properties,
                )
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
            address_relations = addresses_for_feature(con, dict(row), geom)
            properties["addresses"] = enrich_addresses_with_postcode(
                address_relations.addresses,
                geom.representative_point().x,
                geom.representative_point().y,
                state_key,
            )
            properties["address"] = properties["addresses"][0]["label"] if properties["addresses"] else ""
            apply_feature_address_relation_metadata(properties, address_relations)
            properties["geometry"] = mapping(geom)
            properties = normalize_feature_properties_for_response(
                state_key,
                str(row["kind"] or ""),
                properties,
            )
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


def feature_preview_item(item: dict, kind: str) -> dict | None:
    geometry = item.get("geometry")
    if not isinstance(geometry, dict):
        return None
    identity = "|".join(
        str(item.get(key) or "")
        for key in ("source_db", "gml_id", "flurstueckskennzeichen", "id")
    )
    if not identity.replace("|", ""):
        identity = json.dumps(geometry, sort_keys=True, separators=(",", ":"))
    hidden_fields = {
        "geometry", "bbox", "center", "source_db", "gml_id", "id",
        "flurstueckskennzeichen", "zaehler", "nenner",
    }
    available_fields = sorted(
        key for key, value in item.items()
        if key not in hidden_fields
        and value is not None
        and value != ""
        and value != []
        and value != {}
    )
    if kind == "building" and "geometrische_flaeche_m2" not in available_fields:
        available_fields.append("geometrische_flaeche_m2")
    if kind == "parcel" and (item.get("zaehler") or item.get("nenner")) and "flurstueck" not in available_fields:
        available_fields.append("flurstueck")
    return {
        "preview_id": hashlib.sha256(f"{kind}|{identity}".encode("utf-8")).hexdigest()[:20],
        "kind": kind,
        "geometry": geometry,
        "available_fields": available_fields,
    }






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


def _normalize_geocoder_tokens(text: str) -> str:
    text = text.replace("str.", "strasse")
    text = re.sub(r"\bstr\b", "strasse", text)
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def normalize_geocoder_text(value: str | None) -> str:
    return _normalize_geocoder_tokens(normalize_place_search_text(value))


def german_digraph_collapse_variants(value: str, limit: int = 32) -> tuple[str, ...]:
    """Return bounded variants for ambiguous ae/oe/ue transliterations.

    Replacing every ``ue`` at once corrupts ordinary letter sequences such as
    the one in ``quer``: ``Suederquerweg`` became ``suderqurweg``.  Generate
    individual combinations so the useful ``suderquerweg`` variant survives.
    """
    variants = [value]
    for source, target in (("ae", "a"), ("oe", "o"), ("ue", "u")):
        index = 0
        while index < len(variants) and len(variants) < max(1, int(limit)):
            candidate = variants[index]
            offset = 0
            while len(variants) < max(1, int(limit)):
                position = candidate.find(source, offset)
                if position < 0:
                    break
                collapsed = candidate[:position] + target + candidate[position + len(source):]
                if collapsed not in variants:
                    variants.append(collapsed)
                offset = position + 1
            index += 1
    return tuple(variants)


def normalize_geocoder_text_variants(value: str | None) -> tuple[str, ...]:
    variants: list[str] = []
    normalized = normalize_geocoder_text(value)
    plain = _normalize_geocoder_tokens(plain_place_search_text(value))
    candidates: list[str] = []
    for base in (normalized, plain):
        for candidate in german_digraph_collapse_variants(base):
            if candidate and candidate not in candidates:
                candidates.append(candidate)
    for candidate in candidates:
        # The ALKIS search index keeps common street abbreviations such as
        # ``Hauptstr.`` as ``hauptstr``.  Accept both the expanded query form
        # and that index-compatible form so suggestions remain selectable.
        abbreviated = re.sub(r"strasse\b", "str", candidate)
        for variant in (candidate, abbreviated):
            if variant and variant not in variants:
                variants.append(variant)
    return tuple(variants)


CITY_STATE_MUNICIPALITY_ALIASES = {
    "hamburg": ("Hamburg", "Freie und Hansestadt Hamburg"),
    "berlin": ("Berlin", "Land Berlin"),
    "bremen": ("Bremen", "Stadtgemeinde Bremen", "Freie Hansestadt Bremen"),
}


def city_norms_for_state_context(city: str | None, state: str | None) -> tuple[str, ...]:
    variants: list[str] = []

    def add_value(value: str | None) -> None:
        if len(variants) >= 256:
            return
        for candidate in normalize_geocoder_text_variants(value):
            if candidate and candidate not in variants:
                variants.append(candidate)
                if len(variants) >= 256:
                    break

    place_seeds = place_input_context_variants(city)
    for seed in place_seeds:
        add_value(seed)

    state_key = normalize_state_key(state)
    if state_key:
        place_index = exact_place_context_index(gn250_places_signature())
        context_seen: set[tuple[str, str, str]] = set()
        for seed in place_seeds:
            for key in exact_place_key_variants(seed):
                for context in place_index.get(key, tuple()):
                    context_state = normalize_state_key(str(context.get("state") or ""))
                    if context_state != state_key:
                        continue
                    context_key = (
                        context_state,
                        str(context.get("name") or ""),
                        str(context.get("municipality") or ""),
                    )
                    if context_key in context_seen:
                        continue
                    context_seen.add(context_key)
                    context_name = str(context.get("name") or "").strip()
                    municipality = str(context.get("municipality") or "").strip()
                    add_value(context_name)
                    # A district name must not silently broaden an exact city
                    # lookup to the whole parent municipality (for example
                    # Treptow-Köpenick -> all of Berlin).  Different parent
                    # names are considered only by the postcode+BBOX fallback.
                    if (
                        municipality
                        and normalize_geocoder_text(municipality)
                        == normalize_geocoder_text(context_name)
                    ):
                        add_value(municipality)

    # Some official address labels retain a trailing administrative title
    # although GN250 exposes the user-facing municipality without it.
    for variant in tuple(variants):
        if variant.endswith(" kurort"):
            candidate = variant.removesuffix(" kurort").strip()
        else:
            candidate = f"{variant} kurort".strip()
        if candidate and candidate not in variants and len(variants) < 256:
            variants.append(candidate)

    # Some ALKIS states retain the administrative prefix in their address
    # labels (for example ``Stadt Dresden``), while the national place index
    # exposes the user-facing name (``Dresden``).  Treat both forms as the
    # same city without adding state-specific special cases.
    for variant in tuple(variants):
        if variant.startswith("stadt "):
            candidate = variant.removeprefix("stadt ").strip()
        else:
            candidate = f"stadt {variant}".strip()
        if candidate and candidate not in variants and len(variants) < 256:
            variants.append(candidate)
    # The same generic administrative prefix also appears in otherwise
    # ordinary municipality labels (for example Stadtgemeinde Bremerhaven).
    for variant in tuple(variants):
        if variant.startswith("stadtgemeinde "):
            candidate = variant.removeprefix("stadtgemeinde ").strip()
        else:
            candidate = f"stadtgemeinde {variant}".strip()
        if candidate and candidate not in variants and len(variants) < 256:
            variants.append(candidate)
    aliases = CITY_STATE_MUNICIPALITY_ALIASES.get(state_key, ())
    normalized_city = normalize_geocoder_text(city)
    alias_norms = {
        normalize_geocoder_text(alias)
        for alias in aliases
        if normalize_geocoder_text(alias)
    }
    if normalized_city and normalized_city in alias_norms:
        for alias in aliases:
            add_value(alias)
    return tuple(variants)


def city_display_name_for_state(city: str | None, state: str | None) -> str:
    value = str(city or "").strip()
    state_key = normalize_state_key(state)
    aliases = CITY_STATE_MUNICIPALITY_ALIASES.get(state_key, ())
    if normalize_geocoder_text(value) in {
        normalize_geocoder_text(alias)
        for alias in aliases
        if normalize_geocoder_text(alias)
    }:
        return state_display_name(state_key)
    # Correctly cased source labels are authoritative.  Canonical recovery is
    # only for the all-lowercase values observed in a few ALKIS/OpenPLZ rows;
    # broad rewriting would undesirably turn e.g. ``Oldenburg`` into
    # ``Oldenburg (Oldb)`` or ``Mühlhausen`` into ``Mühlhausen/Thüringen``.
    if value != value.casefold():
        return value
    return _official_city_display_name_cached(
        value,
        state_key,
        gn250_places_signature(),
    )


@lru_cache(maxsize=16384)
def _official_city_display_name_cached(
    city: str,
    state: str,
    signature: tuple[int, int],
) -> str:
    """Return official GN250 casing/qualifiers for an exact locality.

    Search shards and OpenPLZ occasionally contain lower-case localities.  We
    deliberately do not apply title-casing because it corrupts names such as
    ``Buchholz i. d. N.``.  Only an exact GN250 alias is allowed to replace
    the source spelling (for example ``freden`` -> ``Freden (Leine)``).
    """
    value = str(city or "").strip()
    state_key = normalize_state_key(state)
    if not value or not state_key or signature == (0, 0) or not GN250_PLACES_DB.exists():
        return value
    storage_state = gn250_storage_state_key(state_key)
    escaped_prefix = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    try:
        con = sqlite3.connect(f"file:{GN250_PLACES_DB}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            rows = con.execute(
                """
                SELECT name, municipality, class, population
                FROM places
                WHERE state_key = ?
                  AND (
                    name LIKE ? ESCAPE '\\' COLLATE NOCASE
                    OR municipality LIKE ? ESCAPE '\\' COLLATE NOCASE
                  )
                ORDER BY
                  CASE class WHEN 'Gemeinde' THEN 0 WHEN 'Ort' THEN 1 ELSE 2 END,
                  COALESCE(population, 0) DESC,
                  name
                LIMIT 128
                """,
                (storage_state, f"{escaped_prefix}%", f"{escaped_prefix}%"),
            ).fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return value
    candidates: list[tuple[tuple[int, int, int, str], str]] = []
    seen: set[tuple[str, str]] = set()
    requested_keys = exact_place_key_variants(value)
    for row in rows:
        name = str(row["name"] or "").strip()
        municipality = str(row["municipality"] or "").strip()
        display_candidates = []
        if name:
            display_candidates.append(name)
        if municipality and municipality not in display_candidates:
            display_candidates.append(municipality)
        for display_name in display_candidates:
            aliases = gn250_place_name_aliases(display_name, state_key)
            alias_keys = {
                key
                for alias in aliases
                for key in exact_place_key_variants(alias)
            }
            if not requested_keys.intersection(alias_keys):
                continue
            dedupe_key = (
                normalize_place_search_text(display_name),
                normalize_place_search_text(municipality),
            )
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            candidates.append((
                (
                    0 if municipality and normalize_place_search_text(display_name) == normalize_place_search_text(municipality) else 1,
                    0 if normalize_place_search_text(value) == normalize_place_search_text(display_name) else 1,
                    -len(display_name),
                    display_name.casefold(),
                ),
                display_name,
            ))
    if not candidates:
        return value
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


def normalize_geocoder_house(value: str | None) -> str:
    """Return the compact legacy SQL key used by existing search.sqlite files."""
    return re.sub(r"\s+", "", normalize_geocoder_text(value))


_HOUSE_NUMBER_SLASH_TRANSLATION = str.maketrans({
    "⁄": "/",
    "∕": "/",
    "／": "/",
})
_HOUSE_NUMBER_DASH_TRANSLATION = str.maketrans({
    "‐": "-",
    "‑": "-",
    "‒": "-",
    "–": "-",
    "—": "-",
    "―": "-",
    "−": "-",
})


def normalize_house_number_semantic(value: str | None) -> str:
    """Normalize a house number without merging meaningful separators."""
    text = str(value or "").strip()
    if not text:
        return ""
    text = text.translate(_HOUSE_NUMBER_SLASH_TRANSLATION)
    text = text.translate(_HOUSE_NUMBER_DASH_TRANSLATION)
    text = text.replace("ß", "ss").replace("ẞ", "ss")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(character for character in text if not unicodedata.combining(character)).casefold()
    return "".join(
        character
        for character in text
        if character.isalnum() or character in "/-;, ."
    ).replace(" ", "")


def filter_address_rows_by_house_number(rows: Iterable, requested_house_number: str) -> list:
    """Verify legacy SQL candidates against their original house-number label."""
    requested = normalize_house_number_semantic(requested_house_number)
    if not requested:
        return []
    return [
        row
        for row in rows
        if normalize_house_number_semantic(str(row["house_number_label"] or "")) == requested
    ]












def fast_compact_norm(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", normalize_geocoder_text(value))


def fast_parcel_number_norm(value: str | None) -> str:
    """Normalize parcel numbers without collapsing numerator/denominator slashes."""
    text = str(value or "").strip()
    if not text:
        return ""
    text = text.replace("ß", "ss").replace("ẞ", "ss")
    text = text.replace("⁄", "/").replace("∕", "/").replace("／", "/")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(character for character in text if not unicodedata.combining(character)).casefold()
    return "".join(character for character in text if character.isalnum() or character == "/")


def openplz_street_norm_variants(value: str | None) -> tuple[str, ...]:
    variants: list[str] = []
    for normalized in normalize_geocoder_text_variants(value):
        compact = re.sub(r"[^a-z0-9]+", "", normalized)
        if compact and compact not in variants:
            variants.append(compact)
    return tuple(variants)


@lru_cache(maxsize=64)
def openplz_storage_state_keys_cached(
    state: str,
    signature: tuple[int, int],
) -> tuple[str, ...]:
    """Resolve canonical OpenKataster states to OpenPLZ's stored slugs."""
    state_key = normalize_state_key(state)
    if signature == (0, 0) or not state_key:
        return tuple()
    try:
        con = sqlite3.connect(f"file:{OPENPLZ_DB}?mode=ro", uri=True)
        rows = con.execute("SELECT DISTINCT state_key FROM streets").fetchall()
        con.close()
    except sqlite3.Error:
        return tuple()
    matches = sorted({
        str(row[0] or "").strip()
        for row in rows
        if str(row[0] or "").strip()
        and normalize_state_key(str(row[0] or "")) == state_key
    })
    return tuple(matches)


def openplz_storage_state_keys(state: str, signature: tuple[int, int] | None = None) -> tuple[str, ...]:
    current_signature = openplz_signature() if signature is None else signature
    return openplz_storage_state_keys_cached(normalize_state_key(state), current_signature)


@lru_cache(maxsize=8192)
def openplz_street_aliases_cached(
    place: str,
    street_query: str,
    state: str,
    limit: int,
    signature: tuple[int, int],
) -> tuple[tuple[str, str, str], ...]:
    if signature == (0, 0):
        return tuple()
    state_key = normalize_state_key(state)
    storage_state_keys = openplz_storage_state_keys(state_key, signature)
    place_norms = tuple(dict.fromkeys(city_norms_for_state_context(place, state_key)))
    street_prefixes = openplz_street_norm_variants(street_query)
    if not state_key or not storage_state_keys or not place_norms or not street_prefixes:
        return tuple()
    state_placeholders = ",".join("?" for _ in storage_state_keys)
    place_placeholders = ",".join("?" for _ in place_norms)
    prefix_clauses = " OR ".join("(street_norm >= ? AND street_norm < ?)" for _ in street_prefixes)
    prefix_params = [bound for prefix in street_prefixes for bound in (prefix, f"{prefix}\uffff")]
    try:
        con = sqlite3.connect(f"file:{OPENPLZ_DB}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            f"""
            SELECT
              street_norm,
              postal_code,
              MAX(locality) AS locality,
              MIN(priority) AS priority
            FROM aliases
            WHERE state_key IN ({state_placeholders})
              AND place_norm IN ({place_placeholders})
              AND ({prefix_clauses})
            GROUP BY street_norm, postal_code
            ORDER BY priority, street_norm, postal_code
            LIMIT ?
            """,
            [*storage_state_keys, *place_norms, *prefix_params, max(64, min(int(limit) * 32, 1024))],
        ).fetchall()
        con.close()
    except sqlite3.Error:
        return tuple()
    return tuple(
        (
            str(row["street_norm"] or "").strip(),
            str(row["postal_code"] or "").strip(),
            str(row["locality"] or place).strip(),
        )
        for row in rows
        if str(row["street_norm"] or "").strip() and str(row["postal_code"] or "").strip()
    )


def openplz_street_aliases(place: str, street_query: str, state: str, limit: int) -> tuple[tuple[str, str, str], ...]:
    return openplz_street_aliases_cached(
        (place or "").strip(),
        (street_query or "").strip(),
        normalize_state_key(state),
        int(limit),
        openplz_signature(),
    )


@lru_cache(maxsize=2048)
def openplz_unique_postcodes_for_place_cached(
    state: str,
    place: str,
    post_codes: tuple[str, ...],
    signature: tuple[int, int],
) -> tuple[str, ...]:
    """Return only postcodes that map to exactly one requested locality.

    This fallback is deliberately independent of street spelling because some
    ALKIS states provide a valid street/postcode but no municipality, and the
    national street catalogue can lag behind ALKIS.  It never guesses between
    multiple localities and always remains scoped to one federal state.
    """
    state_key = normalize_state_key(state)
    storage_state_keys = openplz_storage_state_keys(state_key, signature)
    requested_norms = set(city_norms_for_state_context(place, state_key))
    candidates = tuple(dict.fromkeys(str(value or "").strip() for value in post_codes if str(value or "").strip()))
    if signature == (0, 0) or not state_key or not storage_state_keys or not requested_norms or not candidates:
        return tuple()
    state_placeholders = ",".join("?" for _ in storage_state_keys)
    placeholders = ",".join("?" for _ in candidates)
    try:
        con = sqlite3.connect(f"file:{OPENPLZ_DB}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            f"""
            SELECT
              postal_code,
              MIN(locality) AS locality,
              COUNT(DISTINCT locality_norm) AS locality_count
            FROM streets
            WHERE state_key IN ({state_placeholders})
              AND postal_code IN ({placeholders})
            GROUP BY postal_code
            HAVING COUNT(DISTINCT locality_norm) = 1
            """,
            [*storage_state_keys, *candidates],
        ).fetchall()
        con.close()
    except sqlite3.Error:
        return tuple()
    allowed: list[str] = []
    for row in rows:
        locality_norms = set(city_norms_for_state_context(str(row["locality"] or ""), state_key))
        post_code = str(row["postal_code"] or "").strip()
        if post_code and requested_norms.intersection(locality_norms):
            allowed.append(post_code)
    return tuple(sorted(dict.fromkeys(allowed)))


def openplz_unique_postcodes_for_place(post_codes: Iterable[str], place: str, state: str) -> tuple[str, ...]:
    return openplz_unique_postcodes_for_place_cached(
        normalize_state_key(state),
        (place or "").strip(),
        tuple(sorted(dict.fromkeys(str(value or "").strip() for value in post_codes if str(value or "").strip()))),
        openplz_signature(),
    )


def openplz_place_comparison_norms(place: str | None, state: str | None) -> tuple[str, ...]:
    """Return conservative locality aliases for postcode validation.

    OpenPLZ sometimes uses the short locality (``Endingen``), while GN250 and
    the user-facing search use the official qualified name (``Endingen am
    Kaiserstuhl``).  Only a bounded set of geographic qualifier suffixes is
    removed; arbitrary substrings are never accepted as aliases.
    """
    state_key = normalize_state_key(state)
    norms = list(city_norms_for_state_context(place, state_key))
    if state_key:
        place_index = exact_place_context_index(gn250_places_signature())
        for seed in place_input_context_variants(place):
            for key in exact_place_key_variants(seed):
                for context in place_index.get(key, tuple()):
                    if normalize_state_key(str(context.get("state") or "")) != state_key:
                        continue
                    for value in (
                        str(context.get("name") or ""),
                        str(context.get("municipality") or ""),
                    ):
                        for normalized in normalize_geocoder_text_variants(value):
                            if normalized and normalized not in norms:
                                norms.append(normalized)
    qualifier_pattern = re.compile(
        r"\s+(?:am|an\s+der|an\s+dem|im|in\s+der|in\s+dem|bei|auf\s+der|auf\s+dem)\s+.+$"
    )
    for seed in place_input_context_variants(place):
        for normalized in normalize_geocoder_text_variants(seed):
            shortened = qualifier_pattern.sub("", normalized).strip()
            if shortened and shortened not in norms:
                norms.append(shortened)
    return tuple(norms)


@lru_cache(maxsize=2048)
def openplz_postcodes_for_place_context_cached(
    state: str,
    place: str,
    post_codes: tuple[str, ...],
    signature: tuple[int, int],
) -> tuple[str, ...]:
    """Validate candidate postcodes against a requested locality and state.

    Unlike the older unique-postcode helper, this accepts a postcode shared by
    multiple localities when one of them is the requested place.  Callers must
    additionally constrain candidates to the exact GN250 place extent.
    """
    state_key = normalize_state_key(state)
    storage_state_keys = openplz_storage_state_keys(state_key, signature)
    requested_norms = set(openplz_place_comparison_norms(place, state_key))
    candidates = tuple(dict.fromkeys(
        str(value or "").strip()
        for value in post_codes
        if str(value or "").strip()
    ))
    if signature == (0, 0) or not state_key or not storage_state_keys or not requested_norms or not candidates:
        return tuple()
    state_placeholders = ",".join("?" for _ in storage_state_keys)
    placeholders = ",".join("?" for _ in candidates)
    try:
        con = sqlite3.connect(f"file:{OPENPLZ_DB}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            f"""
            SELECT DISTINCT postal_code, locality
            FROM streets
            WHERE state_key IN ({state_placeholders})
              AND postal_code IN ({placeholders})
            """,
            [*storage_state_keys, *candidates],
        ).fetchall()
        con.close()
    except sqlite3.Error:
        return tuple()
    allowed: set[str] = set()
    for row in rows:
        locality_norms = set(city_norms_for_state_context(str(row["locality"] or ""), state_key))
        post_code = str(row["postal_code"] or "").strip()
        if post_code and requested_norms.intersection(locality_norms):
            allowed.add(post_code)
    return tuple(sorted(allowed))


def openplz_postcodes_for_place_context(
    post_codes: Iterable[str],
    place: str,
    state: str,
) -> tuple[str, ...]:
    return openplz_postcodes_for_place_context_cached(
        normalize_state_key(state),
        (place or "").strip(),
        tuple(sorted(dict.fromkeys(
            str(value or "").strip()
            for value in post_codes
            if str(value or "").strip()
        ))),
        openplz_signature(),
    )


def search_result_city_label(row_city: str | None, post_code: str | None, state: str, city_fallback: str = "") -> str:
    fallback = str(city_fallback or "").strip()
    candidate = fallback or str(row_city or "").strip()
    if candidate and post_code and fast_compact_norm(candidate) == fast_compact_norm(str(post_code)):
        candidate = ""
    return city_display_name_for_state(candidate, state)


def fast_float(value, fallback: float = 0.0) -> float:
    try:
        if value is None:
            return fallback
        return float(value)
    except (TypeError, ValueError):
        return fallback


def filter_address_rows_by_place_extent(
    rows: Iterable,
    place_bboxes: tuple[tuple[float, float, float, float], ...],
) -> list:
    """Keep rows whose address point lies inside an exact GN250 extent."""
    if not place_bboxes:
        return []
    accepted = []
    for row in rows:
        if row["lon"] is None or row["lat"] is None:
            continue
        lon = fast_float(row["lon"], math.nan)
        lat = fast_float(row["lat"], math.nan)
        if not math.isfinite(lon) or not math.isfinite(lat):
            continue
        if any(
            min_lon <= lon <= max_lon and min_lat <= lat <= max_lat
            for min_lon, min_lat, max_lon, max_lat in place_bboxes
        ):
            accepted.append(row)
    return accepted


def filter_address_rows_by_postcode_area(
    rows: Iterable,
    postcode_db_signature: tuple[int, int],
) -> list:
    """Keep rows whose stored postcode agrees with the postcode polygon."""
    if postcode_db_signature == (0, 0):
        return []
    accepted = []
    for row in rows:
        post_code = str(row["post_code"] or "").strip()
        if not post_code or row["lon"] is None or row["lat"] is None:
            continue
        lon = fast_float(row["lon"], math.nan)
        lat = fast_float(row["lat"], math.nan)
        if not math.isfinite(lon) or not math.isfinite(lat):
            continue
        if post_code and postcode_area_lookup(lon, lat, postcode_db_signature) == post_code:
            accepted.append(row)
    return accepted


def city_identity_comparison_norms(value: str | None, state: str) -> tuple[str, ...]:
    """Normalize a stored locality plus bounded label-only adornments."""
    text = str(value or "").strip()
    if not text:
        return tuple()
    candidates = [text]
    without_translation = re.sub(r"\s*\[[^]]*\]\s*$", "", text).strip()
    if without_translation and without_translation not in candidates:
        candidates.append(without_translation)
    for candidate in tuple(candidates):
        if "," in candidate:
            base = candidate.split(",", 1)[0].strip()
            if base and base not in candidates:
                candidates.append(base)
    norms: list[str] = []
    for candidate in candidates:
        for normalized in city_norms_for_state_context(candidate, state):
            if normalized and normalized not in norms:
                norms.append(normalized)
    return tuple(norms)


def filter_address_rows_by_place_context(
    rows: Iterable,
    place: str,
    state: str,
    place_bboxes: tuple[tuple[float, float, float, float], ...],
    postcode_db_signature: tuple[int, int],
) -> list:
    """Validate recovered rows with locality text and independent geometry.

    A postcode or an OpenPLZ street proves that a street exists in a place,
    but not that every equal house number in the same postcode belongs to that
    place.  Every fallback row must lie inside the requested GN250 extent.  A
    compatible stored locality is sufficient.  Missing or contradictory
    locality text additionally needs a matching postcode polygon; a
    contradictory locality whose own GN250 extent contains the row wins and
    is rejected.  This recovers known stale ALKIS city labels without turning
    shared-postcode neighbours into the requested address.
    """
    requested_norms = set(openplz_place_comparison_norms(place, state))
    if not requested_norms:
        return []
    accepted = []
    for row in filter_address_rows_by_place_extent(rows, place_bboxes):
        post_code = str(row["post_code"] or "").strip()
        explicit_identities: list[str] = []
        for field in ("city_label", "city_norm"):
            value = str(row[field] or "").strip()
            if not value:
                continue
            if post_code and fast_compact_norm(value) == fast_compact_norm(post_code):
                continue
            if value not in explicit_identities:
                explicit_identities.append(value)

        row_norms = {
            normalized
            for value in explicit_identities
            for normalized in city_identity_comparison_norms(value, state)
        }
        if requested_norms.intersection(row_norms):
            accepted.append(row)
            continue

        lon = fast_float(row["lon"], math.nan)
        lat = fast_float(row["lat"], math.nan)
        if not math.isfinite(lon) or not math.isfinite(lat):
            continue
        conflicting_bboxes = {
            bbox
            for value in explicit_identities
            for bbox in gn250_place_bboxes_for_state_context(
                value,
                state,
                gn250_places_signature(),
            )
        }
        if any(
            min_lon <= lon <= max_lon and min_lat <= lat <= max_lat
            for min_lon, min_lat, max_lon, max_lat in conflicting_bboxes
        ):
            continue
        if post_code and postcode_area_lookup(lon, lat, postcode_db_signature) == post_code:
            accepted.append(row)
    return accepted













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
    return sqlite_results[:int(limit)]


def search_address_result_from_row(row: sqlite3.Row, state: str, city_fallback: str = "") -> dict:
    lon = fast_float(row["lon"])
    lat = fast_float(row["lat"])
    street_label = str(row["street_label"] or "").strip()
    house_label = str(row["house_number_label"] or "").strip()
    base_label = " ".join(part for part in (street_label, house_label) if part).strip()
    post_code = str(row["post_code"] or "").strip()
    city_label = search_result_city_label(row["city_label"], post_code, state, city_fallback)
    locality = " ".join(part for part in (post_code, city_label) if part)
    label = f"{base_label}, {locality}" if base_label and locality else (base_label or locality)
    address = {
        "label": label,
        "street": street_label,
        "house_number": house_label,
        "city": city_label,
        "country": "Deutschland",
    }
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
    post_code = str(row["post_code"] or "").strip() if "post_code" in row.keys() else ""
    city_label = search_result_city_label(row["city_label"], post_code, state, city_fallback)
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
        post_code = str(row["post_code"] or "").strip() if "post_code" in row.keys() else ""
        city_label = search_result_city_label(row["city_label"], post_code, state, city_fallback)
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
    label = ", ".join(
        part
        for part in (
            f"Flur {flur}" if flur else "",
            f"Flurstück {flurstueck}" if flurstueck else "",
            gemarkung,
        )
        if part
    )
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
    flurstueck_label = (flurstueck or "").strip()
    requested_flurstueck_norm = fast_parcel_number_norm(flurstueck_label)
    flurstueck_norms = tuple(
        dict.fromkeys(
            value
            for value in (
                requested_flurstueck_norm,
                fast_compact_norm(flurstueck_label),
            )
            if value
        )
    )
    if not gemarkung or not flurstueck_norms:
        return []
    gemarkung_base = re.sub(r"\s*\([^)]*\)\s*$", "", gemarkung).strip()
    gemarkung_norms = tuple(
        dict.fromkeys(
            value
            for source in (gemarkung, gemarkung_base)
            for value in normalize_geocoder_text_variants(source)
            if value
        )
    )
    gemarkung_base_norms = tuple(
        dict.fromkeys(
            value
            for value in normalize_geocoder_text_variants(gemarkung_base)
            if value
        )
    )
    parenthesized_codes = re.findall(r"\((\d+)\)", gemarkung)
    gemarkung_numbers = tuple(
        dict.fromkeys(
            value
            for value in (
                gemarkung if re.fullmatch(r"\d+", gemarkung) else "",
                *parenthesized_codes,
            )
            if value
        )
    )
    gemarkung_norm = gemarkung_base_norms[-1] if gemarkung_base_norms else (gemarkung_norms[-1] if gemarkung_norms else "")
    flur_norm = fast_compact_norm(flur)
    query_variants: list[tuple[str, list[object]]] = []
    if gemarkung_numbers:
        # A code selected from the Gemarkung autocomplete is authoritative.
        # Never broaden a miss to similarly normalized Hofen/Höfen names.
        numeric_only = bool(re.fullmatch(r"\d+", gemarkung))
        for number in gemarkung_numbers:
            candidates = ("",) if numeric_only else (gemarkung_base_norms or gemarkung_norms)
            for candidate in candidates:
                for parcel_norm in flurstueck_norms:
                    clauses: list[str] = []
                    params: list[object] = []
                    if candidate:
                        clauses.append("gemarkung_norm = ?")
                        params.append(candidate)
                    clauses.append("gemarkungsnummer = ?")
                    params.append(number)
                    if flur_norm:
                        clauses.append("flur_norm = ?")
                        params.append(flur_norm)
                    clauses.append("flurstueck_norm = ?")
                    params.append(parcel_norm)
                    query_variants.append((" AND ".join(clauses), params))
    else:
        for candidate in gemarkung_norms:
            for parcel_norm in flurstueck_norms:
                if flur_norm:
                    query_variants.append((
                        "gemarkung_norm = ? AND flur_norm = ? AND flurstueck_norm = ?",
                        [candidate, flur_norm, parcel_norm],
                    ))
                else:
                    query_variants.append((
                        "gemarkung_norm = ? AND flurstueck_norm = ?",
                        [candidate, parcel_norm],
                    ))
    query_variants = [
        (where, list(params))
        for where, params in dict.fromkeys((where, tuple(params)) for where, params in query_variants)
    ]
    if not query_variants:
        return []
    entries = search_db_entries_for_states(states)
    seen: set[tuple[str, str, str]] = set()
    results: list[dict] = []
    for entry in entries:
        try:
            for where, params in query_variants:
                rows = search_db_fetchall(
                    entry.path,
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
                    [*params, gemarkung_norm, flur_norm, 5000],
                )
                for row in rows:
                    # Legacy indices removed '/', making 1/11, 11/1 and 111
                    # share one key.  Verify the original label before using it.
                    if fast_parcel_number_norm(row["flurstueck_label"]) != requested_flurstueck_norm:
                        continue
                    key = (entry.name, str(row["source_db"] or ""), str(row["gml_id"] or ""))
                    if key in seen:
                        continue
                    seen.add(key)
                    results.append(search_parcel_result_from_row(row, entry.name))
                    if len(results) >= int(limit):
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
    openplz_db_signature: tuple[int, int],
    postcode_db_signature: tuple[int, int],
    *,
    allow_plain_street: bool = False,
    candidate_override: tuple[tuple[str, str, str, str], ...] = tuple(),
) -> list[dict]:
    if not signature:
        return []
    states = [state for state in states_key if state]
    if not states:
        return []
    entries = search_db_entries_for_states(states)
    results: list[dict] = []
    seen: set[tuple[str, str, str, str]] = set()
    candidates = candidate_override or tuple(
        geocoder_direct_candidates(query, allow_plain_street=allow_plain_street)
    )
    for mode, street, house, city in candidates:
        street_norms = normalize_geocoder_text_variants(street)
        if not street_norms:
            continue
        for entry in entries:
            entry_city_norms = city_norms_for_state_context(city, entry.name)
            try:
                if mode == "address":
                    house_norm = normalize_geocoder_house(house)
                    if not house_norm:
                        continue
                    address_candidate_limit = max(int(limit) * 64, 512)
                    city_clause = f" AND city_norm IN ({','.join('?' for _ in entry_city_norms)})" if entry_city_norms else ""
                    city_params = list(entry_city_norms)
                    street_placeholders = ",".join("?" for _ in street_norms)
                    rows = search_db_fetchall(
                        entry.path,
                        f"""
                        SELECT *
                        FROM address_lookup
                        WHERE street_norm IN ({street_placeholders})
                          AND house_number_norm = ?
                          AND feature_kind = 'building'
                          {city_clause}
                        ORDER BY label
                        LIMIT ?
                        """,
                        [*street_norms, house_norm, *city_params, address_candidate_limit],
                    )
                    rows = filter_address_rows_by_house_number(rows, house)
                    place_bboxes = (
                        gn250_place_bboxes_for_state_context(
                            city,
                            entry.name,
                            gn250_places_signature(),
                        )
                        if not rows and city.strip()
                        else tuple()
                    )
                    if not rows and place_bboxes and openplz_db_signature != (0, 0):
                        exact_alias_norms = set(openplz_street_norm_variants(street))
                        allowed_postcodes = sorted({
                            post_code
                            for alias_norm, post_code, _locality in openplz_street_aliases(city, street, entry.name, int(limit) * 8)
                            if alias_norm in exact_alias_norms and post_code
                        })
                        if allowed_postcodes:
                            postcode_placeholders = ",".join("?" for _ in allowed_postcodes)
                            rows = search_db_fetchall(
                                entry.path,
                                f"""
                                SELECT *
                                FROM address_lookup
                                WHERE street_norm IN ({street_placeholders})
                                  AND house_number_norm = ?
                                  AND feature_kind = 'building'
                                  AND post_code IN ({postcode_placeholders})
                                ORDER BY label
                                LIMIT ?
                                """,
                                [*street_norms, house_norm, *allowed_postcodes, address_candidate_limit],
                            )
                            rows = filter_address_rows_by_house_number(rows, house)
                            rows = filter_address_rows_by_place_context(
                                rows,
                                city,
                                entry.name,
                                place_bboxes,
                                postcode_db_signature,
                            )
                    if not rows and place_bboxes and openplz_db_signature != (0, 0):
                        postcode_candidates = search_db_fetchall(
                            entry.path,
                            f"""
                            SELECT *
                            FROM address_lookup
                            WHERE street_norm IN ({street_placeholders})
                              AND house_number_norm = ?
                              AND feature_kind = 'building'
                              AND post_code <> ''
                            ORDER BY label
                            LIMIT ?
                            """,
                            [*street_norms, house_norm, address_candidate_limit],
                        )
                        postcode_candidates = filter_address_rows_by_house_number(
                            postcode_candidates,
                            house,
                        )
                        postcode_candidates = filter_address_rows_by_place_extent(
                            postcode_candidates,
                            place_bboxes,
                        )
                        postcode_candidates = filter_address_rows_by_postcode_area(
                            postcode_candidates,
                            postcode_db_signature,
                        )
                        unique_postcodes = set(openplz_unique_postcodes_for_place(
                            (str(row["post_code"] or "") for row in postcode_candidates),
                            city,
                            entry.name,
                        ))
                        if unique_postcodes:
                            rows = [
                                row for row in postcode_candidates
                                if str(row["post_code"] or "").strip() in unique_postcodes
                            ][:max(int(limit) * 3, 12)]
                    if not rows and place_bboxes:
                        bbox_clause = " OR ".join(
                            "(lon BETWEEN ? AND ? AND lat BETWEEN ? AND ?)"
                            for _bbox in place_bboxes
                        )
                        bbox_params = [
                            coordinate
                            for min_lon, min_lat, max_lon, max_lat in place_bboxes
                            for coordinate in (min_lon, max_lon, min_lat, max_lat)
                        ]
                        rows = search_db_fetchall(
                            entry.path,
                            f"""
                            SELECT *
                            FROM address_lookup
                            WHERE street_norm IN ({street_placeholders})
                              AND house_number_norm = ?
                              AND feature_kind = 'building'
                              AND post_code <> ''
                              AND lon IS NOT NULL
                              AND lat IS NOT NULL
                              AND ({bbox_clause})
                            ORDER BY label
                            LIMIT ?
                            """,
                            [*street_norms, house_norm, *bbox_params, address_candidate_limit],
                        )
                        rows = filter_address_rows_by_house_number(rows, house)
                        rows = filter_address_rows_by_place_context(
                            rows,
                            city,
                            entry.name,
                            place_bboxes,
                            postcode_db_signature,
                        )
                        allowed_context_postcodes = set(openplz_postcodes_for_place_context(
                            (str(row["post_code"] or "") for row in rows),
                            city,
                            entry.name,
                        ))
                        rows = [
                            row
                            for row in rows
                            if str(row["post_code"] or "").strip() in allowed_context_postcodes
                        ]
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
                    if not entry_city_norms:
                        continue
                    street_placeholders = ",".join("?" for _ in street_norms)
                    rows = search_db_fetchall(
                        entry.path,
                        f"""
                        SELECT *
                        FROM street_lookup
                        WHERE street_norm IN ({street_placeholders})
                          AND city_norm IN ({','.join('?' for _ in entry_city_norms)})
                        ORDER BY address_count DESC, label
                        LIMIT ?
                        """,
                        [*street_norms, *entry_city_norms, int(limit)],
                    )
                    if not rows and city.strip() and openplz_db_signature != (0, 0):
                        exact_alias_norms = set(openplz_street_norm_variants(street))
                        allowed_postcodes = sorted({
                            post_code
                            for alias_norm, post_code, _locality in openplz_street_aliases(city, street, entry.name, int(limit) * 8)
                            if alias_norm in exact_alias_norms and post_code
                        })
                        if allowed_postcodes:
                            postcode_placeholders = ",".join("?" for _ in allowed_postcodes)
                            rows = search_db_fetchall(
                                entry.path,
                                f"""
                                SELECT *
                                FROM street_lookup
                                WHERE street_norm IN ({street_placeholders})
                                  AND post_code IN ({postcode_placeholders})
                                ORDER BY address_count DESC, label
                                LIMIT ?
                                """,
                                [*street_norms, *allowed_postcodes, int(limit)],
                            )
                    if not rows and city.strip() and openplz_db_signature != (0, 0):
                        postcode_candidates = search_db_fetchall(
                            entry.path,
                            f"""
                            SELECT *
                            FROM street_lookup
                            WHERE street_norm IN ({street_placeholders})
                              AND post_code <> ''
                            ORDER BY address_count DESC, label
                            LIMIT ?
                            """,
                            [*street_norms, max(int(limit) * 16, 64)],
                        )
                        unique_postcodes = set(openplz_unique_postcodes_for_place(
                            (str(row["post_code"] or "") for row in postcode_candidates),
                            city,
                            entry.name,
                        ))
                        if unique_postcodes:
                            rows = [
                                row for row in postcode_candidates
                                if str(row["post_code"] or "").strip() in unique_postcodes
                            ][:int(limit)]
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




def _place_fts_query(query: str) -> str:
    tokens = re.findall(r"[0-9A-Za-zÄÖÜäöüß]+", query or "")
    return " ".join(f"{token}*" for token in tokens[:4] if token)


def _place_entry_from_row(row: sqlite3.Row) -> dict:
    bbox = None
    if row["min_lon"] is not None and row["min_lat"] is not None and row["max_lon"] is not None and row["max_lat"] is not None:
        bbox = [float(row["min_lon"]), float(row["min_lat"]), float(row["max_lon"]), float(row["max_lat"])]
    place_class = row["class"] or "Ort"
    return {
        "state": normalize_state_key(row["state_key"]),
        "state_label": row["state_name"],
        "name": row["name"],
        "name_norm": normalize_place_search_text(row["name"]),
        "name_ascii": compact_place_search_text(row["name"]),
        "name_plain": plain_place_search_text(row["name"]),
        "name_plain_ascii": compact_plain_place_search_text(row["name"]),
        "class": place_class,
        "municipality": row["municipality"] or "",
        "municipality_plain": plain_place_search_text(row["municipality"] or ""),
        "district": row["district"] or "",
        "ags": row["ags"] or "",
        "center": [float(row["lon"]), float(row["lat"])],
        "bbox": bbox,
        "zoom": 11.0 if place_class == "Gemeinde" else (13.0 if place_class == "Ortsteil" else 12.5),
        "priority": -10 if place_class == "Gemeinde" else (0 if place_class == "Ort" else 5),
        "population": int(row["population"] or 0),
    }


def _rank_place_suggestion(entry: dict, query_norm: str, query_ascii: str, query_plain: str, query_plain_ascii: str) -> tuple[tuple[int, int, int, str], dict] | None:
    entry_state = normalize_state_key(str(entry.get("state") or ""))
    name = str(entry.get("name") or "").strip()
    if not name:
        return None
    name_variants: list[str] = []
    for alias in gn250_place_name_aliases(name, entry_state):
        for candidate in (alias, re.sub(r"\s*\([^)]*\)\s*$", "", alias).strip()):
            if candidate and candidate not in name_variants:
                name_variants.append(candidate)
    name_norms = {normalize_place_search_text(value) for value in name_variants}
    name_asciis = {compact_place_search_text(value) for value in name_variants}
    name_plains = {plain_place_search_text(value) for value in name_variants}
    name_plain_asciis = {compact_plain_place_search_text(value) for value in name_variants}
    if not (
        any(value.startswith(query_norm) for value in name_norms)
        or any(value.startswith(query_ascii) for value in name_asciis)
        or any(value.startswith(query_plain) for value in name_plains)
        or any(value.startswith(query_plain_ascii) for value in name_plain_asciis)
    ):
        return None
    municipality = str(entry.get("municipality") or "").strip()
    name_norm = str(entry.get("name_norm") or normalize_place_search_text(name))
    state_label = str(entry.get("state_label") or state_display_name(entry_state))
    subtitle_parts = []
    if municipality and normalize_place_search_text(municipality) != name_norm:
        subtitle_parts.append(municipality)
    if state_label:
        subtitle_parts.append(state_label)
    place_class = str(entry.get("class") or "Ort")
    class_rank = 0 if place_class == "Gemeinde" else (1 if place_class == "Ortsteil" else 2)
    if query_norm in name_norms or query_plain in name_plains:
        match_rank = 0
    elif any(value.startswith(f"{query_norm} ") for value in name_norms) or any(
        value.startswith(f"{query_plain} ") for value in name_plains
    ):
        match_rank = 1
    elif any(value.startswith(query_norm) for value in name_norms) or any(
        value.startswith(query_plain) for value in name_plains
    ):
        match_rank = 2
    elif any(value.startswith(query_ascii) for value in name_asciis) or any(
        value.startswith(query_plain_ascii) for value in name_plain_asciis
    ):
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
        "center": entry.get("center"),
        "bbox": entry.get("bbox"),
        "zoom": entry.get("zoom"),
        "result_type": "place",
        "kind": "place",
    }
    population_rank = -int(entry.get("population") or 0)
    return (match_rank, population_rank, class_rank, name.casefold()), payload


def gn250_storage_state_key(value: str | None) -> str:
    state = normalize_state_key(value)
    return {
        "baden-wurttemberg": "baden_wuerttemberg",
    }.get(state, state.replace("-", "_"))


def _search_place_suggestions_from_sqlite(query: str, allowed_states: set[str], limit: int) -> dict | None:
    fts_query = _place_fts_query(query)
    if not fts_query or not GN250_PLACES_DB.exists():
        return None
    query_norm = normalize_place_search_text(query)
    query_ascii = compact_place_search_text(query)
    query_plain = plain_place_search_text(query)
    query_plain_ascii = compact_plain_place_search_text(query)
    state_values = sorted({gn250_storage_state_key(state) for state in allowed_states if gn250_storage_state_key(state)})
    state_clause = ""
    params: list[object] = [fts_query]
    if state_values:
        state_clause = f" AND p.state_key IN ({','.join('?' for _ in state_values)})"
        params.extend(state_values)
    params.append(max(int(limit) * 80, 400))
    try:
        con = sqlite3.connect(f"file:{GN250_PLACES_DB}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            columns = """
              p.id,
              p.state_key, p.state_name, p.class, p.name, p.municipality, p.district, p.ags,
              p.lon, p.lat, p.min_lon, p.min_lat, p.max_lon, p.max_lat, p.population
            """
            prefix_params: list[object] = [f"{query}%"]
            if state_values:
                prefix_params.extend(state_values)
            prefix_params.append(max(int(limit) * 80, 400))
            rows = list(con.execute(
                f"""
                SELECT {columns}
                FROM places p
                WHERE p.name LIKE ? COLLATE NOCASE
                {state_clause}
                LIMIT ?
                """,
                prefix_params,
            ).fetchall())
            seen_row_ids = {int(row["id"]) for row in rows if row["id"] is not None}
            fts_rows = con.execute(
                f"""
                SELECT {columns}
                FROM place_search ps
                JOIN places p ON p.id = ps.place_id
                WHERE place_search MATCH ?
                {state_clause}
                LIMIT ?
                """,
                params,
            ).fetchall()
            rows.extend(row for row in fts_rows if row["id"] is None or int(row["id"]) not in seen_row_ids)
        finally:
            con.close()
    except sqlite3.Error:
        return None
    if not rows:
        return {"results": []}
    seen: set[tuple[str, str, str]] = set()
    candidates: list[tuple[tuple[int, int, int, str], dict]] = []
    for row in rows:
        entry = _place_entry_from_row(row)
        entry_state = normalize_state_key(str(entry.get("state") or ""))
        if entry_state not in allowed_states:
            continue
        name_norm = str(entry.get("name_norm") or normalize_place_search_text(str(entry.get("name") or "")))
        municipality = str(entry.get("municipality") or "").strip()
        key = (entry_state, name_norm, normalize_place_search_text(municipality))
        if key in seen:
            continue
        ranked = _rank_place_suggestion(entry, query_norm, query_ascii, query_plain, query_plain_ascii)
        if not ranked:
            continue
        seen.add(key)
        candidates.append(ranked)
    candidates.sort(key=lambda item: item[0])
    return {"results": [payload for _, payload in candidates[:int(limit)]]}


@lru_cache(maxsize=4096)
def search_place_suggestions_for_dataset(dataset: str, q: str, limit: int, state: str = "") -> dict:
    query = (q or "").strip()
    if len(query) < 2:
        return {"results": []}
    allowed_states = search_suggestion_states_for_dataset(dataset, state)
    indexed_results = _search_place_suggestions_from_sqlite(query, allowed_states, limit)
    if indexed_results is not None:
        return indexed_results
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
        municipality = str(entry.get("municipality") or "").strip()
        key = (entry_state, name_norm, normalize_place_search_text(municipality))
        if key in seen:
            continue
        ranked = _rank_place_suggestion(
            entry,
            query_norm,
            query_ascii,
            query_plain,
            query_plain_ascii,
        )
        if not ranked:
            continue
        seen.add(key)
        candidates.append(ranked)
    candidates.sort(key=lambda item: item[0])
    return {"results": [payload for _, payload in candidates[:int(limit)]]}


def _include_empty_city_for_state_place(entry_state: str, place: str, place_context: dict | None) -> bool:
    entry_state = normalize_state_key(entry_state)
    if not entry_state:
        return False
    if normalize_state_key(place) == entry_state:
        return True
    if place_context and normalize_state_key(str(place_context.get("state") or "")) == entry_state:
        municipality = normalize_geocoder_text(str(place_context.get("municipality") or ""))
        name = normalize_geocoder_text(str(place_context.get("name") or ""))
        if municipality and name and municipality == name:
            return True
    return False


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
    place_context = exact_place_context(place, allowed_states)
    municipality = place_context_as_municipality(place_context)
    inferred_states = states_for_place_context(place, allowed_states)
    entry_states = tuple(sorted(inferred_states)) if len(inferred_states) == 1 else states_key
    city_names: list[str] = []
    for value in (place, str((municipality or {}).get("name") or "")):
        value = value.strip()
        if value and normalize_geocoder_text(value) not in {normalize_geocoder_text(item) for item in city_names}:
            city_names.append(value)
    street_norms = normalize_geocoder_text_variants(query)
    if not city_names or not street_norms:
        return tuple()
    results: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for entry in search_db_entries_for_states(entry_states):
        try:
            rows = []
            for city_name in city_names:
                city_norms = city_norms_for_state_context(city_name, entry.name)
                if not city_norms:
                    continue
                rows.extend(search_db_fetchall(
                    entry.path,
                    f"""
                    SELECT
                      street_label,
                      city_label,
                      label,
                      SUM(address_count) AS address_count,
                      AVG(lon) AS lon,
                      AVG(lat) AS lat,
                      MIN(min_lon) AS min_lon,
                      MIN(min_lat) AS min_lat,
                      MAX(max_lon) AS max_lon,
                      MAX(max_lat) AS max_lat
                    FROM street_lookup
                    WHERE city_norm IN ({','.join('?' for _ in city_norms)})
                      AND ({' OR '.join('street_norm LIKE ?' for _ in street_norms)})
                    GROUP BY street_norm, city_norm
                    ORDER BY address_count DESC, street_label
                    LIMIT ?
                    """,
                    [*city_norms, *(f"{street_norm}%" for street_norm in street_norms), int(limit) * 2],
                ))
        except sqlite3.Error:
            continue
        for row in rows:
            street_label = str(row["street_label"] or "").strip()
            city_label = city_display_name_for_state(row["city_label"] or city_name, entry.name)
            key = (entry.name, normalize_geocoder_text(street_label), normalize_geocoder_text(city_label))
            if not street_label or key in seen:
                continue
            seen.add(key)
            results.append({
                "label": street_label,
                "value": street_label,
                "subtitle": city_label,
                "kind": "street",
                "result_type": "street",
                "state": entry.name,
                "state_label": state_display_name(entry.name),
                "address_count": int(row["address_count"] or 0),
                "center": [float(row["lon"]), float(row["lat"])] if row["lon"] is not None and row["lat"] is not None else None,
                "bbox": [float(row["min_lon"]), float(row["min_lat"]), float(row["max_lon"]), float(row["max_lat"])] if row["min_lon"] is not None and row["min_lat"] is not None and row["max_lon"] is not None and row["max_lat"] is not None else None,
                "zoom": 17.4,
            })
            if len(results) >= int(limit):
                return tuple(results)
    return tuple(results[:int(limit)])


def search_street_suggestions_openplz(
    place: str,
    q: str,
    limit: int,
    states: set[str],
) -> list[dict]:
    place = (place or "").strip()
    query = (q or "").strip()
    if len(place) < 2 or len(query) < 2 or openplz_signature() == (0, 0):
        return []
    place_context = exact_place_context(place, states)
    inferred_states = states_for_place_context(place, states)
    entry_states = tuple(sorted(inferred_states)) if len(inferred_states) == 1 else tuple(sorted(states))
    place_candidates: list[str] = []
    for value in (
        place,
        str((place_context or {}).get("name") or ""),
        str((place_context or {}).get("municipality") or ""),
    ):
        value = value.strip()
        if value and normalize_geocoder_text(value) not in {
            normalize_geocoder_text(candidate) for candidate in place_candidates
        }:
            place_candidates.append(value)
    display_place = str((place_context or {}).get("name") or place).strip()
    street_prefixes = normalize_geocoder_text_variants(query)
    if not entry_states or not place_candidates or not street_prefixes:
        return []

    groups: dict[tuple[str, str], dict] = {}
    for entry in search_db_entries_for_states(entry_states):
        aliases: set[tuple[str, str]] = set()
        for place_candidate in place_candidates:
            aliases.update(
                (alias_norm, post_code)
                for alias_norm, post_code, _locality in openplz_street_aliases(
                    place_candidate,
                    query,
                    entry.name,
                    int(limit),
                )
            )
        if not aliases:
            continue
        allowed_postcodes = sorted({post_code for _alias_norm, post_code in aliases if post_code})
        if not allowed_postcodes:
            continue
        postcode_placeholders = ",".join("?" for _ in allowed_postcodes)
        prefix_clauses = " OR ".join("(street_norm >= ? AND street_norm < ?)" for _ in street_prefixes)
        prefix_params = [bound for prefix in street_prefixes for bound in (prefix, f"{prefix}\uffff")]
        try:
            rows = search_db_fetchall(
                entry.path,
                f"""
                SELECT *
                FROM street_lookup
                WHERE post_code IN ({postcode_placeholders})
                  AND ({prefix_clauses})
                ORDER BY address_count DESC, street_label
                LIMIT ?
                """,
                [*allowed_postcodes, *prefix_params, max(128, min(int(limit) * 64, 1024))],
            )
        except sqlite3.Error:
            continue
        for row in rows:
            post_code = str(row["post_code"] or "").strip()
            row_alias_norms = openplz_street_norm_variants(str(row["street_label"] or row["street_norm"] or ""))
            if not any((row_alias_norm, post_code) in aliases for row_alias_norm in row_alias_norms):
                continue
            street_label = str(row["street_label"] or "").strip()
            if not street_label:
                continue
            key = (entry.name, normalize_geocoder_text(street_label))
            weight = max(int(row["address_count"] or 0), 1)
            lon = fast_float(row["lon"])
            lat = fast_float(row["lat"])
            group = groups.setdefault(
                key,
                {
                    "state": entry.name,
                    "street": street_label,
                    "address_count": 0,
                    "weighted_lon": 0.0,
                    "weighted_lat": 0.0,
                    "weight": 0,
                    "min_lon": fast_float(row["min_lon"], lon),
                    "min_lat": fast_float(row["min_lat"], lat),
                    "max_lon": fast_float(row["max_lon"], lon),
                    "max_lat": fast_float(row["max_lat"], lat),
                },
            )
            group["address_count"] += int(row["address_count"] or 0)
            group["weighted_lon"] += lon * weight
            group["weighted_lat"] += lat * weight
            group["weight"] += weight
            group["min_lon"] = min(group["min_lon"], fast_float(row["min_lon"], lon))
            group["min_lat"] = min(group["min_lat"], fast_float(row["min_lat"], lat))
            group["max_lon"] = max(group["max_lon"], fast_float(row["max_lon"], lon))
            group["max_lat"] = max(group["max_lat"], fast_float(row["max_lat"], lat))

    ranked = sorted(groups.values(), key=lambda group: (-int(group["address_count"]), str(group["street"]).casefold()))
    results: list[dict] = []
    for group in ranked[:int(limit)]:
        weight = max(int(group["weight"]), 1)
        state_key = str(group["state"])
        results.append({
            "label": str(group["street"]),
            "value": str(group["street"]),
            "subtitle": display_place,
            "kind": "street",
            "result_type": "street",
            "state": state_key,
            "state_label": state_display_name(state_key),
            "address_count": int(group["address_count"]),
            "center": [float(group["weighted_lon"]) / weight, float(group["weighted_lat"]) / weight],
            "bbox": [
                float(group["min_lon"]),
                float(group["min_lat"]),
                float(group["max_lon"]),
                float(group["max_lat"]),
            ],
            "zoom": 17.4,
        })
    return results


def search_street_suggestions_unique_postcode(
    place: str,
    q: str,
    limit: int,
    states: set[str],
) -> list[dict]:
    """Recover ALKIS streets missing from OpenPLZ's street catalogue.

    Candidate streets still come from the ALKIS search index.  OpenPLZ is used
    to validate the postcode against the requested locality and federal state.
    Shared postcodes are accepted only when the ALKIS street center also lies
    inside an exact GN250 place extent.
    """
    place = (place or "").strip()
    query = (q or "").strip()
    if len(place) < 2 or len(query) < 2 or openplz_signature() == (0, 0):
        return []
    inferred_states = states_for_place_context(place, states)
    entry_states = tuple(sorted(inferred_states)) if len(inferred_states) == 1 else tuple(sorted(states))
    street_prefixes = normalize_geocoder_text_variants(query)
    if not entry_states or not street_prefixes:
        return []

    groups: dict[tuple[str, str], dict] = {}
    for entry in search_db_entries_for_states(entry_states):
        prefix_clauses = " OR ".join("(street_norm >= ? AND street_norm < ?)" for _ in street_prefixes)
        prefix_params = [bound for prefix in street_prefixes for bound in (prefix, f"{prefix}\uffff")]
        try:
            rows = search_db_fetchall(
                entry.path,
                f"""
                SELECT *
                FROM street_lookup
                WHERE post_code <> ''
                  AND ({prefix_clauses})
                ORDER BY address_count DESC, street_label
                LIMIT ?
                """,
                [*prefix_params, max(256, min(int(limit) * 128, 2048))],
            )
        except sqlite3.Error:
            continue
        unique_postcodes = set(openplz_unique_postcodes_for_place(
            (str(row["post_code"] or "") for row in rows),
            place,
            entry.name,
        ))
        place_bboxes = gn250_place_bboxes_for_state_context(
            place,
            entry.name,
            gn250_places_signature(),
        )
        context_postcodes = set(openplz_postcodes_for_place_context(
            (str(row["post_code"] or "") for row in rows),
            place,
            entry.name,
        )) if place_bboxes else set()
        if not unique_postcodes and not context_postcodes:
            continue
        for row in rows:
            post_code = str(row["post_code"] or "").strip()
            lon = fast_float(row["lon"])
            lat = fast_float(row["lat"])
            inside_place = any(
                min_lon <= lon <= max_lon and min_lat <= lat <= max_lat
                for min_lon, min_lat, max_lon, max_lat in place_bboxes
            )
            if post_code not in unique_postcodes and not (
                post_code in context_postcodes and inside_place
            ):
                continue
            street_label = str(row["street_label"] or "").strip()
            if not street_label:
                continue
            key = (entry.name, normalize_geocoder_text(street_label))
            weight = max(int(row["address_count"] or 0), 1)
            group = groups.setdefault(
                key,
                {
                    "state": entry.name,
                    "street": street_label,
                    "address_count": 0,
                    "weighted_lon": 0.0,
                    "weighted_lat": 0.0,
                    "weight": 0,
                    "min_lon": fast_float(row["min_lon"], lon),
                    "min_lat": fast_float(row["min_lat"], lat),
                    "max_lon": fast_float(row["max_lon"], lon),
                    "max_lat": fast_float(row["max_lat"], lat),
                },
            )
            group["address_count"] += int(row["address_count"] or 0)
            group["weighted_lon"] += lon * weight
            group["weighted_lat"] += lat * weight
            group["weight"] += weight
            group["min_lon"] = min(group["min_lon"], fast_float(row["min_lon"], lon))
            group["min_lat"] = min(group["min_lat"], fast_float(row["min_lat"], lat))
            group["max_lon"] = max(group["max_lon"], fast_float(row["max_lon"], lon))
            group["max_lat"] = max(group["max_lat"], fast_float(row["max_lat"], lat))

    ranked = sorted(groups.values(), key=lambda group: (-int(group["address_count"]), str(group["street"]).casefold()))
    results: list[dict] = []
    for group in ranked[:int(limit)]:
        weight = max(int(group["weight"]), 1)
        state_key = str(group["state"])
        results.append({
            "label": str(group["street"]),
            "value": str(group["street"]),
            "subtitle": place,
            "kind": "street",
            "result_type": "street",
            "state": state_key,
            "state_label": state_display_name(state_key),
            "address_count": int(group["address_count"]),
            "center": [float(group["weighted_lon"]) / weight, float(group["weighted_lat"]) / weight],
            "bbox": [
                float(group["min_lon"]),
                float(group["min_lat"]),
                float(group["max_lon"]),
                float(group["max_lat"]),
            ],
            "zoom": 17.4,
        })
    return results


def search_street_suggestions_for_dataset(dataset: str, place: str, q: str, limit: int, state: str = "") -> dict:
    if not is_virtual_germany_dataset(dataset):
        get_dataset(dataset)
    states = search_suggestion_states_for_dataset(dataset, state)
    results = list(search_street_suggestions_cached(
        place,
        q,
        int(limit),
        tuple(sorted(state for state in states if state)),
        search_db_signature_for_states(states),
    ))
    if not results:
        results = search_street_suggestions_openplz(place, q, int(limit), states)
    if not results:
        results = search_street_suggestions_unique_postcode(place, q, int(limit), states)
    if not results and place.strip():
        seen: set[tuple[str, str, str]] = set()
        for item in results:
            seen.add((str(item.get("state") or ""), normalize_geocoder_text(str(item.get("label") or "")), normalize_geocoder_text(str(item.get("subtitle") or ""))))
        place_suggestions = search_place_suggestions_for_dataset(dataset, place, 8, state=state).get("results") or []
        for suggestion in place_suggestions:
            suggested_place = str(suggestion.get("value") or suggestion.get("label") or "").strip()
            suggested_state = normalize_state_key(str(suggestion.get("state") or ""))
            if not suggested_place or normalize_place_search_text(suggested_place) == normalize_place_search_text(place):
                continue
            candidate_states = {suggested_state} if suggested_state in states else states
            candidate_results = search_street_suggestions_cached(
                suggested_place,
                q,
                int(limit),
                tuple(sorted(state for state in candidate_states if state)),
                search_db_signature_for_states(candidate_states),
            )
            for candidate in candidate_results:
                key = (str(candidate.get("state") or ""), normalize_geocoder_text(str(candidate.get("label") or "")), normalize_geocoder_text(str(candidate.get("subtitle") or "")))
                if key in seen:
                    continue
                seen.add(key)
                results.append(dict(candidate))
                if len(results) >= int(limit):
                    return {"results": results[:int(limit)]}
    return {"results": results[:int(limit)]}


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
    requested_code = ""
    code_match = re.search(
        r"\(\s*((?=[0-9A-Za-z]*[0-9])[0-9A-Za-z]+)\s*\)\s*$",
        query,
    )
    if code_match:
        requested_code = code_match.group(1).strip()
        query = query[:code_match.start()].strip()
        if len(query) < 2:
            return tuple()
    primary_query_norms = tuple(dict.fromkeys(
        value
        for value in (
            normalize_geocoder_text(query),
            _normalize_geocoder_tokens(plain_place_search_text(query)),
        )
        if value
    ))
    query_norms = tuple(dict.fromkeys((
        *primary_query_norms,
        *normalize_geocoder_text_variants(query),
    )))
    if not query_norms:
        return tuple()
    entries = search_db_entries_for_states(states_key)

    def suggestion_payload(entry_name: str, row: sqlite3.Row) -> dict | None:
        gemarkung_label = str(row["gemarkung_label"] or "").strip()
        if not gemarkung_label:
            return None
        gemarkungsnummer = str(row["gemarkungsnummer"] or "").strip()
        return {
            "label": gemarkung_label,
            "gemarkung": gemarkung_label,
            "subtitle": state_display_name(entry_name),
            "state": entry_name,
            "state_label": state_display_name(entry_name),
            "gemarkungsnummer": gemarkungsnummer,
            "parcel_count": int(row["parcel_count"] or 0),
        }

    # Search exact names across every active state before considering prefix
    # matches.  This prevents an exact Gemarkung in a later state from being
    # displaced by popular prefixes in an alphabetically earlier state.  The
    # plain normalization mirrors the producer's NFKD search.sqlite values
    # for real umlauts (for example Überseehafen -> uberseehafen).  The
    # additional variants below also support an ASCII Ueberseehafen input.
    exact_placeholders = ",".join("?" for _ in primary_query_norms)
    code_clause = " AND gemarkungsnummer = ?" if requested_code else ""
    code_params = [requested_code] if requested_code else []
    exact_candidates: list[tuple[tuple[int, str, str, str], dict]] = []
    exact_seen: set[tuple[str, str]] = set()
    for entry in entries:
        try:
            rows = search_db_fetchall(
                entry.path,
                f"""
                SELECT gemarkung_label, gemarkungsnummer, COUNT(*) AS parcel_count
                FROM parcel_lookup
                WHERE gemarkung_norm IN ({exact_placeholders})
                  {code_clause}
                GROUP BY gemarkung_norm, gemarkungsnummer
                ORDER BY parcel_count DESC, gemarkung_label
                """,
                [*primary_query_norms, *code_params],
            )
        except sqlite3.Error:
            continue
        for row in rows:
            payload = suggestion_payload(entry.name, row)
            if payload is None:
                continue
            gemarkungsnummer = str(payload["gemarkungsnummer"])
            key = (entry.name, gemarkungsnummer or normalize_geocoder_text(str(payload["label"])))
            if key in exact_seen:
                continue
            exact_seen.add(key)
            exact_candidates.append((
                (
                    -int(payload["parcel_count"]),
                    str(payload["label"]).casefold(),
                    entry.name,
                    gemarkungsnummer,
                ),
                payload,
            ))
    if exact_candidates:
        exact_candidates.sort(key=lambda item: item[0])
        return tuple(payload for _, payload in exact_candidates[:int(limit)])

    # While the user is still typing, keep the existing bounded/early-return
    # prefix behavior.  Query every supported spelling variant, but rank the
    # producer-compatible primary spelling ahead of transliteration fallbacks.
    primary_globs = tuple(f"{value}*" for value in primary_query_norms)
    query_globs = tuple(f"{value}*" for value in query_norms)
    primary_rank_sql = " OR ".join("gemarkung_norm GLOB ?" for _ in primary_globs)
    query_sql = " OR ".join("gemarkung_norm GLOB ?" for _ in query_globs)
    results: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for entry in entries:
        try:
            rows = search_db_fetchall(
                entry.path,
                f"""
                SELECT
                  gemarkung_label,
                  gemarkungsnummer,
                  COUNT(*) AS parcel_count,
                  CASE WHEN ({primary_rank_sql}) THEN 0 ELSE 1 END AS match_rank
                FROM parcel_lookup
                WHERE ({query_sql})
                  {code_clause}
                GROUP BY gemarkung_norm, gemarkungsnummer
                ORDER BY match_rank, parcel_count DESC, gemarkung_label
                LIMIT ?
                """,
                [*primary_globs, *query_globs, *code_params, int(limit) * 2],
            )
        except sqlite3.Error:
            continue
        for row in rows:
            payload = suggestion_payload(entry.name, row)
            if payload is None:
                continue
            gemarkungsnummer = str(payload["gemarkungsnummer"])
            key = (entry.name, gemarkungsnummer or normalize_geocoder_text(str(payload["label"])))
            if key in seen:
                continue
            seen.add(key)
            results.append(payload)
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
                rows = search_db_fetchall(
                    entry.path,
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
                )
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
    # Interactive search is intentionally served only by the per-state
    # search.sqlite files. A miss must stay cheap and never scan features.sqlite.
    return []






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
        house_spans: list[tuple[str, int]] = []
        # Structured form fields are joined into one query before this parser
        # runs.  Keep separated suffixes/ranges with the house number instead
        # of treating them as the first token of the municipality (``8 a
        # Loose`` previously became house ``8`` in city ``a Loose``).
        if (
            index + 3 < len(tokens)
            and re.fullmatch(r"[A-Za-zÄÖÜäöü]", tokens[index + 1])
            and tokens[index + 2] in {"-", "/"}
            and re.fullmatch(r"\d+[A-Za-zÄÖÜäöü]?", tokens[index + 3])
        ):
            house_spans.append((
                f"{token} {tokens[index + 1]}{tokens[index + 2]}{tokens[index + 3]}",
                index + 4,
            ))
        if index + 1 < len(tokens) and re.fullmatch(
            r"[A-Za-zÄÖÜäöü](?:\d+[A-Za-zÄÖÜäöü]?)?",
            tokens[index + 1],
        ):
            house_spans.append((f"{token} {tokens[index + 1]}", index + 2))
        if index + 1 < len(tokens) and re.fullmatch(
            r"\d+\s*[-/]\s*\d+[A-Za-zÄÖÜäöü]?",
            tokens[index + 1],
        ):
            house_spans.append((f"{token} {tokens[index + 1]}", index + 2))
        if (
            index + 2 < len(tokens)
            and tokens[index + 1] in {"-", "/"}
            and any(ch.isdigit() for ch in tokens[index + 2])
        ):
            house_spans.append((f"{token}{tokens[index + 1]}{tokens[index + 2]}", index + 3))
        house_spans.append((token.strip(), index + 1))
        for house, city_start in house_spans:
            city = " ".join(tokens[city_start:]).strip()
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


def structured_geocoder_candidates(
    street: str | None,
    house_number: str | None,
    city: str | None,
) -> tuple[tuple[str, str, str, str], ...]:
    """Create a hashable direct-search candidate from structured form fields."""
    street_value = str(street or "").strip()
    house_value = str(house_number or "").strip()
    city_value = str(city or "").strip()
    if not street_value:
        return tuple()
    mode = "address" if house_value else "street"
    return ((mode, street_value, house_value, city_value),)




def _unified_exact_place_span(
    value: str,
    allowed_states: set[str],
) -> tuple[str, tuple[dict, ...], str]:
    """Extract an exact GN250 place at either edge of a free-form query."""
    tokens = [token.strip(" ,;") for token in re.findall(r"\S+", value or "")]
    tokens = [token for token in tokens if token]
    if not tokens:
        return "", tuple(), ""
    index = exact_place_context_index(gn250_places_signature())
    candidates: list[tuple[int, int, int, str, tuple[dict, ...]]] = []
    token_count = len(tokens)
    for start in range(token_count):
        for end in range(start + 1, token_count + 1):
            # Edge-only matching covers both common address orders while
            # avoiding arbitrary place-name matches inside a street name.
            if start != 0 and end != token_count:
                continue
            phrase = " ".join(tokens[start:end]).strip()
            if len(phrase) < 2 or any(character.isdigit() for character in phrase):
                continue
            matches: list[dict] = []
            seen: set[tuple[str, str, str]] = set()
            for key in exact_place_key_variants(phrase):
                for context in index.get(key, tuple()):
                    state = normalize_state_key(str(context.get("state") or ""))
                    if state not in allowed_states:
                        continue
                    dedupe_key = (
                        state,
                        normalize_place_search_text(str(context.get("name") or "")),
                        normalize_place_search_text(str(context.get("municipality") or "")),
                    )
                    if dedupe_key in seen:
                        continue
                    seen.add(dedupe_key)
                    matches.append(dict(context))
            if matches:
                candidates.append((end - start, 1 if end == token_count else 0, start, phrase, tuple(matches)))
    if not candidates:
        return "", tuple(), " ".join(tokens)
    _, _, start, phrase, contexts = max(candidates, key=lambda item: (item[0], item[1], -item[2]))
    phrase_length = len(phrase.split())
    remainder = " ".join(tokens[:start] + tokens[start + phrase_length:]).strip()
    return phrase, contexts, remainder


def parse_unified_address_query(query: str, allowed_states: set[str]) -> dict:
    """Parse place, postcode, street and house number from one input line."""
    raw_query = re.sub(r"\s+", " ", str(query or "").replace(",", " ")).strip()
    postcode_match = re.search(r"(?<!\d)(\d{5})(?!\d)", raw_query)
    postcode = postcode_match.group(1) if postcode_match else ""
    without_postcode = re.sub(r"(?<!\d)\d{5}(?!\d)", " ", raw_query, count=1)
    without_postcode = re.sub(r"\s+", " ", without_postcode).strip()
    place, place_contexts, remainder = _unified_exact_place_span(without_postcode, allowed_states)

    address_candidates = [
        candidate
        for candidate in geocoder_direct_candidates(remainder)
        if candidate[0] == "address" and not candidate[3].strip()
    ]
    chosen_address = max(
        address_candidates,
        key=lambda candidate: (
            len(search_tokens(candidate[1])),
            len(candidate[1]),
            len(candidate[2]),
        ),
        default=None,
    )
    street = str(chosen_address[1] if chosen_address else remainder).strip(" ,;")
    house_number = str(chosen_address[2] if chosen_address else "").strip(" ,;")
    place_context = dict(place_contexts[0]) if place_contexts else None
    return {
        "query": str(query or "").strip(),
        "postcode": postcode,
        "place": place,
        "place_context": place_context,
        "place_contexts": [dict(context) for context in place_contexts],
        "street": street,
        "house_number": house_number,
        "has_house_number": bool(house_number),
    }


def _unified_result_distance(item: dict, near_lon: float | None, near_lat: float | None) -> float:
    if near_lon is None or near_lat is None:
        return 0.0
    center = item.get("center") if isinstance(item.get("center"), (list, tuple)) else []
    if len(center) < 2:
        return float("inf")
    try:
        lon = float(center[0])
        lat = float(center[1])
    except (TypeError, ValueError):
        return float("inf")
    lon_scale = max(0.35, math.cos(math.radians((lat + float(near_lat)) / 2.0)))
    return ((lon - float(near_lon)) * lon_scale) ** 2 + (lat - float(near_lat)) ** 2


def rank_unified_search_results(
    results: list[dict],
    parsed: dict,
    near_lon: float | None = None,
    near_lat: float | None = None,
) -> list[dict]:
    """Globally rank and de-duplicate address, street and place results."""
    requested_street_norms = set(normalize_geocoder_text_variants(str(parsed.get("street") or "")))
    requested_house = normalize_house_number_semantic(str(parsed.get("house_number") or ""))
    requested_postcode = str(parsed.get("postcode") or "").strip()
    requested_place_norms: set[str] = set()
    place_values = [parsed.get("place")]
    singular_context = parsed.get("place_context") if isinstance(parsed.get("place_context"), dict) else {}
    place_values.extend((singular_context.get("name"), singular_context.get("municipality")))
    for context in parsed.get("place_contexts") or []:
        place_values.extend((context.get("name"), context.get("municipality")))
    for value in place_values:
        requested_place_norms.update(normalize_geocoder_text_variants(str(value or "")))

    ranked: list[tuple[tuple, int, dict]] = []
    for original_index, item in enumerate(results):
        result_type = str(item.get("result_type") or item.get("kind") or "")
        address = item.get("address") if isinstance(item.get("address"), dict) else {}
        feature = item.get("feature") if isinstance(item.get("feature"), dict) else {}
        street_value = str(address.get("street") or feature.get("street") or item.get("street") or item.get("label") or "")
        street_norms = set(normalize_geocoder_text_variants(street_value))
        house_value = normalize_house_number_semantic(str(address.get("house_number") or ""))
        postcode = str(address.get("post_code") or address.get("postal_code") or item.get("post_code") or "").strip()
        city = str(address.get("city") or feature.get("municipality") or item.get("municipality") or "")
        city_norms = set(normalize_geocoder_text_variants(city))

        if parsed.get("has_house_number"):
            type_rank = {"address": 0, "street": 1, "place": 2}.get(result_type, 3)
        elif parsed.get("street"):
            type_rank = {"street": 0, "address": 1, "place": 2}.get(result_type, 3)
        else:
            type_rank = {"place": 0, "street": 1, "address": 2}.get(result_type, 3)
        rank = (
            type_rank,
            0 if not requested_postcode or postcode == requested_postcode else 1,
            0 if not requested_place_norms or requested_place_norms.intersection(city_norms) else 1,
            0 if not requested_street_norms or requested_street_norms.intersection(street_norms) else 1,
            0 if not requested_house or house_value == requested_house else 1,
            _unified_result_distance(item, near_lon, near_lat),
            original_index,
            str(item.get("label") or "").casefold(),
        )
        ranked.append((rank, original_index, item))
    ranked.sort(key=lambda entry: entry[0])

    visible: list[dict] = []
    seen: set[tuple[str, str, str, str]] = set()
    for _rank, _index, item in ranked:
        center = item.get("center") if isinstance(item.get("center"), (list, tuple)) else []
        center_key = ",".join(f"{fast_float(value):.5f}" for value in center[:2])
        key = (
            str(item.get("result_type") or item.get("kind") or ""),
            normalize_state_key(str(item.get("state") or "")),
            normalize_geocoder_text(str(item.get("label") or "")),
            center_key,
        )
        if key in seen:
            continue
        seen.add(key)
        visible.append(item)
    return visible


def search_direct_geocoder_for_dataset(
    query: str,
    limit: int,
    search_states: set[str],
    *,
    allow_plain_street: bool = False,
    candidate_override: tuple[tuple[str, str, str, str], ...] = tuple(),
) -> list[dict]:
    sqlite_results = search_sqlite_direct_lookup(
        query,
        int(limit),
        tuple(sorted(search_states)),
        search_db_signature_for_states(search_states),
        openplz_signature(),
        postcode_areas_signature(),
        allow_plain_street=allow_plain_street,
        candidate_override=candidate_override,
    )
    return sqlite_results[:int(limit)]


@lru_cache(maxsize=8192)
def _openplz_locality_for_address_cached(
    state: str,
    street: str,
    postcode: str,
    signature: tuple[int, int],
) -> str:
    """Return a locality only when street, postcode and state identify one."""
    state_key = normalize_state_key(state)
    storage_states = openplz_storage_state_keys(state_key, signature)
    street_norms = openplz_street_norm_variants(street)
    if signature == (0, 0) or not storage_states or not street_norms or not postcode:
        return ""
    try:
        con = sqlite3.connect(f"file:{OPENPLZ_DB}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            f"""
            SELECT DISTINCT locality
            FROM streets
            WHERE street_norm IN ({','.join('?' for _ in street_norms)})
              AND state_key IN ({','.join('?' for _ in storage_states)})
              AND postal_code = ?
            LIMIT 8
            """,
            [*street_norms, *storage_states, postcode],
        ).fetchall()
        con.close()
    except sqlite3.Error:
        return ""
    localities: dict[str, str] = {}
    for row in rows:
        locality = str(row["locality"] or "").strip()
        if locality:
            localities.setdefault(normalize_place_search_text(locality), locality)
    return next(iter(localities.values())) if len(localities) == 1 else ""


def _format_unified_address_result(item: dict) -> dict:
    address = item.get("address") if isinstance(item.get("address"), dict) else {}
    state = normalize_state_key(str(item.get("state") or ""))
    street = str(address.get("street") or "").strip()
    house_number = str(address.get("house_number") or "").strip()
    postcode = str(address.get("post_code") or address.get("postal_code") or "").strip()
    city = str(address.get("city") or "").strip()
    if not city and postcode:
        city = _openplz_locality_for_address_cached(state, street, postcode, openplz_signature())
    if not city:
        center = item.get("center") if isinstance(item.get("center"), (list, tuple)) else []
        if len(center) >= 2:
            municipality = municipality_at(state, fast_float(center[0]), fast_float(center[1]))
            city = str((municipality or {}).get("name") or "").strip()
    if city:
        address["city"] = city
    primary = " ".join(part for part in (street, house_number) if part).strip() or "Adresse"
    locality = " ".join(part for part in (postcode, city) if part).strip()
    state_label = state_display_name(state)
    secondary = " · ".join(part for part in (locality, state_label) if part)
    label = f"{primary}, {locality}" if locality else primary
    item["label"] = label
    item["primary_label"] = primary
    item["secondary_label"] = secondary
    item["subtitle"] = secondary or "Adresse"
    item["query"] = label
    item["address"] = address
    feature = item.get("feature") if isinstance(item.get("feature"), dict) else {}
    feature["address"] = label
    feature["addresses"] = [address]
    item["feature"] = feature
    return item


def _unified_address_results(
    parsed: dict,
    allowed_states: set[str],
    limit: int,
    near_lon: float | None,
    near_lat: float | None,
    exact_house_number: bool = False,
) -> list[dict]:
    street = str(parsed.get("street") or "").strip()
    house_number = str(parsed.get("house_number") or "").strip()
    street_norms = normalize_geocoder_text_variants(street)
    house_norm = normalize_geocoder_house(house_number)
    requested_house = normalize_house_number_semantic(house_number)
    postcode = str(parsed.get("postcode") or "").strip()
    if not street_norms or not house_norm or not requested_house:
        return []

    context_states = {
        normalize_state_key(str(context.get("state") or ""))
        for context in parsed.get("place_contexts") or []
        if normalize_state_key(str(context.get("state") or "")) in allowed_states
    }
    search_states = context_states or allowed_states
    results: list[dict] = []
    per_state_limit = max(24, min(int(limit) * 8, 128))
    street_placeholders = ",".join("?" for _ in street_norms)
    for entry in search_db_entries_for_states(tuple(sorted(search_states))):
        clauses = [f"street_norm IN ({street_placeholders})", "feature_kind = 'building'"]
        where_params: list[object] = [*street_norms]
        if exact_house_number:
            clauses.append("house_number_norm = ?")
            where_params.append(house_norm)
        else:
            clauses.extend(("house_number_norm >= ?", "house_number_norm < ?"))
            where_params.extend((house_norm, f"{house_norm}\uffff"))
        if postcode:
            clauses.append("post_code = ?")
            where_params.append(postcode)
        order_sql = "CASE WHEN house_number_norm = ? THEN 0 ELSE 1 END"
        order_params: list[object] = [house_norm]
        if near_lon is not None and near_lat is not None:
            order_sql += ", ((lon - ?) * (lon - ?) + (lat - ?) * (lat - ?))"
            order_params.extend([near_lon, near_lon, near_lat, near_lat])
        try:
            rows = search_db_fetchall(
                entry.path,
                f"""
                SELECT *
                FROM address_lookup
                WHERE {' AND '.join(clauses)}
                ORDER BY {order_sql}, label
                LIMIT ?
                """,
                [*where_params, *order_params, per_state_limit],
            )
        except sqlite3.Error:
            continue
        rows = [
            row
            for row in rows
            if (
                normalize_house_number_semantic(str(row["house_number_label"] or "")) == requested_house
                if exact_house_number
                else normalize_house_number_semantic(str(row["house_number_label"] or "")).startswith(requested_house)
            )
        ]
        place = str(parsed.get("place") or "").strip()
        if place:
            place_bboxes = gn250_place_bboxes_for_state_context(
                place,
                entry.name,
                gn250_places_signature(),
            )
            if place_bboxes:
                rows = filter_address_rows_by_place_context(
                    rows,
                    place,
                    entry.name,
                    place_bboxes,
                    postcode_areas_signature(),
                )
        for row in rows:
            item = search_address_result_from_row(row, entry.name, place)
            results.append(_format_unified_address_result(item))
    return rank_unified_search_results(results, parsed, near_lon, near_lat)[:max(int(limit) * 3, int(limit))]


def _openplz_street_geometry(
    locality: str,
    state: str,
    place_signature: tuple[int, int] | None = None,
) -> tuple[list[float] | None, list[float] | None]:
    state_key = normalize_state_key(state)
    signature = place_signature if place_signature is not None else gn250_places_signature()
    index = exact_place_context_index(signature)
    raw_bbox = None
    for key in exact_place_key_variants(locality):
        raw_bbox = next(
            (
                context.get("bbox")
                for context in index.get(key, tuple())
                if normalize_state_key(str(context.get("state") or "")) == state_key
            ),
            None,
        )
        if raw_bbox:
            break
    bbox = normalized_bbox(raw_bbox)
    if not bbox:
        return None, None
    min_lon, min_lat, max_lon, max_lat = bbox
    return [
        (min_lon + max_lon) / 2.0,
        (min_lat + max_lat) / 2.0,
    ], [min_lon, min_lat, max_lon, max_lat]


def _format_unified_street_result(item: dict, street_label: str, locality: str, postcode: str) -> dict:
    state = normalize_state_key(str(item.get("state") or ""))
    state_label = str(item.get("state_label") or state_display_name(state)).strip()
    locality_label = " ".join(part for part in (postcode, locality) if part).strip()
    full_label = f"{street_label}, {locality_label}" if locality_label else street_label
    secondary = " · ".join(part for part in (locality_label, state_label) if part)
    item["label"] = full_label
    item["value"] = full_label
    item["query"] = full_label
    item["primary_label"] = street_label
    item["secondary_label"] = secondary
    item["subtitle"] = secondary or "Straße"
    item["street"] = street_label
    item["post_code"] = postcode
    item["municipality"] = locality
    item["state"] = state
    item["state_label"] = state_label
    return item


def _openplz_global_street_suggestions(
    parsed: dict,
    allowed_states: set[str],
    limit: int,
) -> list[dict]:
    street = str(parsed.get("street") or "").strip()
    prefixes = openplz_street_norm_variants(street)
    postcode = str(parsed.get("postcode") or "").strip()
    place = str(parsed.get("place") or "").strip()
    openplz_db_signature = openplz_signature()
    if len(street) < 2 or not prefixes or openplz_db_signature == (0, 0):
        return []
    place_signature = gn250_places_signature()
    context_states = {
        normalize_state_key(str(context.get("state") or ""))
        for context in parsed.get("place_contexts") or []
        if normalize_state_key(str(context.get("state") or "")) in allowed_states
    }
    search_states = context_states or allowed_states
    results: list[dict] = []
    seen: set[tuple[str, str, str, str]] = set()
    try:
        con = sqlite3.connect(f"file:{OPENPLZ_DB}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        for state in sorted(search_states):
            storage_states = openplz_storage_state_keys(state, openplz_db_signature)
            if not storage_states:
                continue
            prefix_clause = " OR ".join("(street_norm >= ? AND street_norm < ?)" for _ in prefixes)
            params: list[object] = [bound for prefix in prefixes for bound in (prefix, f"{prefix}\uffff")]
            clauses = [f"({prefix_clause})", f"state_key IN ({','.join('?' for _ in storage_states)})"]
            params.extend(storage_states)
            if postcode:
                clauses.append("postal_code = ?")
                params.append(postcode)
            if place:
                locality_norms = city_norms_for_state_context(place, state)
                if locality_norms:
                    clauses.append(f"locality_norm IN ({','.join('?' for _ in locality_norms)})")
                    params.extend(locality_norms)
            rows = con.execute(
                f"""
                SELECT street, street_norm, postal_code, locality, state_key
                FROM streets
                WHERE {' AND '.join(clauses)}
                ORDER BY street_norm, locality_norm, postal_code
                LIMIT ?
                """,
                [*params, max(48, min(int(limit) * 8, 128))],
            ).fetchall()
            for row in rows:
                street_label = str(row["street"] or "").strip()
                locality = str(row["locality"] or "").strip()
                post_code = str(row["postal_code"] or "").strip()
                state_key = normalize_state_key(str(row["state_key"] or state))
                key = (state_key, normalize_geocoder_text(street_label), post_code, normalize_place_search_text(locality))
                if not street_label or key in seen:
                    continue
                seen.add(key)
                center, bbox = _openplz_street_geometry(locality, state_key, place_signature)
                item = {
                    "kind": "street",
                    "result_type": "street",
                    "state": state_key,
                    "state_label": state_display_name(state_key),
                    "center": center,
                    "bbox": bbox,
                    "zoom": 17.4,
                    "requires_resolution": True,
                    "feature": {
                        "street": street_label,
                        "municipality": locality,
                        "post_code": post_code,
                        "country": "Deutschland",
                    },
                }
                results.append(_format_unified_street_result(item, street_label, locality, post_code))
        con.close()
    except sqlite3.Error:
        return []
    return results


def _state_index_street_suggestions(
    parsed: dict,
    allowed_states: set[str],
    limit: int,
) -> list[dict]:
    """Development fallback used only when the central OpenPLZ DB is absent."""
    prefixes = normalize_geocoder_text_variants(str(parsed.get("street") or ""))
    if not prefixes:
        return []
    results: list[dict] = []
    seen: set[tuple[str, str, str, str]] = set()
    prefix_clause = " OR ".join("(street_norm >= ? AND street_norm < ?)" for _ in prefixes)
    prefix_params = [bound for prefix in prefixes for bound in (prefix, f"{prefix}\uffff")]
    for entry in search_db_entries_for_states(tuple(sorted(allowed_states))):
        try:
            rows = search_db_fetchall(
                entry.path,
                f"""
                SELECT *
                FROM street_lookup
                WHERE {prefix_clause}
                ORDER BY address_count DESC, label
                LIMIT ?
                """,
                [*prefix_params, max(12, int(limit) * 2)],
            )
        except sqlite3.Error:
            continue
        for row in rows:
            street_label = str(row["street_label"] or "").strip()
            postcode = str(row["post_code"] or "").strip()
            locality = search_result_city_label(row["city_label"], postcode, entry.name)
            key = (entry.name, normalize_geocoder_text(street_label), postcode, normalize_place_search_text(locality))
            if not street_label or key in seen:
                continue
            seen.add(key)
            item = search_street_result_from_row(row, entry.name, street_label, locality)
            results.append(_format_unified_street_result(item, street_label, locality, postcode))
    return results


def _format_unified_place_result(item: dict) -> dict:
    state_label = str(item.get("state_label") or state_display_name(str(item.get("state") or ""))).strip()
    subtitle = str(item.get("subtitle") or "").strip()
    if state_label and state_label.casefold() not in subtitle.casefold():
        subtitle = " · ".join(part for part in (subtitle, state_label) if part)
    item["primary_label"] = str(item.get("label") or item.get("value") or "Ort")
    item["secondary_label"] = subtitle
    item["subtitle"] = subtitle
    item["query"] = str(item.get("value") or item.get("label") or "")
    return item


@lru_cache(maxsize=2048)
def _openplz_postcode_places_cached(
    postcode: str,
    states_key: tuple[str, ...],
    limit: int,
    openplz_db_signature: tuple[int, int],
    place_signature: tuple[int, int],
) -> tuple[dict, ...]:
    """Resolve a complete postcode to one or more official place results."""
    del place_signature
    post_code = str(postcode or "").strip()
    allowed_states = set(states_key)
    if (
        not re.fullmatch(r"\d{5}", post_code)
        or not allowed_states
        or openplz_db_signature == (0, 0)
    ):
        return tuple()
    try:
        con = sqlite3.connect(f"file:{OPENPLZ_DB}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            # OpenPLZ is authoritative for postcode/locality membership.  The
            # bounded DISTINCT result also preserves shared postcodes instead
            # of silently selecting the first locality.
            rows = con.execute(
                """
                SELECT DISTINCT locality, state_key
                FROM streets
                WHERE postal_code = ?
                ORDER BY locality, state_key
                LIMIT ?
                """,
                (post_code, max(32, min(int(limit) * 8, 256))),
            ).fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return tuple()

    results: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        state = normalize_state_key(str(row["state_key"] or ""))
        if state not in allowed_states:
            continue
        source_locality = str(row["locality"] or "").strip()
        locality = city_display_name_for_state(source_locality, state)
        if not locality:
            continue
        key = (state, normalize_place_search_text(locality))
        if key in seen:
            continue
        seen.add(key)
        center, bbox = _openplz_street_geometry(locality, state)
        state_label = state_display_name(state)
        secondary = " · ".join(part for part in (post_code, state_label) if part)
        results.append({
            "kind": "place",
            "result_type": "place",
            "label": locality,
            "value": locality,
            "query": locality,
            "primary_label": locality,
            "secondary_label": secondary,
            "subtitle": secondary,
            "municipality": locality,
            "post_code": post_code,
            "state": state,
            "state_label": state_label,
            "center": center,
            "bbox": bbox,
            "zoom": 12.0,
        })
        if len(results) >= int(limit):
            break
    return tuple(results)


def openplz_postcode_place_suggestions(
    postcode: str,
    allowed_states: set[str],
    limit: int,
) -> list[dict]:
    return [
        dict(item)
        for item in _openplz_postcode_places_cached(
            str(postcode or "").strip(),
            tuple(sorted(state for state in allowed_states if state)),
            int(limit),
            openplz_signature(),
            gn250_places_signature(),
        )
    ]


def search_unified_address_suggestions_for_dataset(
    dataset: str,
    q: str,
    limit: int,
    state: str = "",
    near_lon: float | None = None,
    near_lat: float | None = None,
    exact_house_number: bool = False,
    include_parse_metadata: bool = False,
) -> dict:
    query = re.sub(r"\s+", " ", str(q or "")).strip()
    if len(query) < 2:
        return {"query": query, "count": 0, "results": []}
    if not is_virtual_germany_dataset(dataset):
        get_dataset(dataset)
    allowed_states = search_suggestion_states_for_dataset(dataset, state)
    if re.fullmatch(r"\d{5}", query):
        postcode_results = openplz_postcode_place_suggestions(
            query,
            allowed_states,
            int(limit),
        )
        return {
            "query": query,
            "count": len(postcode_results),
            "results": postcode_results,
        }
    parsed = parse_unified_address_query(query, allowed_states)
    results: list[dict] = []

    if parsed.get("has_house_number") and parsed.get("street"):
        results.extend(
            _unified_address_results(
                parsed,
                allowed_states,
                int(limit),
                near_lon,
                near_lat,
                exact_house_number=exact_house_number,
            )
        )

    has_address_results = any(item.get("result_type") == "address" for item in results)
    if not has_address_results and parsed.get("street") and len(str(parsed.get("street") or "")) >= 2:
        place = str(parsed.get("place") or "").strip()
        postcode = str(parsed.get("postcode") or "").strip()
        street_results: list[dict] = []
        if place:
            scoped = search_street_suggestions_for_dataset(
                dataset,
                place,
                str(parsed.get("street") or ""),
                max(int(limit) * 2, 12),
                state=state,
            ).get("results") or []
            for source in scoped:
                item = dict(source)
                street_label = str(item.get("value") or item.get("label") or "").strip()
                item_state = normalize_state_key(str(item.get("state") or state or ""))
                locality_source = str(
                    item.get("municipality")
                    or item.get("subtitle")
                    or (parsed.get("place_context") or {}).get("name")
                    or place
                ).strip()
                locality = city_display_name_for_state(locality_source, item_state)
                postcode_value = str(item.get("post_code") or "").strip()
                street_results.append(_format_unified_street_result(item, street_label, locality, postcode_value))
        if not street_results:
            street_results.extend(_openplz_global_street_suggestions(parsed, allowed_states, max(int(limit) * 3, 18)))
        if not street_results and openplz_signature() == (0, 0):
            street_results.extend(_state_index_street_suggestions(parsed, allowed_states, max(int(limit) * 2, 12)))
        results.extend(street_results)

    place_query = str(parsed.get("place") or "").strip()
    if not parsed.get("street"):
        place_query = place_query or re.sub(r"(?<!\d)\d{5}(?!\d)", " ", query).strip()
    if not has_address_results and place_query and len(place_query) >= 2:
        place_results = search_place_suggestions_for_dataset(dataset, place_query, max(int(limit), 8), state=state).get("results") or []
        results.extend(_format_unified_place_result(dict(item)) for item in place_results)

    ranked = rank_unified_search_results(results, parsed, near_lon, near_lat)[:int(limit)]
    payload = {"query": query, "count": len(ranked), "results": ranked}
    if include_parse_metadata:
        payload["_parsed_address"] = parsed
    return payload


_FREE_TEXT_PARCEL_NUMBER_RE = re.compile(
    r"(?<![\w/])(\d{1,9}(?:\s*/\s*\d{1,9})?)(?![\w/])"
)
_FREE_TEXT_PARCEL_MARKER_RE = re.compile(
    r"\b(?:flur(?:stueck|stück)|flst\.?)(?:\s*nr\.?)?\s*"
    r"(\d{1,9}(?:\s*/\s*\d{1,9})?)\b",
    re.IGNORECASE,
)
_FREE_TEXT_FLUR_MARKER_RE = re.compile(
    r"\bflur\b(?!\s*(?:stueck|stück))(?:\s*nr\.?)?\s*(\d{1,9})\b",
    re.IGNORECASE,
)


def _free_text_parcel_number(value: str) -> str:
    return re.sub(r"\s*/\s*", "/", str(value or "").strip())


def _free_text_parcel_clean_words(value: str) -> str:
    cleaned = re.sub(
        r"\b(?:gemarkung|flur(?:stueck|stück)|flst\.?)\b",
        " ",
        str(value or ""),
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\b(?:in|der)\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"[,;:]", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def _overlaps_span(start: int, end: int, spans: list[tuple[int, int]]) -> bool:
    return any(start < span_end and end > span_start for span_start, span_end in spans)


def _exact_gemarkung_identities(
    phrase: str,
    allowed_states: set[str],
    limit: int = 120,
) -> list[dict]:
    """Resolve a complete Gemarkung name/code through parcel_lookup only."""
    candidate = re.sub(r"\s+", " ", str(phrase or "")).strip(" ,;:")
    if len(candidate) < 2 or not any(character.isalpha() for character in candidate):
        return []
    code_match = re.search(
        r"\(\s*((?=[0-9A-Za-z]*[0-9])[0-9A-Za-z]+)\s*\)\s*$",
        candidate,
    )
    requested_code = code_match.group(1).strip() if code_match else ""
    candidate_name = candidate[:code_match.start()].strip() if code_match else candidate
    candidate_norms = set(normalize_geocoder_text_variants(candidate_name))
    if not candidate_norms:
        return []
    states_key = tuple(sorted(state for state in allowed_states if state))
    suggestions = search_gemarkung_suggestions_cached(
        candidate,
        max(50, int(limit)),
        states_key,
        search_db_signature_for_states(allowed_states),
    )
    exact: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for source in suggestions:
        item = dict(source)
        label = str(item.get("gemarkung") or item.get("label") or "").strip()
        code = str(item.get("gemarkungsnummer") or "").strip()
        label_name = label
        if code:
            label_name = re.sub(
                rf"\s*\(\s*{re.escape(code)}\s*\)\s*$",
                "",
                label_name,
            ).strip()
        label_norms = {
            normalized
            for alias in gn250_place_name_aliases(label_name, str(item.get("state") or ""))
            for normalized in normalize_geocoder_text_variants(alias)
            if normalized
        }
        if not candidate_norms.intersection(label_norms):
            continue
        if requested_code and code.casefold() != requested_code.casefold():
            continue
        state = normalize_state_key(str(item.get("state") or ""))
        if state not in allowed_states:
            continue
        key = (state, code, normalize_geocoder_text(label_name))
        if key in seen:
            continue
        seen.add(key)
        item["state"] = state
        item["gemarkung"] = label
        exact.append(item)

    # Do not let a state with many homonyms consume the entire identity budget.
    by_state: dict[str, list[dict]] = {}
    for item in exact:
        by_state.setdefault(str(item["state"]), []).append(item)
    fair: list[dict] = []
    while by_state and len(fair) < int(limit):
        for state in sorted(tuple(by_state)):
            bucket = by_state[state]
            fair.append(bucket.pop(0))
            if not bucket:
                by_state.pop(state, None)
            if len(fair) >= int(limit):
                break
    return fair


def _free_text_parcel_number_options(query: str) -> tuple[list[dict], list[tuple[int, int]], bool]:
    """Return bounded candidate interpretations without consulting feature data."""
    raw = str(query or "")
    parcel_matches = list(_FREE_TEXT_PARCEL_MARKER_RE.finditer(raw))
    flur_matches = list(_FREE_TEXT_FLUR_MARKER_RE.finditer(raw))
    if len(parcel_matches) > 1 or len(flur_matches) > 1:
        return [], [], False
    explicit_parcel = parcel_matches[0] if parcel_matches else None
    explicit_flur = flur_matches[0] if flur_matches else None
    protected_spans = [match.span() for match in parcel_matches + flur_matches]
    code_spans = [
        match.span()
        for match in re.finditer(
            r"\(\s*(?=[0-9A-Za-z]*[0-9])[0-9A-Za-z]+\s*\)",
            raw,
        )
    ]
    numeric_matches = [
        match
        for match in _FREE_TEXT_PARCEL_NUMBER_RE.finditer(raw)
        if not _overlaps_span(*match.span(), protected_spans + code_spans)
    ]
    parcel_value = _free_text_parcel_number(explicit_parcel.group(1)) if explicit_parcel else ""
    flur_value = str(explicit_flur.group(1)).strip() if explicit_flur else ""
    explicit_signal = bool(
        explicit_parcel
        or explicit_flur
        or re.search(r"\bgemarkung\b", raw, re.IGNORECASE)
        or code_spans
    )
    options: list[dict] = []

    def add(
        flur: str,
        flurstueck: str,
        matches: list[re.Match],
        signal_rank: int,
        gemarkung_code: str = "",
    ) -> None:
        parcel = _free_text_parcel_number(flurstueck)
        if not parcel or not re.fullmatch(r"\d{1,9}(?:/\d{1,9})?", parcel):
            return
        option = {
            "flur": str(flur or "").strip(),
            "flurstueck": parcel,
            "signal_rank": int(signal_rank),
            "gemarkung_code": str(gemarkung_code or "").strip(),
            "number_spans": [match.span() for match in matches],
        }
        key = (option["flur"], option["flurstueck"], option["gemarkung_code"])
        if key not in {
            (item["flur"], item["flurstueck"], item["gemarkung_code"])
            for item in options
        }:
            options.append(option)

    if explicit_parcel:
        if explicit_flur:
            if not numeric_matches:
                add(flur_value, parcel_value, [], 0)
        elif not numeric_matches:
            add("", parcel_value, [], 0)
        elif len(numeric_matches) == 1 and "/" not in numeric_matches[0].group(1):
            add(numeric_matches[0].group(1), parcel_value, numeric_matches, 0)
            if len(numeric_matches[0].group(1)) == 4:
                # Parentheses are commonly omitted when a known four-digit
                # Gemarkungsnummer is pasted after the name.  Keep the Flur
                # interpretation too and let exact parcel_lookup rows decide.
                add("", parcel_value, numeric_matches, 0, numeric_matches[0].group(1))
    elif explicit_flur:
        if len(numeric_matches) == 1:
            add(flur_value, numeric_matches[0].group(1), numeric_matches, 0)
    else:
        slash_matches = [match for match in numeric_matches if "/" in match.group(1)]
        plain_matches = [match for match in numeric_matches if "/" not in match.group(1)]
        if len(slash_matches) == 1 and len(plain_matches) <= 1:
            add(
                plain_matches[0].group(1) if plain_matches else "",
                slash_matches[0].group(1),
                numeric_matches,
                1,
            )
            explicit_signal = True
        elif not slash_matches and len(plain_matches) == 1:
            implicit_value = plain_matches[0].group(1)
            # A bare five-digit token is much more likely to be a postcode.
            if explicit_signal or len(implicit_value) <= 4:
                add("", implicit_value, plain_matches, 2 if not explicit_signal else 0)
        elif not slash_matches and len(plain_matches) == 2 and all(len(match.group(1)) <= 4 for match in plain_matches):
            # Both common compact orders are exact-validated below.  If both
            # exist, returning both is safer than silently picking one.
            add(plain_matches[0].group(1), plain_matches[1].group(1), plain_matches, 2)
            add(plain_matches[1].group(1), plain_matches[0].group(1), plain_matches, 2)
    removal_spans = protected_spans + [span for option in options for span in option["number_spans"]]
    return options, removal_spans, explicit_signal


def parse_free_text_parcel_query(
    query: str,
    allowed_states: set[str],
) -> dict:
    """Conservatively parse parcel text and exact-validate Gemarkung identities."""
    raw_query = re.sub(r"\s+", " ", str(query or "")).strip()
    strong_intent = bool(
        _FREE_TEXT_PARCEL_MARKER_RE.search(raw_query)
        or _FREE_TEXT_FLUR_MARKER_RE.search(raw_query)
        or re.search(r"\bgemarkung\b", raw_query, re.IGNORECASE)
    )
    if len(raw_query) < 2 or not any(character.isalpha() for character in raw_query):
        return {
            "query": raw_query,
            "explicit_signal": False,
            "strong_intent": strong_intent,
            "candidates": [],
        }
    options, removal_spans, explicit_signal = _free_text_parcel_number_options(raw_query)
    if not options:
        return {
            "query": raw_query,
            "explicit_signal": explicit_signal,
            "strong_intent": strong_intent,
            "candidates": [],
        }
    strong_explicit_signal = bool(
        _FREE_TEXT_PARCEL_MARKER_RE.search(raw_query)
        or _FREE_TEXT_FLUR_MARKER_RE.search(raw_query)
        or re.search(r"\bgemarkung\b", raw_query, re.IGNORECASE)
        or re.search(
            r"\(\s*(?=[0-9A-Za-z]*[0-9])[0-9A-Za-z]+\s*\)",
            raw_query,
        )
    )
    if not strong_explicit_signal and is_likely_street_name_query(raw_query):
        return {
            "query": raw_query,
            "explicit_signal": False,
            "strong_intent": strong_intent,
            "candidates": [],
        }

    characters = list(raw_query)
    for start, end in removal_spans:
        characters[start:end] = " " * (end - start)
    words_text = _free_text_parcel_clean_words("".join(characters))
    tokens = [token for token in words_text.split() if token]
    if not tokens or len(tokens) > 10:
        return {
            "query": raw_query,
            "explicit_signal": explicit_signal,
            "strong_intent": strong_intent,
            "candidates": [],
        }
    has_parenthesized_code = bool(re.search(
        r"\(\s*(?=[0-9A-Za-z]*[0-9])[0-9A-Za-z]+\s*\)",
        words_text,
    ))

    phrase_candidates: list[tuple[tuple[int, int, int], str, str]] = []
    seen_phrases: set[tuple[str, str]] = set()

    def add_phrase(start: int, end: int, source_rank: int) -> None:
        phrase = " ".join(tokens[start:end]).strip()
        context = " ".join(tokens[:start] + tokens[end:]).strip()
        key = (normalize_geocoder_text(phrase), re.sub(r"\D", "", phrase))
        if (
            len(phrase) < 2
            or not any(character.isalpha() for character in phrase)
            or (has_parenthesized_code and "(" not in phrase)
            or key in seen_phrases
        ):
            return
        seen_phrases.add(key)
        phrase_candidates.append(((source_rank, start, -(end - start)), phrase, context))

    # Natural language names the Gemarkung first after "in" and commonly
    # follows it with a parent municipality: "... in Bemerode Hannover".
    in_match = re.search(r"\bin\b", raw_query, re.IGNORECASE)
    if in_match:
        for length in range(min(6, len(tokens)), 0, -1):
            add_phrase(0, length, 0)
    if re.search(r"\bgemarkung\b", raw_query, re.IGNORECASE):
        for length in range(min(6, len(tokens)), 0, -1):
            add_phrase(0, length, 0)
    if has_parenthesized_code:
        for start in range(len(tokens)):
            for end in range(min(len(tokens), start + 6), start, -1):
                if "(" in " ".join(tokens[start:end]):
                    add_phrase(start, end, 0)
    compact_slash_tail = bool(
        not strong_explicit_signal
        and len(tokens) >= 2
        and any("/" in str(option.get("flurstueck") or "") for option in options)
        and re.search(r"\d{1,9}\s*/\s*\d{1,9}\s*[.,;:]?\s*$", raw_query)
    )
    if compact_slash_tail:
        # Compact one-box searches often use ``Gemarkung Gemeinde 100/1``
        # without cadastral marker words.  Try bounded contiguous Gemarkung
        # phrases, but only when a separate municipality tail/head remains.
        # Candidates from this branch are accepted below only if that context
        # is an actual municipality and the resolved parcel lies inside it.
        for length in range(min(6, len(tokens) - 1), 0, -1):
            for start in range(0, len(tokens) - length + 1):
                if start == 0 and length == len(tokens):
                    continue
                add_phrase(start, start + length, 1)
                if len(phrase_candidates) >= 32:
                    break
            if len(phrase_candidates) >= 32:
                break
    if not has_parenthesized_code and not strong_explicit_signal:
        add_phrase(0, len(tokens), 2)
    elif not has_parenthesized_code:
        for length in range(min(6, len(tokens)), 0, -1):
            for start in range(0, len(tokens) - length + 1):
                add_phrase(start, start + length, 2)
                if len(phrase_candidates) >= 32:
                    break
            if len(phrase_candidates) >= 32:
                break
    phrase_candidates.sort(key=lambda item: item[0])

    candidates: list[dict] = []
    seen_candidates: set[tuple[str, str, str, str, str]] = set()
    identity_cache: dict[str, list[dict]] = {}
    resolved_phrases: list[tuple[tuple[int, int, int], str, list[dict]]] = []
    for phrase_rank, phrase, context_text in phrase_candidates:
        if phrase not in identity_cache:
            identity_cache[phrase] = _exact_gemarkung_identities(phrase, allowed_states)
        if identity_cache[phrase]:
            resolved_phrases.append((phrase_rank, context_text, identity_cache[phrase]))
    best_source_rank = min((rank[0] for rank, _context, _identities in resolved_phrases), default=None)
    resolved_identity_codes = {
        str(identity.get("gemarkungsnummer") or "").strip().casefold()
        for _rank, _context, identities in resolved_phrases
        for identity in identities
        if str(identity.get("gemarkungsnummer") or "").strip()
    }
    validated_bare_codes = {
        str(option.get("gemarkung_code") or "").strip().casefold()
        for option in options
        if str(option.get("gemarkung_code") or "").strip().casefold() in resolved_identity_codes
    }
    for phrase_rank, context_text, identities in resolved_phrases:
        # An explicit/natural Gemarkung parse is authoritative; do not also
        # reinterpret its municipality tail as another Gemarkung.
        if best_source_rank is not None and phrase_rank[0] != best_source_rank:
            continue
        if phrase_rank[0] >= 2 and context_text and not strong_explicit_signal:
            # A compact parcel query must consist of an exact Gemarkung plus
            # its number.  Otherwise a normal address with a slash-house-number
            # and a trailing city could be misread as a parcel.
            continue
        for identity in identities:
            state = str(identity["state"])
            municipality = requested_municipality(context_text, {state}) if context_text else None
            requires_municipality_match = bool(
                phrase_rank[0] == 1 and compact_slash_tail and not strong_explicit_signal
            )
            if requires_municipality_match:
                if not municipality:
                    continue
                # A district/locality may resolve to its parent municipality.
                # For the compact syntax the remaining words must name the
                # municipality itself, otherwise ``Street 1/2 District`` can
                # be reinterpreted as a parcel in a homonymous Gemarkung.  Do
                # not use ``source_name`` here: GN250 may choose an arbitrary
                # child district even when the input exactly says Hannover.
                municipality_name = str(municipality.get("name") or "").strip()
                if (
                    not municipality_name
                    or normalize_place_search_text(context_text)
                    != normalize_place_search_text(municipality_name)
                ):
                    continue
            label = str(identity.get("gemarkung") or identity.get("label") or "").strip()
            code = str(identity.get("gemarkungsnummer") or "").strip()
            gemarkung_query = label
            if code and not re.search(rf"\(\s*{re.escape(code)}\s*\)\s*$", gemarkung_query):
                # Parentheticals may be part of the official name (for
                # example ``Freden (Leine)``).  Append only the cadastral code
                # and let the exact lookup strip that final code again.
                gemarkung_query = f"{label} ({code})"
            for option in options:
                requested_option_code = str(option.get("gemarkung_code") or "").strip()
                if (
                    validated_bare_codes
                    and not requested_option_code
                    and str(option.get("flur") or "").strip().casefold() in validated_bare_codes
                ):
                    continue
                if requested_option_code and requested_option_code.casefold() != code.casefold():
                    continue
                key = (
                    state,
                    code or normalize_geocoder_text(label),
                    option["flur"],
                    option["flurstueck"],
                    normalize_place_search_text(str((municipality or {}).get("name") or "")),
                )
                if key in seen_candidates:
                    continue
                seen_candidates.add(key)
                candidates.append({
                    "gemarkung": gemarkung_query,
                    "gemarkungsnummer": code,
                    "flur": option["flur"],
                    "flurstueck": option["flurstueck"],
                    "state": state,
                    "state_label": str(identity.get("state_label") or state_display_name(state)),
                    "municipality_context": dict(municipality) if municipality else None,
                    "requires_municipality_match": requires_municipality_match,
                    "signal_rank": option["signal_rank"],
                    "phrase_rank": phrase_rank,
                })
    candidates.sort(key=lambda item: (
        int(item["signal_rank"]),
        item["phrase_rank"],
    ))
    return {
        "query": raw_query,
        "explicit_signal": explicit_signal or any(int(item["signal_rank"]) <= 1 for item in candidates),
        "strong_intent": strong_intent,
        "candidates": candidates[:120],
    }


def _contextual_parcel_query_parts(query: str) -> dict | None:
    """Extract one exact parcel number plus bounded address context."""
    raw_query = re.sub(r"\s+", " ", str(query or "")).strip()
    slash_matches = list(re.finditer(
        r"(?<![\w/])(\d{1,9}\s*/\s*\d{1,9})(?![\w/])",
        raw_query,
    ))
    if len(slash_matches) > 1:
        return None
    flur_match = _FREE_TEXT_FLUR_MARKER_RE.search(raw_query)
    parcel_marker = _FREE_TEXT_PARCEL_MARKER_RE.search(raw_query)
    explicit_parcel = bool(parcel_marker)
    parcel_value_spans = [
        match.span()
        for match in slash_matches
    ]
    if parcel_marker:
        parcel_value_spans.append(parcel_marker.span(1))
    if flur_match:
        parcel_value_spans.append(flur_match.span(1))
    postcode_match = next(
        (
            match
            for match in re.finditer(r"(?<!\d)(\d{5})(?!\d)", raw_query)
            if not _overlaps_span(*match.span(), parcel_value_spans)
        ),
        None,
    )
    postcode = postcode_match.group(1) if postcode_match else ""

    parcel_number_span: tuple[int, int]
    if slash_matches:
        parcel_number = _free_text_parcel_number(slash_matches[0].group(1))
        parcel_number_span = slash_matches[0].span()
    else:
        marker_value = (
            _free_text_parcel_number(parcel_marker.group(1))
            if parcel_marker
            else ""
        )
        if marker_value and re.fullmatch(r"\d{1,9}", marker_value):
            parcel_number = marker_value
            parcel_number_span = parcel_marker.span(1)
        else:
            protected_spans = [
                match.span()
                for match in (flur_match, postcode_match)
                if match is not None
            ]
            plain_matches = [
                match
                for match in re.finditer(
                    r"(?<![\w/])(\d{1,4})(?![\w/])",
                    raw_query,
                )
                if not _overlaps_span(*match.span(), protected_spans)
            ]
            if len(plain_matches) != 1:
                return None
            parcel_number = plain_matches[0].group(1)
            parcel_number_span = plain_matches[0].span()

    numerator, separator, denominator = parcel_number.partition("/")
    if not numerator or (separator and not denominator):
        return None
    flur = str(flur_match.group(1) or "").strip() if flur_match else ""

    characters = list(raw_query)
    removal_spans = [parcel_number_span]
    if flur_match:
        removal_spans.append(flur_match.span())
    if parcel_marker:
        removal_spans.append(parcel_marker.span())
    if postcode_match:
        removal_spans.append(postcode_match.span())
    for start, end in removal_spans:
        characters[start:end] = " " * (end - start)
    context = "".join(characters)
    context = re.sub(
        r"\b(?:gemarkung|flur(?:stueck|stück)|flst\.?)\b",
        " ",
        context,
        flags=re.IGNORECASE,
    )
    context = re.sub(r"[,;:]", " ", context)
    context = re.sub(r"\s+", " ", context).strip()
    if not context and not postcode:
        return None
    address_candidates = [
        candidate
        for candidate in geocoder_direct_candidates(context)
        if candidate[0] == "address" and not candidate[3].strip()
    ]
    chosen_address = max(
        address_candidates,
        key=lambda candidate: (
            len(search_tokens(candidate[1])),
            len(candidate[1]),
            len(candidate[2]),
        ),
        default=None,
    )
    house_number = str(chosen_address[2] if chosen_address else "").strip(" ,;")
    context_tokens = [token for token in context.split() if token]
    if len(context_tokens) > 10:
        return None
    phrases: list[str] = []
    for length in range(min(6, len(context_tokens)), 0, -1):
        for start in range(0, len(context_tokens) - length + 1):
            phrase = " ".join(context_tokens[start:start + length]).strip(" ,;:")
            if len(phrase) >= 2 and any(character.isalpha() for character in phrase):
                if phrase not in phrases:
                    phrases.append(phrase)
            if len(phrases) >= 48:
                break
        if len(phrases) >= 48:
            break
    return {
        "query": raw_query,
        "flurstueck": parcel_number,
        "zaehler": numerator,
        "nenner": denominator,
        "flur": flur,
        "postcode": postcode,
        "house_number": house_number,
        "context": context,
        "phrases": tuple(phrases),
        "explicit_parcel": explicit_parcel,
        "plain_number": not bool(separator),
    }


def _contextual_parcel_words(value: str) -> set[str]:
    words: set[str] = set()
    for variant in normalize_geocoder_text_variants(value):
        words.update(re.findall(r"[a-z0-9]+", variant))
    return words


def _contextual_parcel_display_city(row: sqlite3.Row, state: str) -> str:
    postcode = str(row["linked_post_code"] or "").strip()
    source_city = str(row["linked_city_label"] or "").strip()
    city = search_result_city_label(source_city, postcode, state)
    if city and not re.fullmatch(r"\d+", city):
        return city

    street = str(row["linked_street_label"] or "").strip()
    if street and postcode:
        openplz_city = _openplz_locality_for_address_cached(
            state,
            street,
            postcode,
            openplz_signature(),
        )
        city = search_result_city_label(openplz_city, postcode, state)
        if city and not re.fullmatch(r"\d+", city):
            return city

    if row["lon"] is not None and row["lat"] is not None:
        place = municipality_at(
            state,
            fast_float(row["lon"]),
            fast_float(row["lat"]),
        )
        municipality = city_display_name_for_state(
            str((place or {}).get("name") or ""),
            state,
        )
        if municipality and not re.fullmatch(r"\d+", municipality):
            return municipality

    gemarkung = str(row["gemarkung_label"] or "").strip()
    code = str(row["gemarkungsnummer"] or "").strip()
    if code:
        gemarkung = re.sub(
            rf"\s*\(\s*{re.escape(code)}\s*\)\s*$",
            "",
            gemarkung,
        ).strip()
    fallback = city_display_name_for_state(gemarkung, state)
    return fallback if fallback and not re.fullmatch(r"\d+", fallback) else ""


def _contextual_parcel_row_matches(row: sqlite3.Row, parsed: dict, state: str) -> bool:
    requested_house = normalize_house_number_semantic(
        str(parsed.get("house_number") or "")
    )
    if (
        requested_house
        and normalize_house_number_semantic(
            str(row["linked_house_number_label"] or "")
        ) != requested_house
    ):
        return False
    requested_variants = []
    for variant in normalize_geocoder_text_variants(str(parsed.get("context") or "")):
        words = set(re.findall(r"[a-z0-9]+", variant))
        if parsed.get("explicit_parcel"):
            words.difference_update({"in", "der", "die", "das", "von"})
        requested_variants.append(words)
    source_city = str(row["linked_city_label"] or "").strip()
    canonical_city = city_display_name_for_state(source_city, state)
    available_words: set[str] = set()
    for value in (
        str(row["linked_street_label"] or ""),
        str(row["linked_house_number_label"] or ""),
        source_city,
        canonical_city,
        str(row["gemarkung_label"] or ""),
        str(row["linked_post_code"] or ""),
    ):
        available_words.update(_contextual_parcel_words(value))
    return not requested_variants or any(
        not requested_words or requested_words.issubset(available_words)
        for requested_words in requested_variants
    )


_ADDRESSLESS_PARCEL_MAX_OPENPLZ_CONTEXTS = 6000
_ADDRESSLESS_PARCEL_MAX_CANDIDATES = 128
_ADDRESSLESS_PARCEL_MAX_VALIDATION_ROWS = 8192
_ADDRESSLESS_PARCEL_MAX_BUILDING_DISTANCE_M = 10.0
_CONTEXTUAL_PARCEL_STATE_SCAN_LIMIT = 256
_CONTEXTUAL_PARCEL_STATE_RESULT_LIMIT = 32
_CONTEXTUAL_PARCEL_DIRECT_POOL_LIMIT = 256
_CONTEXTUAL_PARCEL_TOTAL_POOL_LIMIT = 384


def _is_contextual_parcel_street_phrase(value: str) -> bool:
    if is_likely_street_name_query(value):
        return True
    # ``search_tokens`` removes punctuation, so the common ``Bergstr.`` form
    # becomes ``bergstr`` and is not covered by the long ``straße`` suffix.
    # Require a real name prefix to avoid treating a bare ``Str.`` token as a
    # street constraint.
    return any(
        len(token) >= 5 and token.casefold().endswith("str")
        for token in search_tokens(value)
    )


def _addressless_parcel_street_phrases(context: str) -> list[dict]:
    tokens = [token for token in str(context or "").split() if token]
    candidates: list[dict] = []
    seen: set[tuple[str, ...]] = set()
    for length in range(min(6, len(tokens)), 0, -1):
        for start in range(0, len(tokens) - length + 1):
            phrase = " ".join(tokens[start:start + length]).strip()
            if not _is_contextual_parcel_street_phrase(phrase):
                continue
            street_norms = openplz_street_norm_variants(phrase)
            if not street_norms or street_norms in seen:
                continue
            seen.add(street_norms)
            candidates.append({
                "phrase": phrase,
                "street_norms": street_norms,
                "remaining_context": " ".join(
                    tokens[:start] + tokens[start + length:]
                ).strip(),
                "token_count": length,
            })
    return candidates[:48]


def _addressless_parcel_openplz_contexts(
    parsed: dict,
    allowed_states: set[str],
    openplz_db_signature: tuple[int, int],
) -> list[dict] | None:
    if openplz_db_signature == (0, 0):
        return []
    phrase_candidates = _addressless_parcel_street_phrases(
        str(parsed.get("context") or "")
    )
    if not phrase_candidates:
        return []
    by_street_norm: dict[str, list[dict]] = {}
    for candidate in phrase_candidates:
        for street_norm in candidate["street_norms"]:
            by_street_norm.setdefault(street_norm, []).append(candidate)
    street_norms = tuple(sorted(by_street_norm))
    try:
        con = sqlite3.connect(f"file:{OPENPLZ_DB}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            con.execute("PRAGMA query_only = ON")
            # Resolve only state keys attached to this exact street.  Calling
            # the generic resolver once per state would repeatedly scan the
            # complete OpenPLZ table on a cold worker.
            state_rows = con.execute(
                f"""
                SELECT DISTINCT state_key
                FROM streets INDEXED BY idx_streets_norm_state
                WHERE street_norm IN ({','.join('?' for _ in street_norms)})
                """,
                street_norms,
            ).fetchall()
            storage_to_state = {
                str(row["state_key"] or "").strip(): canonical_state
                for row in state_rows
                if (
                    canonical_state := normalize_state_key(
                        str(row["state_key"] or "")
                    )
                ) in allowed_states
            }
            if not storage_to_state:
                return []
            storage_states = tuple(sorted(storage_to_state))
            rows = con.execute(
                f"""
                SELECT
                  street, street_norm, postal_code,
                  locality, locality_norm,
                  borough, borough_norm,
                  suburb, suburb_norm,
                  state_key
                FROM streets INDEXED BY idx_streets_norm_state
                WHERE street_norm IN ({','.join('?' for _ in street_norms)})
                  AND state_key IN ({','.join('?' for _ in storage_states)})
                ORDER BY street_norm, state_key, postal_code, locality_norm
                LIMIT ?
                """,
                [
                    *street_norms,
                    *storage_states,
                    _ADDRESSLESS_PARCEL_MAX_OPENPLZ_CONTEXTS + 1,
                ],
            ).fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return []
    if len(rows) > _ADDRESSLESS_PARCEL_MAX_OPENPLZ_CONTEXTS:
        return None

    matched_street_norms = {
        str(row["street_norm"] or "")
        for row in rows
    }
    matched_phrase_length = max((
        int(candidate["token_count"])
        for street_norm in matched_street_norms
        for candidate in by_street_norm.get(street_norm, ())
    ), default=0)
    contexts: list[dict] = []
    seen: set[tuple] = set()
    for row in rows:
        storage_state = str(row["state_key"] or "").strip()
        state = storage_to_state.get(storage_state, "")
        if not state:
            continue
        for phrase in by_street_norm.get(str(row["street_norm"] or ""), []):
            if int(phrase["token_count"]) != matched_phrase_length:
                continue
            gemarkung_norms: list[str] = []
            city_norms: list[str] = []
            place_labels: list[str] = []
            for label_key, norm_key in (
                ("locality", "locality_norm"),
                ("borough", "borough_norm"),
                ("suburb", "suburb_norm"),
            ):
                label = str(row[label_key] or "").strip()
                stored_norm = str(row[norm_key] or "").strip()
                if label:
                    place_labels.append(label)
                # OpenPLZ already stores the normalized forms required by the
                # exact parcel index.  Re-normalizing every label made common
                # streets such as Bergstraße take seconds across ~4k rows.
                normalized_values = (
                    (stored_norm,)
                    if stored_norm
                    else (
                        normalize_geocoder_text_variants(label)
                        if label
                        else tuple()
                    )
                )
                for normalized in normalized_values:
                    if normalized and normalized not in gemarkung_norms:
                        gemarkung_norms.append(normalized)
                    if normalized and normalized not in city_norms:
                        city_norms.append(normalized)
            postcode = str(row["postal_code"] or "").strip()
            if postcode and postcode not in city_norms:
                city_norms.append(postcode)
            if not gemarkung_norms or not postcode:
                continue
            # Do this before allocating the result dictionary: OpenPLZ can
            # contain multiple source rows for the same search context.
            key = (
                state,
                phrase["phrase"],
                phrase["remaining_context"],
                postcode,
                tuple(gemarkung_norms),
            )
            if key in seen:
                continue
            seen.add(key)
            contexts.append({
                "state": state,
                "street": str(phrase["phrase"]),
                # OpenPLZ and ALKIS may store Straße/Str. differently.  Keep
                # every exact alias of the recognized query phrase.
                "street_norms": tuple(phrase["street_norms"]),
                "postcode": postcode,
                "locality": str(row["locality"] or "").strip(),
                "place_labels": tuple(place_labels),
                "gemarkung_norms": tuple(gemarkung_norms),
                "city_norms": tuple(city_norms),
                "remaining_context": str(phrase["remaining_context"]),
            })
    return contexts


def _addressless_bbox_intersects(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> bool:
    return not (
        first[2] < second[0]
        or first[0] > second[2]
        or first[3] < second[1]
        or first[1] > second[3]
    )


def _addressless_point_bbox_distance_m(
    lon: float,
    lat: float,
    bbox: tuple[float, float, float, float],
) -> float:
    nearest_lon = min(max(lon, bbox[0]), bbox[2])
    nearest_lat = min(max(lat, bbox[1]), bbox[3])
    latitude = (lat + nearest_lat) / 2.0
    dx = (lon - nearest_lon) * 111_320.0 * max(
        0.2,
        math.cos(math.radians(latitude)),
    )
    dy = (lat - nearest_lat) * 110_540.0
    return math.hypot(dx, dy)


def _addressless_context_matches(
    context: dict,
    parcel_row: sqlite3.Row,
    municipality: str,
) -> bool:
    requested_words = _contextual_parcel_words(
        str(context.get("remaining_context") or "")
    )
    if not requested_words:
        return True
    available_words: set[str] = set()
    for value in (
        *context.get("place_labels", ()),
        str(parcel_row["gemarkung_label"] or ""),
        municipality,
    ):
        available_words.update(_contextual_parcel_words(str(value or "")))
    return requested_words.issubset(available_words)


def _addressless_explicit_street_parcel_suggestions(
    parsed: dict,
    entries: tuple[FeatureDbEntry, ...],
    allowed_states: set[str],
    openplz_db_signature: tuple[int, int],
    limit: int,
) -> list[dict]:
    raw_query = str(parsed.get("query") or "")
    if (
        not _FREE_TEXT_FLUR_MARKER_RE.search(raw_query)
        or not _FREE_TEXT_PARCEL_MARKER_RE.search(raw_query)
        or parsed.get("house_number")
        or not parsed.get("flur")
    ):
        return []
    contexts = _addressless_parcel_openplz_contexts(
        parsed,
        allowed_states,
        openplz_db_signature,
    )
    if contexts is None or not contexts:
        return []
    contexts_by_state_norm: dict[str, dict[str, list[dict]]] = {}
    for context in contexts:
        state_map = contexts_by_state_norm.setdefault(
            str(context["state"]),
            {},
        )
        for gemarkung_norm in context["gemarkung_norms"]:
            state_map.setdefault(gemarkung_norm, []).append(context)

    flur_norm = fast_compact_norm(parsed.get("flur"))
    parcel_norms = tuple(dict.fromkeys(
        value
        for value in (
            fast_parcel_number_norm(parsed.get("flurstueck")),
            fast_compact_norm(parsed.get("flurstueck")),
        )
        if value
    ))
    parcel_rows: list[tuple[FeatureDbEntry, sqlite3.Row, list[dict]]] = []
    for entry in entries:
        context_map = contexts_by_state_norm.get(entry.name, {})
        gemarkung_norms = tuple(sorted(context_map))
        if not gemarkung_norms:
            continue
        for start in range(0, len(gemarkung_norms), 128):
            chunk = gemarkung_norms[start:start + 128]
            remaining = (
                _ADDRESSLESS_PARCEL_MAX_CANDIDATES
                + 1
                - len(parcel_rows)
            )
            rows = search_db_fetchall(
                entry.path,
                f"""
                SELECT *
                FROM parcel_lookup INDEXED BY idx_parcel_exact
                WHERE gemarkung_norm IN ({','.join('?' for _ in chunk)})
                  AND flur_norm = ?
                  AND flurstueck_norm IN ({','.join('?' for _ in parcel_norms)})
                ORDER BY gemarkung_norm, flur_norm, flurstueck_norm
                LIMIT ?
                """,
                [*chunk, flur_norm, *parcel_norms, remaining],
            )
            for row in rows:
                if fast_compact_norm(row["flur_label"]) != flur_norm:
                    continue
                if str(row["zaehler"] or "") != str(parsed["zaehler"]):
                    continue
                if str(row["nenner"] or "") != str(parsed["nenner"]):
                    continue
                if fast_parcel_number_norm(
                    row["flurstueck_label"]
                ) != fast_parcel_number_norm(parsed["flurstueck"]):
                    continue
                matching_contexts = context_map.get(
                    str(row["gemarkung_norm"] or ""),
                    [],
                )
                if matching_contexts:
                    parcel_rows.append((entry, row, matching_contexts))
            if len(parcel_rows) > _ADDRESSLESS_PARCEL_MAX_CANDIDATES:
                return []
    results: list[dict] = []
    for entry in entries:
        state_parcels = [
            item for item in parcel_rows if item[0].name == entry.name
        ]
        if not state_parcels:
            continue
        relevant_contexts = [
            context
            for _candidate_entry, _row, candidate_contexts in state_parcels
            for context in candidate_contexts
        ]
        city_norms = tuple(sorted({
            value
            for context in relevant_contexts
            for value in context["city_norms"]
        }))
        street_norms = tuple(sorted({
            value
            for context in relevant_contexts
            for value in context["street_norms"]
        }))
        postcodes = tuple(sorted({
            str(context["postcode"])
            for context in relevant_contexts
        }))
        if not city_norms or not street_norms or not postcodes:
            continue
        street_rows: list[sqlite3.Row] = []
        building_rows: list[sqlite3.Row] = []
        for start in range(0, len(city_norms), 128):
            city_chunk = city_norms[start:start + 128]
            street_rows.extend(search_db_fetchall(
                entry.path,
                f"""
                SELECT *
                FROM street_lookup INDEXED BY idx_street_exact
                WHERE city_norm IN ({','.join('?' for _ in city_chunk)})
                  AND street_norm IN ({','.join('?' for _ in street_norms)})
                  AND post_code IN ({','.join('?' for _ in postcodes)})
                LIMIT ?
                """,
                [
                    *city_chunk,
                    *street_norms,
                    *postcodes,
                    _ADDRESSLESS_PARCEL_MAX_VALIDATION_ROWS + 1,
                ],
            ))
            building_rows.extend(search_db_fetchall(
                entry.path,
                f"""
                SELECT
                  gml_id, street_norm, city_norm, post_code, lon, lat
                FROM address_lookup INDEXED BY idx_address_street
                WHERE city_norm IN ({','.join('?' for _ in city_chunk)})
                  AND street_norm IN ({','.join('?' for _ in street_norms)})
                  AND post_code IN ({','.join('?' for _ in postcodes)})
                  AND feature_kind = ?
                  AND lon IS NOT NULL
                  AND lat IS NOT NULL
                LIMIT ?
                """,
                [
                    *city_chunk,
                    *street_norms,
                    *postcodes,
                    "building",
                    _ADDRESSLESS_PARCEL_MAX_VALIDATION_ROWS + 1,
                ],
            ))
            if (
                len(street_rows) > _ADDRESSLESS_PARCEL_MAX_VALIDATION_ROWS
                or len(building_rows)
                > _ADDRESSLESS_PARCEL_MAX_VALIDATION_ROWS
            ):
                return []
        for _candidate_entry, row, candidate_contexts in state_parcels:
            parcel_bbox = (
                fast_float(row["min_lon"]),
                fast_float(row["min_lat"]),
                fast_float(row["max_lon"]),
                fast_float(row["max_lat"]),
            )
            municipality = ""
            municipality_loaded = False

            def load_municipality() -> str:
                nonlocal municipality, municipality_loaded
                if not municipality_loaded:
                    place = municipality_at(
                        entry.name,
                        fast_float(row["lon"]),
                        fast_float(row["lat"]),
                    )
                    municipality = str(
                        (place or {}).get("name") or ""
                    ).strip()
                    municipality_loaded = True
                return municipality

            accepted_context = None
            for context in candidate_contexts:
                if not _addressless_context_matches(
                    context,
                    row,
                    "",
                ):
                    if not _addressless_context_matches(
                        context,
                        row,
                        load_municipality(),
                    ):
                        continue
                matching_streets = [
                    street_row
                    for street_row in street_rows
                    if str(street_row["street_norm"] or "")
                    in context["street_norms"]
                    and str(street_row["city_norm"] or "")
                    in context["city_norms"]
                    and str(street_row["post_code"] or "")
                    == context["postcode"]
                ]
                if not any(
                    _addressless_bbox_intersects(
                        parcel_bbox,
                        (
                            fast_float(street_row["min_lon"]),
                            fast_float(street_row["min_lat"]),
                            fast_float(street_row["max_lon"]),
                            fast_float(street_row["max_lat"]),
                        ),
                    )
                    for street_row in matching_streets
                ):
                    continue
                nearby_buildings = {
                    str(building_row["gml_id"] or "")
                    for building_row in building_rows
                    if str(building_row["gml_id"] or "")
                    and str(building_row["street_norm"] or "")
                    in context["street_norms"]
                    and str(building_row["city_norm"] or "")
                    in context["city_norms"]
                    and str(building_row["post_code"] or "")
                    == context["postcode"]
                    and _addressless_point_bbox_distance_m(
                        fast_float(building_row["lon"]),
                        fast_float(building_row["lat"]),
                        parcel_bbox,
                    )
                    <= _ADDRESSLESS_PARCEL_MAX_BUILDING_DISTANCE_M
                }
                if len(nearby_buildings) < 2:
                    continue
                accepted_context = context
                break
            if not accepted_context:
                continue
            display_city = (
                str(accepted_context.get("locality") or "").strip()
                or load_municipality()
                or str(row["gemarkung_label"] or "").strip()
            )
            candidate = {
                "gemarkung": str(row["gemarkung_label"] or "").strip(),
                "gemarkungsnummer": str(
                    row["gemarkungsnummer"] or ""
                ).strip(),
                "flur": str(row["flur_label"] or "").strip(),
                "flurstueck": str(row["flurstueck_label"] or "").strip(),
                "state": entry.name,
                "state_label": state_display_name(entry.name),
                "municipality_context": (
                    {"name": display_city} if display_city else None
                ),
                "municipality_label": display_city,
                "signal_rank": 0,
                # Distance remains authoritative when the caller supplied a
                # map position.  Without it, this makes the source relation
                # the deterministic tie-breaker ahead of the derived result.
                "relation_rank": 1,
                "phrase_rank": (0, 0, 0),
            }
            item = _format_free_text_parcel_result(
                search_parcel_result_from_row(row, entry.name),
                candidate,
            )
            item["street_context"] = {
                "street": str(accepted_context["street"]),
                "relation": "nearby",
                "post_code": str(accepted_context["postcode"]),
                "max_distance_m": int(
                    _ADDRESSLESS_PARCEL_MAX_BUILDING_DISTANCE_M
                ),
            }
            results.append(item)
            if len(results) >= min(
                max(int(limit) * 4, 32),
                _ADDRESSLESS_PARCEL_MAX_CANDIDATES,
            ):
                return results
    return results


@lru_cache(maxsize=512)
def search_contextual_parcel_suggestions_cached(
    query: str,
    limit: int,
    states_key: tuple[str, ...],
    signature: tuple[tuple[str, str, int, int], ...],
    openplz_db_signature: tuple[int, int],
    place_signature: tuple[int, int],
) -> tuple[dict, ...]:
    """Find parcels by their exact, precomputed address relation.

    The lookup always starts on the indexed address side (exact street or
    city) and joins to ``parcel_lookup`` by ``source_db,gml_id``.  It never
    reads ``features.sqlite`` and never scans all parcels by parcel number.
    """
    del signature, place_signature
    parsed = _contextual_parcel_query_parts(query)
    if not parsed:
        return tuple()
    allowed_states = set(states_key)
    phrases = list(parsed["phrases"])

    postcode = str(parsed.get("postcode") or "")
    if postcode and not phrases and openplz_db_signature != (0, 0):
        postcode_places = _openplz_postcode_places_cached(
            postcode,
            tuple(sorted(allowed_states)),
            max(int(limit), 8),
            openplz_db_signature,
            gn250_places_signature(),
        )
        phrases.extend(
            str(item.get("municipality") or item.get("label") or "").strip()
            for item in postcode_places
            if str(item.get("municipality") or item.get("label") or "").strip()
        )
    if not phrases:
        return tuple()

    # A street-only query must retain every state (``Feldstraße 37/8`` has
    # valid linked parcels in several of them).  Do not classify arbitrary
    # street text through the global place-name index before the indexed SQL
    # lookup; that was both incomplete and expensive on a cold worker.
    entries = search_db_entries_for_states(tuple(sorted(allowed_states)))
    street_norms = tuple(dict.fromkeys(
        normalized
        for phrase in phrases
        for normalized in normalize_geocoder_text_variants(phrase)
        if normalized
    ))[:128]
    if not street_norms:
        return tuple()

    parcel_norms = tuple(dict.fromkeys(
        value
        for value in (
            fast_parcel_number_norm(parsed["flurstueck"]),
            fast_compact_norm(parsed["flurstueck"]),
        )
        if value
    ))
    flur_norm = fast_compact_norm(parsed.get("flur"))
    house_number = str(parsed.get("house_number") or "")
    house_norm = normalize_geocoder_house(house_number)
    eligible_entries: list[FeatureDbEntry] = []
    for entry in entries:
        # Search-index v1/v2 shards have both tables.  The guard keeps older
        # isolated fixtures/backups safely out of this optional path.
        table_count = search_db_fetchone(
            entry.path,
            """
            SELECT COUNT(*) AS table_count
            FROM sqlite_master
            WHERE type = ? AND name IN (?, ?)
            """,
            ("table", "address_lookup", "parcel_lookup"),
        )
        if table_count and int(table_count["table_count"] or 0) == 2:
            eligible_entries.append(entry)

    common_clauses = [
        "a.feature_kind = ?",
        f"p.flurstueck_norm IN ({','.join('?' for _ in parcel_norms)})",
        "p.zaehler = ?",
        "p.nenner = ?",
    ]
    common_params: list[object] = [
        "parcel",
        *parcel_norms,
        parsed["zaehler"],
        parsed["nenner"],
    ]
    if flur_norm:
        common_clauses.append("p.flur_norm = ?")
        common_params.append(flur_norm)
    if postcode:
        common_clauses.append("a.post_code = ?")
        common_params.append(postcode)
    if house_norm:
        common_clauses.append("a.house_number_norm = ?")
        common_params.append(house_norm)

    def lookup_phase(
        index_name: str,
        lookup_column: str,
        lookup_norms: tuple[str, ...],
    ) -> list[list[dict]]:
        def lookup_entry(entry: FeatureDbEntry) -> list[dict]:
            clauses = [
                f"{lookup_column} IN ({','.join('?' for _ in lookup_norms)})",
                *common_clauses,
            ]
            rows = search_db_fetchall(
                entry.path,
                f"""
                SELECT
                  p.*,
                  a.street_label AS linked_street_label,
                  a.house_number_label AS linked_house_number_label,
                  a.city_label AS linked_city_label,
                  a.post_code AS linked_post_code
                FROM address_lookup AS a INDEXED BY {index_name}
                JOIN parcel_lookup AS p
                  ON p.source_db = a.source_db
                 AND p.gml_id = a.gml_id
                WHERE {' AND '.join(clauses)}
                ORDER BY a.city_label, a.street_label, p.gemarkung_label, p.flur_label
                LIMIT ?
                """,
                [
                    *lookup_norms,
                    *common_params,
                    _CONTEXTUAL_PARCEL_STATE_SCAN_LIMIT,
                ],
            )
            bucket: list[dict] = []
            bucket_seen: set[tuple[str, str, str]] = set()
            for row in rows:
                if fast_parcel_number_norm(row["flurstueck_label"]) != fast_parcel_number_norm(parsed["flurstueck"]):
                    continue
                if not _contextual_parcel_row_matches(row, parsed, entry.name):
                    continue
                key = (entry.name, str(row["source_db"] or ""), str(row["gml_id"] or ""))
                if key in bucket_seen:
                    continue
                bucket_seen.add(key)
                city = _contextual_parcel_display_city(row, entry.name)
                candidate = {
                    "gemarkung": str(row["gemarkung_label"] or "").strip(),
                    "gemarkungsnummer": str(row["gemarkungsnummer"] or "").strip(),
                    "flur": str(row["flur_label"] or "").strip(),
                    "flurstueck": str(row["flurstueck_label"] or "").strip(),
                    "state": entry.name,
                    "state_label": state_display_name(entry.name),
                    "municipality_context": {"name": city} if city else None,
                    "municipality_label": city,
                    "signal_rank": 0 if parsed.get("explicit_parcel") or flur_norm else 1,
                    "phrase_rank": (-1, 0, 0),
                }
                item = _format_free_text_parcel_result(
                    search_parcel_result_from_row(row, entry.name),
                    candidate,
                )
                item["linked_address"] = {
                    "street": str(row["linked_street_label"] or "").strip(),
                    "house_number": str(
                        row["linked_house_number_label"] or ""
                    ).strip(),
                    "city": city,
                    "post_code": str(row["linked_post_code"] or "").strip(),
                }
                bucket.append(item)
                if len(bucket) >= _CONTEXTUAL_PARCEL_STATE_RESULT_LIMIT:
                    break
            return bucket

        if len(eligible_entries) <= 1:
            buckets = [lookup_entry(entry) for entry in eligible_entries]
        else:
            # Every shard has its own SQLite connection and query lock.  A
            # small bounded fan-out avoids adding 10–15 independent disk
            # lookup latencies on nationwide searches while keeping pressure
            # predictable under concurrent requests.
            with ThreadPoolExecutor(
                max_workers=min(4, len(eligible_entries)),
            ) as executor:
                buckets = list(executor.map(
                    lookup_entry,
                    eligible_entries,
                ))
        return [bucket for bucket in buckets if bucket]

    # Street context is both cheaper and more specific.  Crucially, finish it
    # for every state before deciding whether the city fallback is needed:
    # otherwise an early state could trigger expensive city scans even though
    # a later state has the accepted street relation.
    direct_buckets = lookup_phase(
        "idx_address_no_city",
        "a.street_norm",
        street_norms,
    )
    if not direct_buckets:
        direct_buckets = lookup_phase(
            "idx_address_exact",
            "a.city_norm",
            street_norms,
        )

    # Preserve candidates from every matching state before ranking.  Each
    # state has a small quota and the global pool is filled round-robin, so a
    # populous first shard cannot starve a later, nearby result.
    active_buckets = [list(bucket) for bucket in direct_buckets if bucket]
    collected: list[dict] = []
    while (
        active_buckets
        and len(collected) < _CONTEXTUAL_PARCEL_DIRECT_POOL_LIMIT
    ):
        for bucket in list(active_buckets):
            collected.append(bucket.pop(0))
            if not bucket:
                active_buckets.remove(bucket)
            if len(collected) >= _CONTEXTUAL_PARCEL_DIRECT_POOL_LIMIT:
                break
    seen: set[tuple[str, str, str]] = set()
    for item in collected:
        feature = (
            item.get("feature")
            if isinstance(item.get("feature"), dict)
            else {}
        )
        seen.add((
            normalize_state_key(str(item.get("state") or "")),
            str(feature.get("source_db") or ""),
            str(feature.get("gml_id") or ""),
        ))

    derived_results = _addressless_explicit_street_parcel_suggestions(
        parsed,
        tuple(entries),
        allowed_states,
        openplz_db_signature,
        int(limit),
    )
    for item in derived_results:
        feature = item.get("feature") if isinstance(item.get("feature"), dict) else {}
        key = (
            normalize_state_key(str(item.get("state") or "")),
            str(feature.get("source_db") or ""),
            str(feature.get("gml_id") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        collected.append(item)
        if len(collected) >= _CONTEXTUAL_PARCEL_TOTAL_POOL_LIMIT:
            break
    return tuple(collected)


def search_contextual_parcel_suggestions(
    query: str,
    limit: int,
    allowed_states: set[str],
    near_lon: float | None = None,
    near_lat: float | None = None,
) -> list[dict]:
    pool = [
        dict(item)
        for item in search_contextual_parcel_suggestions_cached(
            re.sub(r"\s+", " ", str(query or "")).strip(),
            int(limit),
            tuple(sorted(state for state in allowed_states if state)),
            search_db_signature_for_states(allowed_states),
            openplz_signature(),
            gn250_places_signature(),
        )
    ]
    for fair_index, item in enumerate(pool):
        item["_parcel_contextual_fair_index"] = fair_index
    pool.sort(key=lambda item: (
        int(item.get("_parcel_signal_rank") or 0),
        _unified_result_distance(item, near_lon, near_lat),
        int(item.get("_parcel_relation_rank") or 0),
        item.get("_parcel_phrase_rank") or (9, 9, 9),
        int(item.get("_parcel_contextual_fair_index") or 0),
        str(item.get("label") or "").casefold(),
    ))
    return pool[:int(limit)]


def _format_free_text_parcel_result(item: dict, candidate: dict) -> dict:
    result = dict(item)
    feature = dict(result.get("feature") or {})
    state = normalize_state_key(str(result.get("state") or candidate.get("state") or ""))
    gemarkung = str(feature.get("gemarkung") or candidate.get("gemarkung") or "").strip()
    code = str(feature.get("gemarkungsnummer") or candidate.get("gemarkungsnummer") or "").strip()
    gemarkung_payload = gemarkung
    if code and not re.search(rf"\(\s*{re.escape(code)}\s*\)\s*$", gemarkung_payload):
        gemarkung_payload = f"{gemarkung} ({code})"
    flur = str(feature.get("flur") or "").strip()
    flurstueck = str(feature.get("flurstueck") or candidate.get("flurstueck") or "").strip()
    center = result.get("center") if isinstance(result.get("center"), (list, tuple)) else []
    municipality = str(candidate.get("municipality_label") or "").strip()
    if not municipality and len(center) >= 2:
        place = municipality_at(state, fast_float(center[0]), fast_float(center[1]))
        municipality = str((place or {}).get("name") or "").strip()
    context = candidate.get("municipality_context") if isinstance(candidate.get("municipality_context"), dict) else {}
    context_name = str(context.get("name") or "").strip()
    primary = f"Flurstück {flurstueck}" if flurstueck else "Flurstück"
    secondary = " · ".join(part for part in (
        f"Gemarkung {gemarkung_payload}" if gemarkung_payload else "",
        f"Flur {flur}" if flur else "",
        municipality,
        state_display_name(state),
    ) if part)
    result.update({
        "kind": "parcel",
        "result_type": "feature",
        "search_scope": "parcel",
        "primary_label": primary,
        "secondary_label": secondary,
        "label": ", ".join(part for part in (primary, gemarkung_payload) if part),
        "subtitle": secondary or "Flurstück",
        "query": " ".join(part for part in (
            primary,
            f"Gemarkung {gemarkung_payload}" if gemarkung_payload else "",
            f"Flur {flur}" if flur else "",
        ) if part),
        "state": state,
        "state_label": state_display_name(state),
        "feature": feature,
        "parcel_search": {
            "gemarkung": gemarkung_payload,
            "flur": flur,
            "flurstueck": flurstueck,
            "state": state,
        },
        "_parcel_signal_rank": int(candidate.get("signal_rank") or 0),
        "_parcel_relation_rank": int(candidate.get("relation_rank") or 0),
        "_parcel_phrase_rank": candidate.get("phrase_rank") or (9, 9, 9),
        "_parcel_context_rank": (
            0
            if not context_name
            or normalize_place_search_text(context_name) == normalize_place_search_text(municipality)
            else 1
        ),
    })
    return result


def search_free_text_parcel_suggestions_for_dataset(
    dataset: str,
    q: str,
    limit: int,
    state: str = "",
    near_lon: float | None = None,
    near_lat: float | None = None,
) -> dict:
    if not is_virtual_germany_dataset(dataset):
        get_dataset(dataset)
    allowed_states = search_suggestion_states_for_dataset(dataset, state)
    parsed = parse_free_text_parcel_query(q, allowed_states)
    contextual_results = search_contextual_parcel_suggestions(
        q,
        max(int(limit) * 2, int(limit)),
        allowed_states,
        near_lon=near_lon,
        near_lat=near_lat,
    )
    contextual_parts = _contextual_parcel_query_parts(q)
    has_explicit_street_constraint = bool(
        contextual_parts
        and contextual_parts.get("flur")
        and _FREE_TEXT_FLUR_MARKER_RE.search(str(q or ""))
        and _FREE_TEXT_PARCEL_MARKER_RE.search(str(q or ""))
        and _addressless_parcel_street_phrases(
            str(contextual_parts.get("context") or "")
        )
    )
    if not parsed["candidates"] and not contextual_results:
        return {
            "query": parsed["query"],
            "count": 0,
            "explicit_signal": parsed["explicit_signal"],
            "strong_intent": parsed.get("strong_intent", False),
            "results": [],
        }

    buckets: list[list[dict]] = []
    if contextual_results:
        buckets.append(contextual_results)
    # Once the user explicitly supplied both cadastral fields and a street,
    # only the independently verified address/spatial relation may satisfy
    # that street constraint.  A plain Gemarkung lookup must not reintroduce
    # the same parcel while silently ignoring a mismatching street.
    ordinary_candidates = (
        []
        if has_explicit_street_constraint
        else parsed["candidates"]
    )
    for candidate in ordinary_candidates:
        rows = search_fast_cadastre_parcels_for_dataset(
            candidate["gemarkung"],
            candidate["flur"],
            candidate["flurstueck"],
            max(4, min(int(limit), 12)),
            {candidate["state"]},
        )
        bucket = [_format_free_text_parcel_result(row, candidate) for row in rows]
        if candidate.get("requires_municipality_match"):
            bucket = [
                item
                for item in bucket
                if int(item.get("_parcel_context_rank") or 0) == 0
            ]
        if bucket:
            buckets.append(bucket)

    # Round-robin first so ambiguous names/states cannot be starved before rank.
    collected: list[dict] = []
    while buckets and len(collected) < max(int(limit) * 12, 120):
        for bucket in list(buckets):
            collected.append(bucket.pop(0))
            if not bucket:
                buckets.remove(bucket)
            if len(collected) >= max(int(limit) * 12, 120):
                break
    for fair_index, item in enumerate(collected):
        item["_parcel_fair_index"] = fair_index
    collected.sort(key=lambda item: (
        int(item.get("_parcel_signal_rank") or 0),
        _unified_result_distance(item, near_lon, near_lat),
        int(item.get("_parcel_relation_rank") or 0),
        item.get("_parcel_phrase_rank") or (9, 9, 9),
        int(item.get("_parcel_context_rank") or 0),
        int(item.get("_parcel_fair_index") or 0),
        str(item.get("label") or "").casefold(),
    ))
    results: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for item in collected:
        feature = item.get("feature") if isinstance(item.get("feature"), dict) else {}
        key = (
            str(item.get("state") or ""),
            str(feature.get("source_db") or ""),
            str(feature.get("gml_id") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        for private_key in tuple(key for key in item if key.startswith("_parcel_")):
            item.pop(private_key, None)
        results.append(item)
        if len(results) >= int(limit):
            break
    return {
        "query": parsed["query"],
        "count": len(results),
        # An exact Gemarkung+number match is itself a strong enough signal to
        # rank parcels before generic place suggestions (for example
        # ``Hofen 1066``).  It never invents intent because the candidate has
        # already been validated against parcel_lookup.
        "explicit_signal": parsed["explicit_signal"] or bool(results),
        "strong_intent": parsed.get("strong_intent", False),
        "results": results,
    }


def search_unified_suggestions_for_dataset(
    dataset: str,
    q: str,
    limit: int,
    state: str = "",
    near_lon: float | None = None,
    near_lat: float | None = None,
) -> dict:
    """Merge address and exact parcel suggestions without tracking autocomplete."""
    parcel_payload = search_free_text_parcel_suggestions_for_dataset(
        dataset,
        q,
        max(int(limit) * 2, int(limit)),
        state=state,
        near_lon=near_lon,
        near_lat=near_lat,
    )
    # A clearly cadastral query must not degrade into an unrelated city result
    # when the requested parcel does not exist.  Slash house numbers remain
    # eligible for address search because they do not set ``strong_intent``.
    strong_parcel_intent = bool(parcel_payload.get("strong_intent"))
    if strong_parcel_intent:
        address_payload = {"results": []}
    else:
        address_payload = search_unified_address_suggestions_for_dataset(
            dataset,
            q,
            max(int(limit) * 2, int(limit)),
            state=state,
            near_lon=near_lon,
            near_lat=near_lat,
            include_parse_metadata=True,
        )
    poi_results: list[dict] = []
    address_results = [
        item
        for item in (address_payload.get("results") or [])
        if isinstance(item, dict)
    ]
    parsed_address_metadata = address_payload.get("_parsed_address")
    parsed_address_for_gating = (
        parsed_address_metadata
        if isinstance(parsed_address_metadata, dict)
        else {}
    )
    query_is_postcode_only = bool(re.fullmatch(r"\s*\d{5}\s*", str(q or "")))
    parsed_place_only = (
        bool(parsed_address_for_gating.get("place"))
        and not str(parsed_address_for_gating.get("street") or "").strip()
        and not str(
            parsed_address_for_gating.get("house_number") or ""
        ).strip()
    )
    place_prefix_query = normalize_place_search_text(
        re.sub(r"(?<!\d)\d{5}(?!\d)", " ", str(q or ""))
    )
    only_place_suggestions = bool(address_results) and all(
        str(item.get("result_type") or item.get("kind") or "") == "place"
        for item in address_results
    )
    query_is_place_prefix_only = bool(
        only_place_suggestions
        and place_prefix_query
        and any(
            any(
                normalize_place_search_text(
                    str(item.get(key) or "").split(",", 1)[0]
                ).startswith(place_prefix_query)
                for key in ("value", "primary_label", "label")
                if str(item.get(key) or "").strip()
            )
            for item in address_results
        )
    )
    if (
        not strong_parcel_intent
        and not query_is_postcode_only
        and not parsed_place_only
        and not query_is_place_prefix_only
    ):
        try:
            allowed_states = search_suggestion_states_for_dataset(dataset, state)
            poi_results = search_poi_suggestions(
                q,
                allowed_states,
                min(4, max(1, int(limit))),
                near_lon=near_lon,
                near_lat=near_lat,
            )
        except Exception:
            # The independently versioned POI index is an optional enrichment.
            # A missing or temporarily invalid candidate must never take the
            # established ALKIS address/parcel search down with it.
            poi_results = []
    explicit_parcel = bool(parcel_payload.get("explicit_signal"))
    parsed_address = {} if strong_parcel_intent else parsed_address_metadata
    if not isinstance(parsed_address, dict):
        # Isolated callers/tests may replace the address search.  Parsing
        # without place contexts still recognizes a trailing house number and
        # keeps this fallback independent from local search-shard discovery.
        parsed_address = parse_unified_address_query(str(q or ""), set())
    requested_address_house = normalize_house_number_semantic(
        str(parsed_address.get("house_number") or "")
    )
    slash_match = re.search(
        r"(?<![\w/])(\d{1,9}\s*/\s*\d{1,9})(?![\w/])",
        str(q or ""),
    )
    if not requested_address_house and slash_match:
        requested_address_house = normalize_house_number_semantic(
            slash_match.group(1)
        )
    has_exact_building_address = bool(
        requested_address_house
        and any(
            str(item.get("result_type") or "") == "address"
            and normalize_house_number_semantic(
                str((item.get("address") or {}).get("house_number") or "")
            ) == requested_address_house
            for item in address_payload.get("results") or []
            if isinstance(item, dict)
        )
    )
    contextual_parts = _contextual_parcel_query_parts(str(q or ""))
    implicit_plain_number = bool(
        contextual_parts
        and contextual_parts.get("plain_number")
        and not contextual_parts.get("explicit_parcel")
    )
    merged: list[tuple[tuple, dict]] = []
    for index, source in enumerate(address_payload.get("results") or []):
        item = dict(source)
        item["search_scope"] = "address"
        merged.append((((0 if has_exact_building_address else (1 if explicit_parcel else 0)), index), item))
    for index, source in enumerate(parcel_payload.get("results") or []):
        item = dict(source)
        item["search_scope"] = "parcel"
        merged.append((((1 if has_exact_building_address else (0 if explicit_parcel else 1)), index), item))
    merged.sort(key=lambda entry: entry[0])
    ordered = [item for _rank, item in merged]
    if (
        has_exact_building_address
        and implicit_plain_number
        and int(limit) > 1
        and parcel_payload.get("results")
    ):
        address_items = [
            item for item in ordered if item.get("search_scope") == "address"
        ]
        parcel_items = [
            item for item in ordered if item.get("search_scope") == "parcel"
        ]
        results = address_items[:max(int(limit) - 1, 0)]
        if parcel_items:
            results.append(parcel_items[0])
        selected = {id(item) for item in results}
        for item in ordered:
            if id(item) in selected:
                continue
            results.append(item)
            selected.add(id(item))
            if len(results) >= int(limit):
                break
        results = results[:int(limit)]
    else:
        results = ordered[:int(limit)]
    if poi_results:
        combined = [*results, *poi_results]

        def mixed_kind_rank(item: dict) -> int:
            scope = str(item.get("search_scope") or "")
            result_type = str(item.get("result_type") or "")
            kind = str(item.get("kind") or "")
            if scope == "address" and (
                result_type == "address" or kind in {"address", "building"}
            ):
                return 0
            if scope == "parcel" or kind == "parcel":
                return 1
            if scope == "poi" or kind == "poi":
                return 2
            return 3

        combined.sort(
            key=lambda item: (
                mixed_kind_rank(item),
                0 if item.get("exact_name_match") else 1,
            )
        )
        results = combined[:int(limit)]
    query = re.sub(r"\s+", " ", str(q or "")).strip()
    return {"query": query, "count": len(results), "results": results}


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
        if gemarkung.strip() and flurstueck.strip():
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

    # Address/street autocomplete must stay independent from features.sqlite scans.
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
            "direct-geocoder-v6-openplz",
            dataset,
            stripped_query,
            int(limit),
            normalized_mode,
            state,
            tuple(sorted(search_states)),
            tuple(active_bucket_state_keys()),
            search_db_signature_for_states(search_states),
            openplz_signature(),
        )
        cached = _SEARCH_RESPONSE_CACHE.get(key)
        if cached and cached[0] > now:
            return json.loads(json.dumps(cached[1]))
        # Keep the requested city in the query so exact ALKIS city aliases and
        # the OpenPLZ street/postcode fallback can scope results correctly.
        # The former place-stripped, bbox-only fallback admitted neighbouring
        # municipalities for broad municipal bounding boxes.
        direct_results = search_direct_geocoder_for_dataset(
            stripped_query,
            limit,
            search_states,
            allow_plain_street=street_mode,
        )
        if direct_results or is_probable_address_query(stripped_query) or is_probable_address_query(place_query):
            result = {"query": stripped_query, "count": len(direct_results[:limit]), "results": direct_results[:limit]}
            if len(_SEARCH_RESPONSE_CACHE) >= _SEARCH_RESPONSE_CACHE_MAX:
                for old_key, (expires, _) in list(_SEARCH_RESPONSE_CACHE.items()):
                    if expires <= now or len(_SEARCH_RESPONSE_CACHE) >= _SEARCH_RESPONSE_CACHE_MAX:
                        _SEARCH_RESPONSE_CACHE.pop(old_key, None)
            _SEARCH_RESPONSE_CACHE[key] = (now + SEARCH_CACHE_SECONDS, json.loads(json.dumps(result)))
            return result

    search_signature = tuple((entry.name, str(entry.path), *sqlite_file_signature(entry.path)) for entry in search_db_entries_for_dataset(dataset))
    key = (dataset, stripped_query, int(limit), normalized_mode, state, gemarkung, flur, flurstueck, tuple(active_bucket_state_keys()), gn250_places_signature(), postcode_areas_signature(), search_signature)
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


def _safe_version_name(version: str) -> str:
    value = version.strip()
    if not VERSION_RE.match(value) or "/" in value or ".." in value:
        raise HTTPException(status_code=400, detail="invalid tile version")
    return value


def _canonical_volume_state_slug(value: str) -> str:
    state_slug = normalize_state_key(value)
    if not DATASET_RE.match(state_slug):
        raise HTTPException(status_code=400, detail="invalid state slug")
    return state_slug


def _volume_versions_root() -> Path:
    return ACTIVE_VOLUME_ROOT / "versions"


def _volume_active_root() -> Path:
    return ACTIVE_VOLUME_ROOT / "active"


def _volume_incoming_root() -> Path:
    return ACTIVE_VOLUME_ROOT / ".incoming"


def _volume_version_dir(state_slug: str, version_name: str) -> Path:
    state_slug = _canonical_volume_state_slug(state_slug)
    return _volume_versions_root() / state_slug / _safe_version_name(version_name)


def _volume_upload_dir(state_slug: str, version_name: str) -> Path:
    state_slug = _canonical_volume_state_slug(state_slug)
    return _volume_incoming_root() / state_slug / _safe_version_name(version_name)


@asynccontextmanager
async def _locked_volume_upload_session(upload_dir: Path):
    """Serialize writes and finalization for one upload session across workers."""
    lock_path = upload_dir / ".upload.lock"
    lock_handle = lock_path.open("a+b")
    locked = False
    try:
        while not locked:
            try:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
            except BlockingIOError:
                await asyncio.sleep(0.1)
        yield
    finally:
        if locked:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()


def _prune_stale_volume_upload_sessions(
    state_slug: str | None = None,
    *,
    now: float | None = None,
) -> dict:
    incoming_root = _volume_incoming_root()
    if not incoming_root.is_dir():
        return {"sessions": 0, "bytes_total": 0}
    if state_slug is not None:
        state_slug = _canonical_volume_state_slug(state_slug)
        state_dirs = [incoming_root / state_slug]
    else:
        state_dirs = [path for path in incoming_root.iterdir() if path.is_dir() and not path.is_symlink()]

    cutoff = (time.time() if now is None else now) - max(3600, VOLUME_UPLOAD_SESSION_TTL_SECONDS)
    removed_sessions = 0
    removed_bytes = 0
    for state_dir in state_dirs:
        if not state_dir.is_dir() or state_dir.is_symlink():
            continue
        for upload_dir in list(state_dir.iterdir()):
            if not upload_dir.is_dir() or upload_dir.is_symlink():
                continue
            try:
                file_stats = [
                    path.stat()
                    for path in upload_dir.iterdir()
                    if path.is_file() and not path.is_symlink()
                ]
                updated_at = max(
                    [upload_dir.stat().st_mtime, *(stat.st_mtime for stat in file_stats)],
                )
            except FileNotFoundError:
                continue
            if updated_at >= cutoff:
                continue
            removed_bytes += sum(stat.st_size for stat in file_stats)
            shutil.rmtree(upload_dir)
            removed_sessions += 1
        try:
            state_dir.rmdir()
        except OSError:
            pass
    return {"sessions": removed_sessions, "bytes_total": removed_bytes}


def _validate_volume_filenames(files: list[dict], *, allow_subset: bool = False) -> list[dict]:
    if not isinstance(files, list):
        raise HTTPException(status_code=400, detail="files must be a list")
    if not files:
        raise HTTPException(status_code=400, detail="at least one tile file is required")
    names = []
    for item in files:
        if not isinstance(item, dict):
            raise HTTPException(status_code=400, detail="invalid tile file declaration")
        raw_filename = str(item.get("filename") or "")
        filename = os.path.basename(raw_filename)
        if not filename or filename != raw_filename:
            raise HTTPException(status_code=400, detail=f"invalid tile filename: {raw_filename}")
        names.append(filename)
    incoming = set(names)
    missing = sorted(VOLUME_REQUIRED_FILES - incoming)
    unexpected = sorted(incoming - VOLUME_REQUIRED_FILES)
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if unexpected or duplicates or (missing and not allow_subset):
        contract = "One or more of these standard tile state files are allowed" if allow_subset else "Exactly these 3 standard tile state files are required"
        raise HTTPException(
            status_code=400,
            detail=(
                f"{contract}: "
                f"{', '.join(sorted(VOLUME_REQUIRED_FILES))}. "
                f"missing: {', '.join(missing)} unexpected: {', '.join(unexpected)} duplicates: {', '.join(duplicates)}"
            ),
        )
    result = []
    for item in files:
        filename = str(item["filename"])
        try:
            size_bytes = int(item.get("size_bytes") or 0)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"invalid file size for {filename}") from exc
        if size_bytes <= 0:
            raise HTTPException(status_code=400, detail=f"empty upload file: {filename}")
        result.append({"filename": filename, "size_bytes": size_bytes})
    return sorted(result, key=lambda item: item["filename"])


def _volume_upload_session_manifest_path(upload_dir: Path) -> Path:
    return upload_dir / VOLUME_UPLOAD_SESSION_MANIFEST


def _write_json_atomic(path: Path, payload: dict) -> None:
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    try:
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp_path, path)
    finally:
        tmp_path.unlink(missing_ok=True)


def _read_volume_upload_session_manifest(
    upload_dir: Path,
    *,
    state_slug: str | None = None,
    version_name: str | None = None,
) -> dict:
    manifest_path = _volume_upload_session_manifest_path(upload_dir)
    if not manifest_path.is_file():
        raise HTTPException(status_code=409, detail="upload session manifest not found; create the upload session first")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=409, detail="invalid upload session manifest") from exc
    if not isinstance(manifest, dict) or manifest.get("format") != "openkataster-tile-upload-session-v2":
        raise HTTPException(status_code=409, detail="invalid upload session manifest")
    if state_slug is not None and manifest.get("state_slug") != state_slug:
        raise HTTPException(status_code=409, detail="upload session state does not match request")
    if version_name is not None and manifest.get("version_name") != version_name:
        raise HTTPException(status_code=409, detail="upload session version does not match request")
    base_version = manifest.get("base_version")
    if base_version is not None:
        base_version = _safe_version_name(str(base_version))
    files = _validate_volume_filenames(list(manifest.get("files") or []), allow_subset=base_version is not None)
    mode = "partial" if base_version is not None else "full"
    if manifest.get("mode") != mode:
        raise HTTPException(status_code=409, detail="invalid upload session mode")
    return {
        **manifest,
        "base_version": base_version,
        "mode": mode,
        "files": files,
    }


def _write_volume_upload_session_manifest(
    upload_dir: Path,
    *,
    state_slug: str,
    version_name: str,
    bundesland: str,
    base_version: str | None,
    files: list[dict],
) -> dict:
    manifest = {
        "format": "openkataster-tile-upload-session-v2",
        "state_slug": state_slug,
        "bundesland": bundesland,
        "version_name": version_name,
        "mode": "partial" if base_version is not None else "full",
        "base_version": base_version,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "files": files,
    }
    _write_json_atomic(_volume_upload_session_manifest_path(upload_dir), manifest)
    return manifest


def _volume_destination_must_be_available(state_slug: str, version_name: str) -> Path:
    version_dir = _volume_version_dir(state_slug, version_name)
    if os.path.lexists(version_dir):
        raise HTTPException(status_code=409, detail=f"tile version already exists: {version_name}")
    return version_dir


def _validated_volume_base_dir(state_slug: str, base_version: str) -> Path:
    base_dir = _volume_version_dir(state_slug, base_version)
    if not base_dir.is_dir():
        raise HTTPException(status_code=400, detail=f"base tile version not found: {base_version}; use a full upload")
    try:
        _validate_volume_state_dir(base_dir)
    except HTTPException as exc:
        raise HTTPException(
            status_code=400,
            detail=f"base tile version is incomplete or invalid: {base_version}; use a full upload ({exc.detail})",
        ) from exc
    return base_dir


def _volume_upload_file_status(upload_dir: Path, item: dict) -> dict:
    filename = item["filename"]
    size_bytes = int(item["size_bytes"])
    partial_path = upload_dir / f"{filename}.partial"
    final_path = upload_dir / filename
    partial_bytes = partial_path.stat().st_size if partial_path.is_file() else 0
    final_bytes = final_path.stat().st_size if final_path.is_file() else 0
    if partial_bytes and final_bytes:
        raise HTTPException(status_code=409, detail=f"upload session contains both partial and completed data for {filename}")
    if final_bytes and final_bytes != size_bytes:
        raise HTTPException(
            status_code=409,
            detail=f"completed upload size mismatch for {filename}: expected {size_bytes}, got {final_bytes}",
        )
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
    state_slug = _canonical_volume_state_slug(state_slug)
    _prune_stale_volume_upload_sessions(state_slug)
    state_dir = _volume_incoming_root() / state_slug
    if not state_dir.is_dir():
        return []
    sessions = []
    for upload_dir in sorted(state_dir.iterdir(), key=lambda path: path.stat().st_mtime, reverse=True):
        if not upload_dir.is_dir():
            continue
        manifest = None
        manifest_error = None
        try:
            manifest = _read_volume_upload_session_manifest(upload_dir, state_slug=state_slug, version_name=upload_dir.name)
        except HTTPException as exc:
            manifest_error = str(exc.detail)
        expected_sizes = {
            item["filename"]: int(item["size_bytes"])
            for item in (manifest or {}).get("files", [])
        }
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
                "selected": filename in expected_sizes,
                "size_bytes": expected_sizes.get(filename),
                "uploaded_bytes": uploaded_bytes,
                "partial_bytes": partial_bytes,
                "final_bytes": final_bytes,
                "complete": (
                    filename in expected_sizes
                    and final_bytes == expected_sizes[filename]
                    and partial_bytes == 0
                ),
            })
        if uploaded_total <= 0 and manifest is None:
            continue
        mtimes = [upload_dir.stat().st_mtime]
        mtimes.extend(path.stat().st_mtime for path in upload_dir.iterdir() if path.is_file())
        sessions.append({
            "version_name": upload_dir.name,
            "mode": (manifest or {}).get("mode", "legacy"),
            "base_version": (manifest or {}).get("base_version"),
            "expected_sizes": expected_sizes,
            "expected_bytes": sum(expected_sizes.values()),
            "uploaded_bytes": uploaded_total,
            "complete": bool(expected_sizes) and all(
                item["complete"] for item in files if item["selected"]
            ),
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(max(mtimes))),
            "files": files,
            **({"manifest_error": manifest_error} if manifest_error else {}),
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


def _inherit_volume_file(source: Path, destination: Path) -> str:
    if not source.is_file():
        raise HTTPException(status_code=400, detail=f"missing base file: {source.name}; use a full upload")
    destination.unlink(missing_ok=True)
    try:
        os.link(source, destination)
        return "hardlink"
    except OSError:
        try:
            shutil.copy2(source, destination)
            return "copy"
        except OSError as exc:
            destination.unlink(missing_ok=True)
            raise HTTPException(status_code=500, detail=f"could not inherit base file {source.name}: {exc}") from exc


def _write_volume_upload_manifest(
    path: Path,
    *,
    state_slug: str,
    bundesland: str,
    version_name: str,
    uploaded_files: list[dict],
    base_version: str | None,
    inherited_storage: dict[str, str],
) -> None:
    uploaded_names = {item["filename"] for item in uploaded_files}
    file_details = []
    for filename in sorted(VOLUME_REQUIRED_FILES):
        file_path = path / filename
        inherited = filename not in uploaded_names
        file_details.append({
            "filename": filename,
            "size_bytes": file_path.stat().st_size,
            "source": "inherited" if inherited else "uploaded",
            "source_version": base_version if inherited else None,
            "storage": inherited_storage.get(filename, "upload"),
        })
    manifest = {
        "format": "openkataster-tile-state-folder",
        "bundesland": bundesland,
        "state_slug": state_slug,
        "version_name": version_name,
        "uploaded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "mode": "partial" if base_version is not None else "full",
        "base_version": base_version,
        "files": len(file_details),
        "bytes_total": sum(int(item["size_bytes"]) for item in file_details),
        "file_details": file_details,
        "uploaded_files": sorted(uploaded_names),
        "inherited_files": sorted(VOLUME_REQUIRED_FILES - uploaded_names),
        "upload_contract": "standard-maplibre-pmtiles-features-search-v1",
        "provenance_contract": "openkataster-tile-version-provenance-v1",
    }
    _write_json_atomic(path / "state_upload_manifest.json", manifest)


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
    allow_origins=CORS_ORIGINS,
    allow_methods=["GET", "HEAD", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept"],
)


def _search_analytics_started(
    request: Request,
    analytics_id: str | None,
    analytics_scope: str | None,
    allowed_scopes: set[str],
) -> float | None:
    if analytics_scope not in allowed_scopes:
        return None
    if not valid_analytics_marker(request.method, analytics_id, analytics_scope):
        return None
    return time.perf_counter()


def _record_search_analytics(
    *,
    started_at: float | None,
    scope: str | None,
    query_text: str,
    state: str,
    payload: dict,
    access_mode: str,
) -> dict:
    """Record a completed marked interaction without affecting its response."""

    if started_at is None or scope is None:
        return payload
    try:
        SEARCH_ANALYTICS.record_response(
            scope=scope,
            query_text=query_text,
            state=state,
            payload=payload,
            access_mode=access_mode,
            latency_ms=(time.perf_counter() - started_at) * 1_000,
        )
    except Exception:
        # Analytics is deliberately fail-open: even unexpected implementation
        # errors must never change search or map-selection behaviour.
        pass
    return payload


@app.api_route("/health", methods=["GET", "HEAD"])
def health() -> dict:
    return {"status": "ok"}


@app.on_event("startup")
def warm_search_indexes() -> None:
    SEARCH_ANALYTICS.initialize()
    try:
        _prune_stale_volume_upload_sessions()
    except Exception:
        traceback.print_exc()
    try:
        states = set(active_bucket_state_keys())
        exact_place_context_index(gn250_places_signature())
        for entry in search_db_entries_for_states(tuple(sorted(states))):
            try:
                search_db_fetchone(entry.path, "SELECT 1 FROM address_lookup LIMIT 1")
                search_db_fetchone(entry.path, "SELECT 1 FROM street_lookup LIMIT 1")
                search_db_fetchone(entry.path, "SELECT 1 FROM parcel_lookup LIMIT 1")
            except sqlite3.Error:
                continue
        feature_db_entries_for_dataset("deutschland")
        search_places_for_dataset(VIRTUAL_GERMANY_DATASET, "Freden", 5)
        search_places_for_dataset(VIRTUAL_GERMANY_DATASET, "Hamburg", 5)
        search_direct_geocoder_for_dataset("feldstraße hildesheim", 5, states)
        search_direct_geocoder_for_dataset("feldstraße 18 hildesheim", 5, states)
        search_direct_geocoder_for_dataset("Glasewitzer Str. 3", 5, states)
        search_fast_cadastre_parcels_for_dataset("Könnigde", "1", "66/4", 5, {"sachsen-anhalt"})
        if poi_index_available():
            search_poi_suggestions("Rathaus", states, 1)
    except Exception as exc:
        print(f"search warmup failed: {exc}")




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
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
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




def _cadastre_rendering_capability(state_slug: str) -> dict | None:
    config = KATASTER_WMS_CONFIGS.get(state_slug)
    if not config:
        return None
    return {
        "profile": "official-wms-full-v1",
        "tile_template": f"/katasterbild/{state_slug}/{{z}}/{{x}}/{{y}}.png",
        "tile_size": int(config.get("tile_size", 512)),
        "minzoom": int(config.get("minzoom", 17)),
        "maxzoom": int(config.get("maxzoom", 22)),
        "revision": str(config.get("revision") or "official-wms-v1"),
        "attribution": str(config.get("attribution") or ""),
        "presentation": "full",
    }


def _aerial_rendering_capability(state_slug: str, *, attribution: str = "") -> dict | None:
    config = LUFTBILD_WMS_CONFIGS.get(state_slug)
    if not config:
        return None
    return {
        "profile": "official-wms-aerial-v1",
        "tile_template": f"/luftbild/{state_slug}/{{z}}/{{x}}/{{y}}.png",
        # The upstream request may deliberately use a larger image for a
        # high-DPI tile. MapLibre's logical XYZ tile size remains 512 pixels.
        "tile_size": int(config.get("map_tile_size", 512)),
        "minzoom": int(config.get("minzoom", 17)),
        "maxzoom": int(config.get("maxzoom", 22)),
        "revision": str(config.get("revision") or "aerial-wms-v1"),
        "attribution": str(config.get("attribution") or attribution or ""),
        "presentation": "aerial",
    }


def _export_capability(state_slug: str) -> dict | None:
    if state_slug not in KATASTER_WMS_CONFIGS:
        return None
    return {
        "profile": "official-raster-v1",
        "pdf": "raster",
        "png": "raster",
        "dxf": False,
        "vector_pdf": False,
        "fine_grained_layers": False,
    }


def _api_v1_state_rows() -> list[dict]:
    metadata_by_slug = {
        _state_metadata_slug(str(row.get("bundesland") or row.get("state") or row.get("name") or "")): row
        for row in _state_metadata_cache()
        if isinstance(row, dict)
    }
    active_states = set(active_bucket_state_keys())
    # Official raster-only states are valid visual coverage even before a
    # local feature/search package is available. Keep `active` reserved for
    # interactive local data so API consumers can distinguish both modes.
    visible_states = active_states | set(KATASTER_WMS_CONFIGS)
    rows: list[dict] = []
    for slug in sorted(visible_states):
        name, lon, lat = STATE_LABEL_POINTS.get(slug, (slug.replace("-", " ").title(), 0, 0))
        meta = metadata_by_slug.get(slug, {})
        data_active = slug in active_states
        row = {
            "slug": slug,
            "name": str(meta.get("bundesland") or meta.get("name") or name),
            "center": {"lon": lon, "lat": lat},
            "datenstand": meta.get("datenstand"),
            "datenjahr": meta.get("datenjahr"),
            "quellenvermerk": meta.get("quellenvermerk"),
            "lizenz": meta.get("lizenz"),
            "active": data_active,
            "visual_active": True,
            "interactive": data_active,
        }
        cadastre_rendering = _cadastre_rendering_capability(slug)
        aerial_rendering = _aerial_rendering_capability(
            slug,
            attribution=str(row.get("quellenvermerk") or ""),
        )
        rendering = {}
        if cadastre_rendering:
            rendering["cadastre_raster"] = cadastre_rendering
        if aerial_rendering:
            rendering["aerial_raster"] = aerial_rendering
        if rendering:
            row["rendering"] = rendering
        export_capability = _export_capability(slug)
        if export_capability:
            row["export"] = export_capability
        rows.append(row)
    return rows


def _api_v1_search_query_from_parts(*parts: str) -> str:
    return " ".join(part.strip() for part in parts if part and part.strip()).strip()


class EmbedSessionRequest(BaseModel):
    origin: str = Field(
        min_length=8,
        max_length=255,
        description="Exakte Origin der einbettenden Seite, inklusive https://.",
        examples=["https://www.beispiel-immobilien.de"],
    )
    dataset: str = Field(default=VIRTUAL_GERMANY_DATASET, examples=["deutschland"])
    mode: str = Field(default="standard", pattern="^(standard|onoffice)$", examples=["standard"])


class EmbedSessionResponse(BaseModel):
    session_token: str
    embed_url: str
    expires_at: int
    origin: str
    scopes: list[str]


class InternalViewerSessionRequest(BaseModel):
    access: str = Field(default="free", pattern="^(free|pro)$")
    subject: str = Field(default="public-viewer", max_length=120)
    name: str | None = Field(default=None, max_length=255)


AUTH_ERROR_RESPONSES = {
    401: {"description": "API-Key fehlt oder ist ungültig."},
    403: {"description": "Scope oder freigeschaltete Domain fehlt."},
    429: {"description": "Monatliches Nutzungskontingent ist ausgeschöpft."},
}


@app.api_route("/api/v1", methods=["GET", "HEAD"])
def api_v1_contract() -> dict:
    return {
        "name": "OpenKataster Tiles API",
        "version": "v1",
        "status": "preview",
        "auth": {
            "free": {
                "description": "Public map tiles remain readable. Search and geometry previews use a short-lived viewer session or a project key.",
                "scopes": ["map:view", "search:basic", "layers:basic", "feature:preview"],
            },
            "pro": {
                "description": "Use Authorization: Bearer <project-key> for server API calls. Embed clients receive a short-lived session token.",
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
            "search_poi": "GET /api/v1/search/poi?poi_id=",
            "search_dataset": "GET /api/v1/search/{dataset}?q=&mode=",
            "suggest_search": "GET /api/v1/suggest/search?q=",
            "suggest_places": "GET /api/v1/suggest/places?q=",
            "suggest_streets": "GET /api/v1/suggest/streets?place=&q=",
            "suggest_gemarkungen": "GET /api/v1/suggest/gemarkungen?q=",
            "feature_point": "GET /api/v1/features/point?lon=&lat=",
            "feature_geometry": "GET /api/v1/features/geometry?state=&source_db=&gml_id=&kind=",
            "embed_session": "POST /api/v1/embed/sessions",
            "onoffice_selection_payload": "POST /api/v1/integrations/onoffice/selection-payload",
        },
        "notes": [
            "The iframe viewer should use only /api/v1 endpoints.",
            "Object details require pro access; geometry previews remain public where explicitly exposed.",
            "The onOffice endpoint is a payload adapter only. It does not write to onOffice yet.",
        ],
    }


@app.post(
    "/api/v1/embed/sessions",
    response_model=EmbedSessionResponse,
    tags=["Embed"],
    summary="Kurzlebige Embed-Session erstellen",
    description="Wird serverseitig mit dem geheimen Projekt-Key aufgerufen. Der zurückgegebene Session-Token darf in die iframe-URL eingesetzt werden.",
    responses=AUTH_ERROR_RESPONSES,
)
def api_v1_create_embed_session(
    request: Request,
    payload: EmbedSessionRequest,
    access: Annotated[ApiAccessContext, Depends(require_api_key_access)],
) -> EmbedSessionResponse:
    required_scope = "embed:pro" if payload.mode == "onoffice" else "embed:free"
    if required_scope not in access.scopes and "embed:pro" not in access.scopes:
        raise HTTPException(status_code=403, detail=f"required scope: {required_scope}")

    origin = _normalize_origin(payload.origin)
    if not origin:
        raise HTTPException(status_code=422, detail="origin must be a valid http(s) origin")
    allowed_origins = (access.claims or {}).get("allowed_origins")
    if not isinstance(allowed_origins, list) or not _origin_is_allowed(origin, allowed_origins):
        raise HTTPException(status_code=403, detail="origin is not enabled for this project")
    request_origin = _normalize_origin(request.headers.get("Origin", ""))
    if request_origin and request_origin != origin:
        raise HTTPException(status_code=403, detail="Origin header does not match requested origin")

    dataset = normalize_state_key(payload.dataset)
    if not dataset or (dataset != VIRTUAL_GERMANY_DATASET and dataset not in set(active_bucket_state_keys())):
        raise HTTPException(status_code=404, detail="dataset not found")

    now = int(time.time())
    claims = {
        "typ": "embed",
        "aud": "openkataster-embed",
        "sub": (access.claims or {}).get("sub"),
        "name": (access.claims or {}).get("name"),
        "plan": (access.claims or {}).get("plan") or "free",
        "integration": "embed",
        "project_id": (access.claims or {}).get("sub"),
        "origin": origin,
        "dataset": dataset,
        "mode": payload.mode,
        "scopes": sorted(access.scopes),
        "iat": now,
        "nbf": now - 5,
        "exp": now + EMBED_SESSION_TTL_SECONDS,
        "jti": secrets.token_urlsafe(12),
    }
    session_token = _sign_embed_claims(claims)
    base_url = PUBLIC_BASE_URL or str(request.base_url).rstrip("/")
    query = urllib.parse.urlencode(
        {
            "session": session_token,
            "okParentOrigin": origin,
            "mode": payload.mode,
        }
    )
    return EmbedSessionResponse(
        session_token=session_token,
        embed_url=f"{base_url}/embed/{dataset}?{query}",
        expires_at=claims["exp"],
        origin=origin,
        scopes=claims["scopes"],
    )


@app.post("/internal/v1/viewer-sessions", include_in_schema=False)
def api_v1_create_internal_viewer_session(
    _: Annotated[str, Depends(require_admin_key)],
    payload: InternalViewerSessionRequest,
) -> dict:
    token = _new_viewer_session(
        pro=payload.access == "pro",
        subject=payload.subject or "public-viewer",
        name=payload.name,
        allow_export=True,
    )
    claims = _verify_embed_session(token) or {}
    return {"token": token, "expires_at": claims.get("exp")}


@app.get("/internal/v1/search-analytics/dashboard", include_in_schema=False)
def api_v1_search_analytics_dashboard(
    _: Annotated[str, Depends(require_admin_key)],
    period: str = "30d",
    page: Annotated[int, Query(ge=1)] = 1,
    per_page: Annotated[int, Query(ge=1, le=100)] = 100,
    bucket: str = "day",
    timeline_from: Annotated[int | None, Query(ge=0)] = None,
) -> dict:
    return SEARCH_ANALYTICS.dashboard(
        period,
        page=page,
        per_page=per_page,
        bucket=bucket,
        timeline_from=timeline_from,
    )


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
    payload = {
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
    if poi_index_available():
        metadata = poi_index_metadata()
        payload["poi"] = {
            "source": "OpenStreetMap",
            "license": "ODbL 1.0",
            "attribution": "© OpenStreetMap-Mitwirkende",
            "copyright_url": "https://www.openstreetmap.org/copyright",
            "created_at_utc": metadata.get("created_at_utc"),
            "active_states": metadata.get("active_states"),
        }
        payload["attributions"] = [
            {
                "text": "© OpenStreetMap-Mitwirkende",
                "href": "https://www.openstreetmap.org/copyright",
            }
        ]
    return payload


@app.api_route("/api/v1/datasets", methods=["GET", "HEAD"])
def api_v1_datasets() -> dict:
    payload = datasets("api-v1")
    payload["api_version"] = "v1"
    return payload



@app.get("/api/v1/tilejson/{state}.json")
async def api_v1_tilejson(state: str, request: Request):
    state_key = normalize_state_key(state)
    base_url = public_base_url(request)
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
            {"id": "boundary_point_geometries", "fields": {}},
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
    request: Request,
    access: Annotated[ApiAccessContext, Depends(RequireScopes("search:basic"))],
    q: str = "",
    place: str = "",
    street: str = "",
    house_number: str = "",
    limit: Annotated[int, Query(ge=1, le=30)] = 12,
    state: str = "",
    near_lon: Annotated[float | None, Query(ge=-180, le=180)] = None,
    near_lat: Annotated[float | None, Query(ge=-90, le=90)] = None,
    analytics_query: str = "",
    analytics_id: str | None = None,
    analytics_scope: str | None = None,
) -> dict:
    analytics_started = _search_analytics_started(request, analytics_id, analytics_scope, {"address"})
    query = q.strip() or _api_v1_search_query_from_parts(street, house_number, place)

    def done(payload: dict) -> dict:
        return _record_search_analytics(
            started_at=analytics_started,
            scope=analytics_scope,
            query_text=analytics_query.strip() or query,
            state=state,
            payload=payload,
            access_mode=access.mode,
        )

    if len(query) < 2:
        return done({"query": query, "results": []})
    state_key = state
    if not state_key.strip() and place.strip():
        inferred_states = states_for_place_context(place, set(active_bucket_state_keys()))
        if len(inferred_states) == 1:
            state_key = inferred_states[0]
    mode = "street" if street.strip() and not house_number.strip() else "address"
    candidate_override = (
        structured_geocoder_candidates(street, house_number, place)
        if not q.strip()
        else tuple()
    )
    if q.strip():
        result = search_unified_address_suggestions_for_dataset(
            VIRTUAL_GERMANY_DATASET,
            query,
            limit,
            state=state_key,
            near_lon=near_lon,
            near_lat=near_lat,
            exact_house_number=True,
        )
    elif candidate_override:
        active_states = set(active_bucket_state_keys())
        structured_state = requested_state_context(state_key, active_states)
        search_states = {structured_state} if structured_state else active_states
        direct_results = search_direct_geocoder_for_dataset(
            query,
            limit,
            search_states,
            allow_plain_street=mode == "street",
            candidate_override=candidate_override,
        )
        result = {
            "query": query,
            "count": len(direct_results[:limit]),
            "results": direct_results[:limit],
        }
    else:
        result = cached_search_features_for_dataset(
            VIRTUAL_GERMANY_DATASET,
            query,
            limit,
            mode,
            state=state_key,
        )
    if result.get("results") or q.strip() or not place.strip() or not street.strip():
        return done(result)
    place_suggestions = search_place_suggestions_for_dataset(VIRTUAL_GERMANY_DATASET, place, 8, state=state).get("results") or []
    for suggestion in place_suggestions:
        suggested_place = str(suggestion.get("value") or suggestion.get("label") or "").strip()
        suggested_state = normalize_state_key(str(suggestion.get("state") or ""))
        if not suggested_place or normalize_place_search_text(suggested_place) == normalize_place_search_text(place):
            continue
        fallback_query = _api_v1_search_query_from_parts(street, house_number, suggested_place)
        fallback_candidates = structured_geocoder_candidates(
            street,
            house_number,
            suggested_place,
        )
        fallback_state = suggested_state or requested_state_context(state_key, set(active_bucket_state_keys()))
        fallback_states = {fallback_state} if fallback_state else set(active_bucket_state_keys())
        fallback_results = search_direct_geocoder_for_dataset(
            fallback_query,
            limit,
            fallback_states,
            allow_plain_street=mode == "street",
            candidate_override=fallback_candidates,
        )
        fallback = {
            "query": fallback_query,
            "count": len(fallback_results[:limit]),
            "results": fallback_results[:limit],
        }
        if fallback.get("results"):
            fallback["query"] = query
            return done(fallback)
    return done(result)


@app.api_route("/api/v1/search/parcel", methods=["GET", "HEAD"])
def api_v1_search_parcel(
    request: Request,
    gemarkung: str,
    flurstueck: str,
    access: Annotated[ApiAccessContext, Depends(RequireScopes("search:basic"))],
    flur: str = "",
    limit: Annotated[int, Query(ge=1, le=30)] = 12,
    state: str = "",
    analytics_query: str = "",
    analytics_id: str | None = None,
    analytics_scope: str | None = None,
) -> dict:
    analytics_started = _search_analytics_started(request, analytics_id, analytics_scope, {"parcel"})
    query = _api_v1_search_query_from_parts(gemarkung, flur, flurstueck)
    if len(gemarkung.strip()) < 2 or not flurstueck.strip():
        payload = {"query": query, "results": []}
    else:
        payload = cached_search_features_for_dataset(
            VIRTUAL_GERMANY_DATASET,
            gemarkung.strip(),
            limit,
            "parcel",
            state=state,
            gemarkung=gemarkung,
            flur=flur,
            flurstueck=flurstueck,
        )
    return _record_search_analytics(
        started_at=analytics_started,
        scope=analytics_scope,
        query_text=analytics_query.strip() or query,
        state=state,
        payload=payload,
        access_mode=access.mode,
    )


@app.api_route("/api/v1/search/poi", methods=["GET", "HEAD"])
def api_v1_search_poi(
    request: Request,
    access: Annotated[ApiAccessContext, Depends(RequireScopes("search:basic"))],
    poi_id: Annotated[str, Query(min_length=3, max_length=80)],
    state: str = "",
    analytics_query: str = "",
    analytics_id: str | None = None,
    analytics_scope: str | None = None,
) -> dict:
    # ``poi_id`` is a stable technical identifier, not a user's conscious
    # search input.  Never fall back to it for analytics: direct API calls
    # without the original text remain deliberately untracked.
    requested_query_text = analytics_query.strip()
    analytics_query_text = (
        ""
        if re.fullmatch(
            r"(?:osm:)?[nwr](?::)?[1-9]\d*",
            requested_query_text,
            flags=re.IGNORECASE,
        )
        else requested_query_text
    )
    analytics_started = (
        _search_analytics_started(
            request,
            analytics_id,
            analytics_scope,
            {"poi"},
        )
        if analytics_query_text
        else None
    )
    active_states = set(active_bucket_state_keys())
    requested_state = normalize_state_key(state)
    allowed_states = (
        {requested_state}
        if requested_state and requested_state in active_states
        else (active_states if not requested_state else set())
    )
    result = search_poi_by_id(poi_id, allowed_states)
    payload = {
        "query": requested_query_text or poi_id.strip(),
        "count": 1 if result else 0,
        "results": [result] if result else [],
    }
    analytics_state = (
        str(result.get("state") or state)
        if isinstance(result, dict)
        else state
    )
    return _record_search_analytics(
        started_at=analytics_started,
        scope=analytics_scope,
        query_text=analytics_query_text,
        state=analytics_state,
        payload=payload,
        access_mode=access.mode,
    )


@app.api_route("/api/v1/search/{dataset}", methods=["GET", "HEAD"])
def api_v1_dataset_search(
    request: Request,
    dataset: str,
    access: Annotated[ApiAccessContext, Depends(RequireScopes("search:basic"))],
    q: str = "",
    limit: Annotated[int, Query(ge=1, le=30)] = 12,
    mode: str = "mixed",
    state: str = "",
    gemarkung: str = "",
    flur: str = "",
    flurstueck: str = "",
    analytics_id: str | None = None,
    analytics_scope: str | None = None,
) -> dict:
    analytics_started = _search_analytics_started(request, analytics_id, analytics_scope, {"address", "parcel"})
    if not is_virtual_germany_dataset(dataset):
        get_dataset(dataset)
    query = q.strip() or gemarkung.strip() or flurstueck.strip()
    if len(query) < 2:
        payload = {"query": query, "results": []}
    else:
        payload = cached_search_features_for_dataset(
            dataset,
            query,
            limit,
            mode,
            state=state,
            gemarkung=gemarkung,
            flur=flur,
            flurstueck=flurstueck,
        )
    return _record_search_analytics(
        started_at=analytics_started,
        scope=analytics_scope,
        query_text=_api_v1_search_query_from_parts(gemarkung, flur, flurstueck) if analytics_scope == "parcel" else query,
        state=state,
        payload=payload,
        access_mode=access.mode,
    )


@app.api_route("/api/v1/suggest/addresses", methods=["GET", "HEAD"], tags=["Search"])
def api_v1_suggest_addresses(
    _: Annotated[ApiAccessContext, Depends(RequireScopes("search:basic"))],
    q: Annotated[str, Query(min_length=2, max_length=140)],
    limit: Annotated[int, Query(ge=1, le=20)] = 10,
    state: str = "",
    near_lon: Annotated[float | None, Query(ge=-180, le=180)] = None,
    near_lat: Annotated[float | None, Query(ge=-90, le=90)] = None,
) -> dict:
    # Autocomplete is deliberately untracked.  Only a conscious submit or a
    # selected suggestion reaches /search/address with an analytics marker.
    return search_unified_address_suggestions_for_dataset(
        VIRTUAL_GERMANY_DATASET,
        q,
        limit,
        state=state,
        near_lon=near_lon,
        near_lat=near_lat,
    )


@app.api_route("/api/v1/suggest/search", methods=["GET", "HEAD"], tags=["Search"])
def api_v1_suggest_search(
    _: Annotated[ApiAccessContext, Depends(RequireScopes("search:basic"))],
    q: Annotated[str, Query(min_length=2, max_length=140)],
    limit: Annotated[int, Query(ge=1, le=20)] = 10,
    state: str = "",
    near_lon: Annotated[float | None, Query(ge=-180, le=180)] = None,
    near_lat: Annotated[float | None, Query(ge=-90, le=90)] = None,
) -> dict:
    # This endpoint is intentionally untracked.  A selected result is recorded
    # exactly once by /search/address or /search/parcel.
    return search_unified_suggestions_for_dataset(
        VIRTUAL_GERMANY_DATASET,
        q,
        limit,
        state=state,
        near_lon=near_lon,
        near_lat=near_lat,
    )


@app.api_route("/api/v1/suggest/places", methods=["GET", "HEAD"], tags=["Search"])
def api_v1_suggest_places(
    request: Request,
    access: Annotated[ApiAccessContext, Depends(RequireScopes("search:basic"))],
    q: Annotated[str, Query(min_length=2, max_length=80)],
    limit: Annotated[int, Query(ge=1, le=20)] = 10,
    state: str = "",
    analytics_id: str | None = None,
    analytics_scope: str | None = None,
) -> dict:
    analytics_started = _search_analytics_started(request, analytics_id, analytics_scope, {"place"})
    payload = search_place_suggestions_for_dataset(VIRTUAL_GERMANY_DATASET, q, limit, state=state)
    return _record_search_analytics(
        started_at=analytics_started,
        scope=analytics_scope,
        query_text=q,
        state=state,
        payload=payload,
        access_mode=access.mode,
    )


@app.api_route("/api/v1/suggest/streets", methods=["GET", "HEAD"], tags=["Search"])
def api_v1_suggest_streets(
    request: Request,
    access: Annotated[ApiAccessContext, Depends(RequireScopes("search:basic"))],
    q: Annotated[str, Query(min_length=2, max_length=80)],
    place: Annotated[str, Query(min_length=2, max_length=80)],
    limit: Annotated[int, Query(ge=1, le=20)] = 10,
    state: str = "",
    analytics_id: str | None = None,
    analytics_scope: str | None = None,
) -> dict:
    analytics_started = _search_analytics_started(request, analytics_id, analytics_scope, {"street"})
    payload = search_street_suggestions_for_dataset(VIRTUAL_GERMANY_DATASET, place, q, limit, state=state)
    return _record_search_analytics(
        started_at=analytics_started,
        scope=analytics_scope,
        query_text=_api_v1_search_query_from_parts(q, place),
        state=state,
        payload=payload,
        access_mode=access.mode,
    )


@app.api_route("/api/v1/suggest/gemarkungen", methods=["GET", "HEAD"], tags=["Search"])
def api_v1_suggest_gemarkungen(
    _: Annotated[ApiAccessContext, Depends(RequireScopes("search:basic"))],
    q: Annotated[str, Query(min_length=2, max_length=80)],
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
    state: str = "",
) -> dict:
    return search_gemarkung_suggestions_for_dataset(VIRTUAL_GERMANY_DATASET, q, limit, state=state)




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
                address_relations = compact_feature_relation_addresses(con, row)
                if address_relations.addresses:
                    feature["addresses"] = enrich_addresses_with_postcode(
                        address_relations.addresses,
                        result["center"][0],
                        result["center"][1],
                        state_key,
                    )
                    feature["address"] = feature["addresses"][0]["label"] if feature["addresses"] else feature.get("address", "")
                apply_feature_address_relation_metadata(feature, address_relations)
                if row["kind"] == "parcel":
                    area_m2 = compact_feature_area_m2(con, row["id"])
                    if area_m2 is not None:
                        feature["amtliche_flaeche_m2"] = area_m2
                result["feature"] = normalize_feature_properties_for_response(
                    state_key or entry.name,
                    str(row["kind"] or ""),
                    feature,
                )
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
                address_relations = addresses_for_feature(con, dict(row), geom)
                feature["addresses"] = enrich_addresses_with_postcode(
                    address_relations.addresses,
                    result["center"][0],
                    result["center"][1],
                    state_key,
                )
                feature["address"] = feature["addresses"][0]["label"] if feature["addresses"] else feature.get("address", "")
                apply_feature_address_relation_metadata(feature, address_relations)
            except (GEOSException, TypeError, ValueError):
                pass
            result["feature"] = normalize_feature_properties_for_response(
                state_key or entry.name,
                str(row["kind"] or ""),
                feature,
            )
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


ONOFFICE_SELECTION_MAX_FEATURES = 50
ONOFFICE_INTERSECTION_MAX_CANDIDATES = 200


def _onoffice_reference_key(reference: dict) -> tuple[str, str, str, str]:
    return (
        normalize_state_key(str(reference.get("state") or "")),
        str(reference.get("kind") or "").strip().lower(),
        str(reference.get("source_db") or "").strip(),
        str(reference.get("gml_id") or "").strip(),
    )


def _onoffice_result_reference(result: dict) -> dict:
    feature = result.get("feature") if isinstance(result.get("feature"), dict) else {}
    return {
        "state": normalize_state_key(str(result.get("state") or "")),
        "kind": str(result.get("kind") or "").strip().lower(),
        "source_db": str(result.get("source_db") or feature.get("source_db") or "").strip(),
        "gml_id": str(result.get("gml_id") or feature.get("gml_id") or feature.get("id") or "").strip(),
    }


def _onoffice_compact_feature_row(
    con: sqlite3.Connection,
    reference: dict,
) -> sqlite3.Row | None:
    kind = str(reference.get("kind") or "")
    clauses = ["f.kind = ?"]
    params: list[object] = [kind] if kind in {"parcel", "building"} else [""]
    if kind not in {"parcel", "building"}:
        clauses = ["f.kind IN ('parcel', 'building')"]
        params = []
    clauses.append(
        """
        (
          f.id = ?
          OR json_extract(f.properties_json, '$.gml_id') = ?
          OR (
            json_extract(f.properties_json, '$.source_db') = ?
            AND json_extract(f.properties_json, '$.gml_id') = ?
          )
        )
        """
    )
    params.extend(
        [
            reference["gml_id"],
            reference["gml_id"],
            reference["source_db"],
            reference["gml_id"],
        ]
    )
    geometry_join = ""
    geometry_column = "NULL AS trusted_geometry_wkb"
    if sqlite_table_exists(con, "feature_geometries"):
        geometry_join = "LEFT JOIN feature_geometries g ON g.feature_id = f.id"
        geometry_column = "g.geometry_wkb AS trusted_geometry_wkb"
    return con.execute(
        f"""
        SELECT f.*, {geometry_column}
        FROM features f
        {geometry_join}
        WHERE {' AND '.join(clauses)}
        ORDER BY f.kind, f.id
        LIMIT 1
        """,
        tuple(params),
    ).fetchone()


def _onoffice_standard_feature_row(
    con: sqlite3.Connection,
    reference: dict,
) -> sqlite3.Row | None:
    clauses = ["source_db = ?", "gml_id = ?"]
    params: list[object] = [reference["source_db"], reference["gml_id"]]
    if reference["kind"] in {"parcel", "building"}:
        clauses.insert(0, "kind = ?")
        params.insert(0, reference["kind"])
    return con.execute(
        f"""
        SELECT *
        FROM features
        WHERE {' AND '.join(clauses)}
        ORDER BY kind, source_db, gml_id
        LIMIT 1
        """,
        tuple(params),
    ).fetchone()


def _onoffice_row_geometry(row: sqlite3.Row, compact: bool):
    column = "trusted_geometry_wkb" if compact else "geometry_wkb"
    if column not in row.keys() or row[column] is None:
        return None
    try:
        return wkb.loads(bytes(row[column]))
    except (GEOSException, TypeError, ValueError):
        return None


def _onoffice_compact_addresses(
    con: sqlite3.Connection,
    row: sqlite3.Row,
    state: str,
    center: list[float],
) -> FeatureAddressRelations:
    relations = compact_feature_relation_addresses(con, row)
    return FeatureAddressRelations(
        enrich_addresses_with_postcode(
            relations.addresses,
            center[0],
            center[1],
            state,
        ),
        total=relations.total,
        limit=relations.limit,
    )


def _onoffice_detail_from_row(
    con: sqlite3.Connection,
    entry: FeatureDbEntry,
    row: sqlite3.Row,
    geom,
    compact: bool,
) -> dict:
    if compact:
        result = result_from_compact_feature(row)
        feature = result["feature"]
        if geom is not None:
            point = geom.representative_point()
            result["center"] = [point.x, point.y]
            result["bbox"] = list(geom.bounds)
            feature["geometry"] = mapping(geom)
        address_relations = _onoffice_compact_addresses(
            con,
            row,
            entry.name,
            result["center"],
        )
        if address_relations.addresses:
            feature["addresses"] = address_relations.addresses
            feature["address"] = address_relations.addresses[0].get("label") or feature.get("address", "")
        apply_feature_address_relation_metadata(feature, address_relations)
        if str(row["kind"] or "") == "parcel":
            area_m2 = compact_feature_area_m2(con, str(row["id"] or ""))
            if area_m2 is not None:
                feature["amtliche_flaeche_m2"] = area_m2
    else:
        result = result_from_feature(row, geom)
        feature = result["feature"]
        if str(row["kind"] or "") == "parcel":
            enrich_gemarkung_from_lookup(entry.path, feature)
        address_relations = addresses_for_feature(con, dict(row), geom)
        addresses = enrich_addresses_with_postcode(
            address_relations.addresses,
            result["center"][0],
            result["center"][1],
            entry.name,
        )
        if addresses:
            feature["addresses"] = addresses
            feature["address"] = addresses[0].get("label") or feature.get("address", "")
        apply_feature_address_relation_metadata(feature, address_relations)
    result["feature"] = normalize_feature_properties_for_response(
        entry.name,
        str(row["kind"] or ""),
        feature,
    )
    result["state"] = entry.name
    return result


def _onoffice_intersection_candidate_rows(
    con: sqlite3.Connection,
    *,
    compact: bool,
    target_kind: str,
    bounds: tuple[float, float, float, float],
) -> list[sqlite3.Row] | None:
    min_lon, min_lat, max_lon, max_lat = bounds
    limit = ONOFFICE_INTERSECTION_MAX_CANDIDATES + 1
    if compact:
        if not (
            sqlite_table_exists(con, "feature_bbox_index")
            and sqlite_table_exists(con, "feature_geometries")
        ):
            return None
        return con.execute(
            """
            SELECT f.*, g.geometry_wkb AS trusted_geometry_wkb
            FROM feature_bbox_index i
            JOIN features f ON f.rowid = i.rowid
            JOIN feature_geometries g ON g.feature_id = f.id
            WHERE f.kind = ?
              AND i.min_lon <= ? AND i.max_lon >= ?
              AND i.min_lat <= ? AND i.max_lat >= ?
            ORDER BY f.kind, f.id
            LIMIT ?
            """,
            (target_kind, max_lon, min_lon, max_lat, min_lat, limit),
        ).fetchall()
    if not sqlite_table_exists(con, "feature_index"):
        return None
    return con.execute(
        """
        SELECT f.*
        FROM feature_index i
        JOIN features f ON f.id = i.id
        WHERE f.kind = ?
          AND i.min_lon <= ? AND i.max_lon >= ?
          AND i.min_lat <= ? AND i.max_lat >= ?
        ORDER BY f.kind, f.source_db, f.gml_id, f.id
        LIMIT ?
        """,
        (target_kind, max_lon, min_lon, max_lat, min_lat, limit),
    ).fetchall()


def resolve_onoffice_selection_feature(
    reference: dict,
    *,
    expand_intersections: bool,
) -> tuple[dict | None, list[dict], list[str]]:
    warnings: list[str] = []
    for entry in feature_geometry_entries_for_state(reference["state"]):
        with sqlite_feature_connection(entry.path) as con:
            compact = compact_feature_schema(con)
            row = (
                _onoffice_compact_feature_row(con, reference)
                if compact
                else _onoffice_standard_feature_row(con, reference)
            )
            if row is None:
                continue
            geom = _onoffice_row_geometry(row, compact)
            if geom is None:
                detail = _onoffice_detail_from_row(con, entry, row, None, compact) if compact else None
                if detail is not None:
                    warnings.append(
                        f"Trusted geometry unavailable for {_onoffice_result_reference(detail)['gml_id']}; intersections were not expanded."
                    )
                return detail, [], warnings

            detail = _onoffice_detail_from_row(con, entry, row, geom, compact)
            kind = str(detail.get("kind") or "")
            if not expand_intersections or kind not in {"building", "parcel"}:
                return detail, [], warnings

            target_kind = "parcel" if kind == "building" else "building"
            candidate_rows = _onoffice_intersection_candidate_rows(
                con,
                compact=compact,
                target_kind=target_kind,
                bounds=geom.bounds,
            )
            if candidate_rows is None:
                warnings.append(
                    f"Spatial index unavailable for {entry.name}; intersections were not expanded."
                )
                return detail, [], warnings
            if len(candidate_rows) > ONOFFICE_INTERSECTION_MAX_CANDIDATES:
                raise HTTPException(
                    status_code=422,
                    detail="intersection candidate limit exceeded",
                )

            intersections: dict[tuple[str, str, str, str], dict] = {}
            invalid_geometry_count = 0
            for candidate_row in candidate_rows:
                candidate_geom = _onoffice_row_geometry(candidate_row, compact)
                if candidate_geom is None:
                    invalid_geometry_count += 1
                    continue
                try:
                    intersects = geom.intersects(candidate_geom)
                except GEOSException:
                    invalid_geometry_count += 1
                    continue
                if not intersects:
                    continue
                candidate = _onoffice_detail_from_row(
                    con,
                    entry,
                    candidate_row,
                    candidate_geom,
                    compact,
                )
                candidate_key = _onoffice_reference_key(
                    _onoffice_result_reference(candidate)
                )
                intersections[candidate_key] = candidate
            if invalid_geometry_count:
                warnings.append(
                    f"{invalid_geometry_count} intersection candidate(s) had invalid geometry and were ignored."
                )
            return (
                detail,
                [intersections[key] for key in sorted(intersections)],
                warnings,
            )
    return None, [], warnings


def _onoffice_structured_address(raw: dict) -> dict | None:
    street, house_number = address_display_parts(raw)
    postal_code = str(raw.get("post_code") or raw.get("postal_code") or "").strip()
    city = str(
        raw.get("city")
        or raw.get("municipality")
        or raw.get("locality")
        or ""
    ).strip()
    country = str(raw.get("country") or raw.get("land") or "").strip()
    label = str(raw.get("label") or "").strip()
    if not any((street, house_number, postal_code, city, country, label)):
        return None
    return {
        "street": street,
        "house_number": house_number,
        "postal_code": postal_code,
        "city": city,
        "country": country,
        "label": label,
    }


def _onoffice_feature_addresses(feature: dict) -> list[dict]:
    raw_addresses = (
        feature.get("addresses")
        if isinstance(feature.get("addresses"), list)
        else []
    )
    if not raw_addresses and feature.get("address"):
        raw_addresses = [{"label": feature.get("address")}]
    addresses: dict[tuple[str, str, str, str, str, str], dict] = {}
    for raw in raw_addresses:
        if not isinstance(raw, dict):
            continue
        address = _onoffice_structured_address(raw)
        if address is None:
            continue
        key = tuple(
            str(address[field] or "").strip().casefold()
            for field in (
                "street",
                "house_number",
                "postal_code",
                "city",
                "country",
                "label",
            )
        )
        addresses[key] = address
    return [addresses[key] for key in sorted(addresses)]


def _onoffice_cadastral_fields(feature: dict) -> dict:
    return {
        "gemarkung": str(feature.get("gemarkung") or "").strip(),
        "gemarkungsnummer": str(feature.get("gemarkungsnummer") or "").strip(),
        "flur": str(feature.get("flur") or "").strip(),
        "flurstueck": str(feature.get("flurstueck") or "").strip(),
        "flurstueckskennzeichen": str(
            feature.get("flurstueckskennzeichen") or ""
        ).strip(),
        "zaehler": str(feature.get("zaehler") or "").strip(),
        "nenner": str(feature.get("nenner") or "").strip(),
    }


def _onoffice_official_area(feature: dict) -> int | float | None:
    value = feature.get("amtliche_flaeche_m2")
    if value is None:
        return None
    try:
        area = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(area) or area < 0:
        return None
    return int(area) if area.is_integer() else area


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


def build_onoffice_selection_payload(
    features: list[dict],
    *,
    selection_metadata: dict[tuple[str, str, str, str], dict] | None = None,
    warnings: list[str] | None = None,
) -> dict:
    selection_metadata = selection_metadata or {}
    parcels = [item for item in features if item.get("kind") == "parcel"]
    buildings = [item for item in features if item.get("kind") == "building"]
    addresses = _onoffice_address_labels(buildings) or _onoffice_address_labels(parcels)
    parcel_labels = [_onoffice_parcel_label(item.get("feature") or {}) for item in parcels]
    parcel_labels = list(dict.fromkeys(label for label in parcel_labels if label))
    parcel_areas = [
        _onoffice_official_area(item.get("feature", {}))
        for item in parcels
    ]
    complete_official_area = bool(parcels) and all(
        area is not None for area in parcel_areas
    )
    structured_addresses: dict[
        tuple[str, str, str, str, str, str],
        dict,
    ] = {}
    structured_parcels: list[dict] = []
    feature_payloads: list[dict] = []
    for item in features:
        feature = item.get("feature") if isinstance(item.get("feature"), dict) else {}
        reference = _onoffice_result_reference(item)
        reference_key = _onoffice_reference_key(reference)
        metadata = selection_metadata.get(reference_key) or {}
        feature_addresses = _onoffice_feature_addresses(feature)
        for address in feature_addresses:
            address_key = tuple(
                str(address[field] or "").strip().casefold()
                for field in (
                    "street",
                    "house_number",
                    "postal_code",
                    "city",
                    "country",
                    "label",
                )
            )
            structured_addresses[address_key] = address
        cadastral = (
            _onoffice_cadastral_fields(feature)
            if item.get("kind") == "parcel"
            else None
        )
        official_area_m2 = (
            _onoffice_official_area(feature)
            if item.get("kind") == "parcel"
            else None
        )
        if cadastral is not None:
            structured_parcels.append(
                {
                    "reference": reference,
                    **cadastral,
                    "official_area_m2": official_area_m2,
                }
            )
        geometry = feature.get("geometry")
        feature_payloads.append(
            {
                **reference,
                "label": item.get("label"),
                "subtitle": item.get("subtitle"),
                "center": item.get("center"),
                "bbox": item.get("bbox"),
                "selection_origin": metadata.get("origin") or "requested",
                "expanded_from": metadata.get("expanded_from") or [],
                "addresses": feature_addresses,
                "cadastral": cadastral,
                "official_area_m2": official_area_m2,
                "geometry": geometry if isinstance(geometry, dict) else None,
                "properties": feature,
            }
        )
    suggested_fields = {
        "openkataster_adresse": "; ".join(addresses),
        "openkataster_flurstuecke": "; ".join(parcel_labels),
        "openkataster_amtliche_flaeche_m2": (
            sum(float(area) for area in parcel_areas if area is not None)
            if complete_official_area
            else None
        ),
    }
    requested_count = sum(
        1
        for item in feature_payloads
        if item["selection_origin"] == "requested"
    )
    return {
        "integration": "onoffice",
        "mode": "selection-payload-preview",
        "write_enabled": False,
        "summary": {
            "parcel_count": len(parcels),
            "building_count": len(buildings),
            "address_count": len(addresses),
            "requested_count": requested_count,
            "expanded_count": len(feature_payloads) - requested_count,
        },
        "suggested_fields": suggested_fields,
        "structured_fields": {
            "addresses": [
                structured_addresses[key]
                for key in sorted(structured_addresses)
            ],
            "parcels": structured_parcels,
            "official_area": {
                "complete": complete_official_area,
                "total_m2": suggested_fields[
                    "openkataster_amtliche_flaeche_m2"
                ],
            },
        },
        "features": feature_payloads,
        "warnings": list(
            dict.fromkeys(
                [
            "This endpoint only prepares an onOffice payload. Writing to onOffice requires an authenticated onOffice adapter.",
                    *(warnings or []),
                ]
            )
        ),
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


@app.post("/internal/v1/api-keys/sync", include_in_schema=False)
def api_v1_admin_api_keys_sync(
    payload: Annotated[dict, Body()],
    _: Annotated[str, Depends(require_admin_key)],
) -> dict:
    raw_keys = payload.get("keys") if isinstance(payload, dict) else []
    if not isinstance(raw_keys, list):
        raise HTTPException(status_code=422, detail="keys must be a list")
    if len(raw_keys) > 10000:
        raise HTTPException(status_code=422, detail="too many keys")

    keys: list[dict] = []
    for raw in raw_keys:
        if not isinstance(raw, dict):
            continue
        sanitized = _sanitize_api_key_record(raw)
        if sanitized:
            keys.append(sanitized)

    output = {
        "version": 1,
        "updated_at": int(time.time()),
        "keys": keys,
    }
    API_KEY_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = API_KEY_STORE_PATH.with_name(f".{API_KEY_STORE_PATH.name}.{os.getpid()}.tmp")
    tmp_path.write_text(json.dumps(output, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp_path.replace(API_KEY_STORE_PATH)
    _API_KEY_STORE_CACHE["mtime"] = None
    _API_KEY_STORE_CACHE["records"] = {}
    return {"ok": True, "count": len(keys)}


@app.post("/internal/v1/api-keys/usage", include_in_schema=False)
def api_v1_admin_api_keys_usage(
    payload: Annotated[dict, Body()],
    _: Annotated[str, Depends(require_admin_key)],
) -> dict:
    raw_hashes = payload.get("token_hashes") if isinstance(payload, dict) else []
    if not isinstance(raw_hashes, list):
        raise HTTPException(status_code=422, detail="token_hashes must be a list")
    month = str(payload.get("month") or _api_usage_month())
    usages: dict[str, int] = {}
    for raw_hash in raw_hashes[:1000]:
        token_hash = str(raw_hash or "").strip().lower()
        if not re.fullmatch(r"[a-f0-9]{64}", token_hash):
            continue
        try:
            if not API_USAGE_DB.exists():
                usages[token_hash] = 0
                continue
            with sqlite3.connect(API_USAGE_DB, timeout=1.5) as con:
                record = _api_key_store_records().get(token_hash) or {"token_hash": token_hash}
                usages[token_hash] = _api_key_usage_count(record, month)
        except sqlite3.Error:
            usages[token_hash] = 0
    return {"month": month, "usages": usages}


@app.post("/api/v1/integrations/onoffice/selection-payload")
def api_v1_onoffice_selection_payload(
    access: Annotated[ApiAccessContext, Depends(RequireScopes("feature:read"))],
    payload: Annotated[dict, Body()],
) -> dict:
    if not access.is_pro:
        raise HTTPException(status_code=403, detail="pro access required")
    expand_intersections = payload.get("expand_intersections", False)
    if not isinstance(expand_intersections, bool):
        raise HTTPException(
            status_code=422,
            detail="expand_intersections must be a boolean",
        )
    raw_features = payload.get("features") or payload.get("selection") or []
    if not isinstance(raw_features, list):
        raise HTTPException(status_code=422, detail="features must be a list")
    if len(raw_features) > ONOFFICE_SELECTION_MAX_FEATURES:
        raise HTTPException(status_code=422, detail="selection is too large")

    requested_features: list[dict] = []
    requested_keys: set[tuple[str, str, str, str]] = set()
    raw_keys: set[tuple[str, str, str, str]] = set()
    expansion_candidates: dict[tuple[str, str, str, str], dict] = {}
    expansion_sources: dict[
        tuple[str, str, str, str],
        dict[tuple[str, str, str, str], dict],
    ] = {}
    missing: list[dict] = []
    warnings: list[str] = []
    for raw in raw_features:
        if not isinstance(raw, dict):
            continue
        ref = _onoffice_feature_reference(raw)
        if not ref["state"] or not ref["source_db"] or not ref["gml_id"]:
            missing.append({**ref, "reason": "incomplete reference"})
            continue
        raw_key = _onoffice_reference_key(ref)
        if raw_key in raw_keys:
            continue
        raw_keys.add(raw_key)
        detail, intersections, detail_warnings = resolve_onoffice_selection_feature(
            ref,
            expand_intersections=expand_intersections,
        )
        warnings.extend(detail_warnings)
        if not detail:
            missing.append({**ref, "reason": "feature not found"})
            continue
        actual_reference = _onoffice_result_reference(detail)
        actual_key = _onoffice_reference_key(actual_reference)
        if actual_key not in requested_keys:
            requested_keys.add(actual_key)
            requested_features.append(detail)
        for candidate in intersections:
            candidate_reference = _onoffice_result_reference(candidate)
            candidate_key = _onoffice_reference_key(candidate_reference)
            expansion_candidates[candidate_key] = candidate
            sources = expansion_sources.setdefault(candidate_key, {})
            sources[actual_key] = actual_reference

    expanded_features = [
        expansion_candidates[key]
        for key in sorted(expansion_candidates)
        if key not in requested_keys
    ]
    if (
        len(requested_features) + len(expanded_features)
        > ONOFFICE_SELECTION_MAX_FEATURES
    ):
        raise HTTPException(
            status_code=422,
            detail="expanded selection is too large",
        )

    selection_metadata: dict[tuple[str, str, str, str], dict] = {
        key: {"origin": "requested", "expanded_from": []}
        for key in requested_keys
    }
    for candidate in expanded_features:
        candidate_key = _onoffice_reference_key(
            _onoffice_result_reference(candidate)
        )
        sources = expansion_sources.get(candidate_key, {})
        selection_metadata[candidate_key] = {
            "origin": "intersection",
            "expanded_from": [
                sources[source_key]
                for source_key in sorted(sources)
            ],
        }
    features = [*requested_features, *expanded_features]
    response = build_onoffice_selection_payload(
        features,
        selection_metadata=selection_metadata,
        warnings=warnings,
    )
    response["expand_intersections"] = expand_intersections
    response["missing"] = missing
    return response


@app.api_route("/api/v1/features/point", methods=["GET", "HEAD"])
def api_v1_features_at_point(
    request: Request,
    access: Annotated[ApiAccessContext, Depends(RequireScopes("feature:read"))],
    lon: Annotated[float, Query(ge=-180, le=180)],
    lat: Annotated[float, Query(ge=-90, le=90)],
    dataset: str = VIRTUAL_GERMANY_DATASET,
    analytics_id: str | None = None,
    analytics_scope: str | None = None,
) -> dict:
    analytics_started = _search_analytics_started(request, analytics_id, analytics_scope, {"map_selection"})
    if not is_virtual_germany_dataset(dataset):
        try:
            get_dataset(dataset)
        except HTTPException:
            if not feature_db_entries_for_dataset(dataset):
                raise
    payload = features_at_point_for_dataset(dataset, lon, lat)
    payload["access"] = access.mode
    return _record_search_analytics(
        started_at=analytics_started,
        scope=analytics_scope,
        query_text="Kartenauswahl",
        state="" if is_virtual_germany_dataset(dataset) else dataset,
        payload=payload,
        access_mode=access.mode,
    )


@app.api_route("/api/v1/features/point-preview", methods=["GET", "HEAD"])
def api_v1_features_at_point_preview(
    request: Request,
    access: Annotated[ApiAccessContext, Depends(RequireScopes("feature:preview"))],
    lon: Annotated[float, Query(ge=-180, le=180)],
    lat: Annotated[float, Query(ge=-90, le=90)],
    dataset: str = VIRTUAL_GERMANY_DATASET,
    analytics_id: str | None = None,
    analytics_scope: str | None = None,
) -> dict:
    analytics_started = _search_analytics_started(request, analytics_id, analytics_scope, {"map_selection"})
    if not is_virtual_germany_dataset(dataset):
        try:
            get_dataset(dataset)
        except HTTPException:
            if not feature_db_entries_for_dataset(dataset):
                raise
    payload = features_at_point_for_dataset(dataset, lon, lat)
    parcels = [preview for item in payload["parcels"] if (preview := feature_preview_item(item, "parcel"))]
    buildings = [preview for item in payload["buildings"] if (preview := feature_preview_item(item, "building"))]
    result = {
        "access": "free",
        "lon": lon,
        "lat": lat,
        "count": len(parcels) + len(buildings),
        "parcels": parcels,
        "buildings": buildings,
    }
    return _record_search_analytics(
        started_at=analytics_started,
        scope=analytics_scope,
        query_text="Kartenauswahl",
        state="" if is_virtual_germany_dataset(dataset) else dataset,
        payload=result,
        access_mode=access.mode,
    )


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


def _wms_tile(
    state_slug: str,
    z: int,
    x: int,
    y: int,
    *,
    configs: dict[str, dict],
    service_name: str,
    cache_namespace: str = "",
) -> Response:
    if state_slug not in configs:
        raise HTTPException(status_code=404, detail=f"{service_name} not configured")
    if z < 0 or z > 22 or x < 0 or y < 0 or x >= 2**z or y >= 2**z:
        raise HTTPException(status_code=400, detail="Invalid tile coordinate")

    # Every configured upstream currently advertises Web Mercator. Requesting
    # the native XYZ bounds avoids reprojection seams between adjacent tiles.
    config = {**configs[state_slug], "crs": "EPSG:3857"}
    try:
        min_zoom = max(0, int(config.get("minzoom", 0)))
        max_zoom = min(22, int(config.get("maxzoom", 22)))
    except (TypeError, ValueError):
        min_zoom, max_zoom = 0, 22
    if z < min_zoom or z > max_zoom:
        raise HTTPException(status_code=400, detail=f"{service_name} is unavailable at this zoom")
    layer = config.get("layer")
    if not layer:
        raise HTTPException(status_code=404, detail=f"{service_name} layer not configured")

    image_format = str(config.get("format", "image/png"))
    media_type = _luftbild_media_type(image_format)
    try:
        tile_size = max(256, min(2048, int(config.get("tile_size", LUFTBILD_TILE_SIZE))))
    except (TypeError, ValueError):
        tile_size = LUFTBILD_TILE_SIZE
    try:
        output_dpi = max(0, min(600, int(config.get("dpi", 0))))
    except (TypeError, ValueError):
        output_dpi = 0
    try:
        upstream_timeout = max(1.0, min(30.0, float(config.get("timeout", 20))))
    except (TypeError, ValueError):
        upstream_timeout = 20.0
    try:
        upstream_attempts = max(1, min(3, int(config.get("attempts", 1))))
    except (TypeError, ValueError):
        upstream_attempts = 1

    cache_state = f"{cache_namespace}-{state_slug}" if cache_namespace else state_slug
    cache_layer = str(layer)
    if cache_namespace:
        cache_layer = "__".join(
            (
                str(config.get("revision") or "v1"),
                str(layer),
                str(config.get("styles") or "default"),
                str(config.get("version") or "1.3.0"),
                "transparent" if config.get("transparent") else "opaque",
                f"dpi-{output_dpi}" if output_dpi else "dpi-default",
            )
        )
    cache_path = _luftbild_cache_path(
        cache_state,
        cache_layer,
        str(config["crs"]),
        z,
        x,
        y,
        tile_size=tile_size,
        image_format=image_format,
    )
    try:
        cache_ttl_seconds = max(0, int(config.get("cache_ttl_seconds", 0)))
    except (TypeError, ValueError):
        cache_ttl_seconds = 0
    cache_control = str(config.get("cache_control") or "public, max-age=604800, immutable")
    cache_is_fresh = False
    if cache_path.exists():
        try:
            cache_is_fresh = not cache_ttl_seconds or time.time() - cache_path.stat().st_mtime <= cache_ttl_seconds
        except OSError:
            cache_is_fresh = False
    if cache_is_fresh:
        return FileResponse(
            cache_path,
            media_type=media_type,
            headers={
                "Cache-Control": cache_control,
                "X-OpenKataster-Cache": "HIT",
            },
        )

    bbox, _center_lat = _luftbild_wms_bbox(config, z, x, y)
    params = {
        "SERVICE": "WMS",
        "REQUEST": "GetMap",
        "VERSION": config.get("version", "1.3.0"),
        "LAYERS": layer,
        "STYLES": str(config.get("styles") or ""),
        "FORMAT": image_format,
        "TRANSPARENT": "true" if config.get("transparent") else "false",
        "CRS": config["crs"],
        "BBOX": ",".join(f"{value:.3f}" for value in bbox),
        "WIDTH": str(tile_size),
        "HEIGHT": str(tile_size),
    }
    if output_dpi:
        # XtraServer scales labels and cartographic symbols through the WMS
        # DPI parameter. The XYZ BBOX and logical 512 px MapLibre tile stay
        # unchanged, so this improves screen legibility without changing the
        # map position, zoom level or feature geometry.
        params["DPI"] = str(output_dpi)
    url = f"{config['url']}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": "OpenKataster/1.0"})
    data = b""
    content_type = ""
    for attempt_index in range(upstream_attempts):
        try:
            with urllib.request.urlopen(request, timeout=upstream_timeout) as response:
                content_type = response.headers.get("Content-Type", "")
                data = response.read()
            break
        except urllib.error.HTTPError as exc:
            if attempt_index + 1 < upstream_attempts and exc.code in {429, 500, 502, 503, 504}:
                continue
            if cache_path.exists():
                return FileResponse(
                    cache_path,
                    media_type=media_type,
                    headers={"Cache-Control": "public, max-age=300", "X-OpenKataster-Cache": "STALE"},
                )
            raise HTTPException(
                status_code=502,
                detail=f"{service_name} WMS error: {exc.code}",
                headers={"Cache-Control": "no-store"},
            ) from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException) as exc:
            if attempt_index + 1 < upstream_attempts:
                continue
            if cache_path.exists():
                return FileResponse(
                    cache_path,
                    media_type=media_type,
                    headers={"Cache-Control": "public, max-age=300", "X-OpenKataster-Cache": "STALE"},
                )
            raise HTTPException(
                status_code=502,
                detail=f"{service_name} WMS unavailable",
                headers={"Cache-Control": "no-store"},
            ) from exc

    try:
        max_response_bytes = max(1024, min(32 * 1024 * 1024, int(config.get("max_response_bytes", 10 * 1024 * 1024))))
    except (TypeError, ValueError):
        max_response_bytes = 10 * 1024 * 1024
    if len(data) > max_response_bytes:
        raise HTTPException(
            status_code=502,
            detail=f"{service_name} WMS returned an oversized image",
            headers={"Cache-Control": "no-store"},
        )
    if not data or not data.startswith((b"\x89PNG", b"\xff\xd8", b"GIF")):
        raise HTTPException(
            status_code=502,
            detail=f"{service_name} WMS returned no image",
            headers={"Cache-Control": "no-store"},
        )

    _write_luftbild_cache(cache_path, data)
    return Response(
        content=data,
        media_type=media_type,
        headers={
            "Cache-Control": cache_control,
            "X-OpenKataster-Cache": "MISS",
        },
    )


@app.api_route("/luftbild/{state_slug}/{z}/{x}/{y}.png", methods=["GET", "HEAD"])
def luftbild_tile(state_slug: str, z: int, x: int, y: int) -> Response:
    return _wms_tile(
        state_slug,
        z,
        x,
        y,
        configs=LUFTBILD_WMS_CONFIGS,
        service_name="Luftbild",
    )


@app.api_route("/katasterbild/{state_slug}/{z}/{x}/{y}.png", methods=["GET", "HEAD"])
def katasterbild_tile(state_slug: str, z: int, x: int, y: int) -> Response:
    return _wms_tile(
        state_slug,
        z,
        x,
        y,
        configs=KATASTER_WMS_CONFIGS,
        service_name="Katasterbild",
        cache_namespace="katasterbild",
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
):
    if not is_virtual_germany_dataset(dataset):
        get_dataset(dataset)
    query = urllib.parse.urlencode(dict(request.query_params), doseq=True)
    target = f"/embed/{dataset}" + (f"?{query}" if query else "")
    return RedirectResponse(url=target, status_code=308)


def _canonical_embed_redirect(
    request: Request,
    dataset: str,
    updates: dict[str, str | None],
    *,
    status_code: int = 307,
) -> RedirectResponse:
    params = dict(request.query_params)
    for key, value in updates.items():
        if value is None:
            params.pop(key, None)
        else:
            params[key] = value
    query = urllib.parse.urlencode(params, doseq=True)
    return RedirectResponse(
        url=f"/embed/{dataset}" + (f"?{query}" if query else ""),
        status_code=status_code,
    )


def _viewer_app_response(claims: dict) -> HTMLResponse:
    index_path = VIEWER_ROOT / "viewer-app" / "index.html"
    if not index_path.is_file():
        raise HTTPException(status_code=503, detail="viewer app is not deployed")
    headers = {
        "Cache-Control": "no-store",
        "Referrer-Policy": "strict-origin",
        "X-Content-Type-Options": "nosniff",
    }
    origin = str(claims.get("origin") or "")
    if origin:
        headers["Content-Security-Policy"] = f"frame-ancestors {origin}"
    return HTMLResponse(content=index_path.read_text(encoding="utf-8"), headers=headers)


@app.api_route("/embed/onoffice", methods=["GET", "HEAD"])
def embed_onoffice_viewer(
    request: Request,
    session: Annotated[str | None, Query()] = None,
):
    if session:
        claims = _verify_embed_session(session)
        if not claims or claims.get("mode") != "onoffice":
            raise HTTPException(status_code=403, detail="valid onOffice embed session required")
    return _canonical_embed_redirect(
        request,
        VIRTUAL_GERMANY_DATASET,
        {"session": session, "surface": "embed"},
        status_code=308,
    )


@app.api_route("/embed/{dataset}", methods=["GET", "HEAD"])
def embed_viewer(
    request: Request,
    dataset: str,
    token: Annotated[str | None, Query()] = None,
    session: Annotated[str | None, Query()] = None,
):
    if not is_virtual_germany_dataset(dataset):
        get_dataset(dataset)
    supplied = session or token
    claims = _verify_embed_session(supplied) if supplied else None
    if supplied and not claims:
        raise HTTPException(status_code=401, detail="invalid or expired viewer session")
    if claims and claims.get("dataset") and claims.get("dataset") != dataset:
        raise HTTPException(status_code=403, detail="viewer session is not valid for this dataset")
    if not supplied:
        return _canonical_embed_redirect(
            request,
            dataset,
            {
                "token": _new_viewer_session(subject="public-embed"),
                "session": None,
                "iframe": "1",
                "surface": "embed",
            },
        )
    if session or request.query_params.get("iframe") != "1":
        surface = "planner" if request.query_params.get("surface") == "planner" else "embed"
        return _canonical_embed_redirect(
            request,
            dataset,
            {"token": supplied, "session": None, "iframe": "1", "surface": surface},
        )
    return _viewer_app_response(claims or {})



@app.post("/admin/volume-upload-session/{state_slug}")
async def create_volume_upload_session(
    state_slug: str,
    _: Annotated[str, Depends(require_openkataster_admin_token)],
    request: Request,
    version: Annotated[str, Query(min_length=1)],
    bundesland: Annotated[str | None, Query()] = None,
    base_version: Annotated[str | None, Query()] = None,
) -> dict:
    state_slug = _canonical_volume_state_slug(state_slug)
    _prune_stale_volume_upload_sessions(state_slug)
    version_name = _safe_version_name(version)
    payload = await request.json()
    payload_base_version = payload.get("base_version")
    if payload_base_version is not None:
        payload_base_version = _safe_version_name(str(payload_base_version))
    if base_version is not None:
        base_version = _safe_version_name(base_version)
    if base_version is not None and payload_base_version is not None and base_version != payload_base_version:
        raise HTTPException(status_code=400, detail="base_version differs between query and upload payload")
    selected_base_version = base_version or payload_base_version
    if selected_base_version == version_name:
        raise HTTPException(status_code=400, detail="base_version and target version must differ")
    files = _validate_volume_filenames(
        list(payload.get("files") or []),
        allow_subset=selected_base_version is not None,
    )
    _volume_destination_must_be_available(state_slug, version_name)
    if selected_base_version is not None:
        _validated_volume_base_dir(state_slug, selected_base_version)

    upload_dir = _volume_upload_dir(state_slug, version_name)
    display_name = bundesland or state_slug.replace("-", " ").title()
    existing_manifest = None
    if upload_dir.is_dir() and _volume_upload_session_manifest_path(upload_dir).is_file():
        existing_manifest = _read_volume_upload_session_manifest(
            upload_dir,
            state_slug=state_slug,
            version_name=version_name,
        )
        if existing_manifest["base_version"] != selected_base_version or existing_manifest["files"] != files:
            raise HTTPException(status_code=409, detail="upload session already exists with a different file selection or size")
    elif upload_dir.is_dir() and selected_base_version is not None and any(upload_dir.iterdir()):
        raise HTTPException(status_code=409, detail="legacy upload session cannot be reused for a partial upload; delete it first")

    upload_dir.mkdir(parents=True, exist_ok=True)
    if existing_manifest is None:
        existing_manifest = _write_volume_upload_session_manifest(
            upload_dir,
            state_slug=state_slug,
            version_name=version_name,
            bundesland=display_name,
            base_version=selected_base_version,
            files=files,
        )
    session_files = [_volume_upload_file_status(upload_dir, item) for item in files]
    return {
        "status": "success",
        "target": "tile-volume",
        "state_slug": state_slug,
        "bundesland": existing_manifest.get("bundesland") or display_name,
        "version_name": version_name,
        "mode": existing_manifest["mode"],
        "base_version": existing_manifest["base_version"],
        "part_size": VOLUME_UPLOAD_PART_BYTES,
        "resume": any(int(item.get("uploaded_bytes") or 0) > 0 for item in session_files),
        "files": session_files,
    }


@app.get("/admin/volume-upload-sessions/{state_slug}")
def list_volume_upload_sessions(
    state_slug: str,
    _: Annotated[str, Depends(require_openkataster_admin_token)],
) -> dict:
    state_slug = _canonical_volume_state_slug(state_slug)
    return {
        "status": "success",
        "target": "tile-volume",
        "state_slug": state_slug,
        "sessions": _volume_upload_sessions(state_slug),
    }


@app.get("/admin/volume-version-files/{state_slug}")
def list_volume_version_files(
    state_slug: str,
    _: Annotated[str, Depends(require_openkataster_admin_token)],
    version: Annotated[str, Query(min_length=1)],
) -> dict:
    state_slug = _canonical_volume_state_slug(state_slug)
    version_name = _safe_version_name(version)
    version_dir = _volume_version_dir(state_slug, version_name)
    if not version_dir.is_dir():
        raise HTTPException(status_code=404, detail="tile version not found")
    files = []
    for filename in sorted(VOLUME_REQUIRED_FILES):
        file_path = version_dir / filename
        present = file_path.is_file()
        stat = file_path.stat() if present else None
        files.append({
            "filename": filename,
            "present": present,
            "size_bytes": stat.st_size if stat else None,
            "modified_at": (
                time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(stat.st_mtime))
                if stat
                else None
            ),
        })
    try:
        _validate_volume_state_dir(version_dir)
        complete = True
    except HTTPException:
        complete = False
    return {
        "status": "success",
        "target": "tile-volume",
        "state_slug": state_slug,
        "version_name": version_name,
        "complete": complete,
        "files": files,
    }


@app.delete("/admin/volume-upload-session/{state_slug}")
async def delete_volume_upload_session(
    state_slug: str,
    _: Annotated[str, Depends(require_openkataster_admin_token)],
    version: Annotated[str, Query(min_length=1)],
) -> dict:
    state_slug = _canonical_volume_state_slug(state_slug)
    version_name = _safe_version_name(version)
    upload_dir = _volume_upload_dir(state_slug, version_name)
    if not upload_dir.is_dir():
        raise HTTPException(status_code=404, detail="upload session not found")
    async with _locked_volume_upload_session(upload_dir):
        shutil.rmtree(upload_dir)
    state_dir = upload_dir.parent
    try:
        state_dir.rmdir()
    except OSError:
        pass
    return {
        "status": "success",
        "target": "tile-volume",
        "state_slug": state_slug,
        "version_name": version_name,
        "deleted": True,
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
    state_slug = _canonical_volume_state_slug(state_slug)
    version_name = _safe_version_name(version)
    safe_filename = os.path.basename(filename)
    if safe_filename != filename or safe_filename not in VOLUME_REQUIRED_FILES:
        raise HTTPException(status_code=400, detail=f"unexpected tile file: {filename}")
    if end <= start or end > total_size:
        raise HTTPException(status_code=400, detail="invalid upload byte range")
    expected_chunk_size = end - start
    if expected_chunk_size > VOLUME_UPLOAD_MAX_PART_BYTES:
        raise HTTPException(status_code=413, detail="volume upload part too large")

    _volume_destination_must_be_available(state_slug, version_name)
    upload_dir = _volume_upload_dir(state_slug, version_name)
    if not upload_dir.is_dir():
        raise HTTPException(status_code=409, detail="upload session not found; create the upload session first")
    manifest = _read_volume_upload_session_manifest(
        upload_dir,
        state_slug=state_slug,
        version_name=version_name,
    )
    expected_sizes = {item["filename"]: int(item["size_bytes"]) for item in manifest["files"]}
    if safe_filename not in expected_sizes:
        raise HTTPException(status_code=409, detail=f"file is not part of this upload session: {safe_filename}")
    if total_size != expected_sizes[safe_filename]:
        raise HTTPException(
            status_code=409,
            detail=f"upload size does not match session for {safe_filename}: expected {expected_sizes[safe_filename]}, got {total_size}",
        )
    target_path = upload_dir / f"{safe_filename}.partial"
    final_path = upload_dir / safe_filename
    async with _locked_volume_upload_session(upload_dir):
        # The destination may have been finalized while this request waited for
        # another worker holding the session lock.
        _volume_destination_must_be_available(state_slug, version_name)
        if not upload_dir.is_dir():
            raise HTTPException(status_code=409, detail="upload session is no longer available")
        if final_path.is_file():
            final_size = final_path.stat().st_size
            if final_size != total_size:
                raise HTTPException(status_code=409, detail=f"completed upload size mismatch for {safe_filename}")
            return {"status": "success", "already_uploaded": True, "uploaded_bytes": final_size, "size_bytes": 0}

        current_size = target_path.stat().st_size if target_path.exists() else 0
        if current_size > total_size:
            raise HTTPException(status_code=409, detail=f"partial upload exceeds session size for {safe_filename}")
        if current_size >= end:
            return {"status": "success", "already_uploaded": True, "uploaded_bytes": current_size, "size_bytes": 0}
        if current_size < start:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "upload_offset_mismatch",
                    "message": f"upload offset mismatch for {safe_filename}: expected start {current_size}, got {start}",
                    "filename": safe_filename,
                    "expected_start": current_size,
                    "received_start": start,
                    "end": end,
                    "total_size": total_size,
                },
                headers={"Upload-Offset": str(current_size)},
            )

        recovered_partial_part = current_size > start
        if recovered_partial_part:
            # A forced process stop can leave a prefix of this exact range on
            # disk. The retry still contains the complete range, so roll that
            # uncommitted prefix back and write the part once under the lock.
            with target_path.open("r+b") as handle:
                handle.truncate(start)

        written = 0
        try:
            with target_path.open("ab") as handle:
                async for chunk in request.stream():
                    if not chunk:
                        continue
                    written += len(chunk)
                    if written > expected_chunk_size:
                        handle.truncate(start)
                        handle.flush()
                        os.fsync(handle.fileno())
                        raise HTTPException(status_code=413, detail="upload part exceeded declared range")
                    await asyncio.to_thread(handle.write, chunk)
                if written != expected_chunk_size:
                    handle.truncate(start)
                    handle.flush()
                    os.fsync(handle.fileno())
                    raise HTTPException(status_code=400, detail=f"incomplete upload part: expected {expected_chunk_size}, got {written}")
                handle.flush()
                await asyncio.to_thread(os.fsync, handle.fileno())
        except HTTPException:
            raise
        except asyncio.CancelledError:
            try:
                with target_path.open("ab") as handle:
                    handle.truncate(start)
                    handle.flush()
                    os.fsync(handle.fileno())
            except Exception:
                pass
            raise
        except Exception as exc:
            try:
                with target_path.open("ab") as handle:
                    handle.truncate(start)
                    handle.flush()
                    os.fsync(handle.fileno())
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
            "recovered_partial_part": recovered_partial_part,
        }


@app.post("/admin/complete-volume-upload/{state_slug}")
async def complete_volume_upload(
    state_slug: str,
    _: Annotated[str, Depends(require_openkataster_admin_token)],
    request: Request,
    version: Annotated[str, Query(min_length=1)],
    bundesland: Annotated[str | None, Query()] = None,
    base_version: Annotated[str | None, Query()] = None,
) -> dict:
    state_slug = _canonical_volume_state_slug(state_slug)
    version_name = _safe_version_name(version)
    payload = await request.json()
    payload_base_version = payload.get("base_version")
    if payload_base_version is not None:
        payload_base_version = _safe_version_name(str(payload_base_version))
    if base_version is not None:
        base_version = _safe_version_name(base_version)
    if base_version is not None and payload_base_version is not None and base_version != payload_base_version:
        raise HTTPException(status_code=400, detail="base_version differs between query and upload payload")
    selected_base_version = base_version or payload_base_version
    if selected_base_version == version_name:
        raise HTTPException(status_code=400, detail="base_version and target version must differ")
    files = _validate_volume_filenames(
        list(payload.get("files") or []),
        allow_subset=selected_base_version is not None,
    )
    version_dir = _volume_destination_must_be_available(state_slug, version_name)
    upload_dir = _volume_upload_dir(state_slug, version_name)
    if not upload_dir.is_dir():
        raise HTTPException(status_code=400, detail="upload session not found")
    async with _locked_volume_upload_session(upload_dir):
        manifest = _read_volume_upload_session_manifest(
            upload_dir,
            state_slug=state_slug,
            version_name=version_name,
        )
        if manifest["base_version"] != selected_base_version or manifest["files"] != files:
            raise HTTPException(status_code=409, detail="completion payload does not match upload session")

        base_dir = None
        if selected_base_version is not None:
            base_dir = _validated_volume_base_dir(state_slug, selected_base_version)

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

        uploaded_names = {item["filename"] for item in files}
        inherited_storage = {}
        for filename in sorted(VOLUME_REQUIRED_FILES - uploaded_names):
            if base_dir is None:
                raise HTTPException(status_code=400, detail=f"missing uploaded file: {filename}")
            (upload_dir / f"{filename}.partial").unlink(missing_ok=True)
            inherited_storage[filename] = _inherit_volume_file(base_dir / filename, upload_dir / filename)

        validation = _validate_volume_state_dir(upload_dir)
        display_name = bundesland or manifest.get("bundesland") or state_slug.replace("-", " ").title()
        _write_volume_upload_manifest(
            upload_dir,
            state_slug=state_slug,
            bundesland=display_name,
            version_name=version_name,
            uploaded_files=files,
            base_version=selected_base_version,
            inherited_storage=inherited_storage,
        )

        version_dir.parent.mkdir(parents=True, exist_ok=True)
        tmp_version_dir = version_dir.with_name(f".{version_dir.name}.{os.getpid()}.tmp")
        shutil.rmtree(tmp_version_dir, ignore_errors=True)
        os.replace(upload_dir, tmp_version_dir)
        if os.path.lexists(version_dir):
            os.replace(tmp_version_dir, upload_dir)
            raise HTTPException(status_code=409, detail=f"tile version already exists: {version_name}")
        try:
            os.rename(tmp_version_dir, version_dir)
        except OSError as exc:
            if not upload_dir.exists() and tmp_version_dir.exists():
                os.replace(tmp_version_dir, upload_dir)
            if os.path.lexists(version_dir):
                raise HTTPException(status_code=409, detail=f"tile version already exists: {version_name}") from exc
            raise HTTPException(status_code=500, detail=f"could not publish tile version: {exc}") from exc
        _volume_upload_session_manifest_path(version_dir).unlink(missing_ok=True)
    (version_dir / ".upload.lock").unlink(missing_ok=True)
    _clear_data_caches()
    return {
        "status": "success",
        "target": "tile-volume",
        "state_slug": state_slug,
        "bundesland": display_name,
        "version_name": version_name,
        "mode": manifest["mode"],
        "base_version": selected_base_version,
        "uploaded_files": sorted(uploaded_names),
        "inherited_files": sorted(VOLUME_REQUIRED_FILES - uploaded_names),
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


@app.get("/docs/embed", response_class=HTMLResponse, include_in_schema=False)
def embed_documentation() -> HTMLResponse:
    return HTMLResponse(
        r'''<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>OpenKataster Embed</title>
  <style>
    :root { color-scheme: light; --orange:#f86d14; --line:#e7e7e4; --muted:#666b70; }
    * { box-sizing:border-box; }
    body { margin:0; color:#202326; background:#fff; font:15px/1.55 "IBM Plex Sans",Inter,Arial,sans-serif; }
    header { height:58px; display:flex; align-items:center; border-bottom:1px solid var(--line); padding:0 24px; font-weight:600; }
    header span { color:var(--orange); margin-right:7px; }
    main { width:min(860px,calc(100% - 32px)); margin:42px auto 80px; }
    h1 { font-size:30px; line-height:1.2; margin:0 0 12px; font-weight:600; }
    h2 { font-size:18px; margin:42px 0 10px; font-weight:600; }
    p { margin:8px 0; color:var(--muted); }
    code,pre { font:13px/1.55 ui-monospace,SFMono-Regular,Menlo,monospace; }
    pre { padding:16px; overflow:auto; background:#f7f7f5; border:1px solid var(--line); border-radius:6px; color:#26282a; }
    table { width:100%; border-collapse:collapse; margin-top:10px; }
    th,td { text-align:left; padding:10px 8px; border-bottom:1px solid var(--line); vertical-align:top; }
    th { font-size:12px; color:var(--muted); font-weight:500; }
    a { color:#303438; text-underline-offset:3px; }
    .note { border-left:2px solid var(--orange); padding:2px 0 2px 14px; margin:18px 0; }
  </style>
</head>
<body>
  <header><span>▧</span> OpenKataster Developer</header>
  <main>
    <h1>Karte einbetten</h1>
    <p>Die OpenKataster-Karte und die Suche können ohne Konto und ohne API-Key kostenlos eingebettet werden.</p>

    <h2>Kostenlos einbetten</h2>
    <pre><code>&lt;iframe
  id="openkataster-map"
  src="https://tiles.openkataster.de/embed/deutschland"
  title="OpenKataster"
  style="width:100%;height:720px;border:0"
&gt;&lt;/iframe&gt;</code></pre>
    <p>Das kostenlose Embed enthält Karte, Layer und Suche mit OpenKataster-Branding. Es benötigt kein API-Kontingent. Vollständige Objektdaten, Messwerte und Exporte bleiben geschützte Funktionen.</p>

    <h2>Geschützte Funktionen freischalten</h2>
    <p>Für Pro- und Exportfunktionen verbindet eine kurzlebige Embed-Session ein Developer-Projekt mit einer freigeschalteten Domain. Der geheime API-Key bleibt auf Ihrem Server.</p>

    <h2>1. Session serverseitig erstellen</h2>
    <pre><code>curl -X POST https://tiles.openkataster.de/api/v1/embed/sessions \
  -H "Authorization: Bearer OK_IHR_PROJEKT_KEY" \
  -H "Content-Type: application/json" \
  -d '{"origin":"https://www.beispiel.de","dataset":"deutschland","mode":"standard"}'</code></pre>
    <div class="note"><p>Der Projekt-Key darf nicht in HTML, JavaScript oder einer iframe-URL veröffentlicht werden.</p></div>

    <h2>2. Zurückgegebene URL einsetzen</h2>
    <pre><code>&lt;iframe
  id="openkataster-map"
  src="EMBED_URL_AUS_DER_ANTWORT"
  title="OpenKataster"
  style="width:100%;height:720px;border:0"
&gt;&lt;/iframe&gt;</code></pre>
    <p>Session-Tokens sind kurzlebig und an die im Projekt freigeschaltete Origin gebunden.</p>

    <h2>Nachrichten vom Viewer</h2>
    <table>
      <thead><tr><th>Typ</th><th>Inhalt</th></tr></thead>
      <tbody>
        <tr><td><code>openkataster:ready</code></td><td>Viewer und Karte sind einsatzbereit.</td></tr>
        <tr><td><code>openkataster:selection</code></td><td>Aktuelle Gebäude- und Flurstücksauswahl.</td></tr>
        <tr><td><code>openkataster:state</code></td><td>Kartenposition und Auswahl nach einer Statusabfrage.</td></tr>
      </tbody>
    </table>
    <pre><code>window.addEventListener("message", (event) =&gt; {
  if (event.origin !== "https://tiles.openkataster.de") return;
  if (event.data?.type === "openkataster:selection") {
    console.log(event.data.selection);
  }
});</code></pre>

    <h2>Befehle an den Viewer</h2>
    <table>
      <thead><tr><th>Typ</th><th>Parameter</th></tr></thead>
      <tbody>
        <tr><td><code>openkataster:set-view</code></td><td><code>center: [lon, lat]</code>, <code>zoom</code></td></tr>
        <tr><td><code>openkataster:search-address</code></td><td><code>address: { place, street, house_number }</code></td></tr>
        <tr><td><code>openkataster:set-layers</code></td><td><code>layers: { aerial: true, alkis: true }</code></td></tr>
        <tr><td><code>openkataster:clear-selection</code></td><td>Keine weiteren Parameter.</td></tr>
        <tr><td><code>openkataster:request-state</code></td><td>Antwortet mit <code>openkataster:state</code>.</td></tr>
      </tbody>
    </table>
    <pre><code>const frame = document.getElementById("openkataster-map");
frame.contentWindow.postMessage({
  type: "openkataster:set-view",
  version: 1,
  center: [9.9937, 53.5511],
  zoom: 18
}, "https://tiles.openkataster.de");</code></pre>

    <h2>REST-API</h2>
    <p>Die interaktive REST-Referenz liegt unter <a href="/api/docs">/api/docs</a>. Die maschinenlesbare Spezifikation steht unter <a href="/api/openapi.json">/api/openapi.json</a>.</p>
  </main>
</body>
</html>'''
    )


PUBLIC_OPENAPI_PATHS = {
    "/health",
    "/api/v1",
    "/api/v1/states",
    "/api/v1/sources",
    "/api/v1/datasets",
    "/api/v1/tilejson/{state}.json",
    "/api/v1/tiles/{state}/{z}/{x}/{y}.mvt",
    "/api/v1/search/address",
    "/api/v1/search/parcel",
    "/api/v1/search/poi",
    "/api/v1/search/{dataset}",
    "/api/v1/suggest/search",
    "/api/v1/suggest/addresses",
    "/api/v1/suggest/places",
    "/api/v1/suggest/streets",
    "/api/v1/suggest/gemarkungen",
    "/api/v1/features/geometry",
    "/api/v1/features/point",
    "/api/v1/features/point-preview",
    "/api/v1/session",
    "/api/v1/embed/sessions",
    "/api/v1/integrations/onoffice/selection-payload",
    "/embed/{dataset}",
}


def public_openapi_schema() -> dict:
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title="OpenKataster API",
        version="1.0.0-preview",
        description=(
            "Versionierte Schnittstelle für Katasterkarten, Suche, Objektinformationen und sichere Einbettungen. "
            "Projekt-Keys werden als Bearer-Token ausschließlich serverseitig verwendet."
        ),
        routes=app.routes,
        tags=[
            {"name": "Embed", "description": "Kurzlebige, domain-gebundene iframe-Sessions."},
            {"name": "Search", "description": "Adress-, Flurstücks- und POI-Suche."},
            {"name": "Features", "description": "Geometrien und fachliche Objektinformationen."},
            {"name": "Map", "description": "Öffentliche Karten- und Quellenressourcen."},
        ],
    )
    schema["paths"] = {path: operations for path, operations in schema.get("paths", {}).items() if path in PUBLIC_OPENAPI_PATHS}
    schema["servers"] = [{"url": PUBLIC_BASE_URL or "https://tiles.openkataster.de"}]
    protected_paths = {
        "/api/v1/search/address",
        "/api/v1/search/parcel",
        "/api/v1/search/poi",
        "/api/v1/search/{dataset}",
        "/api/v1/suggest/search",
        "/api/v1/suggest/addresses",
        "/api/v1/suggest/places",
        "/api/v1/suggest/streets",
        "/api/v1/suggest/gemarkungen",
        "/api/v1/features/point",
        "/api/v1/features/point-preview",
        "/api/v1/embed/sessions",
        "/api/v1/integrations/onoffice/selection-payload",
    }
    hidden_auth_parameters = {"session", "token", "api_key", "x-api-key", "authorization"}
    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            if method.lower() not in {"get", "post", "put", "patch", "delete", "head"} or not isinstance(operation, dict):
                continue
            operation["parameters"] = [
                parameter
                for parameter in operation.get("parameters", [])
                if str(parameter.get("name") or "").lower() not in hidden_auth_parameters
            ]
            if path in protected_paths:
                operation["security"] = [{"OpenKatasterApiKey": []}]

    examples = {
        "/api/v1/session": {
            "access": "partner",
            "authenticated": True,
            "plan": "api_beta",
            "scopes": ["search:basic", "feature:read", "embed:pro"],
        },
        "/api/v1/search/address": {
            "query": "Feldstraße 18 Hildesheim",
            "count": 1,
            "results": [{"label": "Feldstraße 18, 31134 Hildesheim", "center": [9.95, 52.15]}],
        },
        "/api/v1/search/poi": {
            "query": "Hannover Hauptbahnhof",
            "count": 1,
            "results": [
                {
                    "kind": "poi",
                    "poi_id": "osm:n:123",
                    "label": "Hannover Hauptbahnhof",
                    "center": [9.741, 52.377],
                }
            ],
        },
        "/api/v1/features/point-preview": {
            "access": "free",
            "count": 1,
            "parcels": [{"kind": "parcel", "label": "17/3", "state": "niedersachsen"}],
            "buildings": [],
        },
    }
    for path, example in examples.items():
        operation = schema["paths"].get(path, {}).get("get")
        if operation:
            response = operation.setdefault("responses", {}).setdefault("200", {"description": "Erfolgreiche Antwort"})
            response.setdefault("content", {}).setdefault("application/json", {})["example"] = example
    app.openapi_schema = schema
    return schema


app.openapi = public_openapi_schema
