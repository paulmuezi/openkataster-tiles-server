#!/usr/bin/env python3
"""Build the OpenKataster OSM POI search index from one Geofabrik PBF pass.

The importer deliberately keeps OpenStreetMap POIs separate from official
ALKIS/address data. It stores one row per exact OSM object, never merges nearby
objects, and publishes the SQLite file only after schema and integrity checks.
"""

from __future__ import annotations

import argparse
import configparser
import contextlib
import fcntl
import hashlib
import json
import math
import os
import platform
import re
import resource
import shutil
import sqlite3
import sys
import tempfile
import time
import unicodedata
import uuid
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from osgeo import gdal, ogr, osr


SCHEMA_VERSION = 4
FORMAT_NAME = "openkataster-osm-poi-sqlite"
DEFAULT_OSM_CONFIG = Path(__file__).with_name("osmconf-poi.ini")
OSM_ATTRIBUTION = "© OpenStreetMap contributors (ODbL 1.0)"
OSM_COPYRIGHT_URL = "https://www.openstreetmap.org/copyright"

ARS_TO_STATE = {
    "01": ("schleswig-holstein", "Schleswig-Holstein", "schleswig_holstein"),
    "02": ("hamburg", "Hamburg", "hamburg"),
    "03": ("niedersachsen", "Niedersachsen", "niedersachsen"),
    "04": ("bremen", "Bremen", "bremen"),
    "05": ("nordrhein-westfalen", "Nordrhein-Westfalen", "nordrhein_westfalen"),
    "06": ("hessen", "Hessen", "hessen"),
    "07": ("rheinland-pfalz", "Rheinland-Pfalz", "rheinland_pfalz"),
    "08": ("baden-wurttemberg", "Baden-Württemberg", "baden_wuerttemberg"),
    "09": ("bayern", "Bayern", "bayern"),
    "10": ("saarland", "Saarland", "saarland"),
    "11": ("berlin", "Berlin", "berlin"),
    "12": ("brandenburg", "Brandenburg", "brandenburg"),
    "13": ("mecklenburg-vorpommern", "Mecklenburg-Vorpommern", "mecklenburg_vorpommern"),
    "14": ("sachsen", "Sachsen", "sachsen"),
    "15": ("sachsen-anhalt", "Sachsen-Anhalt", "sachsen_anhalt"),
    "16": ("thueringen", "Thüringen", "thueringen"),
}
ALL_STATE_SLUGS = frozenset(row[0] for row in ARS_TO_STATE.values())
STATE_ALIASES = {
    "baden-wuerttemberg": "baden-wurttemberg",
    "baden-württemberg": "baden-wurttemberg",
    "thuringen": "thueringen",
    "thüringen": "thueringen",
}

NAME_FIELDS = (
    "name",
    "name_de",
    "name_en",
    "official_name",
    "short_name",
    "loc_name",
    "alt_name",
    "old_name",
)
CLASS_FIELDS = (
    "amenity",
    "shop",
    "tourism",
    "information",
    "leisure",
    "office",
    "government",
    "diplomatic",
    "craft",
    "club",
    "healthcare",
    "healthcare_speciality",
    "social_facility",
    "historic",
    "public_transport",
    "railway",
    "aeroway",
    "natural",
    "man_made",
    "emergency",
    "landuse",
    "highway",
    "aerialway",
    "waterway",
    "building",
    "religion",
    "denomination",
    "sport",
    "cuisine",
)

# A named feature with a real POI tag remains searchable even when its subtype
# is small infrastructure (for example toilets, recycling or drinking water).
# Only values that explicitly carry no useful classification are ignored.
EXCLUDED_GENERIC_VALUES = {"", "no", "none", "vacant"}
RAILWAY_POIS = {"station", "halt", "tram_stop", "subway_entrance"}
AEROWAY_POIS = {"aerodrome", "terminal", "helipad", "gate"}
NATURAL_POIS = {"peak", "cave_entrance", "spring", "beach", "volcano", "cliff", "geyser"}
MAN_MADE_POIS = {
    "lighthouse",
    "tower",
    "water_tower",
    "windmill",
    "watermill",
    "observatory",
}
EMERGENCY_POIS = {"defibrillator", "phone", "ambulance_station"}
RELIGIOUS_BUILDINGS = {
    "church",
    "cathedral",
    "chapel",
    "mosque",
    "synagogue",
    "temple",
    "shrine",
}
PUBLIC_TRANSPORT_POIS = {"station", "stop_area", "stop_position", "platform"}
FOOD_AMENITIES = {
    "restaurant",
    "fast_food",
    "cafe",
    "pub",
    "bar",
    "biergarten",
    "food_court",
    "ice_cream",
}
HEALTH_AMENITIES = {"pharmacy", "hospital", "clinic", "doctors", "dentist", "veterinary"}
EDUCATION_AMENITIES = {
    "school",
    "kindergarten",
    "university",
    "college",
    "music_school",
    "language_school",
}
TRANSPORT_AMENITIES = {
    "bus_station",
    "car_rental",
    "car_sharing",
    "car_wash",
    "charging_station",
    "ferry_terminal",
    "fuel",
    "parking",
    "taxi",
}
FINANCE_AMENITIES = {"bank", "atm", "bureau_de_change"}
RELIGION_AMENITIES = {"place_of_worship", "grave_yard", "crematorium"}
CULTURE_AMENITIES = {
    "arts_centre",
    "cinema",
    "community_centre",
    "events_venue",
    "library",
    "theatre",
}

TYPE_TERMS = {
    "restaurant": "restaurant gastronomie essen",
    "fast_food": "imbiss fast food essen",
    "cafe": "cafe café kaffee",
    "pub": "kneipe pub",
    "bar": "bar",
    "biergarten": "biergarten",
    "pharmacy": "apotheke pharmacy",
    "hospital": "krankenhaus klinik hospital",
    "clinic": "klinik clinic",
    "doctors": "arzt ärzte praxis",
    "dentist": "zahnarzt praxis",
    "school": "schule",
    "kindergarten": "kindergarten kita",
    "university": "universität universitaet hochschule",
    "college": "hochschule college",
    "library": "bibliothek",
    "townhall": "rathaus",
    "police": "polizei",
    "fire_station": "feuerwehr",
    "post_office": "post postfiliale",
    "bank": "bank",
    "atm": "geldautomat atm",
    "fuel": "tankstelle",
    "parking": "parkplatz parken",
    "car_wash": "waschanlage autowäsche autowaesche",
    "place_of_worship": "kirche gotteshaus gebetsstätte gebetsstaette",
    "grave_yard": "friedhof",
    "cemetery": "friedhof",
    "theatre": "theater",
    "cinema": "kino",
    "museum": "museum",
    "hotel": "hotel",
    "guest_house": "pension gästehaus gaestehaus",
    "hostel": "hostel jugendherberge",
    "supermarket": "supermarkt",
    "bakery": "bäckerei baeckerei",
    "station": "bahnhof station haltestelle",
    "stop_area": "bahnhof haltestelle",
    "halt": "bahnhof haltepunkt",
    "tram_stop": "straßenbahn strassenbahn haltestelle",
    "bus_stop": "bushaltestelle haltestelle",
    "stop_position": "haltestelle",
    "platform": "bahnsteig haltestelle",
    "aerodrome": "flughafen flugplatz",
    "terminal": "terminal flughafen",
    "peak": "gipfel berg",
    "viewpoint": "aussichtspunkt",
    "playground": "spielplatz",
    "sports_centre": "sportzentrum sporthalle",
    "swimming_pool": "schwimmbad",
    "park": "park",
    "castle": "schloss burg",
    "memorial": "denkmal gedenkstätte gedenkstaette",
    "monument": "denkmal monument",
}

CATEGORY_LABELS = {
    "amenity": "Einrichtung",
    "community": "Gemeinschaft",
    "culture": "Kultur",
    "education": "Bildung",
    "emergency": "Notfall",
    "finance": "Finanzen",
    "food": "Gastronomie",
    "healthcare": "Gesundheit",
    "landmark": "Sehenswürdigkeit",
    "leisure": "Freizeit",
    "natural": "Natur",
    "religion": "Religion",
    "retail": "Einkaufen",
    "services": "Dienstleistungen",
    "tourism": "Tourismus",
    "transport": "Verkehr",
}

PLACEHOLDER_NAMES = {"", "fixme", "ja", "kein name", "name", "no", "none", "unknown", "unbekannt", "yes"}
SOURCE_TYPE_CODE = {"n": 0, "w": 1, "r": 2}

