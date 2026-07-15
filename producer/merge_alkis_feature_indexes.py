#!/usr/bin/env python3
"""Merge validated per-chunk ALKIS feature indexes into one runtime index."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

try:
    from .alkis_feature_schema import create_schema
except ImportError:  # direct script execution
    from alkis_feature_schema import create_schema


FEATURE_COLUMNS = (
    "state_key",
    "kind",
    "source_db",
    "gml_id",
    "properties_json",
    "geometry_wkb",
    "center_lon",
    "center_lat",
    "min_lon",
    "max_lon",
    "min_lat",
    "max_lat",
)
ADDRESS_POINT_COLUMNS = (
    "source_db",
    "properties_json",
    "geometry_wkb",
    "lon",
    "lat",
)
FEATURE_ADDRESS_COLUMNS = (
    "source_db",
    "kind",
    "gml_id",
    "properties_json",
)
REQUIRED_TABLES = {
    "metadata",
    "features",
    "feature_index",
    "address_points",
    "address_index",
    "feature_addresses",
}


def validate_part(path: Path) -> None:
    if not path.is_file() or path.stat().st_size <= 0:
        raise RuntimeError(f"Feature-Teilindex fehlt oder ist leer: {path}")
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as con:
        quick_check = con.execute("PRAGMA quick_check").fetchone()
        if not quick_check or quick_check[0] != "ok":
            raise RuntimeError(f"Feature-Teilindex ist beschaedigt: {path}")
        tables = {
            row[0]
            for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'virtual table')"
            )
        }
        missing = REQUIRED_TABLES - tables
        if missing:
            raise RuntimeError(
                f"Feature-Teilindex {path} enthaelt nicht alle Tabellen: {sorted(missing)}"
            )


def metadata_rows(con: sqlite3.Connection) -> dict[str, str]:
    return {
        str(key): str(value)
        for key, value in con.execute("SELECT key, value FROM part.metadata")
    }


def insert_select(
    con: sqlite3.Connection,
    table: str,
    columns: tuple[str, ...],
) -> None:
    column_sql = ", ".join(columns)
    con.execute(
        f"INSERT OR IGNORE INTO {table} ({column_sql}) "
        f"SELECT {column_sql} FROM part.{table} ORDER BY id"
    )


def insert_feature_addresses_for_canonical_features(con: sqlite3.Connection) -> None:
    """Merge relation addresses onto the feature chosen by global GML dedupe.

    Overlapping source shards can contain the same ALKIS feature while only one
    shard contains the referenced ``AX_LagebezeichnungMitHausnummer`` row.  The
    feature table deliberately keeps one geometry, but relation addresses from
    every part must follow that canonical feature's ``source_db``.  Runtime and
    search joins are source-aware, so retaining the part-local source here would
    silently drop a valid address after the geometry dedupe.
    """
    con.execute(
        """
        INSERT OR IGNORE INTO feature_addresses (
            source_db, kind, gml_id, properties_json
        )
        SELECT
            canonical.source_db,
            relation.kind,
            relation.gml_id,
            relation.properties_json
        FROM part.feature_addresses AS relation
        JOIN features AS canonical
          ON canonical.kind = relation.kind
         AND canonical.gml_id = relation.gml_id
        ORDER BY relation.id
        """
    )


def merge_parts(parts: list[Path], out_path: Path) -> None:
    for part in parts:
        validate_part(part)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{out_path.name}.",
        suffix=".tmp",
        dir=str(out_path.parent),
    )
    os.close(fd)
    temporary_path = Path(temporary_name)
    metadata_values: dict[str, set[str]] = {}
    databases: set[str] = set()

    try:
        with closing(sqlite3.connect(temporary_path)) as con:
            con.execute("PRAGMA journal_mode=OFF")
            con.execute("PRAGMA synchronous=OFF")
            con.execute("PRAGMA locking_mode=EXCLUSIVE")
            con.execute("PRAGMA temp_store=FILE")
            con.execute("PRAGMA cache_size=-65536")
            create_schema(con)

            for index, part in enumerate(parts, start=1):
                print(f"Feature-Teilindex {index}/{len(parts)}: {part}", flush=True)
                con.execute("ATTACH DATABASE ? AS part", (str(part),))
                part_metadata = metadata_rows(con)
                for key, value in part_metadata.items():
                    if key.startswith("count_") or key == "databases":
                        continue
                    metadata_values.setdefault(key, set()).add(value)
                databases.update(
                    value.strip()
                    for value in part_metadata.get("databases", "").split(",")
                    if value.strip()
                )
                insert_select(con, "features", FEATURE_COLUMNS)
                insert_select(con, "address_points", ADDRESS_POINT_COLUMNS)
                insert_feature_addresses_for_canonical_features(con)
                con.commit()
                con.execute("DETACH DATABASE part")

            print("RTree-Indizes des Gesamtbestands werden aufgebaut", flush=True)
            con.execute(
                """
                INSERT INTO feature_index(id, min_lon, max_lon, min_lat, max_lat)
                SELECT id, min_lon, max_lon, min_lat, max_lat
                FROM features ORDER BY id
                """
            )
            con.execute(
                """
                INSERT INTO address_index(id, min_lon, max_lon, min_lat, max_lat)
                SELECT id, lon, lon, lat, lat
                FROM address_points ORDER BY id
                """
            )

            counts = {
                "parcels": con.execute(
                    "SELECT count(*) FROM features WHERE kind='parcel'"
                ).fetchone()[0],
                "buildings": con.execute(
                    "SELECT count(*) FROM features WHERE kind='building'"
                ).fetchone()[0],
                "address_points": con.execute(
                    "SELECT count(*) FROM address_points"
                ).fetchone()[0],
                "parcel_relation_addresses": con.execute(
                    "SELECT count(*) FROM feature_addresses WHERE kind='parcel'"
                ).fetchone()[0],
                "building_relation_addresses": con.execute(
                    "SELECT count(*) FROM feature_addresses WHERE kind='building'"
                ).fetchone()[0],
                "parcels_missing_gemarkung_name": con.execute(
                    """
                    SELECT count(*) FROM features
                    WHERE kind='parcel'
                      AND json_extract(properties_json, '$.gemarkungsnummer') IS NOT NULL
                      AND coalesce(json_extract(properties_json, '$.gemarkung'), '') = ''
                    """
                ).fetchone()[0],
                "parcels_with_usage": 0,
                "parcels_with_multiple_usages": 0,
                "building_parcel_fallback_addresses": 0,
            }

            resolved_metadata: dict[str, str] = {}
            for key, values in metadata_values.items():
                non_empty = sorted(value for value in values if value)
                if key in {"db_prefixes", "plz_reference", "municipality_reference"}:
                    resolved_metadata[key] = ",".join(dict.fromkeys(non_empty))
                elif len(non_empty) <= 1:
                    resolved_metadata[key] = non_empty[0] if non_empty else ""
                else:
                    raise RuntimeError(
                        f"Unvereinbare Feature-Metadaten fuer {key}: {json.dumps(non_empty)}"
                    )
            resolved_metadata.setdefault("format", "openkataster-features-sqlite")
            resolved_metadata.setdefault("format_version", "1")
            resolved_metadata["databases"] = ",".join(sorted(databases))
            resolved_metadata.update(
                {f"count_{key}": str(int(value)) for key, value in counts.items()}
            )
            con.executemany(
                "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)",
                sorted(resolved_metadata.items()),
            )
            con.execute("PRAGMA optimize")
            con.commit()

        os.replace(temporary_path, out_path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise

    print(f"Feature-Gesamtindex geschrieben: {out_path}", flush=True)
    print(f"Groesse: {out_path.stat().st_size / 1024 / 1024:.2f} MiB", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Merge per-chunk ALKIS features.sqlite files.",
    )
    parser.add_argument("--parts-dir", type=Path)
    parser.add_argument("--part", type=Path, action="append", default=[])
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    parts_dir = args.parts_dir.expanduser().resolve() if args.parts_dir else None
    parts = [path.expanduser().resolve() for path in args.part]
    if not parts and parts_dir is not None:
        parts = sorted(parts_dir.glob("chunk_*.sqlite"))
    if not parts:
        raise RuntimeError(f"Keine Feature-Teilindizes gefunden: {parts_dir or '(keine Auswahl)'}")
    merge_parts(parts, args.out.expanduser().resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
