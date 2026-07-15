from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from producer.apply_alkis_building_relation_patch import apply_patch


class ApplyBuildingRelationPatchTests(unittest.TestCase):
    def create_features(self, path: Path) -> None:
        with sqlite3.connect(path) as con:
            con.executescript(
                """
                CREATE TABLE features(
                    id INTEGER PRIMARY KEY,
                    kind TEXT NOT NULL,
                    source_db TEXT NOT NULL,
                    gml_id TEXT NOT NULL
                );
                CREATE TABLE feature_addresses(
                    id INTEGER PRIMARY KEY,
                    source_db TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    gml_id TEXT NOT NULL,
                    properties_json TEXT NOT NULL,
                    UNIQUE(source_db, kind, gml_id, properties_json)
                );
                """
            )
            con.execute(
                "INSERT INTO features(kind,source_db,gml_id) VALUES('building','shard_11','b1')"
            )
            con.execute(
                "INSERT INTO features(kind,source_db,gml_id) VALUES('parcel','shard_11','p1')"
            )

    def create_relations(self, path: Path) -> None:
        address = {
            "street": "Feldstraße",
            "house_number": "18",
            "city": "Hildesheim",
            "land": "03",
            "regierungsbezirk": "2",
            "kreis": "54",
            "gemeinde": "021",
        }
        signature = json.dumps(address, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with sqlite3.connect(path) as con:
            con.executescript(
                """
                CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
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
                );
                """
            )
            con.executemany(
                "INSERT INTO metadata(key,value) VALUES(?,?)",
                (
                    ("format", "openkataster-building-relation-patch-v1"),
                    ("lage_collisions", "0"),
                    ("building_ref_collisions", "0"),
                ),
            )
            con.execute(
                """
                INSERT INTO building_relations VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "b1", signature, "lage-1", "shard_14",
                    "Feldstraße", "18", "Hildesheim", "03", "2", "54", "021",
                ),
            )

    def create_search(self, path: Path) -> None:
        with sqlite3.connect(path) as con:
            con.execute(
                """
                CREATE TABLE address_lookup(
                    city_norm TEXT NOT NULL,
                    street_norm TEXT NOT NULL,
                    house_number_norm TEXT NOT NULL,
                    post_code TEXT NOT NULL
                )
                """
            )
            con.executemany(
                "INSERT INTO address_lookup VALUES('hildesheim','feldstrasse','18',?)",
                (("31141",), ("31137",)),
            )

    def test_apply_uses_canonical_source_and_omits_ambiguous_postcode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            features = root / "features.sqlite"
            relations = root / "relations.sqlite"
            search = root / "search.sqlite"
            self.create_features(features)
            self.create_relations(relations)
            self.create_search(search)

            dry_run = apply_patch(features, relations, search, dry_run=True)
            self.assertEqual(dry_run["would_insert"], 1)
            self.assertEqual(dry_run["inserted"], 0)
            with sqlite3.connect(features) as con:
                self.assertEqual(con.execute("SELECT count(*) FROM feature_addresses").fetchone()[0], 0)

            report = apply_patch(features, relations, search)
            self.assertEqual(report["inserted"], 1)
            self.assertEqual(report["ambiguous_postcodes_omitted"], 1)
            with sqlite3.connect(features) as con:
                row = con.execute(
                    "SELECT source_db,kind,gml_id,properties_json FROM feature_addresses"
                ).fetchone()
                self.assertEqual(row[:3], ("shard_11", "building", "b1"))
                self.assertEqual(json.loads(row[3])["post_code"], "")
                self.assertEqual(
                    con.execute("SELECT count(*) FROM feature_addresses WHERE kind='parcel'").fetchone()[0],
                    0,
                )


if __name__ == "__main__":
    unittest.main()
