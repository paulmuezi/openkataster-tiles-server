from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from shapely import wkb
from shapely.geometry import Polygon

from openkataster_tiles import main


class SachsenAnhaltFeatureNormalizationTests(unittest.TestCase):
    def test_parcel_aliases_and_usage_are_converted_to_the_public_contract(self) -> None:
        geometry = {"type": "Polygon", "coordinates": []}
        normalized = main.normalize_feature_properties_for_response(
            "sachsen-anhalt",
            "parcel",
            {
                "id": "st_parcel_1",
                "type": "parcel",
                "label": "1190",
                "fill": "rgba(0,0,0,0)",
                "fill_color": "rgba(0,0,0,0)",
                "gemarkung": "Großbadegast",
                "flur": "3",
                "flurstueck": "1190",
                "lage": "Am Mühlfeld 16 B",
                "usage": (
                    "Sport-, Freizeit- und Erholungsfläche(funktion:Grünanlage);83"
                    "|Straßenverkehr(funktion:null);17"
                ),
                "amtliche_flaeche_m2": 100,
                "flstkennz": "15185500301190______",
                "gemaschl": "151855",
                "flurschl": "151855003",
                "source_db": "sachsen-anhalt",
                "gml_id": "st_parcel_1",
                "flurstueckskennzeichen": "15185500301190______",
                "address_count": 2,
                "geometry": geometry,
            },
        )

        self.assertEqual(normalized["gemarkungsschluessel"], "151855")
        self.assertEqual(normalized["nutzung_haupt"], "Sport-, Freizeit- und Erholungsfläche (Grünanlage)")
        self.assertEqual(
            normalized["nutzungen"],
            [
                {
                    "thema": "Sport-, Freizeit- und Erholungsfläche (Grünanlage)",
                    "flaeche_m2": 83,
                    "anteil": 0.83,
                },
                {"thema": "Straßenverkehr", "flaeche_m2": 17, "anteil": 0.17},
            ],
        )
        self.assertIs(normalized["geometry"], geometry)
        self.assertFalse(
            {
                "type",
                "label",
                "fill",
                "fill_color",
                "usage",
                "flstkennz",
                "gemaschl",
                "flurschl",
                "address_count",
            }
            & normalized.keys()
        )

    def test_building_keeps_business_fields_but_drops_renderer_properties(self) -> None:
        normalized = main.normalize_feature_properties_for_response(
            "",
            "building",
            {
                "id": "st_building_2",
                "type": "building",
                "funktion": "Wohngebäude",
                "underground": True,
                "rellage": "Unter der Erdoberfläche",
                "fill": "#f37f82",
                "source_db": "sachsen-anhalt",
                "gml_id": "st_building_2",
                "address": "Pfortegasse 4",
                "gebaeudekennzeichen": "",
            },
        )

        self.assertEqual(normalized["gebaeudefunktion_text"], "Wohngebäude")
        self.assertEqual(normalized["lage_zur_erdoberflaeche_text"], "Unter der Erdoberfläche")
        self.assertEqual(normalized["address"], "Pfortegasse 4")
        self.assertNotIn("type", normalized)
        self.assertNotIn("funktion", normalized)
        self.assertNotIn("underground", normalized)
        self.assertNotIn("rellage", normalized)
        self.assertNotIn("fill", normalized)
        self.assertNotIn("gebaeudekennzeichen", normalized)

    def test_other_states_are_not_filtered(self) -> None:
        properties = {"source_db": "niedersachsen.gml", "custom_field": "bleibt erhalten"}
        self.assertEqual(
            main.normalize_feature_properties_for_response("niedersachsen", "parcel", properties),
            properties,
        )

    def test_point_query_applies_normalization_without_rewriting_source_database(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "sachsen-anhalt.features.sqlite"
            geometry = Polygon(((11.0, 51.0), (11.01, 51.0), (11.01, 51.01), (11.0, 51.01), (11.0, 51.0)))
            min_lon, min_lat, max_lon, max_lat = geometry.bounds
            properties = {
                "id": "st_parcel_1",
                "type": "parcel",
                "label": "831",
                "fill": "rgba(0,0,0,0)",
                "gemarkung": "Zörbig",
                "flur": "12",
                "flurstueck": "831",
                "usage": "Straßenverkehr(funktion:null);100",
                "gemaschl": "151786",
                "source_db": "sachsen-anhalt",
                "gml_id": "st_parcel_1",
            }
            connection = sqlite3.connect(path)
            connection.executescript(
                """
                CREATE TABLE features (
                    id INTEGER PRIMARY KEY,
                    state_key TEXT,
                    kind TEXT NOT NULL,
                    source_db TEXT NOT NULL,
                    gml_id TEXT NOT NULL,
                    properties_json TEXT NOT NULL,
                    geometry_wkb BLOB NOT NULL,
                    min_lon REAL NOT NULL,
                    max_lon REAL NOT NULL,
                    min_lat REAL NOT NULL,
                    max_lat REAL NOT NULL
                );
                CREATE VIRTUAL TABLE feature_index USING rtree(id, min_lon, max_lon, min_lat, max_lat);
                CREATE TABLE feature_addresses (
                    source_db TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    gml_id TEXT NOT NULL,
                    properties_json TEXT NOT NULL
                );
                CREATE TABLE address_points (
                    id INTEGER PRIMARY KEY,
                    source_db TEXT NOT NULL,
                    properties_json TEXT NOT NULL,
                    geometry_wkb BLOB NOT NULL,
                    lon REAL NOT NULL,
                    lat REAL NOT NULL
                );
                CREATE VIRTUAL TABLE address_index USING rtree(id, min_lon, max_lon, min_lat, max_lat);
                """
            )
            connection.execute(
                """
                INSERT INTO features(
                    id, state_key, kind, source_db, gml_id, properties_json,
                    geometry_wkb, min_lon, max_lon, min_lat, max_lat
                ) VALUES(1, 'sachsen-anhalt', 'parcel', 'sachsen-anhalt', 'st_parcel_1', ?, ?, ?, ?, ?, ?)
                """,
                (json.dumps(properties), wkb.dumps(geometry), min_lon, max_lon, min_lat, max_lat),
            )
            connection.execute(
                "INSERT INTO feature_index VALUES(1, ?, ?, ?, ?)",
                (min_lon, max_lon, min_lat, max_lat),
            )
            connection.commit()
            connection.close()

            parcels, buildings = main.query_features_in_index(path, 11.005, 51.005)

            self.assertEqual(buildings, [])
            self.assertEqual(len(parcels), 1)
            self.assertEqual(parcels[0]["nutzung_haupt"], "Straßenverkehr")
            self.assertEqual(parcels[0]["gemarkungsschluessel"], "151786")
            self.assertNotIn("fill", parcels[0])
            self.assertNotIn("usage", parcels[0])

            with sqlite3.connect(path) as source:
                stored = json.loads(source.execute("SELECT properties_json FROM features").fetchone()[0])
            self.assertEqual(stored["fill"], "rgba(0,0,0,0)")
            self.assertEqual(stored["usage"], "Straßenverkehr(funktion:null);100")


if __name__ == "__main__":
    unittest.main()