CREATE_SCHEMA_SQL = f"""
PRAGMA user_version={SCHEMA_VERSION};
PRAGMA page_size=4096;
PRAGMA journal_mode=DELETE;
PRAGMA synchronous=OFF;
PRAGMA locking_mode=EXCLUSIVE;
PRAGMA temp_store=FILE;
PRAGMA foreign_keys=ON;

CREATE TABLE meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
) WITHOUT ROWID;

CREATE TABLE poi (
    id INTEGER PRIMARY KEY,
    poi_id TEXT NOT NULL UNIQUE,
    osm_type TEXT NOT NULL CHECK (osm_type IN ('n', 'w', 'r')),
    osm_id INTEGER NOT NULL CHECK (osm_id > 0),
    name TEXT NOT NULL CHECK (name <> ''),
    display_source TEXT NOT NULL CHECK (
        display_source IN ('name', 'name_de', 'official_name', 'short_name', 'loc_name', 'name_en', 'brand')
    ),
    name_norm TEXT NOT NULL CHECK (name_norm <> ''),
    search_norm TEXT NOT NULL CHECK (search_norm <> ''),
    aliases TEXT NOT NULL DEFAULT '',
    brand TEXT NOT NULL DEFAULT '',
    operator TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL,
    category_label TEXT NOT NULL DEFAULT '',
    class_key TEXT NOT NULL,
    subtype TEXT NOT NULL,
    category_terms TEXT NOT NULL,
    address TEXT NOT NULL DEFAULT '',
    address_norm TEXT NOT NULL DEFAULT '',
    street TEXT NOT NULL DEFAULT '',
    housenumber TEXT NOT NULL DEFAULT '',
    postcode TEXT NOT NULL DEFAULT '',
    city TEXT NOT NULL DEFAULT '',
    city_norm TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL,
    quality INTEGER NOT NULL CHECK (quality BETWEEN 0 AND 100),
    locality TEXT NOT NULL DEFAULT '',
    locality_source TEXT NOT NULL CHECK (locality_source IN ('osm', 'gn250_nearest', 'none')),
    locality_ags TEXT NOT NULL DEFAULT '',
    locality_distance_m INTEGER,
    state_slug TEXT NOT NULL,
    state_name TEXT NOT NULL,
    lon REAL NOT NULL CHECK (lon BETWEEN 5.0 AND 16.0),
    lat REAL NOT NULL CHECK (lat BETWEEN 47.0 AND 56.0),
    utm_epsg INTEGER NOT NULL CHECK (utm_epsg IN (25832, 25833)),
    easting REAL NOT NULL,
    northing REAL NOT NULL,
    CHECK (poi_id = osm_type || CAST(osm_id AS TEXT)),
    CHECK (id = osm_id * 4 + CASE osm_type WHEN 'n' THEN 0 WHEN 'w' THEN 1 ELSE 2 END),
    CHECK (city = locality),
    CHECK (state = state_slug)
);

CREATE TABLE poi_source (
    osm_type TEXT NOT NULL CHECK (osm_type IN ('n', 'w', 'r')),
    osm_id INTEGER NOT NULL CHECK (osm_id > 0),
    poi_id INTEGER NOT NULL REFERENCES poi(id) ON DELETE CASCADE,
    PRIMARY KEY (osm_type, osm_id),
    UNIQUE (poi_id)
) WITHOUT ROWID;

CREATE TRIGGER poi_source_identity_guard
BEFORE INSERT ON poi_source
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
          FROM poi
         WHERE id = NEW.poi_id
           AND osm_type = NEW.osm_type
           AND osm_id = NEW.osm_id
    ) THEN RAISE(ABORT, 'poi_source identity mismatch') END;
END;
"""

FINALIZE_SCHEMA_SQL = """
CREATE INDEX poi_state_name_idx ON poi(state, name_norm);
CREATE INDEX poi_category_idx ON poi(state, category, subtype);
CREATE INDEX poi_state_city_idx ON poi(state, city_norm);

CREATE VIRTUAL TABLE poi_fts USING fts5(
    search_norm,
    content='poi',
    content_rowid='id',
    tokenize='unicode61 remove_diacritics 2',
    prefix='2 3 4'
);
INSERT INTO poi_fts(rowid, search_norm)
SELECT id, search_norm FROM poi;
INSERT INTO poi_fts(poi_fts) VALUES ('optimize');

CREATE VIRTUAL TABLE poi_rtree USING rtree(
    id,
    min_lon, max_lon,
    min_lat, max_lat
);
INSERT INTO poi_rtree(id, min_lon, max_lon, min_lat, max_lat)
SELECT id, lon, lon, lat, lat FROM poi;

ANALYZE;
PRAGMA optimize;
"""


@dataclass(slots=True)
class StateBoundary:
    ars: str
    slug: str
    name: str
    places_key: str
    geometry: ogr.Geometry
    prepared: ogr.PreparedGeometry
    envelope: tuple[float, float, float, float]


@dataclass(slots=True)
class MunicipalityPoint:
    name: str
    ags: str
    x: float
    y: float


@dataclass(slots=True)
class KDNode:
    point: MunicipalityPoint
    axis: int
    left: KDNode | None
    right: KDNode | None


class MunicipalityIndex:
    def __init__(
        self,
        places_db: Path,
        states: Sequence[StateBoundary],
        transformers: dict[int, osr.CoordinateTransformation],
    ) -> None:
        uri = f"file:{places_db.resolve().as_posix()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
        try:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(places)")}
            required = {"state_key", "class", "name", "municipality", "ags", "lon", "lat"}
            missing = required - columns
            if missing:
                raise ValueError(f"places DB lacks columns: {sorted(missing)}")
            self.trees: dict[tuple[str, int], KDNode] = {}
            self.counts: dict[str, int] = {}
            for state in states:
                rows = connection.execute(
                    """
                    SELECT COALESCE(NULLIF(TRIM(municipality), ''), name),
                           COALESCE(ags, ''), lon, lat
                      FROM places
                     WHERE state_key = ? AND class = 'Gemeinde'
                       AND lon IS NOT NULL AND lat IS NOT NULL
                    """,
                    (state.places_key,),
                ).fetchall()
                if not rows:
                    raise ValueError(f"No GN250 municipalities for {state.slug}")
                self.counts[state.slug] = len(rows)
                for epsg in (25832, 25833):
                    points = []
                    transformer = transformers[epsg]
                    for name, ags, lon, lat in rows:
                        x, y, _z = transformer.TransformPoint(float(lon), float(lat))
                        points.append(MunicipalityPoint(clean_text(name), clean_text(ags, 16), x, y))
                    self.trees[(state.slug, epsg)] = build_kd_tree(points)
        finally:
            connection.close()

    def nearest(
        self, state_slug: str, epsg: int, x: float, y: float
    ) -> tuple[MunicipalityPoint, float] | None:
        root = self.trees.get((state_slug, epsg))
        if root is None:
            return None
        best_point, best_distance_sq = nearest_kd(root, x, y, None, math.inf)
        if best_point is None:
            return None
        return best_point, math.sqrt(best_distance_sq)


def clean_text(value: Any, limit: int = 256) -> str:
    if value is None:
        return ""
    text = str(value).replace("\x00", " ")
    text = "".join(char if char >= " " else " " for char in text)
    return " ".join(text.split())[:limit].strip()


def normalize(value: str | None) -> str:
    if not value:
        return ""
    value = unicodedata.normalize("NFKD", value.casefold())
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = re.sub(r"[^\w]+", " ", value, flags=re.UNICODE)
    return " ".join(value.split())


def normalize_state_slug(value: str) -> str:
    raw = clean_text(value).casefold()
    if raw in STATE_ALIASES:
        return STATE_ALIASES[raw]
    slug = normalize(raw).replace("_", "-").replace(" ", "-")
    slug = re.sub(r"-+", "-", slug).strip("-")
    return STATE_ALIASES.get(slug, slug)


def parse_active_states(value: str) -> list[str]:
    states = []
    seen = set()
    for part in value.split(","):
        slug = normalize_state_slug(part)
        if not slug:
            continue
        if slug not in ALL_STATE_SLUGS:
            raise ValueError(f"Unknown state slug: {part!r} -> {slug!r}")
        if slug not in seen:
            states.append(slug)
            seen.add(slug)
    if not states:
        raise ValueError("--active-states must contain at least one state")
    return states


