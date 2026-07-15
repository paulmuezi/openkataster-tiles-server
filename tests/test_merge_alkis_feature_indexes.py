from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from producer.alkis_feature_schema import create_schema
from producer.merge_alkis_feature_indexes import merge_parts


class MergeFeatureIndexesTest(unittest.TestCase):
    def create_part(self, path: Path, source_db: str, features: list[tuple[str, str]]) -> None:
        with sqlite3.connect(path) as con:
            create_schema(con)
            for kind, gml_id in features:
                con.execute(
                    """
                    INSERT INTO features(
                        state_key, kind, source_db, gml_id, properties_json, geometry_wkb,
                        center_lon, center_lat, min_lon, max_lon, min_lat, max_lat
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "05",
                        kind,
                        source_db,
                        gml_id,
                        '{"gemarkungsnummer":"1","gemarkung":"Test"}',
                        b"wkb",
                        7.0,
                        51.0,
                        6.9,
                        7.1,
                        50.9,
                        51.1,
                    ),
                )
            con.execute(
                """
                INSERT INTO address_points(source_db, properties_json, geometry_wkb, lon, lat)
                VALUES (?, ?, ?, ?, ?)
                """,
                (source_db, '{}', b"wkb", 7.0, 51.0),
            )
            con.execute(
                """
                INSERT INTO feature_addresses(source_db, kind, gml_id, properties_json)
                VALUES (?, 'building', ?, ?)
                """,
                (source_db, f"address-{source_db}", '{"post_code":"12345","city":"Test"}'),
            )
            con.executemany(
                "INSERT INTO metadata(key, value) VALUES (?, ?)",
                (
                    ("format", "openkataster-features-sqlite"),
                    ("format_version", "1"),
                    ("source_srid", "25832"),
                    ("db_prefixes", "alkis_test"),
                    ("databases", source_db),
                    ("parcel_usage_enabled", "0"),
                    ("building_parcel_address_fallback_enabled", "0"),
                    ("plz_reference", "/tmp/plz.gpkg"),
                    ("municipality_reference", "/tmp/gemeinden.gpkg"),
                ),
            )
            con.commit()

    def test_merge_deduplicates_features_and_rebuilds_indexes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            first = root / "chunk_001.sqlite"
            second = root / "chunk_002.sqlite"
            out = root / "features.sqlite"
            self.create_part(first, "alkis_test_1", [("parcel", "p1"), ("building", "b1")])
            self.create_part(second, "alkis_test_2", [("building", "b1"), ("parcel", "p2")])

            merge_parts([first, second], out)

            with sqlite3.connect(out) as con:
                self.assertEqual(con.execute("SELECT count(*) FROM features").fetchone()[0], 3)
                self.assertEqual(con.execute("SELECT count(*) FROM feature_index").fetchone()[0], 3)
                self.assertEqual(con.execute("SELECT count(*) FROM address_points").fetchone()[0], 2)
                metadata = dict(con.execute("SELECT key, value FROM metadata"))
                self.assertEqual(metadata["count_parcels"], "2")
                self.assertEqual(metadata["count_buildings"], "1")
                self.assertEqual(metadata["databases"], "alkis_test_1,alkis_test_2")

    def test_relation_from_overlapping_part_uses_canonical_feature_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            first = root / "chunk_001.sqlite"
            second = root / "chunk_002.sqlite"
            out = root / "features.sqlite"
            self.create_part(first, "alkis_test_1", [("building", "building-1")])
            self.create_part(second, "alkis_test_2", [("building", "building-1")])

            with sqlite3.connect(second) as con:
                con.execute(
                    """
                    INSERT INTO feature_addresses(source_db, kind, gml_id, properties_json)
                    VALUES (?, 'building', 'building-1', ?)
                    """,
                    (
                        "alkis_test_2",
                        '{"street":"Feldstraße","house_number":"18","post_code":"31141","city":"Hildesheim"}',
                    ),
                )
                con.commit()

            merge_parts([first, second], out)

            with sqlite3.connect(out) as con:
                feature_source = con.execute(
                    "SELECT source_db FROM features WHERE kind='building' AND gml_id='building-1'"
                ).fetchone()[0]
                relation = con.execute(
                    """
                    SELECT source_db, properties_json
                    FROM feature_addresses
                    WHERE kind='building' AND gml_id='building-1'
                    """
                ).fetchone()
                self.assertEqual(feature_source, "alkis_test_1")
                self.assertEqual(relation[0], feature_source)
                self.assertIn('"house_number":"18"', relation[1])


if __name__ == "__main__":
    unittest.main()
