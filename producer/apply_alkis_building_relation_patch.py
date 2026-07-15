#!/usr/bin/env python3
"""Apply explicit ALKIS building-address relations to a features.sqlite copy.

The input relation database must be produced by
``extract_alkis_building_relation_patch.py``.  Only buildings already present
in the target feature index are updated.  Postal codes are optional label
context copied from an existing exact address in the old search index; they do
not decide whether a relation is attached.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import time
import unicodedata
from pathlib import Path


EXPECTED_FORMAT = "openkataster-building-relation-patch-v1"


def normalize_text(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = text.replace("ß", "ss").replace("ẞ", "ss")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(character for character in text if not unicodedata.combining(character))
    text = text.casefold()
    return " ".join(re.sub(r"[^\w]+", " ", text, flags=re.UNICODE).split())


def normalize_compact(value: object) -> str:
    return normalize_text(value).replace(" ", "")


def metadata(con: sqlite3.Connection, database: str) -> dict[str, str]:
    return {
        str(key): str(value)
        for key, value in con.execute(f"SELECT key, value FROM {database}.metadata")
    }


def apply_patch(
    features_path: Path,
    relations_path: Path,
    postcode_search_path: Path,
    *,
    dry_run: bool = False,
) -> dict[str, object]:
    started = time.monotonic()
    for label, path in (
        ("features.sqlite", features_path),
        ("relation patch", relations_path),
        ("postcode search.sqlite", postcode_search_path),
    ):
        if not path.is_file():
            raise FileNotFoundError(f"{label} fehlt: {path}")

    mode = "ro" if dry_run else "rw"
    con = sqlite3.connect(f"file:{features_path.resolve()}?mode={mode}", uri=True)
    con.row_factory = sqlite3.Row
    con.create_function("normalize_text", 1, normalize_text, deterministic=True)
    con.create_function("normalize_compact", 1, normalize_compact, deterministic=True)
    relation_uri = f"file:{relations_path.resolve()}?mode=ro"
    search_uri = f"file:{postcode_search_path.resolve()}?mode=ro"
    con.execute("ATTACH DATABASE ? AS relations", (relation_uri,))
    con.execute("ATTACH DATABASE ? AS old_search", (search_uri,))
    try:
        if not dry_run:
            con.execute("PRAGMA journal_mode=DELETE")
            con.execute("PRAGMA synchronous=FULL")
        con.execute("PRAGMA temp_store=FILE")
        con.execute("PRAGMA cache_size=-262144")

        relation_metadata = metadata(con, "relations")
        if relation_metadata.get("format") != EXPECTED_FORMAT:
            raise RuntimeError(
                f"Unerwartetes Patch-Format: {relation_metadata.get('format')!r}"
            )
        for key in ("lage_collisions", "building_ref_collisions"):
            if int(relation_metadata.get(key, "-1")) != 0:
                raise RuntimeError(f"Relation-Patch hat Kollisionen: {key}={relation_metadata.get(key)}")
        if con.execute("PRAGMA relations.quick_check").fetchone()[0] != "ok":
            raise RuntimeError("Relation-Patch besteht quick_check nicht")

        patch_relations = int(
            con.execute("SELECT COUNT(*) FROM relations.building_relations").fetchone()[0]
        )
        patch_buildings = int(
            con.execute(
                "SELECT COUNT(DISTINCT building_gml_id) FROM relations.building_relations"
            ).fetchone()[0]
        )
        unmatched_relations = int(
            con.execute(
                """
                SELECT COUNT(*)
                FROM relations.building_relations AS relation
                LEFT JOIN features AS feature
                  ON feature.kind='building'
                 AND feature.gml_id=relation.building_gml_id
                WHERE feature.id IS NULL
                """
            ).fetchone()[0]
        )

        con.executescript(
            """
            CREATE TEMP TABLE candidate_relations(
                id INTEGER PRIMARY KEY,
                building_gml_id TEXT NOT NULL,
                canonical_source_db TEXT NOT NULL,
                street TEXT NOT NULL,
                house_number TEXT NOT NULL,
                city TEXT NOT NULL,
                street_norm TEXT NOT NULL,
                house_number_norm TEXT NOT NULL,
                city_norm TEXT NOT NULL,
                UNIQUE(
                    building_gml_id,
                    canonical_source_db,
                    street_norm,
                    house_number_norm,
                    city_norm
                )
            );
            """
        )
        con.execute(
            """
            INSERT OR IGNORE INTO candidate_relations(
                building_gml_id, canonical_source_db,
                street, house_number, city,
                street_norm, house_number_norm, city_norm
            )
            SELECT
                relation.building_gml_id,
                feature.source_db,
                relation.street,
                relation.house_number,
                relation.city,
                normalize_text(relation.street),
                normalize_compact(relation.house_number),
                normalize_text(relation.city)
            FROM relations.building_relations AS relation
            JOIN features AS feature
              ON feature.kind='building'
             AND feature.gml_id=relation.building_gml_id
            WHERE trim(relation.street)<>''
              AND trim(relation.house_number)<>''
              AND trim(relation.city)<>''
            ORDER BY
                relation.building_gml_id,
                relation.address_signature,
                relation.source_db
            """
        )
        candidates = int(con.execute("SELECT COUNT(*) FROM candidate_relations").fetchone()[0])
        con.execute(
            """
            CREATE INDEX temp.idx_candidate_feature
            ON candidate_relations(canonical_source_db, building_gml_id)
            """
        )

        con.executescript(
            """
            CREATE TEMP TABLE postcode_context(
                candidate_id INTEGER PRIMARY KEY,
                postcode_count INTEGER NOT NULL,
                post_code TEXT NOT NULL
            );
            """
        )
        con.execute(
            """
            INSERT INTO postcode_context(candidate_id, postcode_count, post_code)
            SELECT
                candidate.id,
                COUNT(DISTINCT NULLIF(trim(address.post_code), '')),
                CASE
                    WHEN COUNT(DISTINCT NULLIF(trim(address.post_code), '')) = 1
                    THEN MIN(NULLIF(trim(address.post_code), ''))
                    ELSE ''
                END
            FROM candidate_relations AS candidate
            LEFT JOIN old_search.address_lookup AS address
              ON address.city_norm=candidate.city_norm
             AND address.street_norm=candidate.street_norm
             AND address.house_number_norm=candidate.house_number_norm
            GROUP BY candidate.id
            """
        )
        ambiguous_postcodes = int(
            con.execute(
                "SELECT COUNT(*) FROM postcode_context WHERE postcode_count > 1"
            ).fetchone()[0]
        )
        postcodes_resolved = int(
            con.execute(
                "SELECT COUNT(*) FROM postcode_context WHERE postcode_count = 1"
            ).fetchone()[0]
        )

        matching_address_sql = """
            address.source_db=candidate.canonical_source_db
            AND address.kind='building'
            AND address.gml_id=candidate.building_gml_id
            AND normalize_text(json_extract(address.properties_json, '$.street'))=candidate.street_norm
            AND normalize_compact(json_extract(address.properties_json, '$.house_number'))=candidate.house_number_norm
            AND normalize_text(json_extract(address.properties_json, '$.city'))=candidate.city_norm
        """
        already_joined = int(
            con.execute(
                f"""
                SELECT COUNT(*)
                FROM candidate_relations AS candidate
                WHERE EXISTS (
                    SELECT 1 FROM feature_addresses AS address
                    WHERE {matching_address_sql}
                )
                """
            ).fetchone()[0]
        )
        orphan_source_matches = int(
            con.execute(
                """
                SELECT COUNT(*)
                FROM candidate_relations AS candidate
                WHERE NOT EXISTS (
                    SELECT 1 FROM feature_addresses AS address
                    WHERE address.source_db=candidate.canonical_source_db
                      AND address.kind='building'
                      AND address.gml_id=candidate.building_gml_id
                      AND normalize_text(json_extract(address.properties_json, '$.street'))=candidate.street_norm
                      AND normalize_compact(json_extract(address.properties_json, '$.house_number'))=candidate.house_number_norm
                      AND normalize_text(json_extract(address.properties_json, '$.city'))=candidate.city_norm
                )
                  AND EXISTS (
                    SELECT 1 FROM feature_addresses AS address
                    WHERE address.kind='building'
                      AND address.gml_id=candidate.building_gml_id
                      AND normalize_text(json_extract(address.properties_json, '$.street'))=candidate.street_norm
                      AND normalize_compact(json_extract(address.properties_json, '$.house_number'))=candidate.house_number_norm
                      AND normalize_text(json_extract(address.properties_json, '$.city'))=candidate.city_norm
                )
                """
            ).fetchone()[0]
        )

        would_insert = candidates - already_joined
        inserted = 0
        if not dry_run:
            con.commit()
            con.execute("BEGIN IMMEDIATE")
            con.execute(
                f"""
                INSERT OR IGNORE INTO feature_addresses(
                    source_db, kind, gml_id, properties_json
                )
                SELECT
                    candidate.canonical_source_db,
                    'building',
                    candidate.building_gml_id,
                    json_object(
                        'street', candidate.street,
                        'house_number', candidate.house_number,
                        'post_code', context.post_code,
                        'city', candidate.city,
                        'label',
                            candidate.street || ' ' || candidate.house_number || ', ' ||
                            CASE WHEN context.post_code<>'' THEN context.post_code || ' ' ELSE '' END ||
                            candidate.city
                    )
                FROM candidate_relations AS candidate
                JOIN postcode_context AS context
                  ON context.candidate_id=candidate.id
                WHERE NOT EXISTS (
                    SELECT 1 FROM feature_addresses AS address
                    WHERE {matching_address_sql}
                )
                ORDER BY candidate.id
                """
            )
            inserted = int(con.execute("SELECT changes()").fetchone()[0])
            con.commit()

        missing_after = int(
            con.execute(
                f"""
                SELECT COUNT(*)
                FROM candidate_relations AS candidate
                WHERE NOT EXISTS (
                    SELECT 1 FROM feature_addresses AS address
                    WHERE {matching_address_sql}
                )
                """
            ).fetchone()[0]
        )
        if missing_after and not dry_run:
            raise RuntimeError(f"{missing_after} Gebäuderelationen fehlen nach dem Insert")
        hildesheim_relations = int(
            con.execute(
                """
                SELECT COUNT(*)
                FROM candidate_relations
                WHERE street='Feldstraße' AND house_number='18'
                  AND normalize_text(city)=normalize_text('Hildesheim')
                """
            ).fetchone()[0]
        )
        hildesheim_joined = int(
            con.execute(
                f"""
                SELECT COUNT(*)
                FROM candidate_relations AS candidate
                WHERE candidate.street='Feldstraße'
                  AND candidate.house_number='18'
                  AND normalize_text(candidate.city)=normalize_text('Hildesheim')
                  AND EXISTS (
                      SELECT 1 FROM feature_addresses AS address
                      WHERE {matching_address_sql}
                  )
                """
            ).fetchone()[0]
        )
        quick_check = str(con.execute("PRAGMA main.quick_check").fetchone()[0])
        if quick_check != "ok":
            raise RuntimeError(f"features.sqlite quick_check fehlgeschlagen: {quick_check}")
        return {
            "format": "openkataster-applied-building-relations-v1",
            "dry_run": dry_run,
            "features": str(features_path),
            "relations": str(relations_path),
            "postcode_context": str(postcode_search_path),
            "patch_relations": patch_relations,
            "patch_buildings": patch_buildings,
            "unmatched_patch_relations": unmatched_relations,
            "canonical_candidates": candidates,
            "already_joined": already_joined,
            "orphan_source_matches": orphan_source_matches,
            "would_insert": would_insert,
            "inserted": inserted,
            "postcodes_resolved": postcodes_resolved,
            "ambiguous_postcodes_omitted": ambiguous_postcodes,
            "missing_after": missing_after,
            "hildesheim_feldstrasse_18_candidates": hildesheim_relations,
            "hildesheim_feldstrasse_18_joined": hildesheim_joined,
            "quick_check": quick_check,
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }
    finally:
        con.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--features", type=Path, required=True)
    parser.add_argument("--relations", type=Path, required=True)
    parser.add_argument("--postcode-search", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    report = apply_patch(
        args.features.expanduser().resolve(),
        args.relations.expanduser().resolve(),
        args.postcode_search.expanduser().resolve(),
        dry_run=args.dry_run,
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.report:
        args.report.expanduser().resolve().write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
