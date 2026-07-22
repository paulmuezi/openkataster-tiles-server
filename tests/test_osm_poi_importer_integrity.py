from __future__ import annotations

import importlib.util
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "build_osm_poi_index.py"
)


def load_importer():
    spec = importlib.util.spec_from_file_location("openkataster_poi_importer", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load importer: {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def sample_row() -> dict[str, object]:
    osm_id = 123_456_789
    return {
        "id": osm_id * 4,
        "poi_id": f"n{osm_id}",
        "osm_type": "n",
        "osm_id": osm_id,
        "name": "Test-Apotheke",
        "display_source": "name",
        "name_norm": "test apotheke",
        "search_norm": "test apotheke hannover",
        "aliases": "",
        "brand": "",
        "operator": "",
        "category": "healthcare",
        "category_label": "Gesundheit",
        "class_key": "amenity",
        "subtype": "pharmacy",
        "category_terms": "apotheke gesundheit",
        "address": "Teststraße 1, 30159 Hannover",
        "address_norm": "teststrasse 1 30159 hannover",
        "street": "Teststraße",
        "housenumber": "1",
        "postcode": "30159",
        "city": "Hannover",
        "city_norm": "hannover",
        "state": "niedersachsen",
        "quality": 65,
        "locality": "Hannover",
        "locality_source": "osm",
        "locality_ags": "",
        "locality_distance_m": None,
        "state_slug": "niedersachsen",
        "state_name": "Niedersachsen",
        "lon": 9.7357,
        "lat": 52.3745,
        "utm_epsg": 25832,
        "easting": 550_000.0,
        "northing": 5_804_000.0,
    }


class ImporterIntegrityTests(unittest.TestCase):
    def test_duplicate_and_constraint_errors_leave_database_consistent(self) -> None:
        importer = load_importer()
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "poi.sqlite"
            connection = sqlite3.connect(database)
            try:
                importer.initialize_database(connection, 8)
                self.assertEqual(
                    connection.execute("PRAGMA journal_mode").fetchone()[0],
                    "delete",
                )
                self.assertEqual(
                    connection.execute("PRAGMA locking_mode").fetchone()[0],
                    "exclusive",
                )
                self.assertEqual(
                    connection.execute("PRAGMA user_version").fetchone()[0],
                    4,
                )
                self.assertEqual(
                    [
                        item[1]
                        for item in connection.execute(
                            "PRAGMA table_info(poi_source)"
                        ).fetchall()
                    ],
                    ["osm_type", "osm_id", "poi_id"],
                )

                row = sample_row()
                connection.execute("BEGIN")
                self.assertTrue(importer.insert_poi(connection, row))
                for _ in range(10_000):
                    self.assertFalse(importer.insert_poi(connection, row))

                invalid = dict(row)
                invalid["id"] = int(row["id"]) + 100
                invalid["poi_id"] = "n123456814"
                invalid["osm_id"] = 123_456_814
                invalid["state_slug"] = "hessen"
                with self.assertRaises(sqlite3.IntegrityError):
                    importer.insert_poi(connection, invalid)

                connection.commit()
                importer.finalize_database(
                    connection,
                    {
                        "active_states": ["niedersachsen"],
                        "build_id": "integrity-test",
                    },
                )
                self.assertEqual(
                    connection.execute("PRAGMA integrity_check").fetchone()[0],
                    "ok",
                )
                self.assertEqual(
                    connection.execute("PRAGMA quick_check").fetchone()[0],
                    "ok",
                )
                self.assertEqual(
                    connection.execute("PRAGMA foreign_key_check").fetchall(),
                    [],
                )
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM poi").fetchone()[0],
                    1,
                )
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM poi_source").fetchone()[0],
                    1,
                )
            finally:
                connection.close()


if __name__ == "__main__":
    unittest.main()
