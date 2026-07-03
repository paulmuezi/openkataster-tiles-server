#!/usr/bin/env python3
import json
import os
import re
import sqlite3
import time
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path

DATA_DIR = Path(os.environ.get("OPENKATASTER_TILE_DATA_DIR", "/srv/openkataster-tiles/data"))
TARGET = DATA_DIR / "vg250_places.sqlite"
BASE_URL = "https://sgx.geodatenzentrum.de/wfs_vg250"

LKZ_TO_STATE = {
    "BW": ("baden-wurttemberg", "Baden-Württemberg"),
    "BY": ("bayern", "Bayern"),
    "BE": ("berlin", "Berlin"),
    "BB": ("brandenburg", "Brandenburg"),
    "HB": ("bremen", "Bremen"),
    "HH": ("hamburg", "Hamburg"),
    "HE": ("hessen", "Hessen"),
    "MV": ("mecklenburg-vorpommern", "Mecklenburg-Vorpommern"),
    "NI": ("niedersachsen", "Niedersachsen"),
    "NW": ("nordrhein-westfalen", "Nordrhein-Westfalen"),
    "RP": ("rheinland-pfalz", "Rheinland-Pfalz"),
    "SL": ("saarland", "Saarland"),
    "SN": ("sachsen", "Sachsen"),
    "ST": ("sachsen-anhalt", "Sachsen-Anhalt"),
    "SH": ("schleswig-holstein", "Schleswig-Holstein"),
    "TH": ("thuringen", "Thüringen"),
}


def normalize_text(value: str) -> str:
    text = (value or "").strip().casefold()
    for source, target in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        text = text.replace(source, target)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text).strip()


def compact_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", normalize_text(value))


def fetch_feature_collection(type_name: str) -> dict:
    query = {
        "SERVICE": "WFS",
        "VERSION": "2.0.0",
        "REQUEST": "GetFeature",
        "TYPENAMES": type_name,
        "COUNT": "20000",
        "SRSNAME": "EPSG:4326",
        "OUTPUTFORMAT": "application/json",
    }
    url = f"{BASE_URL}?{urllib.parse.urlencode(query)}"
    request = urllib.request.Request(url, headers={"User-Agent": "OpenKataster VG250 indexer"})
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                payload = response.read()
            return json.loads(payload.decode("utf-8"))
        except Exception:
            if attempt == 3:
                raise
            time.sleep(attempt * 2)
    raise RuntimeError("unreachable")


def iter_points(value):
    if not isinstance(value, (list, tuple)):
        return
    if len(value) >= 2 and all(isinstance(item, (int, float)) for item in value[:2]):
        yield float(value[0]), float(value[1])
        return
    for item in value:
        yield from iter_points(item)


def geometry_bbox(geometry: dict | None):
    if not isinstance(geometry, dict):
        return None
    points = list(iter_points(geometry.get("coordinates")))
    if not points:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return min(xs), min(ys), max(xs), max(ys)


def bbox_from_ring(ring):
    points = [(float(point[0]), float(point[1])) for point in ring if isinstance(point, (list, tuple)) and len(point) >= 2]
    if not points:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return min(xs), min(ys), max(xs), max(ys)


def point_in_ring(lon: float, lat: float, ring) -> bool:
    inside = False
    points = [(float(point[0]), float(point[1])) for point in ring if isinstance(point, (list, tuple)) and len(point) >= 2]
    if len(points) < 3:
        return False
    j = len(points) - 1
    for i, (xi, yi) in enumerate(points):
        xj, yj = points[j]
        intersects = (yi > lat) != (yj > lat)
        if intersects:
            x_at_y = (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi
            if lon < x_at_y:
                inside = not inside
        j = i
    return inside


def geometry_components(geometry: dict | None):
    if not isinstance(geometry, dict):
        return []
    geometry_type = str(geometry.get("type") or "")
    coords = geometry.get("coordinates")
    result = []
    if geometry_type == "Polygon" and isinstance(coords, list):
        ring = coords[0] if coords else []
        bbox = bbox_from_ring(ring)
        if bbox:
            result.append({"bbox": bbox, "ring": ring})
    elif geometry_type == "MultiPolygon" and isinstance(coords, list):
        for polygon in coords:
            ring = polygon[0] if isinstance(polygon, list) and polygon else []
            bbox = bbox_from_ring(ring)
            if bbox:
                result.append({"bbox": bbox, "ring": ring})
    elif geometry_type == "GeometryCollection":
        for child in geometry.get("geometries") or []:
            result.extend(geometry_components(child))
    else:
        bbox = geometry_bbox(geometry)
        if bbox:
            result.append({"bbox": bbox, "ring": None})
    return result


def merge_bbox(existing, bbox):
    if existing is None:
        return bbox
    return (
        min(existing[0], bbox[0]),
        min(existing[1], bbox[1]),
        max(existing[2], bbox[2]),
        max(existing[3], bbox[3]),
    )


def select_display_bbox(parts, fallback_bbox, lon: float, lat: float):
    containing = []
    for part in parts or []:
        bbox = part.get("bbox")
        if not bbox:
            continue
        if not (bbox[0] <= lon <= bbox[2] and bbox[1] <= lat <= bbox[3]):
            continue
        ring = part.get("ring")
        if ring and not point_in_ring(lon, lat, ring):
            continue
        area = max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])
        containing.append((area, bbox))
    if containing:
        return min(containing, key=lambda item: item[0])[1]
    return fallback_bbox


