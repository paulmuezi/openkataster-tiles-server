#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import unicodedata
from pathlib import Path

DATA_DIR = Path(os.environ.get("OPENKATASTER_FEATURE_DATA_DIR", "/srv/openkataster-tiles/data"))
OUT_DB = Path(os.environ.get("OPENKATASTER_GEOCODER_DB", "/srv/openkataster-tiles/geocoder/geocoder.sqlite"))
TMP_DB = OUT_DB.with_suffix(".sqlite.tmp")
BATCH_SIZE = int(os.environ.get("OPENKATASTER_GEOCODER_BATCH_SIZE", "50000"))

STATE_LABELS = {
    "baden-wurttemberg": "Baden-Wuerttemberg",
    "bayern": "Bayern",
    "berlin": "Berlin",
    "brandenburg": "Brandenburg",
    "bremen": "Bremen",
    "hamburg": "Hamburg",
    "hessen": "Hessen",
    "mecklenburg-vorpommern": "Mecklenburg-Vorpommern",
    "niedersachsen": "Niedersachsen",
    "nordrhein-westfalen": "Nordrhein-Westfalen",
    "rheinland-pfalz": "Rheinland-Pfalz",
    "saarland": "Saarland",
    "sachsen": "Sachsen",
    "sachsen-anhalt": "Sachsen-Anhalt",
    "schleswig-holstein": "Schleswig-Holstein",
    "thuringen": "Thueringen",
}
CITY_STATES = {"berlin", "bremen", "hamburg"}
HOUSE_RE = re.compile(r"^(.+?)\s+([0-9][0-9A-Za-zÄÖÜäöüß./\- ]*)$")


