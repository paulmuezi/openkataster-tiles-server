#!/usr/bin/env python3
"""Extract explicit ALKIS building-address relations directly from pg_dump files.

The extractor is intentionally read-only with respect to the source dumps.  It
streams only the four relation tables needed to repair a previously generated
``features.sqlite`` without restoring all Postgres databases or rebuilding any
geometry.  Output is a deterministic SQLite relation patch plus collision
guardrails for overlapping WFS shards.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import tempfile
import time
from collections import defaultdict
from pathlib import Path
from typing import Iterable, Iterator


TABLES = (
    "ax_gemeinde",
    "ax_lagebezeichnungkatalogeintrag",
    "ax_lagebezeichnungmithausnummer",
    "ax_gebaeude",
)


def normalize_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def normalize_postal_city(value: object) -> str:
    text = normalize_text(value)
    text = re.sub(r"^Stadt\s+", "", text, count=1, flags=re.IGNORECASE)
    return normalize_text(
        re.sub(
            r",\s*(Stadt|Landeshauptstadt|Kreisstadt|Hansestadt|Universitaetsstadt|Universitätsstadt|Gemeinde|Flecken|Markt)$",
            "",
            text,
            flags=re.IGNORECASE,
        )
    )


def copy_unescape(value: str) -> str | None:
    if value == r"\N":
        return None
    replacements = {"b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v"}
    result: list[str] = []
    index = 0
    while index < len(value):
        character = value[index]
        if character != "\\" or index + 1 >= len(value):
            result.append(character)
            index += 1
            continue
        index += 1
        escaped = value[index]
        if escaped in replacements:
            result.append(replacements[escaped])
            index += 1
            continue
        if escaped == "x":
            match = re.match(r"[0-9A-Fa-f]{1,2}", value[index + 1 :])
            if match:
                result.append(chr(int(match.group(0), 16)))
                index += 1 + len(match.group(0))
                continue
        if escaped in "01234567":
            match = re.match(r"[0-7]{1,3}", value[index:])
            if match:
                result.append(chr(int(match.group(0), 8)))
                index += len(match.group(0))
                continue
        result.append(escaped)
        index += 1
    return "".join(result)


def toc_entry(dump_path: Path, table: str) -> str:
    output = subprocess.check_output(["pg_restore", "-l", str(dump_path)], text=True)
    suffix = f"TABLE DATA public {table} "
    matches = [line for line in output.splitlines() if suffix in line]
    if not matches:
        return ""
    if len(matches) > 1:
        raise RuntimeError(f"Mehrere TOC-Eintraege fuer {table} in {dump_path}")
    return matches[0] + "\n"


def table_rows(dump_path: Path, table: str) -> Iterator[dict[str, str | None]]:
    entry = toc_entry(dump_path, table)
    if not entry:
        return
    process = subprocess.Popen(
        ["pg_restore", "--use-list=/dev/stdin", "-f", "-", str(dump_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        bufsize=1024 * 1024,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    process.stdin.write(entry)
    process.stdin.close()
    columns: list[str] | None = None
    in_copy = False
    for line in process.stdout:
        line = line.rstrip("\n")
        if not in_copy:
            match = re.match(rf"COPY public\.{re.escape(table)} \((.*)\) FROM stdin;", line)
            if match:
                columns = match.group(1).split(", ")
                in_copy = True
            continue
        if line == r"\.":
            break
        assert columns is not None
        values = [copy_unescape(value) for value in line.split("\t")]
        if len(values) != len(columns):
            process.kill()
            raise RuntimeError(
                f"Unerwartete COPY-Spaltenzahl in {dump_path.name}/{table}: "
                f"{len(values)} statt {len(columns)}"
            )
        yield dict(zip(columns, values))
    process.stdout.close()
    stderr = process.stderr.read() if process.stderr is not None else ""
    return_code = process.wait()
    if return_code:
        raise RuntimeError(f"pg_restore {dump_path.name}/{table}: {stderr.strip()}")


def relation_values(value: object) -> tuple[str, ...]:
    text = normalize_text(value)
    if not text:
        return tuple()
    if text.startswith("{") and text.endswith("}"):
        text = text[1:-1]
    return tuple(
        dict.fromkeys(
            item.strip().strip('"')
            for item in text.split(",")
            if item.strip().strip('"')
        )
    )


def administrative_key(row: dict[str, object], *, include_lage: bool = False) -> tuple[str, ...]:
    fields = ["land", "regierungsbezirk", "kreis", "gemeinde"]
    if include_lage:
        fields.append("lage")
    return tuple(normalize_text(row.get(field)) for field in fields)


def address_signature(address: dict[str, str]) -> str:
    payload = {
        key: normalize_text(address.get(key))
        for key in (
            "street",
            "house_number",
            "city",
            "land",
            "regierungsbezirk",
            "kreis",
            "gemeinde",
        )
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def resolve_catalog_candidates(
    candidates: dict[tuple[str, ...], list[dict[str, str]]],
) -> tuple[dict[tuple[str, ...], str], int]:
    """Resolve historical duplicate catalogue keys deterministically.

    Niedersachsen dumps can contain more than one un-ended catalogue object
    for the same administrative street key.  ALKIS ``beginnt`` is the object
    lifecycle timestamp, so the latest object is authoritative.  Different
    names with the same latest timestamp remain a hard ambiguity.
    """
    resolved: dict[tuple[str, ...], str] = {}
    collision_keys = 0
    for key, rows in candidates.items():
        unique_names = {row["street"] for row in rows}
        if len(unique_names) > 1:
            collision_keys += 1
        current = [row for row in rows if not row["endet"]] or rows
        latest = max(row["beginnt"] for row in current)
        latest_rows = [row for row in current if row["beginnt"] == latest]
        latest_names = {row["street"] for row in latest_rows}
        if len(latest_names) != 1:
            raise RuntimeError(
                f"Mehrdeutiger aktueller Katalogschluessel {key}: {sorted(latest_names)}"
            )
        resolved[key] = next(iter(latest_names))
    return resolved, collision_keys


def create_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID;
        CREATE TABLE dump_sources(
            source_db TEXT PRIMARY KEY,
            path TEXT NOT NULL,
            size INTEGER NOT NULL,
            mtime_ns INTEGER NOT NULL
        ) WITHOUT ROWID;
        CREATE TABLE lage_candidates(
            lage_gml_id TEXT NOT NULL,
            address_signature TEXT NOT NULL,
            source_db TEXT NOT NULL,
            address_json TEXT NOT NULL,
            PRIMARY KEY(lage_gml_id, address_signature)
        ) WITHOUT ROWID;
        CREATE TABLE building_ref_sets(
            building_gml_id TEXT NOT NULL,
            ref_signature TEXT NOT NULL,
            source_db TEXT NOT NULL,
            PRIMARY KEY(building_gml_id, ref_signature, source_db)
        ) WITHOUT ROWID;
        CREATE TABLE building_ref_candidates(
            building_gml_id TEXT NOT NULL,
            lage_gml_id TEXT NOT NULL,
            source_db TEXT NOT NULL,
            PRIMARY KEY(building_gml_id, lage_gml_id, source_db)
        ) WITHOUT ROWID;
        CREATE TABLE building_relations(
            building_gml_id TEXT NOT NULL,
            address_signature TEXT NOT NULL,
            lage_gml_id TEXT NOT NULL,
            source_db TEXT NOT NULL,
            street TEXT NOT NULL,
            house_number TEXT NOT NULL,
            city TEXT NOT NULL,
            land TEXT NOT NULL,
            regierungsbezirk TEXT NOT NULL,
            kreis TEXT NOT NULL,
            gemeinde TEXT NOT NULL,
            PRIMARY KEY(building_gml_id, address_signature)
        ) WITHOUT ROWID;
        CREATE INDEX idx_building_relations_lage ON building_relations(lage_gml_id);
        """
    )