def feature_key(props: dict) -> str:
    ags = str(props.get("ags") or "").strip()
    ars = str(props.get("ars") or "").strip()
    return ags or ars


def priority_for(bez: str, name: str) -> int:
    folded = normalize_text(f"{bez} {name}")
    if "stadt" in folded:
        return 1
    if "gemeinde" in folded:
        return 2
    return 5


def main() -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    points_fc = fetch_feature_collection("vg250:vg250_pk")
    areas_fc = fetch_feature_collection("vg250:vg250_gem")

    area_by_key = {}
    area_parts_by_key = {}
    for feature in areas_fc.get("features", []):
        props = feature.get("properties") or {}
        components = geometry_components(feature.get("geometry"))
        if not components:
            continue
        bbox = None
        for component in components:
            bbox = merge_bbox(bbox, component["bbox"])
        for key in (str(props.get("ags") or "").strip(), str(props.get("ars") or "").strip()):
            if key:
                area_by_key[key] = merge_bbox(area_by_key.get(key), bbox)
                area_parts_by_key.setdefault(key, []).extend(components)

    tmp = TARGET.with_name(f".{TARGET.name}.tmp")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    con.execute("PRAGMA journal_mode=OFF")
    con.execute("PRAGMA synchronous=OFF")
    con.executescript(
        """
        CREATE TABLE places (
            id INTEGER PRIMARY KEY,
            state TEXT NOT NULL,
            state_label TEXT NOT NULL,
            lkz TEXT,
            ags TEXT,
            ars TEXT,
            name TEXT NOT NULL,
            name_norm TEXT NOT NULL,
            name_ascii TEXT NOT NULL,
            bez TEXT,
            bem TEXT,
            otl TEXT,
            lon REAL NOT NULL,
            lat REAL NOT NULL,
            min_lon REAL,
            min_lat REAL,
            max_lon REAL,
            max_lat REAL,
            priority INTEGER NOT NULL
        );
        """
    )

    count = 0
    skipped = 0
    for feature in points_fc.get("features", []):
        props = feature.get("properties") or {}
        name = str(props.get("gen") or "").strip()
        lkz = str(props.get("lkz") or "").strip()
        state_entry = LKZ_TO_STATE.get(lkz)
        geom = feature.get("geometry") or {}
        coords = geom.get("coordinates") or []
        if not name or not state_entry or len(coords) < 2:
            skipped += 1
            continue
        try:
            lon = float(props.get("lon_dez") or coords[0])
            lat = float(props.get("lat_dez") or coords[1])
        except (TypeError, ValueError):
            skipped += 1
            continue
        ags = str(props.get("ags") or "").strip()
        ars = str(props.get("ars") or "").strip()
        raw_bbox = area_by_key.get(ags) or area_by_key.get(ars) or (lon, lat, lon, lat)
        parts = area_parts_by_key.get(ags) or area_parts_by_key.get(ars) or []
        bbox = select_display_bbox(parts, raw_bbox, lon, lat)
        state, state_label = state_entry
        bez = str(props.get("bez") or "").strip()
        con.execute(
            """
            INSERT INTO places (
                state, state_label, lkz, ags, ars, name, name_norm, name_ascii,
                bez, bem, otl, lon, lat, min_lon, min_lat, max_lon, max_lat, priority
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                state,
                state_label,
                lkz,
                ags,
                ars,
                name,
                normalize_text(name),
                compact_key(name),
                bez,
                str(props.get("bem") or "").strip(),
                str(props.get("otl") or "").strip(),
                lon,
                lat,
                float(bbox[0]),
                float(bbox[1]),
                float(bbox[2]),
                float(bbox[3]),
                priority_for(bez, name),
            ),
        )
        count += 1

    con.executescript(
        """
        CREATE INDEX idx_places_state_name ON places(state, name_norm);
        CREATE INDEX idx_places_name ON places(name_norm);
        CREATE INDEX idx_places_ascii ON places(name_ascii);
        CREATE INDEX idx_places_ags ON places(ags);
        CREATE INDEX idx_places_ars ON places(ars);
        """
    )
    con.commit()
    con.close()
    os.replace(tmp, TARGET)
    print(json.dumps({"target": str(TARGET), "places": count, "skipped": skipped, "size": TARGET.stat().st_size}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