def normalize_text(value: str | None) -> str:
    text = (value or "").strip().casefold()
    replacements = (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss"))
    for source, target in replacements:
        text = text.replace(source, target)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("str.", "strasse")
    text = re.sub(r"\bstr\b", "strasse", text)
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def normalize_house(value: str | None) -> str:
    text = normalize_text(value)
    return re.sub(r"\s+", "", text)


def state_key_from_path(path: Path) -> str:
    name = path.name
    if name.endswith(".features.sqlite"):
        return name[: -len(".features.sqlite")]
    return path.stem.replace("_", "-")


def state_label(state: str) -> str:
    return STATE_LABELS.get(state, state.replace("-", " ").title())


def table_names(con: sqlite3.Connection) -> set[str]:
    return {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def table_columns(con: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in con.execute(f"PRAGMA table_info({table})")}


def load_json(value: object) -> dict:
    if isinstance(value, dict):
        return value
    if value is None:
        return {}
    try:
        data = json.loads(str(value))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def split_street_house(value: str | None) -> tuple[str, str]:
    text = str(value or "").strip()
    if "," in text:
        text = text.split(",", 1)[0].strip()
    match = HOUSE_RE.match(text)
    if not match:
        return "", ""
    return match.group(1).strip(), match.group(2).strip()


def clean_label_part(value: str | None) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def infer_address(props: dict, state: str) -> tuple[str, str, str, str]:
    street = clean_label_part(
        props.get("street")
        or props.get("strasse")
        or props.get("straße")
        or props.get("street_name")
        or props.get("strassenname")
    )
    house = clean_label_part(
        props.get("house_number")
        or props.get("hausnummer")
        or props.get("house")
        or props.get("number")
    )
    city = clean_label_part(
        props.get("city")
        or props.get("ort")
        or props.get("municipality")
        or props.get("gemeinde")
        or props.get("place")
    )
    street_house = clean_label_part(props.get("street_house") or props.get("address") or props.get("adresse"))
    label = clean_label_part(props.get("label") or street_house)
    if (not street or not house) and street_house:
        parsed_street, parsed_house = split_street_house(street_house)
        street = street or parsed_street
        house = house or parsed_house
    if (not street or not house) and label:
        parsed_street, parsed_house = split_street_house(label)
        street = street or parsed_street
        house = house or parsed_house
    if not city and state in CITY_STATES:
        city = state_label(state)
    if not label:
        label = " ".join(part for part in (street, house) if part).strip()
        if city:
            label = f"{label}, {city}" if label else city
    return label, street, house, city


def bounded(value, fallback):
    try:
        if value is None:
            return fallback
        return float(value)
    except (TypeError, ValueError):
        return fallback


def iter_feature_addresses(path: Path, state: str):
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        tables = table_names(con)
        if "feature_addresses" not in tables or "features" not in tables:
            return
        fa_cols = table_columns(con, "feature_addresses")
        f_cols = table_columns(con, "features")
        if {"source_db", "kind", "gml_id", "properties_json"}.issubset(fa_cols) and {"source_db", "kind", "gml_id"}.issubset(f_cols):
            center_lon_expr = "f.center_lon" if "center_lon" in f_cols else "((f.min_lon + f.max_lon) / 2.0)"
            center_lat_expr = "f.center_lat" if "center_lat" in f_cols else "((f.min_lat + f.max_lat) / 2.0)"
            sql = (
                "SELECT fa.properties_json AS address_properties_json, fa.kind AS address_kind, "
                "fa.source_db AS source_db, fa.gml_id AS gml_id, f.kind AS feature_kind, "
                f"{center_lon_expr} AS center_lon, {center_lat_expr} AS center_lat, "
                "f.min_lon AS min_lon, f.min_lat AS min_lat, f.max_lon AS max_lon, f.max_lat AS max_lat "
                "FROM feature_addresses fa "
                "JOIN features f ON f.source_db = fa.source_db AND f.kind = fa.kind AND f.gml_id = fa.gml_id"
            )
            for row in con.execute(sql):
                props = load_json(row["address_properties_json"])
                label, street, house, city = infer_address(props, state)
                if not street:
                    continue
                lon = bounded(row["center_lon"], None)
                lat = bounded(row["center_lat"], None)
                if lon is None or lat is None:
                    continue
                min_lon = bounded(row["min_lon"], lon)
                min_lat = bounded(row["min_lat"], lat)
                max_lon = bounded(row["max_lon"], lon)
                max_lat = bounded(row["max_lat"], lat)
                yield (
                    state,
                    state_label(state),
                    label,
                    street,
                    normalize_text(street),
                    house,
                    normalize_house(house),
                    city,
                    normalize_text(city),
                    lon,
                    lat,
                    min_lon,
                    min_lat,
                    max_lon,
                    max_lat,
                    str(row["feature_kind"] or row["address_kind"] or ""),
                    str(row["source_db"] or ""),
                    str(row["gml_id"] or ""),
                )
            return
        if "feature_id" in fa_cols and "id" in f_cols:
            address_expr = "fa.properties_json" if "properties_json" in fa_cols else "fa.address"
            street_house_expr = "fa.street_house" if "street_house" in fa_cols else "NULL"
            center_lon_expr = "f.center_lon" if "center_lon" in f_cols else "((f.min_lon + f.max_lon) / 2.0)"
            center_lat_expr = "f.center_lat" if "center_lat" in f_cols else "((f.min_lat + f.max_lat) / 2.0)"
            sql = (
                f"SELECT {address_expr} AS address_value, {street_house_expr} AS street_house, "
                "f.kind AS feature_kind, f.source_db AS source_db, f.gml_id AS gml_id, "
                f"{center_lon_expr} AS center_lon, {center_lat_expr} AS center_lat, "
                "f.min_lon AS min_lon, f.min_lat AS min_lat, f.max_lon AS max_lon, f.max_lat AS max_lat "
                "FROM feature_addresses fa JOIN features f ON f.id = fa.feature_id"
            )
            for row in con.execute(sql):
                props = load_json(row["address_value"])
                if not props:
                    props = {"label": row["address_value"], "street_house": row["street_house"]}
                label, street, house, city = infer_address(props, state)
                if not street:
                    continue
                lon = bounded(row["center_lon"], None)
                lat = bounded(row["center_lat"], None)
                if lon is None or lat is None:
                    continue
                min_lon = bounded(row["min_lon"], lon)
                min_lat = bounded(row["min_lat"], lat)
                max_lon = bounded(row["max_lon"], lon)
                max_lat = bounded(row["max_lat"], lat)
                yield (
                    state,
                    state_label(state),
                    label,
                    street,
                    normalize_text(street),
                    house,
                    normalize_house(house),
                    city,
                    normalize_text(city),
                    lon,
                    lat,
                    min_lon,
                    min_lat,
                    max_lon,
                    max_lat,
                    str(row["feature_kind"] or ""),
                    str(row["source_db"] or ""),
                    str(row["gml_id"] or ""),
                )
    finally:
        con.close()


def create_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        "PRAGMA journal_mode=OFF;"
        "PRAGMA synchronous=OFF;"
        "PRAGMA temp_store=MEMORY;"
        "CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);"
        "CREATE TABLE addresses("
        "id INTEGER PRIMARY KEY,"
        "state TEXT NOT NULL,state_label TEXT NOT NULL,label TEXT NOT NULL,"
        "street TEXT NOT NULL,street_norm TEXT NOT NULL,house_number TEXT NOT NULL,house_norm TEXT NOT NULL,"
        "city TEXT NOT NULL,city_norm TEXT NOT NULL,"
        "lon REAL NOT NULL,lat REAL NOT NULL,min_lon REAL NOT NULL,min_lat REAL NOT NULL,max_lon REAL NOT NULL,max_lat REAL NOT NULL,"
        "feature_kind TEXT NOT NULL,source_db TEXT NOT NULL,gml_id TEXT NOT NULL"
        ");"
        "CREATE TABLE streets("
        "id INTEGER PRIMARY KEY,state TEXT NOT NULL,state_label TEXT NOT NULL,street TEXT NOT NULL,street_norm TEXT NOT NULL,"
        "city TEXT NOT NULL,city_norm TEXT NOT NULL,lon REAL NOT NULL,lat REAL NOT NULL,"
        "min_lon REAL NOT NULL,min_lat REAL NOT NULL,max_lon REAL NOT NULL,max_lat REAL NOT NULL,address_count INTEGER NOT NULL"
        ");"
    )


def flush(con: sqlite3.Connection, rows: list[tuple]) -> int:
    if not rows:
        return 0
    con.executemany(
        "INSERT INTO addresses(state,state_label,label,street,street_norm,house_number,house_norm,city,city_norm,lon,lat,min_lon,min_lat,max_lon,max_lat,feature_kind,source_db,gml_id) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    return len(rows)


def finalize(con: sqlite3.Connection) -> None:
    con.executescript(
        "CREATE INDEX idx_addresses_street_house_state ON addresses(street_norm, house_norm, state);"
        "CREATE INDEX idx_addresses_street_state ON addresses(street_norm, state);"
        "CREATE INDEX idx_addresses_city ON addresses(city_norm);"
        "CREATE INDEX idx_addresses_gml ON addresses(state, source_db, feature_kind, gml_id);"
        "CREATE VIRTUAL TABLE address_rtree USING rtree(id, min_lon, max_lon, min_lat, max_lat);"
        "INSERT INTO address_rtree SELECT id, min_lon, max_lon, min_lat, max_lat FROM addresses;"
        "INSERT INTO streets(state,state_label,street,street_norm,city,city_norm,lon,lat,min_lon,min_lat,max_lon,max_lat,address_count) "
        "SELECT state,state_label,MIN(street),street_norm,MIN(city),city_norm,AVG(lon),AVG(lat),MIN(min_lon),MIN(min_lat),MAX(max_lon),MAX(max_lat),COUNT(*) "
        "FROM addresses WHERE street_norm <> '' GROUP BY state, street_norm, city_norm;"
        "CREATE INDEX idx_streets_street_state ON streets(street_norm, state);"
        "CREATE INDEX idx_streets_city ON streets(city_norm);"
        "CREATE VIRTUAL TABLE street_rtree USING rtree(id, min_lon, max_lon, min_lat, max_lat);"
        "INSERT INTO street_rtree SELECT id, min_lon, max_lon, min_lat, max_lat FROM streets;"
        "ANALYZE;"
    )


def main() -> int:
    start = time.time()
    OUT_DB.parent.mkdir(parents=True, exist_ok=True)
    TMP_DB.unlink(missing_ok=True)
    con = sqlite3.connect(TMP_DB)
    create_schema(con)
    files = sorted(DATA_DIR.glob("*.features.sqlite"))
    total = 0
    try:
        for path in files:
            state = state_key_from_path(path)
            real_path = path.resolve()
            count = 0
            batch: list[tuple] = []
            print(f"{state}: lese {real_path}", flush=True)
            for row in iter_feature_addresses(real_path, state):
                batch.append(row)
                if len(batch) >= BATCH_SIZE:
                    count += flush(con, batch)
                    total += len(batch)
                    batch.clear()
                    if count % 250000 == 0:
                        con.commit()
                        print(f"  {state}: {count:,} Adressen", flush=True)
            count += flush(con, batch)
            total += len(batch)
            con.commit()
            print(f"  {state}: fertig {count:,} Adressen", flush=True)
        print("erstelle Suchindizes ...", flush=True)
        finalize(con)
        address_count = con.execute("SELECT COUNT(*) FROM addresses").fetchone()[0]
        street_count = con.execute("SELECT COUNT(*) FROM streets").fetchone()[0]
        con.executemany(
            "INSERT INTO metadata(key,value) VALUES (?,?)",
            [
                ("format", "openkataster-geocoder-sqlite"),
                ("built_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())),
                ("address_count", str(address_count)),
                ("street_count", str(street_count)),
            ],
        )
        con.commit()
    finally:
        con.close()
    os.replace(TMP_DB, OUT_DB)
    elapsed = time.time() - start
    print(f"Geocoder geschrieben: {OUT_DB}", flush=True)
    print(f"Adressen: {address_count:,}; Straßen: {street_count:,}; Dauer: {elapsed:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