def static_contract_check() -> None:
    """Fail before scanning if importer/runtime state contracts drift."""

    if ARS_TO_STATE["16"][0] != "thueringen":
        raise RuntimeError("ARS 16 must use canonical state slug 'thueringen'")
    variants = ("thuringen", "thueringen", "Thüringen")
    for variant in variants:
        if normalize_state_slug(variant) != "thueringen":
            raise RuntimeError(
                f"Thüringen slug alias is not canonical: {variant!r}"
            )
    if parse_active_states(",".join(variants)) != ["thueringen"]:
        raise RuntimeError("Thüringen active-state aliases do not collapse")
    if len(ALL_STATE_SLUGS) != len(ARS_TO_STATE):
        raise RuntimeError("Duplicate canonical state slugs in ARS mapping")
    named_poi_contracts = (
        {"amenity": "toilets"},
        {"amenity": "recycling"},
        {"amenity": "drinking_water"},
        {"tourism": "information", "information": "board"},
    )
    if any(choose_class(tags, "named poi") is None for tags in named_poi_contracts):
        raise RuntimeError("Named POI subtype was accidentally excluded")


def make_transformers() -> dict[int, osr.CoordinateTransformation]:
    source = osr.SpatialReference()
    source.ImportFromEPSG(4326)
    source.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    result = {}
    for epsg in (25832, 25833):
        target = osr.SpatialReference()
        target.ImportFromEPSG(epsg)
        target.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        result[epsg] = osr.CoordinateTransformation(source, target)
    return result


def load_state_boundaries(path: Path, active_slugs: Sequence[str]) -> list[StateBoundary]:
    data = json.loads(path.read_text(encoding="utf-8"))
    crs_name = str(data.get("crs", {}).get("properties", {}).get("name", ""))
    if crs_name and "4326" not in crs_name:
        raise ValueError(f"states GeoJSON must use EPSG:4326, got {crs_name!r}")
    active = set(active_slugs)
    found: dict[str, StateBoundary] = {}
    for feature in data.get("features", []):
        properties = feature.get("properties") or {}
        try:
            gf = int(properties.get("gf"))
        except (TypeError, ValueError):
            continue
        if gf != 4:
            continue
        ars = str(properties.get("ars") or properties.get("ags") or "").zfill(2)[:2]
        mapping = ARS_TO_STATE.get(ars)
        if mapping is None:
            continue
        slug, canonical_name, places_key = mapping
        if slug not in active:
            continue
        if slug in found:
            raise ValueError(f"Duplicate gf=4 state boundary for {slug}")
        geometry = ogr.CreateGeometryFromJson(
            json.dumps(feature.get("geometry"), ensure_ascii=False)
        )
        if geometry is None or geometry.IsEmpty():
            raise ValueError(f"Empty state geometry for {slug}")
        if not geometry.IsValid():
            geometry = geometry.MakeValid()
        prepared = geometry.CreatePreparedGeometry()
        if prepared is None:
            raise ValueError(f"Could not prepare state geometry for {slug}")
        found[slug] = StateBoundary(
            ars=ars,
            slug=slug,
            name=canonical_name,
            places_key=places_key,
            geometry=geometry,
            prepared=prepared,
            envelope=geometry.GetEnvelope(),
        )
    missing = active - set(found)
    if missing:
        raise ValueError(f"Missing gf=4 boundaries for: {sorted(missing)}")
    return sorted(found.values(), key=lambda state: state.ars)


def state_for_point(point: ogr.Geometry, states: Sequence[StateBoundary]) -> StateBoundary | None:
    lon, lat = point.GetX(), point.GetY()
    for state in states:
        min_lon, max_lon, min_lat, max_lat = state.envelope
        if not (min_lon <= lon <= max_lon and min_lat <= lat <= max_lat):
            continue
        if state.prepared.Intersects(point):
            return state
    return None


def build_kd_tree(points: list[MunicipalityPoint], depth: int = 0) -> KDNode | None:
    if not points:
        return None
    axis = depth % 2
    points.sort(key=(lambda point: point.x) if axis == 0 else (lambda point: point.y))
    middle = len(points) // 2
    return KDNode(
        point=points[middle],
        axis=axis,
        left=build_kd_tree(points[:middle], depth + 1),
        right=build_kd_tree(points[middle + 1 :], depth + 1),
    )


def nearest_kd(
    node: KDNode | None,
    x: float,
    y: float,
    best_point: MunicipalityPoint | None,
    best_distance_sq: float,
) -> tuple[MunicipalityPoint | None, float]:
    if node is None:
        return best_point, best_distance_sq
    dx = x - node.point.x
    dy = y - node.point.y
    distance_sq = dx * dx + dy * dy
    if distance_sq < best_distance_sq:
        best_point, best_distance_sq = node.point, distance_sq
    delta = dx if node.axis == 0 else dy
    near, far = (node.left, node.right) if delta < 0 else (node.right, node.left)
    best_point, best_distance_sq = nearest_kd(
        near, x, y, best_point, best_distance_sq
    )
    if delta * delta < best_distance_sq:
        best_point, best_distance_sq = nearest_kd(
            far, x, y, best_point, best_distance_sq
        )
    return best_point, best_distance_sq


def feature_field(feature: ogr.Feature, indexes: dict[str, int], name: str) -> str:
    index = indexes.get(name, -1)
    if index < 0 or not feature.IsFieldSetAndNotNull(index):
        return ""
    return clean_text(feature.GetField(index))


def source_identity(
    feature: ogr.Feature, layer_name: str, indexes: dict[str, int]
) -> tuple[str, int] | None:
    if layer_name == "points":
        osm_type, raw_id = "n", feature_field(feature, indexes, "osm_id")
    elif layer_name == "lines":
        osm_type, raw_id = "w", feature_field(feature, indexes, "osm_id")
    elif layer_name == "multipolygons":
        way_id = feature_field(feature, indexes, "osm_way_id")
        osm_type = "w" if way_id else "r"
        raw_id = way_id or feature_field(feature, indexes, "osm_id")
    else:
        osm_type, raw_id = "r", feature_field(feature, indexes, "osm_id")
    try:
        osm_id = int(raw_id)
    except (TypeError, ValueError):
        return None
    if osm_id <= 0:
        return None
    encoded = osm_id * 4 + SOURCE_TYPE_CODE[osm_type]
    if encoded > 9_223_372_036_854_775_807:
        return None
    return osm_type, osm_id


def line_midpoint(geometry: ogr.Geometry) -> ogr.Geometry | None:
    flat_type = ogr.GT_Flatten(geometry.GetGeometryType())
    if flat_type == ogr.wkbLineString:
        length = geometry.Length()
        return geometry.Value(length / 2.0) if length > 0 else None
    if flat_type == ogr.wkbMultiLineString:
        longest = None
        longest_length = -1.0
        for index in range(geometry.GetGeometryCount()):
            child = geometry.GetGeometryRef(index)
            length = child.Length()
            if length > longest_length:
                longest, longest_length = child, length
        return line_midpoint(longest) if longest is not None else None
    return None


def representative_point(geometry: ogr.Geometry | None) -> ogr.Geometry | None:
    if geometry is None or geometry.IsEmpty():
        return None
    flat_type = ogr.GT_Flatten(geometry.GetGeometryType())
    if flat_type == ogr.wkbPoint:
        return geometry.Clone()
    if flat_type in {ogr.wkbLineString, ogr.wkbMultiLineString}:
        point = line_midpoint(geometry)
        if point is not None and not point.IsEmpty():
            return point
    if flat_type in {ogr.wkbPolygon, ogr.wkbMultiPolygon}:
        try:
            point = geometry.PointOnSurface()
            if point is not None and not point.IsEmpty():
                return point
        except RuntimeError:
            pass
    if flat_type == ogr.wkbGeometryCollection:
        candidates: list[tuple[int, float, ogr.Geometry]] = []
        for index in range(geometry.GetGeometryCount()):
            child = geometry.GetGeometryRef(index)
            if child is None or child.IsEmpty():
                continue
            child_type = ogr.GT_Flatten(child.GetGeometryType())
            if child_type in {ogr.wkbPolygon, ogr.wkbMultiPolygon}:
                candidates.append((3, child.GetArea(), child))
            elif child_type in {ogr.wkbLineString, ogr.wkbMultiLineString}:
                candidates.append((2, child.Length(), child))
            elif child_type == ogr.wkbPoint:
                candidates.append((1, 0.0, child))
        for _priority, _measure, child in sorted(
            candidates, key=lambda row: (row[0], row[1]), reverse=True
        ):
            point = representative_point(child)
            if point is not None:
                return point
    try:
        point = geometry.Centroid()
        if point is not None and not point.IsEmpty():
            return point
    except RuntimeError:
        pass
    return None


