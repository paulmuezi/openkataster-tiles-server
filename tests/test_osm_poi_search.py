from __future__ import annotations

import inspect
import os
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from openkataster_tiles import poi_search


SCHEMA = """
CREATE TABLE meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
) WITHOUT ROWID;

CREATE TABLE poi (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    name_norm TEXT NOT NULL,
    search_norm TEXT NOT NULL,
    aliases TEXT NOT NULL DEFAULT '',
    brand TEXT NOT NULL DEFAULT '',
    operator TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL,
    category_label TEXT NOT NULL DEFAULT '',
    class_key TEXT NOT NULL,
    subtype TEXT NOT NULL,
    category_terms TEXT NOT NULL,
    address TEXT NOT NULL DEFAULT '',
    address_norm TEXT NOT NULL DEFAULT '',
    street TEXT NOT NULL DEFAULT '',
    housenumber TEXT NOT NULL DEFAULT '',
    postcode TEXT NOT NULL DEFAULT '',
    city TEXT NOT NULL DEFAULT '',
    city_norm TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL,
    lon REAL NOT NULL,
    lat REAL NOT NULL,
    quality INTEGER NOT NULL
);

CREATE TABLE poi_source (
    poi_id INTEGER NOT NULL REFERENCES poi(id),
    osm_type TEXT NOT NULL,
    osm_id INTEGER NOT NULL,
    PRIMARY KEY (osm_type, osm_id)
) WITHOUT ROWID;
CREATE INDEX poi_source_poi_idx ON poi_source(poi_id);
CREATE INDEX poi_state_name_idx ON poi(state, name_norm);

CREATE VIRTUAL TABLE poi_fts USING fts5(
    search_norm,
    content='poi',
    content_rowid='id',
    prefix='2 3 4',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE VIRTUAL TABLE poi_rtree USING rtree(
    id,
    min_lon,
    max_lon,
    min_lat,
    max_lat
);
"""


class OsmPoiSearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.directory = Path(self.temporary_directory.name)
        self.active_path = self.directory / "active" / "osm-poi.sqlite"
        self.active_path.parent.mkdir()
        self.environment = patch.dict(
            os.environ,
            {
                poi_search.POI_DB_ENV: str(self.active_path),
                poi_search.POI_SEARCH_ENABLED_ENV: "1",
            },
        )
        self.environment.start()
        poi_search._reset_for_tests()

    def tearDown(self) -> None:
        poi_search._reset_for_tests()
        self.environment.stop()
        self.temporary_directory.cleanup()

    def create_database(
        self,
        name: str,
        rows: list[dict] | None = None,
        *,
        metadata: dict[str, str] | None = None,
    ) -> Path:
        path = self.directory / name
        connection = sqlite3.connect(path)
        connection.executescript(SCHEMA)
        for key, value in (metadata or {}).items():
            connection.execute(
                "INSERT INTO meta(key, value) VALUES (?, ?)", (key, value)
            )
        for index, values in enumerate(rows or (), start=1):
            row = {
                "id": index,
                "name": "Beispiel",
                "name_norm": "beispiel",
                "search_norm": "beispiel einrichtung",
                "aliases": "",
                "brand": "",
                "operator": "",
                "category": "amenity",
                "category_label": "Einrichtung",
                "class_key": "amenity",
                "subtype": "community_centre",
                "category_terms": "einrichtung",
                "address": "",
                "address_norm": "",
                "street": "",
                "housenumber": "",
                "postcode": "",
                "city": "",
                "city_norm": "",
                "state": "niedersachsen",
                "lon": 9.73,
                "lat": 52.37,
                "quality": 50,
                "osm_type": "n",
                "osm_id": 1000 + index,
            }
            row.update(values)
            row["search_norm"] = poi_search._normalize_text(
                " ".join(
                    str(row.get(field) or "")
                    for field in (
                        "name",
                        "aliases",
                        "brand",
                        "operator",
                        "category_terms",
                        "address",
                        "city",
                    )
                )
            )
            connection.execute(
                """
                INSERT INTO poi(
                    id, name, name_norm, search_norm, aliases, brand, operator,
                    category, category_label, class_key, subtype,
                    category_terms, address, address_norm, street,
                    housenumber, postcode, city, city_norm, state,
                    lon, lat, quality
                ) VALUES (
                    :id, :name, :name_norm, :search_norm, :aliases, :brand, :operator,
                    :category, :category_label, :class_key, :subtype,
                    :category_terms, :address, :address_norm, :street,
                    :housenumber, :postcode, :city, :city_norm, :state,
                    :lon, :lat, :quality
                )
                """,
                row,
            )
            connection.execute(
                """
                INSERT INTO poi_source(poi_id, osm_type, osm_id)
                VALUES (:id, :osm_type, :osm_id)
                """,
                row,
            )
            connection.execute(
                """
                INSERT INTO poi_rtree(
                    id, min_lon, max_lon, min_lat, max_lat
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    row["id"],
                    row["lon"],
                    row["lon"],
                    row["lat"],
                    row["lat"],
                ),
            )
        connection.execute("INSERT INTO poi_fts(poi_fts) VALUES ('rebuild')")
        connection.commit()
        connection.close()
        return path

    def activate(self, database: Path) -> None:
        replacement = self.active_path.with_name("osm-poi.sqlite.next")
        replacement.unlink(missing_ok=True)
        replacement.symlink_to(database)
        os.replace(replacement, self.active_path)

    def test_missing_and_invalid_database_fail_open(self) -> None:
        self.assertFalse(poi_search.poi_index_available())
        self.assertEqual(
            poi_search.search_poi_suggestions(
                "Beispiel", {"niedersachsen"}, 8
            ),
            [],
        )
        self.assertIsNone(
            poi_search.search_poi_by_id(
                "osm:n:1001", {"niedersachsen"}
            )
        )

        invalid = self.directory / "invalid.sqlite"
        connection = sqlite3.connect(invalid)
        connection.execute("CREATE TABLE unrelated(value TEXT)")
        connection.close()
        self.activate(invalid)

        self.assertFalse(poi_search.poi_index_available())
        self.assertEqual(
            poi_search.search_poi_suggestions(
                "Beispiel", {"niedersachsen"}, 8
            ),
            [],
        )

    def test_fts_query_is_safe_and_does_not_fall_back_to_like(self) -> None:
        database = self.create_database(
            "safe.sqlite",
            [
                {
                    "name": "Café Alpha",
                    "name_norm": "cafe alpha",
                    "category": "food",
                    "category_label": "Gastronomie",
                    "category_terms": "cafe gastronomie",
                }
            ],
        )
        self.activate(database)

        hostile = 'Café" OR * NOT poi: DROP TABLE poi; --'
        self.assertEqual(
            poi_search.search_poi_suggestions(
                hostile, {"niedersachsen"}, 8
            ),
            [],
        )
        normal = poi_search.search_poi_suggestions(
            "Cafe Alp", {"niedersachsen"}, 8
        )
        self.assertEqual([item["label"] for item in normal], ["Café Alpha"])
        source = inspect.getsource(poi_search._fetch_candidates_locked).upper()
        self.assertNotIn(" LIKE ", source)

    def test_two_character_name_prefix_uses_bounded_state_index_path(self) -> None:
        database = self.create_database(
            "short-prefix.sqlite",
            [
                {
                    "name": "Stadtmuseum Hannover",
                    "name_norm": "stadtmuseum hannover",
                    "category": "culture",
                    "category_terms": "museum kultur",
                    "state": "niedersachsen",
                    "quality": 90,
                    "osm_id": 8101,
                },
                {
                    "name": "Stadtpark Bremen",
                    "name_norm": "stadtpark bremen",
                    "category": "leisure",
                    "category_terms": "park freizeit",
                    "state": "bremen",
                    "quality": 100,
                    "osm_id": 8102,
                },
                {
                    "name": "Museum am Markt",
                    "name_norm": "museum am markt",
                    "aliases": "Stadtgalerie",
                    "category": "culture",
                    "category_terms": "museum kultur",
                    "state": "niedersachsen",
                    "quality": 100,
                    "osm_id": 8103,
                },
            ],
        )
        self.activate(database)

        self.assertEqual(
            ["Stadtmuseum Hannover"],
            [
                result["label"]
                for result in poi_search.search_poi_suggestions(
                    "St", {"niedersachsen"}, 8
                )
            ],
        )
        # Longer queries retain alias/category/address matching through FTS.
        self.assertEqual(
            ["Museum am Markt"],
            [
                result["label"]
                for result in poi_search.search_poi_suggestions(
                    "Stadtgalerie", {"niedersachsen"}, 8
                )
            ],
        )
        source = inspect.getsource(
            poi_search._fetch_name_prefix_candidates_locked
        ).upper()
        self.assertNotIn("POI_FTS MATCH", source)
        self.assertNotIn("BM25", source)
        self.assertNotIn(" LIKE ", source)

    def test_two_character_prefix_ranks_exact_and_near_matches(self) -> None:
        database = self.create_database(
            "short-prefix-ranking.sqlite",
            [
                {
                    "name": "dm",
                    "name_norm": "dm",
                    "category": "retail",
                    "class_key": "shop",
                    "category_terms": "drogerie einkaufen",
                    "lon": 12.0,
                    "lat": 52.0,
                    "quality": 100,
                    "osm_id": 8201,
                },
                {
                    "name": "DMS Zentrum",
                    "name_norm": "dms zentrum",
                    "category": "services",
                    "class_key": "office",
                    "category_terms": "dienstleistung",
                    "lon": 9.731,
                    "lat": 52.371,
                    "quality": 10,
                    "osm_id": 8202,
                },
                {
                    "name": "DMS Fern",
                    "name_norm": "dms fern",
                    "category": "services",
                    "class_key": "office",
                    "category_terms": "dienstleistung",
                    "lon": 11.0,
                    "lat": 52.0,
                    "quality": 100,
                    "osm_id": 8203,
                },
            ],
        )
        self.activate(database)

        results = poi_search.search_poi_suggestions(
            "dm",
            {"niedersachsen"},
            8,
            near_lon=9.73,
            near_lat=52.37,
        )
        self.assertEqual("dm", results[0]["label"])
        self.assertEqual(
            ["DMS Zentrum", "DMS Fern"],
            [result["label"] for result in results[1:]],
        )

    def test_sharp_s_and_ss_match_normalized_name_and_address(self) -> None:
        database = self.create_database(
            "sharp-s.sqlite",
            [
                {
                    "name": "Straße der Weserrenaissance",
                    "name_norm": "strasse der weserrenaissance",
                    "category": "culture",
                    "category_label": "Kultur",
                    "category_terms": "kultur denkmal",
                    "address": "Pappelstraße 7, Bremen",
                    "address_norm": "pappelstrasse 7 bremen",
                    "street": "Pappelstraße",
                    "housenumber": "7",
                    "city": "Bremen",
                    "city_norm": "bremen",
                    "state": "bremen",
                    "osm_id": 2026,
                }
            ],
        )
        self.activate(database)

        for query in (
            "Straße Weserrenaissance",
            "Strasse Weserrenaissance",
            "Pappelstraße",
            "Pappelstrasse",
        ):
            with self.subTest(query=query):
                self.assertEqual(
                    ["Straße der Weserrenaissance"],
                    [
                        result["label"]
                        for result in poi_search.search_poi_suggestions(
                            query, {"bremen"}, 8
                        )
                    ],
                )

    def test_state_filter_and_result_contract(self) -> None:
        database = self.create_database(
            "states.sqlite",
            [
                {
                    "name": "Stadtmuseum Hannover",
                    "name_norm": "stadtmuseum hannover",
                    "category": "culture",
                    "category_label": "Museum",
                    "category_terms": "museum kultur",
                    "city": "Hannover",
                    "city_norm": "hannover",
                    "state": "niedersachsen",
                    "osm_type": "w",
                    "osm_id": 42,
                },
                {
                    "name": "Stadtmuseum Bremen",
                    "name_norm": "stadtmuseum bremen",
                    "category": "culture",
                    "category_label": "Museum",
                    "category_terms": "museum kultur",
                    "city": "Bremen",
                    "city_norm": "bremen",
                    "state": "bremen",
                    "osm_type": "r",
                    "osm_id": 43,
                },
            ],
        )
        self.activate(database)

        results = poi_search.search_poi_suggestions(
            "Stadtmuseum", {"niedersachsen"}, 8
        )
        self.assertEqual(len(results), 1)
        result = results[0]
        self.assertEqual(result["kind"], "poi")
        self.assertEqual(result["result_type"], "poi")
        self.assertEqual(result["search_scope"], "poi")
        self.assertEqual(result["poi_id"], "osm:w:42")
        self.assertEqual(result["category_label"], "Museum")
        self.assertEqual(result["primary_label"], "Stadtmuseum Hannover")
        self.assertEqual(result["secondary_label"], "Hannover")
        self.assertNotIn("Museum", result["secondary_label"])
        self.assertEqual(result["state"], "niedersachsen")
        self.assertEqual(result["source"], "OpenStreetMap")
        self.assertEqual(result["center"], [9.73, 52.37])
        self.assertFalse(any(key.startswith("_") for key in result))

    def test_near_point_ranks_equal_text_matches_by_distance(self) -> None:
        database = self.create_database(
            "distance.sqlite",
            [
                {
                    "name": "Café Beispiel",
                    "name_norm": "cafe beispiel",
                    "category": "food",
                    "category_label": "Café",
                    "category_terms": "cafe gastronomie",
                    "lon": 12.0,
                    "lat": 52.0,
                    "quality": 100,
                    "osm_id": 501,
                },
                {
                    "name": "Café Beispiel",
                    "name_norm": "cafe beispiel",
                    "category": "food",
                    "category_label": "Café",
                    "category_terms": "cafe gastronomie",
                    "lon": 9.731,
                    "lat": 52.371,
                    "quality": 10,
                    "osm_id": 502,
                },
            ],
        )
        self.activate(database)

        results = poi_search.search_poi_suggestions(
            "Cafe Beispiel",
            {"niedersachsen"},
            8,
            near_lon=9.73,
            near_lat=52.37,
        )
        self.assertEqual(
            [result["poi_id"] for result in results],
            ["osm:n:502", "osm:n:501"],
        )
        self.assertLess(results[0]["distance_m"], results[1]["distance_m"])

    def test_station_representations_are_collapsed_but_all_index_rows_remain(self) -> None:
        database = self.create_database(
            "duplicates.sqlite",
            [
                {
                    "name": "Bremen Hauptbahnhof",
                    "name_norm": "bremen hauptbahnhof",
                    "category": "transport",
                    "category_label": "Verkehr",
                    "class_key": "public_transport",
                    "subtype": "station",
                    "category_terms": "bahnhof station verkehr",
                    "address": "Bahnhofsplatz 15, 28195 Bremen",
                    "address_norm": "bahnhofsplatz 15 28195 bremen",
                    "street": "Bahnhofsplatz",
                    "housenumber": "15",
                    "postcode": "28195",
                    "city": "Bremen",
                    "city_norm": "bremen",
                    "state": "bremen",
                    "lon": 8.81375,
                    "lat": 53.08330,
                    "quality": 90,
                    "osm_type": "n",
                    "osm_id": 7001,
                },
                {
                    "name": "Bremen Hauptbahnhof",
                    "name_norm": "bremen hauptbahnhof",
                    "category": "transport",
                    "category_label": "Verkehr",
                    "class_key": "public_transport",
                    "subtype": "platform",
                    "category_terms": "bahnsteig bahnhof verkehr",
                    "city": "Bremen",
                    "city_norm": "bremen",
                    "state": "bremen",
                    "lon": 8.81383,
                    "lat": 53.08351,
                    "quality": 40,
                    "osm_type": "w",
                    "osm_id": 7002,
                },
                {
                    "name": "A&O Bremen Hauptbahnhof",
                    "name_norm": "a o bremen hauptbahnhof",
                    "category": "tourism",
                    "category_label": "Tourismus",
                    "class_key": "tourism",
                    "subtype": "hostel",
                    "category_terms": "hostel tourismus",
                    "city": "Bremen",
                    "city_norm": "bremen",
                    "state": "bremen",
                    "lon": 8.80471,
                    "lat": 53.08555,
                    "quality": 95,
                    "osm_type": "n",
                    "osm_id": 7003,
                },
            ],
        )
        self.activate(database)

        results = poi_search.search_poi_suggestions(
            "Hauptbahnhof Bremen", {"bremen"}, 8
        )
        self.assertEqual(
            ["Bremen Hauptbahnhof", "A&O Bremen Hauptbahnhof"],
            [result["label"] for result in results],
        )
        self.assertEqual("osm:n:7001", results[0]["poi_id"])
        connection = sqlite3.connect(database)
        try:
            self.assertEqual(3, connection.execute("SELECT COUNT(*) FROM poi").fetchone()[0])
        finally:
            connection.close()

    def test_equal_names_at_distinct_structured_addresses_remain_selectable(self) -> None:
        database = self.create_database(
            "branches.sqlite",
            [
                {
                    "name": "dm",
                    "name_norm": "dm",
                    "category": "retail",
                    "category_label": "Einkaufen",
                    "class_key": "shop",
                    "subtype": "chemist",
                    "category_terms": "drogerie einkaufen",
                    "address": "Marktstraße 1, Bremen",
                    "address_norm": "marktstrasse 1 bremen",
                    "street": "Marktstraße",
                    "housenumber": "1",
                    "city": "Bremen",
                    "city_norm": "bremen",
                    "state": "bremen",
                    "lon": 8.8100,
                    "lat": 53.0800,
                    "osm_id": 7101,
                },
                {
                    "name": "dm",
                    "name_norm": "dm",
                    "category": "retail",
                    "category_label": "Einkaufen",
                    "class_key": "shop",
                    "subtype": "chemist",
                    "category_terms": "drogerie einkaufen",
                    "address": "Marktstraße 3, Bremen",
                    "address_norm": "marktstrasse 3 bremen",
                    "street": "Marktstraße",
                    "housenumber": "3",
                    "city": "Bremen",
                    "city_norm": "bremen",
                    "state": "bremen",
                    "lon": 8.8102,
                    "lat": 53.0801,
                    "osm_id": 7102,
                },
            ],
        )
        self.activate(database)

        results = poi_search.search_poi_suggestions("dm", {"bremen"}, 8)
        self.assertEqual(2, len(results))
        self.assertEqual(
            {"Marktstraße 1, Bremen", "Marktstraße 3, Bremen"},
            {result["secondary_label"] for result in results},
        )

    def test_nearby_same_type_pois_without_addresses_remain_selectable(self) -> None:
        database = self.create_database(
            "nearby-stops.sqlite",
            [
                {
                    "name": "Marktplatz",
                    "name_norm": "marktplatz",
                    "category": "transport",
                    "category_label": "Verkehr",
                    "class_key": "highway",
                    "subtype": "bus_stop",
                    "category_terms": "bushaltestelle verkehr",
                    "city": "Bremen",
                    "city_norm": "bremen",
                    "state": "bremen",
                    "lon": 8.8100,
                    "lat": 53.0800,
                    "osm_type": "n",
                    "osm_id": 7201,
                },
                {
                    "name": "Marktplatz",
                    "name_norm": "marktplatz",
                    "category": "transport",
                    "category_label": "Verkehr",
                    "class_key": "highway",
                    "subtype": "bus_stop",
                    "category_terms": "bushaltestelle verkehr",
                    "city": "Bremen",
                    "city_norm": "bremen",
                    "state": "bremen",
                    "lon": 8.8102,
                    "lat": 53.0801,
                    "osm_type": "n",
                    "osm_id": 7202,
                },
            ],
        )
        self.activate(database)

        results = poi_search.search_poi_suggestions(
            "Marktplatz Bremen", {"bremen"}, 8
        )
        self.assertEqual(
            ["osm:n:7201", "osm:n:7202"],
            [result["poi_id"] for result in results],
        )

    def test_exact_stable_id_respects_state_filter(self) -> None:
        database = self.create_database(
            "exact.sqlite",
            [
                {
                    "name": "Hannover Hauptbahnhof",
                    "name_norm": "hannover hauptbahnhof",
                    "category": "transport",
                    "category_label": "Bahnhof",
                    "category_terms": "bahnhof station",
                    "osm_type": "r",
                    "osm_id": 123456,
                }
            ],
        )
        self.activate(database)

        result = poi_search.search_poi_by_id(
            "osm:r:123456", {"niedersachsen"}
        )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["poi_id"], "osm:r:123456")
        self.assertIsNone(
            poi_search.search_poi_by_id("osm:r:123456", {"bremen"})
        )
        self.assertIsNone(
            poi_search.search_poi_by_id(
                "osm:r:123456 OR 1=1", {"niedersachsen"}
            )
        )

    def test_atomic_symlink_switch_reopens_the_index(self) -> None:
        first = self.create_database(
            "first.sqlite",
            [
                {
                    "name": "Alpha Ort",
                    "name_norm": "alpha ort",
                    "osm_id": 701,
                }
            ],
        )
        second = self.create_database(
            "second.sqlite",
            [
                {
                    "name": "Beta Ort",
                    "name_norm": "beta ort",
                    "osm_id": 702,
                }
            ],
        )
        self.activate(first)
        first_signature = poi_search.poi_index_signature()
        self.assertEqual(
            [
                result["label"]
                for result in poi_search.search_poi_suggestions(
                    "Alpha", {"niedersachsen"}, 8
                )
            ],
            ["Alpha Ort"],
        )

        self.activate(second)
        second_signature = poi_search.poi_index_signature()
        self.assertNotEqual(first_signature, second_signature)
        self.assertEqual(
            poi_search.search_poi_suggestions(
                "Alpha", {"niedersachsen"}, 8
            ),
            [],
        )
        self.assertEqual(
            [
                result["label"]
                for result in poi_search.search_poi_suggestions(
                    "Beta", {"niedersachsen"}, 8
                )
            ],
            ["Beta Ort"],
        )

    def test_candidate_and_result_count_are_hard_bounded(self) -> None:
        database = self.create_database(
            "bounded.sqlite",
            [
                {
                    "id": index,
                    "name": f"Apotheke {index:03d}",
                    "name_norm": f"apotheke {index:03d}",
                    "category": "healthcare",
                    "category_label": "Apotheke",
                    "category_terms": "apotheke gesundheit",
                    "osm_id": 10_000 + index,
                    "lon": 9.0 + index / 10_000,
                }
                for index in range(1, 121)
            ],
        )
        self.activate(database)
        results = poi_search.search_poi_suggestions(
            "Apotheke", {"niedersachsen"}, 500
        )
        self.assertEqual(len(results), poi_search.MAX_CANDIDATES)
        short_prefix_results = poi_search.search_poi_suggestions(
            "Ap", {"niedersachsen"}, 500
        )
        self.assertEqual(
            len(short_prefix_results), poi_search.MAX_CANDIDATES
        )

    def test_metadata_and_feature_flag(self) -> None:
        database = self.create_database(
            "metadata.sqlite",
            metadata={
                "format_version": "2",
                "source": '"OpenStreetMap / Geofabrik"',
            },
        )
        self.activate(database)
        self.assertTrue(poi_search.poi_index_available())
        self.assertEqual(
            poi_search.poi_index_metadata(),
            {
                "format_version": 2,
                "source": "OpenStreetMap / Geofabrik",
            },
        )

        with patch.dict(
            os.environ, {poi_search.POI_SEARCH_ENABLED_ENV: "0"}
        ):
            self.assertFalse(poi_search.poi_index_available())
            self.assertEqual(
                poi_search.search_poi_suggestions(
                    "Beispiel", {"niedersachsen"}, 8
                ),
                [],
            )
            self.assertEqual(poi_search.poi_index_metadata(), {})


if __name__ == "__main__":
    unittest.main()
