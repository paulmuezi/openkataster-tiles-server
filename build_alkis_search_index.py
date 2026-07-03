#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import unicodedata
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
TILES_TARGET_DIR = Path(os.environ.get("ALKIS_TILES_TARGET_DIR", ".")).expanduser().resolve()
SOURCE_FEATURES = Path(
    os.environ.get("ALKIS_SEARCH_INDEX_SOURCE_FEATURES", str(TILES_TARGET_DIR / "features.sqlite"))
).expanduser().resolve()
OUT_PATH = Path(os.environ.get("ALKIS_SEARCH_INDEX_OUT", str(TILES_TARGET_DIR / "search.sqlite"))).expanduser().resolve()


def normalize_text(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = text.replace("ß", "ss").replace("ẞ", "ss")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.casefold()
    cleaned = []
    previous_space = False
    for ch in text:
        if ch.isalnum():
            cleaned.append(ch)
            previous_space = False
        elif not previous_space:
            cleaned.append(" ")
            previous_space = True
    return " ".join("".join(cleaned).split())


def normalize_compact(value: object) -> str:
    return normalize_text(value).replace(" ", "")


def load_json(raw: object) -> dict:
    try:
        data = json.loads(str(raw or "{}"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def split_street_house(value: object) -> tuple[str, str]:
    text = str(value or "").strip()
    if not text:
        return "", ""
    match = re.match(r"^(?P<street>.+\D)\s+(?P<number>\d+\s*[A-Za-z]?(?:\s*[-/]\s*\d+\s*[A-Za-z]?)?)$", text)
    if not match:
        return text, ""
    return " ".join(match.group("street").split()), " ".join(match.group("number").split())


def city_from_address_label(value: object) -> str:
    parts = [part.strip() for part in str(value or "").split(",") if part.strip()]
    if len(parts) < 2:
        return ""
    return parts[1]


def table_columns(con: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in con.execute(f"PRAGMA table_info({table})")}


def center_from_row(row: sqlite3.Row) -> tuple[float | None, float | None]:
    keys = row.keys()
    if "center_lon" in keys and "center_lat" in keys and row["center_lon"] is not None and row["center_lat"] is not None:
        return float(row["center_lon"]), float(row["center_lat"])
    min_lon = row["min_lon"]
    max_lon = row["max_lon"]
    min_lat = row["min_lat"]
    max_lat = row["max_lat"]
    if min_lon is None or max_lon is None or min_lat is None or max_lat is None:
        return None, None
    return (float(min_lon) + float(max_lon)) / 2, (float(min_lat) + float(max_lat)) / 2


def create_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        PRAGMA journal_mode = OFF;
        PRAGMA synchronous = OFF;
        PRAGMA temp_store = FILE;

        CREATE TABLE metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE address_lookup (
            id INTEGER PRIMARY KEY,
            feature_kind TEXT NOT NULL,
            source_db TEXT NOT NULL,
            gml_id TEXT NOT NULL,
            street_norm TEXT NOT NULL,
            street_label TEXT NOT NULL,
            house_number_norm TEXT NOT NULL,
            house_number_label TEXT NOT NULL,
            city_norm TEXT NOT NULL,
            city_label TEXT NOT NULL,
            post_code TEXT NOT NULL,
            label TEXT NOT NULL,
            lon REAL,
            lat REAL,
            min_lon REAL NOT NULL,
            max_lon REAL NOT NULL,
            min_lat REAL NOT NULL,
            max_lat REAL NOT NULL,
            UNIQUE(feature_kind, source_db, gml_id, street_norm, house_number_norm)
        );

        CREATE TABLE parcel_lookup (
            id INTEGER PRIMARY KEY,
            source_db TEXT NOT NULL,
            gml_id TEXT NOT NULL,
            gemarkung_norm TEXT NOT NULL,
            gemarkung_label TEXT NOT NULL,
            gemarkungsnummer TEXT NOT NULL,
            flur_norm TEXT NOT NULL,
            flur_label TEXT NOT NULL,
            flurstueck_norm TEXT NOT NULL,
            flurstueck_label TEXT NOT NULL,
            zaehler TEXT NOT NULL,
            nenner TEXT NOT NULL,
            amtliche_flaeche_m2 REAL,
            lon REAL,
            lat REAL,
            min_lon REAL NOT NULL,
            max_lon REAL NOT NULL,
            min_lat REAL NOT NULL,
            max_lat REAL NOT NULL,
            UNIQUE(source_db, gml_id)
        );

        CREATE TABLE street_lookup (
            id INTEGER PRIMARY KEY,
            street_norm TEXT NOT NULL,
            street_label TEXT NOT NULL,
            city_norm TEXT NOT NULL,
            city_label TEXT NOT NULL,
            post_code TEXT NOT NULL,
            label TEXT NOT NULL,
            address_count INTEGER NOT NULL,
            feature_count INTEGER NOT NULL,
            lon REAL,
            lat REAL,
            min_lon REAL NOT NULL,
            max_lon REAL NOT NULL,
            min_lat REAL NOT NULL,
            max_lat REAL NOT NULL,
            UNIQUE(city_norm, street_norm, post_code)
        );
        """
    )


def create_indexes(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE INDEX idx_address_exact
            ON address_lookup(city_norm, street_norm, house_number_norm);
        CREATE INDEX idx_address_street
            ON address_lookup(city_norm, street_norm);
        CREATE INDEX idx_address_no_city
            ON address_lookup(street_norm, house_number_norm);
        CREATE INDEX idx_address_gml
            ON address_lookup(feature_kind, gml_id);
        CREATE INDEX idx_street_exact
            ON street_lookup(city_norm, street_norm);
        CREATE INDEX idx_street_no_city
            ON street_lookup(street_norm);

        CREATE INDEX idx_parcel_exact
            ON parcel_lookup(gemarkung_norm, flur_norm, flurstueck_norm);
        CREATE INDEX idx_parcel_gemarkung
            ON parcel_lookup(gemarkung_norm);
        CREATE INDEX idx_parcel_gml
            ON parcel_lookup(gml_id);

        ANALYZE;
        """
    )


def insert_metadata(con: sqlite3.Connection, key: str, value: object) -> None:
    con.execute(
        "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)",
        (key, str(value)),
    )


def build_address_lookup(source: sqlite3.Connection, target: sqlite3.Connection) -> int:
    feature_cols = table_columns(source, "features")
    feature_address_cols = table_columns(source, "feature_addresses")
    center_select = (
        "f.center_lon, f.center_lat,"
        if {"center_lon", "center_lat"}.issubset(feature_cols)
        else ""
    )
    join_conditions = ["f.kind = fa.kind", "f.gml_id = fa.gml_id"]
    if "source_db" in feature_cols and "source_db" in feature_address_cols:
        join_conditions.insert(0, "f.source_db = fa.source_db")
    join_sql = "\n         AND ".join(join_conditions)
    sql = f"""
        SELECT
            fa.source_db,
            fa.kind AS feature_kind,
            fa.gml_id,
            fa.properties_json,
            {center_select}
            f.min_lon,
            f.max_lon,
            f.min_lat,
            f.max_lat
        FROM feature_addresses fa
        JOIN features f
          ON {join_sql}
        WHERE fa.kind IN ('building', 'parcel')
    """
    count = 0
    for row in source.execute(sql):
        props = load_json(row["properties_json"])
        street = str(props.get("street") or props.get("strasse") or props.get("straße") or "").strip()
        house_number = str(props.get("house_number") or props.get("hausnummer") or "").strip()
        label = str(props.get("label") or " ".join(part for part in [street, house_number] if part)).strip()
        if (not street or not house_number) and props.get("street_house"):
            parsed_street, parsed_house_number = split_street_house(props.get("street_house"))
            street = street or parsed_street
            house_number = house_number or parsed_house_number
        if not street and not label:
            continue
        city = str(props.get("city") or props.get("ort") or props.get("gemeinde") or city_from_address_label(label)).strip()
        post_code = str(props.get("post_code") or props.get("postal_code") or props.get("plz") or "").strip()
        lon, lat = center_from_row(row)
        target.execute(
            """
            INSERT OR IGNORE INTO address_lookup (
                feature_kind, source_db, gml_id,
                street_norm, street_label,
                house_number_norm, house_number_label,
                city_norm, city_label, post_code, label,
                lon, lat, min_lon, max_lon, min_lat, max_lat
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["feature_kind"],
                row["source_db"],
                row["gml_id"],
                normalize_text(street),
                street,
                normalize_compact(house_number),
                house_number,
                normalize_text(city),
                city,
                post_code,
                label,
                lon,
                lat,
                row["min_lon"],
                row["max_lon"],
                row["min_lat"],
                row["max_lat"],
            ),
        )
        count += 1
        if count % 100_000 == 0:
            print(f"  Adress-Suchindex: {count:,}", flush=True)
    row = target.execute("SELECT count(*) FROM address_lookup").fetchone()
    return int(row[0] or 0)


def build_street_lookup(target: sqlite3.Connection) -> int:
    target.execute(
        """
        INSERT OR IGNORE INTO street_lookup (
            street_norm, street_label,
            city_norm, city_label, post_code, label,
            address_count, feature_count,
            lon, lat, min_lon, max_lon, min_lat, max_lat
        )
        SELECT
            street_norm,
            street_label,
            city_norm,
            city_label,
            post_code,
            CASE
                WHEN city_label <> '' THEN street_label || ', ' || city_label
                ELSE street_label
            END AS label,
            count(DISTINCT label) AS address_count,
            count(*) AS feature_count,
            avg(lon) AS lon,
            avg(lat) AS lat,
            min(min_lon) AS min_lon,
            max(max_lon) AS max_lon,
            min(min_lat) AS min_lat,
            max(max_lat) AS max_lat
        FROM address_lookup
        WHERE street_norm <> ''
        GROUP BY city_norm, street_norm, post_code
        """
    )
    row = target.execute("SELECT count(*) FROM street_lookup").fetchone()
    return int(row[0] or 0)


def build_parcel_lookup(source: sqlite3.Connection, target: sqlite3.Connection) -> int:
    feature_cols = table_columns(source, "features")
    center_select = (
        "center_lon, center_lat,"
        if {"center_lon", "center_lat"}.issubset(feature_cols)
        else ""
    )
    sql = f"""
        SELECT
            source_db,
            gml_id,
            properties_json,
            {center_select}
            min_lon,
            max_lon,
            min_lat,
            max_lat
        FROM features
        WHERE kind = 'parcel'
    """
    count = 0
    for row in source.execute(sql):
        props = load_json(row["properties_json"])
        gemarkung = str(props.get("gemarkung") or "").strip()
        flur = str(props.get("flur") if props.get("flur") is not None else "").strip()
        zaehler = str(props.get("zaehler") or "").strip()
        nenner = str(props.get("nenner") or "").strip()
        flurstueck = str(props.get("flurstueck") or (f"{zaehler}/{nenner}" if nenner else zaehler)).strip()
        if not gemarkung and not flur and not flurstueck:
            continue
        area = props.get("amtliche_flaeche_m2")
        try:
            area_value = float(area) if area not in (None, "") else None
        except (TypeError, ValueError):
            area_value = None
        lon, lat = center_from_row(row)
        target.execute(
            """
            INSERT OR IGNORE INTO parcel_lookup (
                source_db, gml_id,
                gemarkung_norm, gemarkung_label, gemarkungsnummer,
                flur_norm, flur_label,
                flurstueck_norm, flurstueck_label,
                zaehler, nenner, amtliche_flaeche_m2,
                lon, lat, min_lon, max_lon, min_lat, max_lat
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["source_db"],
                row["gml_id"],
                normalize_text(gemarkung),
                gemarkung,
                str(props.get("gemarkungsnummer") or "").strip(),
                normalize_compact(flur),
                flur,
                normalize_compact(flurstueck),
                flurstueck,
                zaehler,
                nenner,
                area_value,
                lon,
                lat,
                row["min_lon"],
                row["max_lon"],
                row["min_lat"],
                row["max_lat"],
            ),
        )
        count += 1
        if count % 100_000 == 0:
            print(f"  Flurstuecks-Suchindex: {count:,}", flush=True)
    row = target.execute("SELECT count(*) FROM parcel_lookup").fetchone()
    return int(row[0] or 0)


def main() -> int:
    if not SOURCE_FEATURES.is_file():
        raise FileNotFoundError(f"features.sqlite fehlt: {SOURCE_FEATURES}")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = OUT_PATH.with_name(f".{OUT_PATH.name}.{os.getpid()}.tmp")
    tmp_path.unlink(missing_ok=True)

    started = time.perf_counter()
    print(f"Search-Index Quelle: {SOURCE_FEATURES}", flush=True)
    print(f"Search-Index Ziel: {OUT_PATH}", flush=True)

    source = sqlite3.connect(f"file:{SOURCE_FEATURES}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    target = sqlite3.connect(tmp_path)
    target.row_factory = sqlite3.Row
    try:
        create_schema(target)
        address_count = build_address_lookup(source, target)
        street_count = build_street_lookup(target)
        parcel_count = build_parcel_lookup(source, target)
        create_indexes(target)
        insert_metadata(target, "format", "openkataster-state-search-v1")
        insert_metadata(target, "source_features", str(SOURCE_FEATURES))
        insert_metadata(target, "source_features_size", SOURCE_FEATURES.stat().st_size)
        insert_metadata(target, "address_lookup_count", address_count)
        insert_metadata(target, "street_lookup_count", street_count)
        insert_metadata(target, "parcel_lookup_count", parcel_count)
        insert_metadata(target, "built_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        target.commit()
    finally:
        source.close()
        target.close()

    os.replace(tmp_path, OUT_PATH)
    elapsed = time.perf_counter() - started
    print(
        f"Search-Index geschrieben: {OUT_PATH} "
        f"(Adressen={address_count:,}, Strassen={street_count:,}, Flurstuecke={parcel_count:,}, "
        f"{OUT_PATH.stat().st_size / 1024 / 1024:.2f} MiB, {elapsed:.1f}s)",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