def display_name(
    feature: ogr.Feature, indexes: dict[str, int]
) -> tuple[str, str, dict[str, str]] | None:
    values = {name: feature_field(feature, indexes, name) for name in NAME_FIELDS}
    brand = feature_field(feature, indexes, "brand")
    candidates = (
        ("name", values["name"]),
        ("name_de", values["name_de"]),
        ("official_name", values["official_name"]),
        ("short_name", values["short_name"]),
        ("loc_name", values["loc_name"]),
        ("name_en", values["name_en"]),
        ("brand", brand),
    )
    for source, value in candidates:
        if value and normalize(value) not in PLACEHOLDER_NAMES:
            values["brand"] = brand
            return value, source, values
    return None


def choose_class(
    tags: dict[str, str], _display_name_norm: str
) -> tuple[str, str, str] | None:
    amenity = tags.get("amenity", "")
    if amenity not in EXCLUDED_GENERIC_VALUES:
        if amenity in FOOD_AMENITIES:
            category = "food"
        elif amenity in HEALTH_AMENITIES:
            category = "healthcare"
        elif amenity in EDUCATION_AMENITIES:
            category = "education"
        elif amenity in TRANSPORT_AMENITIES:
            category = "transport"
        elif amenity in FINANCE_AMENITIES:
            category = "finance"
        elif amenity in RELIGION_AMENITIES:
            category = "religion"
        elif amenity in CULTURE_AMENITIES:
            category = "culture"
        else:
            category = "amenity"
        return "amenity", amenity, category

    shop = tags.get("shop", "")
    if shop not in EXCLUDED_GENERIC_VALUES and shop != "vacant":
        return "shop", shop, "retail"

    tourism = tags.get("tourism", "")
    if tourism not in EXCLUDED_GENERIC_VALUES:
        return "tourism", tourism, "tourism"

    leisure = tags.get("leisure", "")
    if leisure not in EXCLUDED_GENERIC_VALUES:
        return "leisure", leisure, "leisure"
    office = tags.get("office", "")
    if office not in EXCLUDED_GENERIC_VALUES and office != "vacant":
        return "office", office, "services"
    government = tags.get("government", "")
    if government not in EXCLUDED_GENERIC_VALUES:
        return "government", government, "amenity"
    diplomatic = tags.get("diplomatic", "")
    if diplomatic not in EXCLUDED_GENERIC_VALUES:
        return "diplomatic", diplomatic, "services"
    craft = tags.get("craft", "")
    if craft not in EXCLUDED_GENERIC_VALUES:
        return "craft", craft, "services"
    club = tags.get("club", "")
    if club not in EXCLUDED_GENERIC_VALUES:
        return "club", club, "community"
    healthcare = tags.get("healthcare", "")
    if healthcare not in EXCLUDED_GENERIC_VALUES:
        return "healthcare", healthcare, "healthcare"
    social_facility = tags.get("social_facility", "")
    if social_facility not in EXCLUDED_GENERIC_VALUES:
        return "social_facility", social_facility, "community"
    historic = tags.get("historic", "")
    if historic not in EXCLUDED_GENERIC_VALUES:
        return "historic", historic, "landmark"
    public_transport = tags.get("public_transport", "")
    if public_transport in PUBLIC_TRANSPORT_POIS:
        return "public_transport", public_transport, "transport"
    railway = tags.get("railway", "")
    if railway in RAILWAY_POIS:
        return "railway", railway, "transport"
    if tags.get("highway") == "bus_stop":
        return "highway", "bus_stop", "transport"
    aeroway = tags.get("aeroway", "")
    if aeroway in AEROWAY_POIS:
        return "aeroway", aeroway, "transport"
    if tags.get("aerialway") == "station":
        return "aerialway", "station", "transport"
    natural = tags.get("natural", "")
    if natural in NATURAL_POIS:
        return "natural", natural, "natural"
    man_made = tags.get("man_made", "")
    if man_made in MAN_MADE_POIS:
        return "man_made", man_made, "landmark"
    emergency = tags.get("emergency", "")
    if emergency in EMERGENCY_POIS:
        return "emergency", emergency, "emergency"
    if tags.get("landuse") == "cemetery":
        return "landuse", "cemetery", "religion"
    if tags.get("waterway") == "waterfall":
        return "waterway", "waterfall", "natural"
    building = tags.get("building", "")
    if building in RELIGIOUS_BUILDINGS:
        return "building", building, "religion"
    return None


def build_aliases(
    values: dict[str, str],
    display: str,
    feature: ogr.Feature,
    indexes: dict[str, int],
) -> str:
    seen = {normalize(display)}
    aliases = []
    for key in NAME_FIELDS:
        value = values.get(key, "")
        for part in value.split(";"):
            alias = clean_text(part)
            normalized = normalize(alias)
            if alias and normalized and normalized not in seen:
                aliases.append(alias)
                seen.add(normalized)
            if len(aliases) >= 24:
                break
    for key in ("ref", "iata", "icao"):
        alias = feature_field(feature, indexes, key)
        normalized = normalize(alias)
        if alias and normalized and normalized not in seen:
            aliases.append(alias)
            seen.add(normalized)
    return "\x1f".join(aliases)


def build_category_terms(
    class_key: str, subtype: str, category: str, tags: dict[str, str]
) -> str:
    terms = {
        normalize(category.replace("_", " ")),
        normalize(class_key.replace("_", " ")),
        normalize(subtype.replace("_", " ")),
    }
    terms.update(normalize(TYPE_TERMS.get(subtype, "")).split())
    for key in (
        "cuisine",
        "sport",
        "religion",
        "denomination",
        "healthcare_speciality",
        "social_facility",
        "government",
        "diplomatic",
    ):
        terms.update(normalize(tags.get(key, "").replace(";", " ")).split())
    return " ".join(sorted(term for term in terms if term))


def calculate_quality(
    *,
    display_source: str,
    street: str,
    housenumber: str,
    postcode: str,
    city: str,
    brand: str,
    operator: str,
    osm_type: str,
) -> int:
    """Produce a deterministic completeness score used only as a rank tie-break."""

    score = 30
    if display_source in {"name", "name_de", "official_name"}:
        score += 8
    if street and housenumber:
        score += 15
    elif street:
        score += 5
    if postcode:
        score += 5
    if city:
        score += 4
    if brand:
        score += 3
    if operator:
        score += 2
    score += {"n": 3, "w": 2, "r": 1}[osm_type]
    return min(score, 100)


