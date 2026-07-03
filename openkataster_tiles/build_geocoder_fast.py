#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import unicodedata
from pathlib import Path

DATA_DIR = Path(os.environ.get("OPENKATASTER_TILE_DATA_DIR", "/srv/openkataster-tiles/data"))
GEOCODER_DB = Path(os.environ.get("OPENKATASTER_GEOCODER_DB", "/srv/openkataster-tiles/geocoder/geocoder.sqlite"))
FAST_DB = Path(os.environ.get("OPENKATASTER_FAST_GEOCODER_DB", "/srv/openkataster-tiles/geocoder/geocoder_fast.sqlite"))
COUNTRY = "Deutschland"
STATE_LABELS = {
    "baden-wurttemberg": "Baden-Württemberg", "bayern": "Bayern", "berlin": "Berlin", "brandenburg": "Brandenburg",
    "bremen": "Bremen", "hamburg": "Hamburg", "hessen": "Hessen", "mecklenburg-vorpommern": "Mecklenburg-Vorpommern",
    "niedersachsen": "Niedersachsen", "nordrhein-westfalen": "Nordrhein-Westfalen", "rheinland-pfalz": "Rheinland-Pfalz",
    "saarland": "Saarland", "sachsen": "Sachsen", "sachsen-anhalt": "Sachsen-Anhalt", "schleswig-holstein": "Schleswig-Holstein",
    "thueringen": "Thüringen",
}