def extract_dump(con: sqlite3.Connection, dump_path: Path) -> dict[str, int]:
    source_db = dump_path.stem
    municipalities: dict[tuple[str, ...], str] = {}
    catalog_candidates: dict[tuple[str, ...], list[dict[str, str]]] = defaultdict(list)
    lage: dict[str, dict[str, str]] = {}
    conflicts: list[str] = []

    for row in table_rows(dump_path, "ax_gemeinde"):
        key = administrative_key(row)
        city = normalize_postal_city(row.get("bezeichnung"))
        if not city:
            continue
        previous = municipalities.setdefault(key, city)
        if previous != city:
            conflicts.append(f"Gemeinde {key}: {previous!r} / {city!r}")

    for row in table_rows(dump_path, "ax_lagebezeichnungkatalogeintrag"):
        key = administrative_key(row, include_lage=True)
        street = normalize_text(row.get("bezeichnung"))
        if not street:
            continue
        candidate = {
            "street": street,
            "beginnt": normalize_text(row.get("beginnt")),
            "endet": normalize_text(row.get("endet")),
            "gml_id": normalize_text(row.get("gml_id")),
        }
        if candidate not in catalog_candidates[key]:
            catalog_candidates[key].append(candidate)

    catalog, catalog_collision_keys = resolve_catalog_candidates(catalog_candidates)

    for row in table_rows(dump_path, "ax_lagebezeichnungmithausnummer"):
        lage_gml_id = normalize_text(row.get("gml_id"))
        house_number = normalize_text(row.get("hausnummer"))
        key = administrative_key(row, include_lage=True)
        street = catalog.get(key, "")
        if not lage_gml_id or not street or not house_number:
            continue
        admin_key = administrative_key(row)
        address = {
            "street": street,
            "house_number": house_number,
            "city": municipalities.get(admin_key, ""),
            "land": admin_key[0],
            "regierungsbezirk": admin_key[1],
            "kreis": admin_key[2],
            "gemeinde": admin_key[3],
        }
        signature = address_signature(address)
        lage[lage_gml_id] = address
        con.execute(
            """
            INSERT OR IGNORE INTO lage_candidates(
                lage_gml_id, address_signature, source_db, address_json
            ) VALUES (?, ?, ?, ?)
            """,
            (
                lage_gml_id,
                signature,
                source_db,
                json.dumps(address, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            ),
        )

    building_rows = 0
    relation_rows = 0
    for row in table_rows(dump_path, "ax_gebaeude"):
        building_gml_id = normalize_text(row.get("gml_id"))
        refs = tuple(sorted(relation_values(row.get("zeigtauf"))))
        if not building_gml_id or not refs:
            continue
        building_rows += 1
        ref_signature = ",".join(refs)
        con.execute(
            """
            INSERT OR IGNORE INTO building_ref_sets(
                building_gml_id, ref_signature, source_db
            ) VALUES (?, ?, ?)
            """,
            (building_gml_id, ref_signature, source_db),
        )
        for lage_gml_id in refs:
            con.execute(
                """
                INSERT OR IGNORE INTO building_ref_candidates(
                    building_gml_id, lage_gml_id, source_db
                ) VALUES (?, ?, ?)
                """,
                (building_gml_id, lage_gml_id, source_db),
            )
            if lage_gml_id in lage:
                relation_rows += 1

    if conflicts:
        raise RuntimeError(f"{dump_path.name}: widerspruechliche Katalogdaten: {conflicts[:3]}")
    stat = dump_path.stat()
    con.execute(
        "INSERT INTO dump_sources(source_db,path,size,mtime_ns) VALUES(?,?,?,?)",
        (source_db, str(dump_path), stat.st_size, stat.st_mtime_ns),
    )
    con.commit()
    return {
        "municipalities": len(municipalities),
        "catalog": len(catalog),
        "catalog_collision_keys_resolved": catalog_collision_keys,
        "lage": len(lage),
        "buildings_with_refs": building_rows,
        "resolved_relations": relation_rows,
    }


def resolve_relations_globally(con: sqlite3.Connection) -> int:
    """Resolve building refs after every dump's Lage rows are available.

    WFS shards overlap, and a building can be stored in one shard while the
    referenced AX_LagebezeichnungMitHausnummer row is stored in another.  A
    per-dump join silently loses those explicit relations.  Ordering makes the
    provenance source deterministic when the same relation occurs in several
    overlapping shards.
    """
    con.execute(
        """
        INSERT OR IGNORE INTO building_relations(
            building_gml_id, address_signature, lage_gml_id, source_db,
            street, house_number, city, land, regierungsbezirk, kreis, gemeinde
        )
        SELECT
            refs.building_gml_id,
            lage.address_signature,
            refs.lage_gml_id,
            refs.source_db,
            json_extract(lage.address_json, '$.street'),
            json_extract(lage.address_json, '$.house_number'),
            json_extract(lage.address_json, '$.city'),
            json_extract(lage.address_json, '$.land'),
            json_extract(lage.address_json, '$.regierungsbezirk'),
            json_extract(lage.address_json, '$.kreis'),
            json_extract(lage.address_json, '$.gemeinde')
        FROM building_ref_candidates AS refs
        JOIN lage_candidates AS lage
          ON lage.lage_gml_id = refs.lage_gml_id
        ORDER BY
            refs.building_gml_id,
            lage.address_signature,
            refs.lage_gml_id,
            refs.source_db,
            lage.source_db
        """
    )
    return int(con.execute("SELECT COUNT(*) FROM building_relations").fetchone()[0])


def collision_count(con: sqlite3.Connection, table: str, id_column: str, variant_column: str) -> int:
    return int(
        con.execute(
            f"""
            SELECT COUNT(*) FROM (
                SELECT {id_column}
                FROM {table}
                GROUP BY {id_column}
                HAVING COUNT(DISTINCT {variant_column}) > 1
            )
            """
        ).fetchone()[0]
    )


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def extract(dumps: Iterable[Path], output: Path) -> dict[str, object]:
    dumps = sorted(path.resolve() for path in dumps)
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{output.name}.", suffix=".tmp", dir=output.parent)
    os.close(fd)
    temporary = Path(temporary_name)
    started = time.monotonic()
    try:
        with sqlite3.connect(temporary) as con:
            con.execute("PRAGMA journal_mode=OFF")
            con.execute("PRAGMA synchronous=OFF")
            con.execute("PRAGMA locking_mode=EXCLUSIVE")
            con.execute("PRAGMA temp_store=FILE")
            con.execute("PRAGMA cache_size=-65536")
            create_schema(con)
            per_dump = {}
            for index, dump_path in enumerate(dumps, 1):
                counts = extract_dump(con, dump_path)
                per_dump[dump_path.stem] = counts
                print(f"{index}/{len(dumps)} {dump_path.name}: {counts}", flush=True)

            lage_collisions = collision_count(con, "lage_candidates", "lage_gml_id", "address_signature")
            building_ref_collisions = collision_count(
                con,
                "building_ref_sets",
                "building_gml_id",
                "ref_signature",
            )
            relation_count = resolve_relations_globally(con)
            summary = {
                "format": "openkataster-building-relation-patch-v1",
                "source_dumps": len(dumps),
                "lage_candidates": con.execute("SELECT COUNT(*) FROM lage_candidates").fetchone()[0],
                "building_relations": relation_count,
                "lage_collisions": lage_collisions,
                "building_ref_collisions": building_ref_collisions,
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "per_dump": per_dump,
            }
            con.executemany(
                "INSERT INTO metadata(key,value) VALUES(?,?)",
                (
                    ("format", summary["format"]),
                    ("source_dumps", str(summary["source_dumps"])),
                    ("lage_candidates", str(summary["lage_candidates"])),
                    ("building_relations", str(summary["building_relations"])),
                    ("lage_collisions", str(lage_collisions)),
                    ("building_ref_collisions", str(building_ref_collisions)),
                ),
            )
            con.execute("DROP TABLE building_ref_sets")
            con.execute("DROP TABLE building_ref_candidates")
            con.execute("DROP TABLE lage_candidates")
            con.commit()
            con.execute("VACUUM")
            con.commit()
        if summary["lage_collisions"] or summary["building_ref_collisions"]:
            raise RuntimeError(
                "Relation-Patch wegen GML-ID-Kollisionen verworfen: "
                f"lage={summary['lage_collisions']}, building_refs={summary['building_ref_collisions']}"
            )
        os.replace(temporary, output)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    summary["output"] = str(output)
    summary["output_bytes"] = output.stat().st_size
    summary["sha256"] = file_sha256(output)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dumps-dir", type=Path, required=True)
    parser.add_argument("--pattern", default="*.dump")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary", type=Path)
    args = parser.parse_args()
    dumps = sorted(args.dumps_dir.expanduser().glob(args.pattern))
    if not dumps:
        raise RuntimeError(f"Keine Dumps unter {args.dumps_dir}/{args.pattern}")
    summary = extract(dumps, args.output.expanduser().resolve())
    rendered = json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True)
    if args.summary:
        args.summary.expanduser().resolve().write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