def database_schema_fingerprint() -> str:
    normalized = " ".join((CREATE_SCHEMA_SQL + FINALIZE_SCHEMA_SQL).split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def initialize_database(connection: sqlite3.Connection, cache_mib: int) -> None:
    connection.executescript(CREATE_SCHEMA_SQL)
    connection.execute(f"PRAGMA cache_size={-max(8, cache_mib) * 1024}")


def insert_poi(connection: sqlite3.Connection, poi_row: dict[str, Any]) -> bool:
    """Insert one POI and its stable OSM identity without expected SQL errors.

    The same OSM object can occur in more than one GDAL layer.  Those exact
    duplicates are normal input, so they must not be implemented by catching a
    uniqueness error: statement rollback is unavailable in journal_mode=OFF
    and an expected constraint error can then corrupt the build database.
    """

    insert_cursor = connection.execute(
        """
        INSERT INTO poi(
            id, poi_id, osm_type, osm_id, name, display_source,
            name_norm, search_norm, aliases, brand, operator, category,
            category_label, class_key, subtype, category_terms,
            address, address_norm, street, housenumber, postcode,
            city, city_norm, state, quality, locality,
            locality_source, locality_ags, locality_distance_m,
            state_slug, state_name, lon, lat, utm_epsg, easting,
            northing
        ) VALUES (
            :id, :poi_id, :osm_type, :osm_id, :name, :display_source,
            :name_norm, :search_norm, :aliases, :brand, :operator, :category,
            :category_label, :class_key, :subtype, :category_terms,
            :address, :address_norm, :street, :housenumber, :postcode,
            :city, :city_norm, :state, :quality, :locality,
            :locality_source, :locality_ags, :locality_distance_m,
            :state_slug, :state_name, :lon, :lat, :utm_epsg, :easting,
            :northing
        )
        ON CONFLICT DO NOTHING
        """,
        poi_row,
    )
    if insert_cursor.rowcount == 0:
        existing = connection.execute(
            """
            SELECT 1 FROM poi
             WHERE id=:id
               AND poi_id=:poi_id
               AND osm_type=:osm_type
               AND osm_id=:osm_id
            """,
            poi_row,
        ).fetchone()
        if existing is None:
            raise sqlite3.IntegrityError(
                "POI identity collision for "
                f"{poi_row['poi_id']} "
                f"({poi_row['osm_type']}/{poi_row['osm_id']}, "
                f"encoded={poi_row['id']})"
            )
        return False

    connection.execute(
        """
        INSERT INTO poi_source(poi_id, osm_type, osm_id)
        VALUES (:id, :osm_type, :osm_id)
        """,
        poi_row,
    )
    return True


def finalize_database(connection: sqlite3.Connection, meta: dict[str, Any]) -> None:
    connection.executemany(
        "INSERT INTO meta(key, value) VALUES (?, ?)",
        (
            (key, json.dumps(value, ensure_ascii=False, sort_keys=True))
            for key, value in sorted(meta.items())
        ),
    )
    connection.executescript(FINALIZE_SCHEMA_SQL)
    connection.commit()


def sqlite_capability_check() -> None:
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("CREATE VIRTUAL TABLE fts_probe USING fts5(value, prefix='2 3 4')")
        connection.execute(
            "CREATE VIRTUAL TABLE rtree_probe USING rtree(id, min_x, max_x, min_y, max_y)"
        )
    finally:
        connection.close()


def validate_osm_config(path: Path) -> None:
    parser = configparser.ConfigParser(interpolation=None)
    # GDAL's osmconf.ini syntax permits dataset-wide settings before the first
    # layer section, whereas ConfigParser requires every key to have a section.
    parser.read_string(
        "[dataset]\n" + path.read_text(encoding="utf-8"),
        source=str(path),
    )
    expected_sections = {
        "points",
        "lines",
        "multipolygons",
        "multilinestrings",
        "other_relations",
    }
    missing_sections = expected_sections - set(parser.sections())
    if missing_sections:
        raise ValueError(f"OSM config lacks sections: {sorted(missing_sections)}")
    unexpected_sections = set(parser.sections()) - expected_sections - {"dataset"}
    if unexpected_sections:
        raise ValueError(
            f"OSM config has unsupported sections: {sorted(unexpected_sections)}"
        )
    closed_way_keys = {
        item.strip()
        for item in parser.get(
            "dataset", "closed_ways_are_polygons", fallback=""
        ).split(",")
        if item.strip()
    }
    required_closed_way_keys = {
        "amenity",
        "building",
        "club",
        "emergency",
        "healthcare",
        "shop",
        "tourism",
    }
    missing_closed_way_keys = required_closed_way_keys - closed_way_keys
    if missing_closed_way_keys:
        raise ValueError(
            "OSM config polygon classification lacks: "
            f"{sorted(missing_closed_way_keys)}"
        )
    if parser.get("dataset", "tags_format", fallback="").casefold() != "json":
        raise ValueError("OSM config must use tags_format=json")
    forbidden = {"phone", "contact:phone", "website", "contact:website"}
    required = {"name", "brand", "operator", "amenity", "shop", "tourism"}
    for section in expected_sections:
        attributes = {
            field.strip()
            for field in parser.get(section, "attributes", fallback="").split(",")
            if field.strip()
        }
        leaked = attributes & forbidden
        if leaked:
            raise ValueError(
                f"OSM config section {section!r} exposes forbidden fields: {sorted(leaked)}"
            )
        absent = required - attributes
        if absent:
            raise ValueError(
                f"OSM config section {section!r} lacks required fields: {sorted(absent)}"
            )


def self_test_database(
    path: Path, active_states: Sequence[str]
) -> dict[str, Any]:
    uri = f"file:{path.resolve().as_posix()}?mode=rw"
    connection = sqlite3.connect(uri, uri=True)
    checks: dict[str, Any] = {}
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"SQLite integrity_check failed: {integrity}")
        checks["integrity_check"] = integrity

        foreign_rows = connection.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_rows:
            raise RuntimeError(f"foreign_key_check failed: {foreign_rows[:5]}")
        checks["foreign_key_check_rows"] = 0

        expected_tables = {"meta", "poi", "poi_source", "poi_fts", "poi_rtree"}
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
            )
        }
        missing = expected_tables - tables
        if missing:
            raise RuntimeError(f"Missing tables: {sorted(missing)}")
        checks["required_tables"] = sorted(expected_tables)

        poi_count = connection.execute("SELECT COUNT(*) FROM poi").fetchone()[0]
        source_count = connection.execute("SELECT COUNT(*) FROM poi_source").fetchone()[0]
        fts_count = connection.execute("SELECT COUNT(*) FROM poi_fts").fetchone()[0]
        rtree_count = connection.execute("SELECT COUNT(*) FROM poi_rtree").fetchone()[0]
        if (
            not poi_count
            or poi_count != source_count
            or poi_count != fts_count
            or poi_count != rtree_count
        ):
            raise RuntimeError(
                "Row-count mismatch: "
                f"poi={poi_count}, source={source_count}, "
                f"fts={fts_count}, rtree={rtree_count}"
            )
        checks["row_counts"] = {
            "poi": poi_count,
            "poi_source": source_count,
            "fts": fts_count,
            "rtree": rtree_count,
        }

        try:
            connection.execute(
                "INSERT INTO poi_fts(poi_fts, rank) VALUES ('integrity-check', 1)"
            )
        except sqlite3.DatabaseError as error:
            raise RuntimeError(f"FTS external-content integrity check failed: {error}") from error
        checks["fts_external_content_integrity"] = "ok"

        required_runtime_columns = {
            "id",
            "name",
            "name_norm",
            "search_norm",
            "aliases",
            "brand",
            "operator",
            "category",
            "class_key",
            "subtype",
            "category_terms",
            "address",
            "address_norm",
            "street",
            "housenumber",
            "postcode",
            "city",
            "state",
            "lon",
            "lat",
            "quality",
        }
        actual_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(poi)")
        }
        missing_runtime_columns = required_runtime_columns - actual_columns
        if missing_runtime_columns:
            raise RuntimeError(
                "Runtime-required POI columns missing: "
                f"{sorted(missing_runtime_columns)}"
            )
        source_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(poi_source)")
        }
        if not {"poi_id", "osm_type", "osm_id"}.issubset(source_columns):
            raise RuntimeError("Runtime-required poi_source columns missing")
        checks["runtime_schema_contract"] = "ok"

        source_mismatches = connection.execute(
            """
            SELECT COUNT(*)
              FROM poi_source source
              JOIN poi ON poi.id = source.poi_id
             WHERE poi.osm_type != source.osm_type
                OR poi.osm_id != source.osm_id
            """
        ).fetchone()[0]
        if source_mismatches:
            raise RuntimeError(
                f"Found {source_mismatches} mismatched poi_source rows"
            )
        checks["poi_source_identity_mismatches"] = 0

        invalid_ids = connection.execute(
            """
            SELECT COUNT(*) FROM poi
             WHERE poi_id != osm_type || CAST(osm_id AS TEXT)
                OR id != osm_id * 4
                         + CASE osm_type WHEN 'n' THEN 0 WHEN 'w' THEN 1 ELSE 2 END
            """
        ).fetchone()[0]
        if invalid_ids:
            raise RuntimeError(f"Found {invalid_ids} unstable POI IDs")
        checks["invalid_stable_ids"] = 0

        invalid_display = connection.execute(
            "SELECT COUNT(*) FROM poi WHERE display_source='operator' OR name=''"
        ).fetchone()[0]
        if invalid_display:
            raise RuntimeError(f"Found {invalid_display} operator-only/empty display names")
        checks["operator_only_display_names"] = 0

        forbidden_columns = {"phone", "website", "contact_phone", "contact_website"}
        leaked = forbidden_columns & actual_columns
        if leaked:
            raise RuntimeError(f"Forbidden contact columns present: {sorted(leaked)}")
        checks["forbidden_contact_columns"] = []

        unexpected_states = [
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT state FROM poi WHERE state NOT IN ({})".format(
                    ",".join("?" for _ in active_states)
                ),
                tuple(active_states),
            )
        ]
        if unexpected_states:
            raise RuntimeError(f"Unexpected states in output: {unexpected_states}")
        present_states = {
            row[0] for row in connection.execute("SELECT DISTINCT state FROM poi")
        }
        missing_states = set(active_states) - present_states
        if missing_states:
            raise RuntimeError(
                f"No accepted POIs for active states: {sorted(missing_states)}"
            )
        checks["active_state_filter"] = list(active_states)

        invalid_coordinates = connection.execute(
            """
            SELECT COUNT(*) FROM poi
             WHERE lon NOT BETWEEN 5 AND 16 OR lat NOT BETWEEN 47 AND 56
                OR utm_epsg != CASE WHEN lon < 12 THEN 25832 ELSE 25833 END
                OR easting IS NULL OR northing IS NULL
            """
        ).fetchone()[0]
        if invalid_coordinates:
            raise RuntimeError(f"Found {invalid_coordinates} invalid coordinate rows")
        checks["invalid_coordinates"] = 0

        first = connection.execute(
            """
            SELECT poi.id, poi.name_norm, poi.lon, poi.lat,
                   source.osm_type, source.osm_id
              FROM poi
              JOIN poi_source source ON source.poi_id = poi.id
             ORDER BY poi.id
             LIMIT 1
            """
        ).fetchone()
        if first is None:
            raise RuntimeError("No POI available for self-tests")
        first_id, first_name_norm, lon, lat, osm_type, osm_id = first
        token = next((part for part in first_name_norm.split() if len(part) >= 4), "")
        if token:
            for prefix_length in (2, 3, 4):
                query = f'"{token[:prefix_length]}"*'
                found = connection.execute(
                    "SELECT 1 FROM poi_fts WHERE rowid=? AND poi_fts MATCH ?",
                    (first_id, query),
                ).fetchone()
                if found is None:
                    raise RuntimeError(f"FTS prefix-{prefix_length} self-test failed")
            checks["fts_prefix_lengths"] = [2, 3, 4]

        source_found = connection.execute(
            """
            SELECT poi.id
              FROM poi
              JOIN poi_source source ON source.poi_id = poi.id
             WHERE source.osm_type=? AND source.osm_id=?
            """,
            (osm_type, osm_id),
        ).fetchone()
        if source_found is None or source_found[0] != first_id:
            raise RuntimeError("Stable POI source lookup self-test failed")
        checks["stable_source_lookup"] = "ok"

        spatial_found = connection.execute(
            """
            SELECT 1 FROM poi_rtree
             WHERE id=? AND min_lon<=? AND max_lon>=? AND min_lat<=? AND max_lat>=?
            """,
            (first_id, lon, lon, lat, lat),
        ).fetchone()
        if spatial_found is None:
            raise RuntimeError("RTree self-test failed")
        checks["rtree_point_lookup"] = "ok"

        fts_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='poi_fts'"
        ).fetchone()[0]
        if "prefix='2 3 4'" not in fts_sql.replace('"', "'"):
            raise RuntimeError("FTS prefix configuration missing")
        fts_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(poi_fts)")
        }
        if fts_columns != {"search_norm"}:
            raise RuntimeError(
                f"FTS must contain only normalized search text: {sorted(fts_columns)}"
            )
        checks["fts_schema"] = "ok"
    finally:
        connection.close()
    return checks