def normalize_text(value: str | None) -> str:
    text = str(value or "").casefold()
    text = text.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    text = "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))
    text = text.replace("str.", "strasse")
    text = re.sub(r"\bstr\b", "strasse", text)
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def normalize_compact(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", normalize_text(value))


def normalize_parcel(value: str | None) -> str:
    return str(value or "").strip().replace(" ", "")


def split_street_house(value: str | None) -> tuple[str, str]:
    text = str(value or "").strip()
    if not text:
        return ("", "")
    # Common form: "Kiefernweg 5" or "Glasewitzer Str. 3".
    match = re.match(r"^(.*?)[,\s]+([0-9]+\s*[A-Za-z]?(?:[-/][0-9]+\s*[A-Za-z]?)?)$", text)
    if not match:
        return ("", "")
    return (match.group(1).strip(), re.sub(r"\s+", "", match.group(2).strip()))


def state_from_path(path: Path) -> str:
    name = path.name
    return name[:-len(".features.sqlite")] if name.endswith(".features.sqlite") else name


def center(min_lon, max_lon, min_lat, max_lat):
    try:
        return ((float(min_lon) + float(max_lon)) / 2.0, (float(min_lat) + float(max_lat)) / 2.0)
    except Exception:
        return (None, None)


def feature_paths() -> list[Path]:
    return sorted(DATA_DIR.glob("*.features.sqlite"))


def setup(con: sqlite3.Connection):
    con.executescript("""
    PRAGMA journal_mode=DELETE;
    PRAGMA synchronous=NORMAL;
    PRAGMA temp_store=FILE;
    CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
    DROP TABLE IF EXISTS address_exact;
    DROP TABLE IF EXISTS street_exact;
    DROP TABLE IF EXISTS parcel_exact;
    CREATE TABLE address_exact(
        id INTEGER PRIMARY KEY,
        state TEXT NOT NULL,
        state_label TEXT NOT NULL,
        source_db TEXT NOT NULL,
        gml_id TEXT NOT NULL,
        feature_kind TEXT NOT NULL,
        label TEXT NOT NULL,
        street TEXT NOT NULL,
        street_norm TEXT NOT NULL,
        house_number TEXT NOT NULL,
        house_norm TEXT NOT NULL,
        city TEXT NOT NULL,
        city_norm TEXT NOT NULL,
        country TEXT NOT NULL,
        lon REAL,
        lat REAL,
        min_lon REAL,
        min_lat REAL,
        max_lon REAL,
        max_lat REAL
    );
    CREATE TABLE street_exact(
        id INTEGER PRIMARY KEY,
        state TEXT NOT NULL,
        state_label TEXT NOT NULL,
        street TEXT NOT NULL,
        street_norm TEXT NOT NULL,
        city TEXT NOT NULL,
        city_norm TEXT NOT NULL,
        country TEXT NOT NULL,
        lon REAL,
        lat REAL,
        min_lon REAL,
        min_lat REAL,
        max_lon REAL,
        max_lat REAL,
        address_count INTEGER NOT NULL
    );
    CREATE TABLE parcel_exact(
        id INTEGER PRIMARY KEY,
        state TEXT NOT NULL,
        state_label TEXT NOT NULL,
        source_db TEXT NOT NULL,
        gml_id TEXT NOT NULL,
        gemarkung TEXT NOT NULL,
        gemarkung_norm TEXT NOT NULL,
        gemarkungsnummer TEXT NOT NULL,
        flur TEXT NOT NULL,
        flur_norm TEXT NOT NULL,
        flurstueck TEXT NOT NULL,
        flurstueck_norm TEXT NOT NULL,
        zaehler TEXT NOT NULL,
        nenner TEXT NOT NULL,
        label TEXT NOT NULL,
        lon REAL,
        lat REAL,
        min_lon REAL,
        min_lat REAL,
        max_lon REAL,
        max_lat REAL,
        amtliche_flaeche_m2 REAL
    );
    """)
    con.execute("insert or replace into metadata(key,value) values('format','openkataster-fast-geocoder-v3-active-features')")
    con.execute("insert or replace into metadata(key,value) values('built_at',datetime('now'))")


def build_addresses(con: sqlite3.Connection):
    print("address_exact: build from", GEOCODER_DB, flush=True)
    con.execute("ATTACH DATABASE ? AS geo", (str(GEOCODER_DB),))
    t = time.time()
    con.execute("""
        INSERT INTO address_exact(state,state_label,source_db,gml_id,feature_kind,label,street,street_norm,house_number,house_norm,city,city_norm,country,lon,lat,min_lon,min_lat,max_lon,max_lat)
        SELECT state,state_label,COALESCE(source_db,''),COALESCE(gml_id,''),COALESCE(feature_kind,'address'),COALESCE(label,''),COALESCE(street,''),street_norm,COALESCE(house_number,''),house_norm,COALESCE(city,''),COALESCE(city_norm,''),? AS country,lon,lat,min_lon,min_lat,max_lon,max_lat
        FROM geo.addresses
        WHERE street_norm <> '' AND house_norm <> ''
    """, (COUNTRY,))
    con.commit()
    con.execute("DETACH DATABASE geo")
    print("address_exact: rows", con.execute("select count(*) from address_exact").fetchone()[0], "seconds", round(time.time()-t,1), flush=True)


def build_feature_addresses(con: sqlite3.Connection):
    """Add live feature.sqlite addresses to address_exact.

    The old geocoder database is not complete for all states. The runtime
    feature databases are the source of truth for active building/parcel
    addresses, so the fast geocoder must index them directly.
    """
    total = 0
    insert_sql = """
        INSERT INTO address_exact(state,state_label,source_db,gml_id,feature_kind,label,street,street_norm,house_number,house_norm,city,city_norm,country,lon,lat,min_lon,min_lat,max_lon,max_lat)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """
    for path in feature_paths():
        state = state_from_path(path)
        state_label = STATE_LABELS.get(state, state.replace('-', ' ').title())
        print("address_exact feature_addresses:", state, path, flush=True)
        src = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
        src.row_factory = sqlite3.Row
        batch = []
        count = 0
        try:
            rows = src.execute("""
                SELECT fa.source_db, fa.kind, fa.gml_id, fa.properties_json,
                       f.min_lon, f.max_lon, f.min_lat, f.max_lat
                FROM feature_addresses fa
                JOIN features f
                  ON f.source_db = fa.source_db
                 AND f.kind = fa.kind
                 AND f.gml_id = fa.gml_id
            """)
        except sqlite3.OperationalError as exc:
            print("  skip", state, exc, flush=True)
            src.close()
            continue
        scanned = 0
        for row in rows:
            scanned += 1
            if scanned % 250000 == 0:
                print("  scanned", state, scanned, "accepted", count + len(batch), flush=True)
            try:
                props = json.loads(row["properties_json"] or "{}")
            except Exception:
                props = {}
            street = str(props.get("street") or "").strip()
            house_number = str(props.get("house_number") or props.get("hausnummer") or "").strip()
            if not street or not house_number:
                fallback_street, fallback_house = split_street_house(props.get("street_house"))
                if not fallback_street or not fallback_house:
                    fallback_street, fallback_house = split_street_house(str(props.get("label") or "").split(",", 1)[0])
                street = street or fallback_street
                house_number = house_number or fallback_house
            if not street or not house_number:
                continue
            city = str(props.get("city") or "").strip()
            post_code = str(props.get("post_code") or props.get("postal_code") or "").strip()
            label = str(props.get("label") or "").strip()
            if not label:
                label = f"{street} {house_number}".strip()
            # Keep postal code out of the label. The API enriches display text
            # from coordinate-based PLZ/GN250 data so stale feature metadata
            # cannot dominate the user-facing address.
            lon, lat = center(row["min_lon"], row["max_lon"], row["min_lat"], row["max_lat"])
            batch.append((
                state,
                state_label,
                row["source_db"] or "",
                row["gml_id"] or "",
                row["kind"] or "address",
                label,
                street,
                normalize_text(street),
                house_number,
                normalize_compact(house_number),
                city,
                normalize_text(city),
                COUNTRY,
                lon,
                lat,
                row["min_lon"],
                row["min_lat"],
                row["max_lon"],
                row["max_lat"],
            ))
            if len(batch) >= 10000:
                con.executemany(insert_sql, batch)
                con.commit()
                count += len(batch)
                total += len(batch)
                batch.clear()
                if count % 250000 == 0:
                    print("  ", state, count, flush=True)
        if batch:
            con.executemany(insert_sql, batch)
            con.commit()
            count += len(batch)
            total += len(batch)
            batch.clear()
        src.close()
        print("  done", state, count, flush=True)
    print("address_exact feature_addresses: total", total, flush=True)



def build_streets(con: sqlite3.Connection):
    print("street_exact: build from legacy geocoder plus active address_exact", flush=True)
    t = time.time()
    if GEOCODER_DB.exists():
        con.execute("ATTACH DATABASE ? AS geo", (str(GEOCODER_DB),))
        con.execute("""
            INSERT INTO street_exact(state,state_label,street,street_norm,city,city_norm,country,lon,lat,min_lon,min_lat,max_lon,max_lat,address_count)
            SELECT state,state_label,MIN(street),street_norm,MIN(city),city_norm,? AS country,
                   AVG(lon),AVG(lat),MIN(min_lon),MIN(min_lat),MAX(max_lon),MAX(max_lat),COUNT(*)
            FROM geo.addresses
            WHERE street_norm <> '' AND city_norm <> ''
            GROUP BY state, street_norm, city_norm
        """, (COUNTRY,))
        con.commit()
        con.execute("DETACH DATABASE geo")
    con.execute("""
        INSERT INTO street_exact(state,state_label,street,street_norm,city,city_norm,country,lon,lat,min_lon,min_lat,max_lon,max_lat,address_count)
        SELECT state,state_label,MIN(street),street_norm,MIN(city),city_norm,? AS country,
               AVG(lon),AVG(lat),MIN(min_lon),MIN(min_lat),MAX(max_lon),MAX(max_lat),COUNT(*)
        FROM address_exact
        WHERE street_norm <> '' AND city_norm <> ''
        GROUP BY state, street_norm, city_norm
    """, (COUNTRY,))
    con.commit()
    print("street_exact: rows", con.execute("select count(*) from street_exact").fetchone()[0], "seconds", round(time.time()-t,1), flush=True)

def build_parcels(con: sqlite3.Connection):
    total = 0
    insert_sql = """
        INSERT INTO parcel_exact(state,state_label,source_db,gml_id,gemarkung,gemarkung_norm,gemarkungsnummer,flur,flur_norm,flurstueck,flurstueck_norm,zaehler,nenner,label,lon,lat,min_lon,min_lat,max_lon,max_lat,amtliche_flaeche_m2)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """
    for path in feature_paths():
        state = state_from_path(path)
        state_label = STATE_LABELS.get(state, state.replace('-', ' ').title())
        print("parcel_exact:", state, path, flush=True)
        src = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
        src.row_factory = sqlite3.Row
        batch = []
        count = 0
        for row in src.execute("""
            SELECT source_db,gml_id,properties_json,min_lon,max_lon,min_lat,max_lat
            FROM features
            WHERE kind='parcel'
        """):
            try:
                props = json.loads(row["properties_json"] or "{}")
            except Exception:
                props = {}
            gemarkung = str(props.get("gemarkung") or "").strip()
            gemarkungsnummer = str(props.get("gemarkungsnummer") or props.get("gemaschl") or "").strip()
            flur = str(props.get("flur") if props.get("flur") is not None else props.get("flurschl") or "").strip()
            flurstueck = str(props.get("flurstueck") or props.get("label") or "").strip()
            zaehler = str((props.get("zaehler") or (flurstueck.split('/')[0] if flurstueck else ""))).strip()
            nenner = str((props.get("nenner") or (flurstueck.split('/')[1] if '/' in flurstueck else ""))).strip()
            if not flurstueck and zaehler:
                flurstueck = f"{zaehler}/{nenner}" if nenner else zaehler
            if not flurstueck:
                continue
            lon, lat = center(row["min_lon"], row["max_lon"], row["min_lat"], row["max_lat"])
            if gemarkung and flur:
                label = f"Flur {flur}, Flurstück {flurstueck}, {gemarkung}"
            elif gemarkung:
                label = f"Flurstück {flurstueck}, {gemarkung}"
            else:
                label = f"Flurstück {flurstueck}"
            area = props.get("amtliche_flaeche_m2")
            try:
                area = float(area) if area is not None and area != "" else None
            except Exception:
                area = None
            batch.append((state, state_label, row["source_db"] or "", row["gml_id"] or "", gemarkung, normalize_text(gemarkung), gemarkungsnummer, flur, normalize_compact(flur), flurstueck, normalize_parcel(flurstueck), zaehler, nenner, label, lon, lat, row["min_lon"], row["min_lat"], row["max_lon"], row["max_lat"], area))
            if len(batch) >= 10000:
                con.executemany(insert_sql, batch)
                con.commit()
                count += len(batch); total += len(batch); batch.clear()
                if count % 250000 == 0:
                    print("  ", state, count, flush=True)
        if batch:
            con.executemany(insert_sql, batch)
            con.commit()
            count += len(batch); total += len(batch); batch.clear()
        src.close()
        print("  done", state, count, flush=True)
    print("parcel_exact: total", total, flush=True)



def create_indexes(con: sqlite3.Connection):
    print("indexes...", flush=True)
    con.execute("PRAGMA temp_store=FILE")
    con.execute("PRAGMA cache_size=-20000")
    indexes = [
        ("idx_address_exact_lookup", "CREATE INDEX IF NOT EXISTS idx_address_exact_lookup ON address_exact(street_norm, house_norm, city_norm, state)"),
        ("idx_address_exact_no_city", "CREATE INDEX IF NOT EXISTS idx_address_exact_no_city ON address_exact(street_norm, house_norm, state)"),
        ("idx_street_exact_lookup", "CREATE INDEX IF NOT EXISTS idx_street_exact_lookup ON street_exact(street_norm, city_norm, state)"),
        ("idx_street_exact_state", "CREATE INDEX IF NOT EXISTS idx_street_exact_state ON street_exact(state, street_norm)"),
        ("idx_parcel_exact_lookup", "CREATE INDEX IF NOT EXISTS idx_parcel_exact_lookup ON parcel_exact(gemarkung_norm, flur_norm, flurstueck_norm, state)"),
        ("idx_parcel_exact_gemarkung_number", "CREATE INDEX IF NOT EXISTS idx_parcel_exact_gemarkung_number ON parcel_exact(gemarkungsnummer, flur_norm, flurstueck_norm, state)"),
        ("idx_parcel_exact_gml", "CREATE INDEX IF NOT EXISTS idx_parcel_exact_gml ON parcel_exact(state, source_db, gml_id)"),
    ]
    for name, sql in indexes:
        print("  create", name, flush=True)
        t = time.time()
        con.execute(sql)
        con.commit()
        print("  done", name, "seconds", round(time.time() - t, 1), flush=True)
    print("  analyze", flush=True)
    con.execute("ANALYZE address_exact")
    con.execute("ANALYZE street_exact")
    con.execute("ANALYZE parcel_exact")
    con.commit()

def main():
    FAST_DB.parent.mkdir(parents=True, exist_ok=True)
    tmp = FAST_DB.with_suffix('.sqlite.tmp')
    for candidate in (tmp, Path(str(tmp) + '-wal'), Path(str(tmp) + '-shm')):
        if candidate.exists():
            candidate.unlink()
    con = sqlite3.connect(tmp)
    setup(con)
    # Active feature databases are the address source of truth.
    # The legacy geocoder DB is intentionally not copied into address_exact.
    build_feature_addresses(con)
    build_streets(con)
    build_parcels(con)
    create_indexes(con)
    con.execute("insert or replace into metadata(key,value) values('finished_at',datetime('now'))")
    con.commit()
    con.close()
    for candidate in (Path(str(tmp) + '-wal'), Path(str(tmp) + '-shm')):
        if candidate.exists():
            candidate.unlink()
    os.replace(tmp, FAST_DB)
    print("wrote", FAST_DB, FAST_DB.stat().st_size, flush=True)

if __name__ == "__main__":
    main()