def json_atomic_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.candidate.", suffix=".json", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        fsync_directory(path.parent)
    except BaseException:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def input_file_snapshot(path: Path) -> dict[str, int]:
    stat = path.stat()
    return {
        "device": stat.st_dev,
        "inode": stat.st_ino,
        "bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def require_unchanged_input(
    path: Path, expected: dict[str, int], label: str
) -> None:
    actual = input_file_snapshot(path)
    if actual != expected:
        raise RuntimeError(
            f"{label} changed while the POI index was being built: "
            f"before={expected}, after={actual}. Use a versioned input file "
            "published by atomic rename."
        )


def fsync_file(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def available_memory_bytes() -> int | None:
    """Return the tightest Linux host/cgroup memory estimate when available."""

    candidates: list[int] = []
    meminfo = Path("/proc/meminfo")
    if meminfo.is_file():
        for line in meminfo.read_text(encoding="utf-8").splitlines():
            if line.startswith("MemAvailable:"):
                candidates.append(int(line.split()[1]) * 1024)
                break
    cgroup_pairs = (
        (
            Path("/sys/fs/cgroup/memory.max"),
            Path("/sys/fs/cgroup/memory.current"),
        ),
        (
            Path("/sys/fs/cgroup/memory/memory.limit_in_bytes"),
            Path("/sys/fs/cgroup/memory/memory.usage_in_bytes"),
        ),
    )
    for limit_path, current_path in cgroup_pairs:
        if not limit_path.is_file() or not current_path.is_file():
            continue
        raw_limit = limit_path.read_text(encoding="utf-8").strip()
        if raw_limit == "max":
            continue
        try:
            limit = int(raw_limit)
            current = int(current_path.read_text(encoding="utf-8").strip())
        except ValueError:
            continue
        if 0 < limit < 1 << 60:
            candidates.append(max(0, limit - current))
    return min(candidates) if candidates else None


def resource_preflight(args: argparse.Namespace) -> dict[str, Any]:
    """Conservatively reject builds likely to die from disk or memory pressure."""

    pbf_bytes = args.pbf.stat().st_size
    gib = 1024**3
    estimates = {
        "candidate_output_bytes": max(512 * 1024**2, math.ceil(pbf_bytes * 0.75)),
        "temporary_bytes": max(gib, math.ceil(pbf_bytes * 1.25)),
        "safety_margin_bytes_per_filesystem": 512 * 1024**2,
    }
    result: dict[str, Any] = {
        "enabled": not args.skip_resource_preflight,
        "estimates": estimates,
        "filesystems": [],
        "available_memory_bytes": available_memory_bytes(),
        "minimum_available_memory_bytes": args.min_available_memory_mib * 1024**2,
    }
    if args.skip_resource_preflight:
        return result

    requirements_by_device: dict[int, dict[str, Any]] = {}
    for label, directory, required in (
        ("output", args.output.parent, estimates["candidate_output_bytes"]),
        ("temporary", args.temp_dir, estimates["temporary_bytes"]),
    ):
        device = os.stat(directory).st_dev
        entry = requirements_by_device.setdefault(
            device,
            {
                "paths": [],
                "required_bytes": estimates["safety_margin_bytes_per_filesystem"],
            },
        )
        entry["paths"].append({"role": label, "path": str(directory)})
        entry["required_bytes"] += required
    for entry in requirements_by_device.values():
        probe_path = Path(entry["paths"][0]["path"])
        free_bytes = shutil.disk_usage(probe_path).free
        entry["free_bytes"] = free_bytes
        result["filesystems"].append(entry)
        if free_bytes < entry["required_bytes"]:
            raise RuntimeError(
                "Insufficient free disk for POI build on "
                f"{probe_path}: need about {entry['required_bytes'] / gib:.1f} GiB, "
                f"have {free_bytes / gib:.1f} GiB. "
                "Use --skip-resource-preflight only after an operator review."
            )

    memory = result["available_memory_bytes"]
    minimum_memory = result["minimum_available_memory_bytes"]
    if memory is not None and minimum_memory and memory < minimum_memory:
        raise RuntimeError(
            "Insufficient available memory for POI build: "
            f"need at least {minimum_memory / 1024**2:.0f} MiB, "
            f"have {memory / 1024**2:.0f} MiB"
        )
    return result


@contextlib.contextmanager
def exclusive_output_lock(output: Path) -> Iterable[Path]:
    """Serialize writers targeting the same output and sidecar names."""

    lock_path = output.with_name(f".{output.name}.publish.lock")
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError(
                f"Another POI build is already publishing {output}"
            ) from error
        os.ftruncate(descriptor, 0)
        os.write(descriptor, f"pid={os.getpid()}\n".encode("ascii"))
        os.fsync(descriptor)
        yield lock_path
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def max_rss_bytes() -> int:
    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(raw if sys.platform == "darwin" else raw * 1024)


def make_candidate_path(output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    return output.parent / f".{output.name}.candidate.{os.getpid()}.{uuid.uuid4().hex}.sqlite"


def field_indexes(layer: ogr.Layer) -> dict[str, int]:
    definition = layer.GetLayerDefn()
    return {
        definition.GetFieldDefn(index).GetName(): index
        for index in range(definition.GetFieldCount())
    }


def build_index(args: argparse.Namespace, candidate_path: Path) -> dict[str, Any]:
    started_wall = time.time()
    started = time.perf_counter()
    pbf_snapshot = input_file_snapshot(args.pbf)
    static_contract_check()
    active_slugs = parse_active_states(args.active_states)
    preflight = resource_preflight(args)
    states = load_state_boundaries(args.states_geojson, active_slugs)
    transformers = make_transformers()
    municipalities = MunicipalityIndex(args.places_db, states, transformers)

    sqlite_capability_check()
    validate_osm_config(args.osm_config)
    connection = sqlite3.connect(candidate_path)
    initialize_database(connection, args.sqlite_cache_mib)
    gdal.SetConfigOption("CPL_TMPDIR", str(args.temp_dir))

    dataset = gdal.OpenEx(
        str(args.pbf),
        gdal.OF_VECTOR,
        open_options=[
            f"CONFIG_FILE={args.osm_config}",
            "INTERLEAVED_READING=YES",
            "USE_CUSTOM_INDEXING=YES",
            "COMPRESS_NODES=YES",
            f"MAX_TMPFILE_SIZE={args.gdal_max_tmpfile_mib}",
        ],
    )
    if dataset is None:
        raise RuntimeError(f"Could not open PBF: {args.pbf}")

    scanned_by_layer: Counter[str] = Counter()
    accepted_by_state: Counter[str] = Counter()
    accepted_by_category: Counter[str] = Counter()
    accepted_by_type: Counter[str] = Counter()
    locality_sources: Counter[str] = Counter()
    rejected: Counter[str] = Counter()
    diagnostics: Counter[str] = Counter()
    exact_duplicates = 0
    accepted = 0
    layer_fields: dict[str, dict[str, int]] = {}
    last_progress = started

    connection.execute("BEGIN")
    while True:
        feature, layer = dataset.GetNextFeature()
        if feature is None:
            break
        layer_name = layer.GetName()
        scanned_by_layer[layer_name] += 1
        scanned_total = sum(scanned_by_layer.values())
        if layer_name not in layer_fields:
            layer_fields[layer_name] = field_indexes(layer)
        indexes = layer_fields[layer_name]
        now = time.perf_counter()
        if not args.quiet and (
            scanned_total % args.progress_every == 0 or now - last_progress >= 60
        ):
            elapsed = now - started
            print(
                f"[poi-import] scanned={scanned_total:,} accepted={accepted:,} "
                f"elapsed={elapsed:.1f}s rss={max_rss_bytes() / 2**20:.1f}MiB",
                file=sys.stderr,
                flush=True,
            )
            last_progress = now

        name_result = display_name(feature, indexes)
        if name_result is None:
            rejected["no_display_name_or_brand"] += 1
            continue
        name, display_source, name_values = name_result
        name_norm = normalize(name)

        if feature_field(feature, indexes, "disused").casefold() in {"1", "true", "yes"}:
            rejected["disused"] += 1
            continue
        if feature_field(feature, indexes, "abandoned").casefold() in {"1", "true", "yes"}:
            rejected["abandoned"] += 1
            continue

        tags = {
            key: value
            for key in CLASS_FIELDS
            if (value := feature_field(feature, indexes, key))
        }
        classification = choose_class(tags, name_norm)
        if classification is None:
            rejected["not_in_conservative_whitelist"] += 1
            continue

        identity = source_identity(feature, layer_name, indexes)
        if identity is None:
            rejected["invalid_osm_identity"] += 1
            continue
        osm_type, osm_id = identity
        encoded_id = osm_id * 4 + SOURCE_TYPE_CODE[osm_type]
        poi_id = f"{osm_type}{osm_id}"

        point = representative_point(feature.GetGeometryRef())
        if point is None:
            rejected["invalid_geometry"] += 1
            continue
        lon, lat = point.GetX(), point.GetY()
        if not (5.0 <= lon <= 16.0 and 47.0 <= lat <= 56.0):
            rejected["outside_germany_bbox"] += 1
            continue
        state = state_for_point(point, states)
        if state is None:
            rejected["outside_active_states"] += 1
            continue

        epsg = 25832 if lon < 12.0 else 25833
        easting, northing, _z = transformers[epsg].TransformPoint(lon, lat)
        street = feature_field(feature, indexes, "addr_street")
        housenumber = feature_field(feature, indexes, "addr_housenumber",)
        postcode = feature_field(feature, indexes, "addr_postcode")
        osm_city = feature_field(feature, indexes, "addr_city")
        addr_place = feature_field(feature, indexes, "addr_place")

        if osm_city:
            locality = osm_city
            locality_source = "osm"
            locality_ags = ""
            locality_distance_m = None
        else:
            nearest = municipalities.nearest(state.slug, epsg, easting, northing)
            if nearest is None or nearest[1] > args.max_locality_distance_m:
                locality = ""
                locality_source = "none"
                locality_ags = ""
                locality_distance_m = None
                diagnostics["locality_fallback_unavailable"] += 1
            else:
                municipality, distance = nearest
                locality = municipality.name
                locality_source = "gn250_nearest"
                locality_ags = municipality.ags
                locality_distance_m = int(round(distance))
        locality_sources[locality_source] += 1

        address_line = " ".join(part for part in (street or addr_place, housenumber) if part)
        locality_line = " ".join(part for part in (postcode, locality) if part)
        address = ", ".join(part for part in (address_line, locality_line) if part)
        class_key, subtype, category = classification
        aliases = build_aliases(name_values, name, feature, indexes)
        brand = feature_field(feature, indexes, "brand")
        operator = feature_field(feature, indexes, "operator")
        terms = build_category_terms(class_key, subtype, category, tags)
        address_norm = normalize(address)
        city_norm = normalize(locality)
        search_norm = normalize(
            " ".join(
                part
                for part in (
                    name,
                    aliases,
                    brand,
                    operator,
                    terms,
                    address,
                    locality,
                )
                if part
            )
        )
        quality = calculate_quality(
            display_source=display_source,
            street=street,
            housenumber=housenumber,
            postcode=postcode,
            city=locality,
            brand=brand,
            operator=operator,
            osm_type=osm_type,
        )
        poi_row = {
            "id": encoded_id,
            "poi_id": poi_id,
            "osm_type": osm_type,
            "osm_id": osm_id,
            "name": name,
            "display_source": display_source,
            "name_norm": name_norm,
            "search_norm": search_norm,
            "aliases": aliases,
            "brand": brand,
            "operator": operator,
            "category": category,
            "category_label": CATEGORY_LABELS.get(category, ""),
            "class_key": class_key,
            "subtype": subtype,
            "category_terms": terms,
            "address": address,
            "address_norm": address_norm,
            "street": street,
            "housenumber": housenumber,
            "postcode": postcode,
            "city": locality,
            "city_norm": city_norm,
            "state": state.slug,
            "quality": quality,
            "locality": locality,
            "locality_source": locality_source,
            "locality_ags": locality_ags,
            "locality_distance_m": locality_distance_m,
            "state_slug": state.slug,
            "state_name": state.name,
            "lon": lon,
            "lat": lat,
            "utm_epsg": epsg,
            "easting": easting,
            "northing": northing,
        }
        if not insert_poi(connection, poi_row):
            exact_duplicates += 1
            continue
        accepted += 1
        accepted_by_state[state.slug] += 1
        accepted_by_category[category] += 1
        accepted_by_type[osm_type] += 1

        if accepted % args.commit_every == 0:
            connection.commit()
            connection.execute("BEGIN")

    dataset = None
    connection.commit()
    scan_seconds = time.perf_counter() - started

    meta = {
        "format": FORMAT_NAME,
        "schema_version": SCHEMA_VERSION,
        "schema_fingerprint": database_schema_fingerprint(),
        "build_id": args.build_id,
        "created_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": "OpenStreetMap via Geofabrik",
        "attribution": OSM_ATTRIBUTION,
        "copyright_url": OSM_COPYRIGHT_URL,
        "active_states": active_slugs,
        "coordinate_systems": ["EPSG:4326", "EPSG:25832", "EPSG:25833"],
        "utm_zone_rule": "EPSG:25832 for longitude < 12°, otherwise EPSG:25833",
        "display_name_policy": "name/name:de/official/short/local/name:en/brand; never operator-only",
        "selection_policy": (
            "named or branded features with a semantic POI classification; "
            "named small-infrastructure subtypes are retained"
        ),
        "locality_fallback_policy": (
            "OSM addr:city first; otherwise nearest GN250 Gemeinde reference "
            "point within the already matched federal state"
        ),
        "max_locality_distance_m": args.max_locality_distance_m,
        "deduplication_policy": "one exact row per stable OSM n/w/r object; no semantic proximity merge",
        "aliases_separator": "U+001F",
    }
    finalize_started = time.perf_counter()
    finalize_database(connection, meta)
    finalize_seconds = time.perf_counter() - finalize_started
    connection.close()

    validate_started = time.perf_counter()
    self_tests = self_test_database(candidate_path, active_slugs)
    validate_seconds = time.perf_counter() - validate_started
    require_unchanged_input(args.pbf, pbf_snapshot, "Source PBF")
    os.chmod(candidate_path, 0o644)
    fsync_file(candidate_path)

    ended_wall = time.time()
    return {
        "format": FORMAT_NAME,
        "schema_version": SCHEMA_VERSION,
        "build_id": args.build_id,
        "started_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started_wall)),
        "finished_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ended_wall)),
        "source_pbf": {
            "path": str(args.pbf),
            "bytes": pbf_snapshot["bytes"],
            "mtime_utc": time.strftime(
                "%Y-%m-%dT%H:%M:%SZ",
                time.gmtime(pbf_snapshot["mtime_ns"] / 1_000_000_000),
            ),
            "identity": pbf_snapshot,
        },
        "states_geojson": str(args.states_geojson),
        "places_db": str(args.places_db),
        "osm_config": str(args.osm_config),
        "active_states": active_slugs,
        "municipality_counts": municipalities.counts,
        "resource_preflight": preflight,
        "scanned_by_layer": dict(scanned_by_layer),
        "accepted_by_state": dict(accepted_by_state),
        "accepted_by_category": dict(accepted_by_category),
        "accepted_by_osm_type": dict(accepted_by_type),
        "locality_sources": dict(locality_sources),
        "rejected": dict(rejected),
        "diagnostics": dict(diagnostics),
        "exact_duplicate_features": exact_duplicates,
        "rows": accepted,
        "timings_seconds": {
            "scan": round(scan_seconds, 3),
            "finalize_indexes": round(finalize_seconds, 3),
            "validate": round(validate_seconds, 3),
            "total": round(time.perf_counter() - started, 3),
        },
        "max_rss_bytes": max_rss_bytes(),
        "candidate_database_bytes": candidate_path.stat().st_size,
        "versions": {
            "python": platform.python_version(),
            "sqlite": sqlite3.sqlite_version,
            "gdal": gdal.VersionInfo("--version"),
        },
        "self_tests": self_tests,
    }


def default_sidecar(output: Path, kind: str) -> Path:
    name = output.name
    if name.endswith(".sqlite"):
        name = name[: -len(".sqlite")]
    return output.with_name(f"{name}.{kind}.json")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a minimal OpenKataster OSM POI SQLite index in one PBF pass."
    )
    parser.add_argument("--pbf", type=Path, required=True)
    parser.add_argument("--states-geojson", type=Path, required=True)
    parser.add_argument("--active-states", required=True, help="Comma-separated canonical slugs")
    parser.add_argument("--places-db", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--metrics", type=Path)
    parser.add_argument("--osm-config", type=Path, default=DEFAULT_OSM_CONFIG)
    parser.add_argument("--temp-dir", type=Path)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--commit-every", type=int, default=50_000)
    parser.add_argument("--progress-every", type=int, default=250_000)
    parser.add_argument("--gdal-max-tmpfile-mib", type=int, default=64)
    parser.add_argument("--sqlite-cache-mib", type=int, default=64)
    parser.add_argument("--max-locality-distance-m", type=float, default=100_000)
    parser.add_argument("--min-available-memory-mib", type=int, default=768)
    parser.add_argument(
        "--skip-resource-preflight",
        action="store_true",
        help="Bypass conservative disk/RAM checks after an operator review",
    )
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    args.pbf = args.pbf.resolve()
    args.states_geojson = args.states_geojson.resolve()
    args.places_db = args.places_db.resolve()
    args.output = args.output.resolve()
    args.osm_config = args.osm_config.resolve()
    args.manifest = (
        args.manifest.resolve() if args.manifest else default_sidecar(args.output, "manifest")
    )
    args.metrics = (
        args.metrics.resolve() if args.metrics else default_sidecar(args.output, "metrics")
    )
    args.temp_dir = (
        args.temp_dir.resolve() if args.temp_dir else args.output.parent.resolve()
    )
    artifact_paths = (args.output, args.manifest, args.metrics)
    if len(set(artifact_paths)) != len(artifact_paths):
        parser.error(
            "--output, --manifest and --metrics must be three distinct paths"
        )

    for label, path in (
        ("PBF", args.pbf),
        ("states GeoJSON", args.states_geojson),
        ("places DB", args.places_db),
        ("OSM config", args.osm_config),
    ):
        if not path.is_file():
            parser.error(f"{label} not found: {path}")
    if args.commit_every < 1 or args.progress_every < 1:
        parser.error("commit/progress values must be positive")
    if args.gdal_max_tmpfile_mib < 1 or args.sqlite_cache_mib < 8:
        parser.error("temporary/cache memory values are too small")
    if args.min_available_memory_mib < 0:
        parser.error("--min-available-memory-mib must not be negative")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.metrics.parent.mkdir(parents=True, exist_ok=True)
    args.temp_dir.mkdir(parents=True, exist_ok=True)
    if not args.overwrite:
        existing = [path for path in (args.output, args.manifest, args.metrics) if path.exists()]
        if existing:
            parser.error(f"Output exists; use --overwrite: {existing}")
    parse_active_states(args.active_states)
    return args


def main(argv: Sequence[str] | None = None) -> int:
    gdal.UseExceptions()
    ogr.UseExceptions()
    osr.UseExceptions()
    args = parse_args(argv)
    main_started = time.perf_counter()
    with exclusive_output_lock(args.output):
        if not args.overwrite:
            existing = [
                path
                for path in (args.output, args.manifest, args.metrics)
                if path.exists()
            ]
            if existing:
                raise RuntimeError(f"Output appeared while waiting for lock: {existing}")
        args.build_id = uuid.uuid4().hex
        candidate = make_candidate_path(args.output)
        try:
            metrics = build_index(args, candidate)
            sha_started = time.perf_counter()
            database_sha256 = sha256_file(candidate)
            metrics["timings_seconds"]["sha256"] = round(
                time.perf_counter() - sha_started, 3
            )
            metrics["database"] = {
                "path": str(args.output),
                "bytes": candidate.stat().st_size,
                "sha256": database_sha256,
            }
            manifest = {
                "format": FORMAT_NAME,
                "schema_version": SCHEMA_VERSION,
                "schema_fingerprint": database_schema_fingerprint(),
                "build_id": args.build_id,
                "created_at_utc": metrics["finished_at_utc"],
                "database": metrics["database"],
                "rows": metrics["rows"],
                "active_states": metrics["active_states"],
                "source_pbf": metrics["source_pbf"],
                "states_geojson": str(args.states_geojson),
                "places_db": str(args.places_db),
                "attribution": OSM_ATTRIBUTION,
                "copyright_url": OSM_COPYRIGHT_URL,
                "locality_fallback": {
                    "policy": (
                        "OSM addr:city first; otherwise nearest GN250 Gemeinde "
                        "reference point inside the matched federal state"
                    ),
                    "max_distance_m": args.max_locality_distance_m,
                    "source_counts": metrics["locality_sources"],
                },
                "resource_preflight": metrics["resource_preflight"],
                "versions": metrics["versions"],
                "self_tests": metrics["self_tests"],
            }
            os.replace(candidate, args.output)
            fsync_directory(args.output.parent)
            json_atomic_write(args.manifest, manifest)
            metrics["timings_seconds"]["total"] = round(
                time.perf_counter() - main_started, 3
            )
            json_atomic_write(args.metrics, metrics)
            if not args.quiet:
                print(
                    json.dumps(
                        {
                            "status": "ok",
                            "output": str(args.output),
                            "rows": metrics["rows"],
                            "bytes": metrics["database"]["bytes"],
                            "sha256": database_sha256,
                            "seconds": metrics["timings_seconds"]["total"],
                            "max_rss_bytes": metrics["max_rss_bytes"],
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                )
            return 0
        except BaseException:
            for suffix in ("", "-journal", "-wal", "-shm"):
                try:
                    Path(str(candidate) + suffix).unlink()
                except FileNotFoundError:
                    pass
            raise


if __name__ == "__main__":
    raise SystemExit(main())
