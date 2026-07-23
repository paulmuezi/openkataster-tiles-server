from __future__ import annotations

import json
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi import HTTPException
from shapely import wkb
from shapely.geometry import Polygon

from openkataster_tiles import main
from producer.alkis_feature_schema import create_schema


class OnOfficeSelectionPayloadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.database_path = (
            Path(self.temporary_directory.name)
            / "niedersachsen.features.sqlite"
        )
        self.entry = main.FeatureDbEntry(
            name="niedersachsen",
            path=self.database_path,
        )
        self.access = main.ApiAccessContext(
            mode="pro",
            token="onoffice-test-token",
            claims={"scopes": ["feature:read"]},
        )
        self._create_fixture()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _insert_feature(
        self,
        connection: sqlite3.Connection,
        *,
        feature_id: int,
        kind: str,
        gml_id: str,
        properties: dict,
        geometry: Polygon,
    ) -> None:
        min_lon, min_lat, max_lon, max_lat = geometry.bounds
        center = geometry.representative_point()
        connection.execute(
            """
            INSERT INTO features (
                id, state_key, kind, source_db, gml_id, properties_json,
                geometry_wkb, center_lon, center_lat,
                min_lon, max_lon, min_lat, max_lat
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                feature_id,
                "niedersachsen",
                kind,
                "fixture.gml",
                gml_id,
                json.dumps(
                    {
                        "source_db": "fixture.gml",
                        "gml_id": gml_id,
                        **properties,
                    }
                ),
                wkb.dumps(geometry),
                center.x,
                center.y,
                min_lon,
                max_lon,
                min_lat,
                max_lat,
            ),
        )
        connection.execute(
            """
            INSERT INTO feature_index (
                id, min_lon, max_lon, min_lat, max_lat
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (feature_id, min_lon, max_lon, min_lat, max_lat),
        )

    def _create_fixture(self) -> None:
        connection = sqlite3.connect(self.database_path)
        create_schema(connection)
        parcel = Polygon(((0, 0), (2, 0), (0, 2), (0, 0)))
        inside_building = Polygon(
            ((0.2, 0.2), (0.5, 0.2), (0.5, 0.5), (0.2, 0.5), (0.2, 0.2))
        )
        bbox_only_building = Polygon(
            ((1.4, 1.4), (1.6, 1.4), (1.6, 1.6), (1.4, 1.6), (1.4, 1.4))
        )
        second_parcel = Polygon(
            ((3, 3), (4, 3), (4, 4), (3, 4), (3, 3))
        )
        self._insert_feature(
            connection,
            feature_id=1,
            kind="parcel",
            gml_id="parcel-1",
            properties={
                "gemarkung": "Bemerode",
                "gemarkungsnummer": "4887",
                "flur": "1",
                "flurstueck": "100/1",
                "flurstueckskennzeichen": "03123400100100/0001",
                "zaehler": "100",
                "nenner": "1",
                "amtliche_flaeche_m2": 321.5,
            },
            geometry=parcel,
        )
        self._insert_feature(
            connection,
            feature_id=2,
            kind="building",
            gml_id="building-inside",
            properties={"gebaeudefunktion_text": "Wohngebäude"},
            geometry=inside_building,
        )
        self._insert_feature(
            connection,
            feature_id=3,
            kind="building",
            gml_id="building-bbox-only",
            properties={"gebaeudefunktion_text": "Nebengebäude"},
            geometry=bbox_only_building,
        )
        self._insert_feature(
            connection,
            feature_id=4,
            kind="parcel",
            gml_id="parcel-without-area",
            properties={
                "gemarkung": "Bemerode",
                "flur": "1",
                "flurstueck": "101/1",
            },
            geometry=second_parcel,
        )
        connection.execute(
            """
            INSERT INTO feature_addresses (
                source_db, kind, gml_id, properties_json
            ) VALUES (?, ?, ?, ?)
            """,
            (
                "fixture.gml",
                "building",
                "building-inside",
                json.dumps(
                    {
                        "street": "Lehmbuschfeld",
                        "house_number": "14",
                        "post_code": "30539",
                        "city": "Hannover",
                        "country": "Deutschland",
                        "label": "Lehmbuschfeld 14, 30539 Hannover",
                    }
                ),
            ),
        )
        connection.commit()
        connection.close()

    def _create_land_register_schema(self) -> None:
        connection = sqlite3.connect(self.database_path)
        connection.executescript(
            """
            CREATE TABLE formal_land_register_entries (
                book_key TEXT PRIMARY KEY,
                land TEXT NOT NULL,
                district_code TEXT NOT NULL,
                sheet_number TEXT NOT NULL,
                district_name TEXT,
                land_register_office_land TEXT,
                land_register_office_code TEXT,
                land_register_office_name TEXT,
                completeness TEXT NOT NULL
            ) WITHOUT ROWID;
            CREATE TABLE feature_formal_land_register (
                id INTEGER PRIMARY KEY,
                source_db TEXT NOT NULL,
                kind TEXT NOT NULL,
                gml_id TEXT NOT NULL,
                book_key TEXT NOT NULL,
                resolution TEXT NOT NULL,
                resolution_depth INTEGER NOT NULL,
                origin_book_key TEXT,
                relation_source_db TEXT NOT NULL,
                UNIQUE(kind, gml_id, book_key)
            );
            CREATE TABLE formal_land_register_offices (
                land TEXT NOT NULL,
                office_code TEXT NOT NULL,
                office_name TEXT,
                completeness TEXT NOT NULL,
                PRIMARY KEY (land, office_code)
            ) WITHOUT ROWID;
            CREATE TABLE land_register_office_authority_sources (
                id INTEGER PRIMARY KEY,
                relation_source_db TEXT NOT NULL,
                method TEXT NOT NULL,
                precedence INTEGER NOT NULL,
                source_object_key TEXT NOT NULL,
                office_land TEXT NOT NULL,
                office_code TEXT NOT NULL
            );
            CREATE TABLE feature_land_register_office_authority (
                feature_id INTEGER NOT NULL,
                source_id INTEGER NOT NULL,
                PRIMARY KEY (feature_id, source_id)
            ) WITHOUT ROWID;
            CREATE VIEW feature_land_register_office_authority_status AS
            SELECT
                feature.kind,
                feature.gml_id,
                feature.source_db,
                MIN(source.precedence) AS winning_precedence,
                COUNT(DISTINCT source.office_land || X'1F' || source.office_code)
                    AS office_count,
                CASE
                    WHEN COUNT(DISTINCT source.office_land || X'1F' || source.office_code) = 1
                    THEN MIN(source.office_land)
                END AS office_land,
                CASE
                    WHEN COUNT(DISTINCT source.office_land || X'1F' || source.office_code) = 1
                    THEN MIN(source.office_code)
                END AS office_code,
                CASE
                    WHEN COUNT(DISTINCT source.office_land || X'1F' || source.office_code) = 1
                    THEN MIN(office.office_name)
                END AS office_name,
                CASE
                    WHEN COUNT(DISTINCT source.office_land || X'1F' || source.office_code) > 1
                    THEN 'ambiguous'
                    WHEN MIN(office.office_name) IS NULL THEN 'key_only'
                    ELSE 'exact'
                END AS status
            FROM features feature
            JOIN feature_land_register_office_authority relation
              ON relation.feature_id = feature.id
            JOIN land_register_office_authority_sources source
              ON source.id = relation.source_id
            JOIN formal_land_register_offices office
              ON office.land = source.office_land
             AND office.office_code = source.office_code
            WHERE source.precedence = (
                SELECT MIN(candidate_source.precedence)
                FROM feature_land_register_office_authority candidate_relation
                JOIN land_register_office_authority_sources candidate_source
                  ON candidate_source.id = candidate_relation.source_id
                WHERE candidate_relation.feature_id = feature.id
            )
            GROUP BY feature.id, feature.kind, feature.gml_id, feature.source_db;
            """
        )
        connection.commit()
        connection.close()

    def _insert_formal_land_register_entry(
        self,
        connection: sqlite3.Connection,
        *,
        book_key: str,
        sheet_number: str,
        source_db: str = "fixture.gml",
        gml_id: str = "parcel-1",
        district_code: str = "6300",
        district_name: str = "Zippendorf",
        resolution: str = "direct",
        resolution_depth: int = 0,
    ) -> None:
        connection.execute(
            """
            INSERT INTO formal_land_register_entries (
                book_key, land, district_code, sheet_number, district_name,
                land_register_office_land, land_register_office_code,
                land_register_office_name, completeness
            ) VALUES (?, '13', ?, ?, ?, '13', 'AG13',
                      'Amtsgericht Schwerin', 'sheet_district_office')
            """,
            (book_key, district_code, sheet_number, district_name),
        )
        connection.execute(
            """
            INSERT INTO feature_formal_land_register (
                source_db, kind, gml_id, book_key, resolution,
                resolution_depth, origin_book_key, relation_source_db
            ) VALUES (?, 'parcel', ?, ?, ?, ?, ?, 'fixture-relations.gml')
            """,
            (
                source_db,
                gml_id,
                book_key,
                resolution,
                resolution_depth,
                book_key if resolution == "inverse_an" else None,
            ),
        )

    @staticmethod
    def _reference(kind: str, gml_id: str) -> dict:
        return {
            "state": "niedersachsen",
            "kind": kind,
            "source_db": "fixture.gml",
            "gml_id": gml_id,
        }

    def _request(self, features: list[dict], *, expand: bool) -> dict:
        with patch.object(
            main,
            "feature_geometry_entries_for_state",
            return_value=(self.entry,),
        ):
            return main.api_v1_onoffice_selection_payload(
                access=self.access,
                payload={
                    "features": features,
                    "expand_intersections": expand,
                },
            )

    def _point_response(self) -> dict:
        with patch.object(
            main,
            "feature_db_entries_for_dataset",
            return_value=(self.entry,),
        ):
            return main.features_at_point_for_dataset(
                main.VIRTUAL_GERMANY_DATASET,
                0.3,
                0.3,
            )

    def test_building_expands_to_exactly_intersecting_parcel(self) -> None:
        response = self._request(
            [self._reference("building", "building-inside")],
            expand=True,
        )

        self.assertEqual(
            ["building-inside", "parcel-1"],
            [feature["gml_id"] for feature in response["features"]],
        )
        self.assertEqual("requested", response["features"][0]["selection_origin"])
        self.assertEqual(
            "intersection",
            response["features"][1]["selection_origin"],
        )
        self.assertEqual(
            [self._reference("building", "building-inside")],
            response["features"][1]["expanded_from"],
        )
        self.assertEqual(1, response["summary"]["requested_count"])
        self.assertEqual(1, response["summary"]["expanded_count"])
        self.assertEqual("Polygon", response["features"][0]["geometry"]["type"])
        self.assertEqual("Polygon", response["features"][1]["geometry"]["type"])

        addresses = response["structured_fields"]["addresses"]
        self.assertEqual(1, len(addresses))
        self.assertEqual("Lehmbuschfeld", addresses[0]["street"])
        self.assertEqual("14", addresses[0]["house_number"])
        self.assertEqual("30539", addresses[0]["postal_code"])
        self.assertEqual("Hannover", addresses[0]["city"])

        parcel = response["structured_fields"]["parcels"][0]
        self.assertEqual("Bemerode", parcel["gemarkung"])
        self.assertEqual("1", parcel["flur"])
        self.assertEqual("100/1", parcel["flurstueck"])
        self.assertEqual(321.5, parcel["official_area_m2"])
        self.assertTrue(
            response["structured_fields"]["official_area"]["complete"]
        )
        self.assertEqual(
            321.5,
            response["suggested_fields"][
                "openkataster_amtliche_flaeche_m2"
            ],
        )

    def test_parcel_expansion_uses_rtree_then_exact_intersects(self) -> None:
        response = self._request(
            [self._reference("parcel", "parcel-1")],
            expand=True,
        )

        feature_ids = [feature["gml_id"] for feature in response["features"]]
        self.assertEqual(["parcel-1", "building-inside"], feature_ids)
        self.assertNotIn("building-bbox-only", feature_ids)

    def test_requested_features_are_stably_deduplicated_from_expansion(self) -> None:
        response = self._request(
            [
                self._reference("parcel", "parcel-1"),
                self._reference("building", "building-inside"),
                self._reference("parcel", "parcel-1"),
            ],
            expand=True,
        )

        self.assertEqual(
            ["parcel-1", "building-inside"],
            [feature["gml_id"] for feature in response["features"]],
        )
        self.assertEqual(2, response["summary"]["requested_count"])
        self.assertEqual(0, response["summary"]["expanded_count"])
        self.assertTrue(
            all(
                feature["selection_origin"] == "requested"
                for feature in response["features"]
            )
        )

    def test_expansion_is_opt_in_and_incomplete_official_area_has_no_sum(
        self,
    ) -> None:
        response = self._request(
            [
                self._reference("parcel", "parcel-1"),
                self._reference("parcel", "parcel-without-area"),
            ],
            expand=False,
        )

        self.assertEqual(2, len(response["features"]))
        self.assertFalse(response["expand_intersections"])
        self.assertFalse(
            response["structured_fields"]["official_area"]["complete"]
        )
        self.assertIsNone(
            response["suggested_fields"][
                "openkataster_amtliche_flaeche_m2"
            ]
        )

    def test_legacy_database_without_land_register_tables_is_compatible(
        self,
    ) -> None:
        response = self._request(
            [self._reference("parcel", "parcel-1")],
            expand=False,
        )

        feature = response["features"][0]
        self.assertEqual([], feature["formal_land_register_entries"])
        self.assertNotIn("land_register_office_authority", feature)
        self.assertEqual(
            0,
            response["summary"]["formal_land_register_entry_count"],
        )

        point_parcel = self._point_response()["parcels"][0]
        self.assertEqual([], point_parcel["formal_land_register_entries"])
        self.assertNotIn("land_register_office_authority", point_parcel)
        self.assertNotIn("_onoffice_feature_db_path", point_parcel)

    def test_point_selection_exposes_formal_entries_from_trusted_identity(
        self,
    ) -> None:
        self._create_land_register_schema()
        connection = sqlite3.connect(self.database_path)
        self._insert_formal_land_register_entry(
            connection,
            book_key="book-trusted-point",
            sheet_number="17",
        )
        self._insert_formal_land_register_entry(
            connection,
            book_key="book-spoofed-point",
            sheet_number="999",
            source_db="spoofed-source.gml",
            gml_id="spoofed-parcel",
        )
        row = connection.execute(
            "SELECT properties_json FROM features WHERE id = 1"
        ).fetchone()
        spoofed_properties = json.loads(row[0])
        spoofed_properties["source_db"] = "spoofed-source.gml"
        spoofed_properties["gml_id"] = "spoofed-parcel"
        connection.execute(
            "UPDATE features SET properties_json = ? WHERE id = 1",
            (json.dumps(spoofed_properties),),
        )
        connection.commit()
        connection.close()

        parcel = self._point_response()["parcels"][0]

        self.assertEqual("fixture.gml", parcel["source_db"])
        self.assertEqual("parcel-1", parcel["gml_id"])
        self.assertEqual(
            ["book-trusted-point"],
            [
                entry["book_key"]
                for entry in parcel["formal_land_register_entries"]
            ],
        )
        self.assertNotIn("_onoffice_feature_db_path", parcel)

        preview = main.feature_preview_item(parcel, "parcel")
        self.assertIn(
            "formal_land_register_entries",
            preview["available_fields"],
        )
        self.assertNotIn("formal_land_register_entries", preview)

    def test_point_selection_keeps_all_950_formal_entries(self) -> None:
        self._create_land_register_schema()
        connection = sqlite3.connect(self.database_path)
        expected_count = 950
        for sheet_number in range(expected_count, 0, -1):
            self._insert_formal_land_register_entry(
                connection,
                book_key=f"point-book-{sheet_number}",
                sheet_number=str(sheet_number),
            )
        connection.commit()
        connection.close()

        entries = self._point_response()["parcels"][0][
            "formal_land_register_entries"
        ]

        self.assertEqual(expected_count, len(entries))
        self.assertEqual(
            [str(number) for number in range(1, expected_count + 1)],
            [entry["sheet_number"] for entry in entries],
        )

    def test_point_selection_relation_limit_fails_before_fetching_rows(
        self,
    ) -> None:
        with (
            patch.object(
                main,
                "_onoffice_formal_land_register_entry_count",
                return_value=main.ONOFFICE_LAND_REGISTER_MAX_RELATIONS + 1,
            ),
            patch.object(
                main,
                "_onoffice_formal_land_register_entries",
            ) as fetch_relations,
        ):
            with self.assertRaises(HTTPException) as raised:
                self._point_response()

        self.assertEqual(422, raised.exception.status_code)
        self.assertEqual(
            "land register relation limit exceeded",
            raised.exception.detail,
        )
        fetch_relations.assert_not_called()

    def test_all_formal_entries_are_complete_and_naturally_sorted(self) -> None:
        self._create_land_register_schema()
        connection = sqlite3.connect(self.database_path)
        self._insert_formal_land_register_entry(
            connection,
            book_key="book-10",
            sheet_number="10",
            resolution="inverse_an",
            resolution_depth=1,
        )
        self._insert_formal_land_register_entry(
            connection,
            book_key="book-2",
            sheet_number="2",
        )
        self._insert_formal_land_register_entry(
            connection,
            book_key="book-other-source",
            sheet_number="1",
            source_db="other-source.gml",
        )
        self._insert_formal_land_register_entry(
            connection,
            book_key="book-other-feature",
            sheet_number="3",
            gml_id="other-parcel",
        )
        connection.commit()
        connection.close()

        response = self._request(
            [self._reference("parcel", "parcel-1")],
            expand=False,
        )

        entries = response["features"][0]["formal_land_register_entries"]
        self.assertEqual(["2", "10"], [entry["sheet_number"] for entry in entries])
        self.assertEqual(
            ["book-2", "book-10"],
            [entry["book_key"] for entry in entries],
        )
        self.assertEqual("Zippendorf", entries[0]["district_name"])
        self.assertEqual("Amtsgericht Schwerin", entries[0]["land_register_office_name"])
        self.assertEqual("sheet_district_office", entries[0]["completeness"])
        self.assertEqual("inverse_an", entries[1]["resolution"])
        self.assertEqual(1, entries[1]["resolution_depth"])
        self.assertEqual(2, response["summary"]["formal_land_register_entry_count"])
        self.assertEqual(1, response["summary"]["formal_land_register_parcel_count"])
        self.assertEqual(
            1,
            response["summary"]["formal_land_register_multiple_parcel_count"],
        )

    def test_standard_schema_uses_indexed_identity_not_properties_json(
        self,
    ) -> None:
        self._create_land_register_schema()
        connection = sqlite3.connect(self.database_path)
        self._insert_formal_land_register_entry(
            connection,
            book_key="book-trusted",
            sheet_number="17",
        )
        self._insert_formal_land_register_entry(
            connection,
            book_key="book-spoofed",
            sheet_number="999",
            source_db="spoofed-source.gml",
            gml_id="spoofed-parcel",
        )
        row = connection.execute(
            "SELECT properties_json FROM features WHERE id = 1"
        ).fetchone()
        spoofed_properties = json.loads(row[0])
        spoofed_properties["source_db"] = "spoofed-source.gml"
        spoofed_properties["gml_id"] = "spoofed-parcel"
        connection.execute(
            "UPDATE features SET properties_json = ? WHERE id = 1",
            (json.dumps(spoofed_properties),),
        )
        connection.commit()
        connection.close()

        response = self._request(
            [self._reference("parcel", "parcel-1")],
            expand=False,
        )

        feature = response["features"][0]
        self.assertEqual("fixture.gml", feature["source_db"])
        self.assertEqual("parcel-1", feature["gml_id"])
        self.assertEqual("fixture.gml", feature["properties"]["source_db"])
        self.assertEqual("parcel-1", feature["properties"]["gml_id"])
        self.assertEqual(
            ["book-trusted"],
            [
                entry["book_key"]
                for entry in feature["formal_land_register_entries"]
            ],
        )

    def test_formal_entry_array_is_never_truncated(self) -> None:
        self._create_land_register_schema()
        connection = sqlite3.connect(self.database_path)
        # The complete Mecklenburg-Vorpommern pilot contains a real parcel
        # with 950 formal sheet relations.  Keep the regression boundary at
        # that observed statewide maximum rather than the earlier one-shard
        # sample maximum of 465.
        expected_count = 950
        for sheet_number in range(expected_count, 0, -1):
            self._insert_formal_land_register_entry(
                connection,
                book_key=f"book-{sheet_number}",
                sheet_number=str(sheet_number),
            )
        connection.commit()
        connection.close()

        response = self._request(
            [self._reference("parcel", "parcel-1")],
            expand=False,
        )

        entries = response["features"][0]["formal_land_register_entries"]
        self.assertEqual(expected_count, len(entries))
        self.assertEqual(
            [str(number) for number in range(1, expected_count + 1)],
            [entry["sheet_number"] for entry in entries],
        )
        self.assertEqual(
            expected_count,
            response["summary"]["formal_land_register_entry_count"],
        )

    def test_request_wide_relation_limit_fails_before_fetching_rows(
        self,
    ) -> None:
        with (
            patch.object(
                main,
                "_onoffice_formal_land_register_entry_count",
                side_effect=(6_000, 4_001),
            ) as count_relations,
            patch.object(
                main,
                "_onoffice_formal_land_register_entries",
            ) as fetch_relations,
        ):
            with self.assertRaises(HTTPException) as raised:
                self._request(
                    [
                        self._reference("parcel", "parcel-1"),
                        self._reference("parcel", "parcel-without-area"),
                    ],
                    expand=False,
                )

        self.assertEqual(422, raised.exception.status_code)
        self.assertEqual(
            "land register relation limit exceeded",
            raised.exception.detail,
        )
        self.assertEqual(2, count_relations.call_count)
        fetch_relations.assert_not_called()

    def test_duplicate_expansion_loads_register_relations_once(self) -> None:
        self._create_land_register_schema()
        connection = sqlite3.connect(self.database_path)
        self._insert_formal_land_register_entry(
            connection,
            book_key="book-once",
            sheet_number="1",
        )
        connection.commit()
        connection.close()

        original_lookup = main._onoffice_formal_land_register_entries
        with patch.object(
            main,
            "_onoffice_formal_land_register_entries",
            wraps=original_lookup,
        ) as lookup:
            response = self._request(
                [
                    self._reference("parcel", "parcel-1"),
                    self._reference("building", "building-inside"),
                ],
                expand=True,
            )

        self.assertEqual(
            ["parcel-1", "building-inside"],
            [feature["gml_id"] for feature in response["features"]],
        )
        self.assertEqual(1, lookup.call_count)
        parcel = next(
            feature
            for feature in response["features"]
            if feature["kind"] == "parcel"
        )
        self.assertEqual(
            ["book-once"],
            [
                entry["book_key"]
                for entry in parcel["formal_land_register_entries"]
            ],
        )

    def test_ambiguous_authority_stays_separate_and_returns_every_office(
        self,
    ) -> None:
        self._create_land_register_schema()
        connection = sqlite3.connect(self.database_path)
        connection.executemany(
            """
            INSERT INTO formal_land_register_offices (
                land, office_code, office_name, completeness
            ) VALUES ('13', ?, ?, 'named')
            """,
            (
                ("AG01", "Amtsgericht Schwerin"),
                ("AG02", "Amtsgericht Ludwigslust"),
            ),
        )
        connection.executemany(
            """
            INSERT INTO land_register_office_authority_sources (
                id, relation_source_db, method, precedence,
                source_object_key, office_land, office_code
            ) VALUES (?, 'fixture-relations.gml', 'parcel_zustaendige_stelle',
                      10, ?, '13', ?)
            """,
            (
                (1, "source-object-1", "AG01"),
                (2, "source-object-2", "AG02"),
            ),
        )
        connection.executemany(
            """
            INSERT INTO feature_land_register_office_authority (
                feature_id, source_id
            ) VALUES (1, ?)
            """,
            ((1,), (2,)),
        )
        connection.commit()
        connection.close()

        response = self._request(
            [self._reference("parcel", "parcel-1")],
            expand=False,
        )

        feature = response["features"][0]
        self.assertEqual([], feature["formal_land_register_entries"])
        authority = feature["land_register_office_authority"]
        self.assertEqual("ambiguous", authority["status"])
        self.assertEqual(2, authority["office_count"])
        self.assertIsNone(authority["office_code"])
        self.assertIsNone(authority["office_name"])
        self.assertEqual(
            ["AG02", "AG01"],
            [office["code"] for office in authority["offices"]],
        )
        self.assertTrue(
            all("district_name" not in office for office in authority["offices"])
        )
        self.assertTrue(
            all("sheet_number" not in office for office in authority["offices"])
        )
        self.assertEqual(
            1,
            response["summary"]["land_register_office_authority_parcel_count"],
        )

    def test_expanded_selection_never_silently_exceeds_existing_limit(
        self,
    ) -> None:
        requested = [
            self._reference("building", f"building-{index}")
            for index in range(main.ONOFFICE_SELECTION_MAX_FEATURES)
        ]

        def fake_resolver(reference: dict, *, expand_intersections: bool):
            detail = {
                **reference,
                "label": reference["gml_id"],
                "subtitle": "",
                "center": [0, 0],
                "bbox": [0, 0, 1, 1],
                "feature": {
                    "source_db": reference["source_db"],
                    "gml_id": reference["gml_id"],
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [],
                    },
                },
            }
            intersections = []
            if reference["gml_id"] == "building-0":
                intersections.append(
                    {
                        **self._reference("parcel", "parcel-expanded"),
                        "label": "Flurstück",
                        "subtitle": "",
                        "center": [0, 0],
                        "bbox": [0, 0, 1, 1],
                        "feature": {
                            "source_db": "fixture.gml",
                            "gml_id": "parcel-expanded",
                            "geometry": {
                                "type": "Polygon",
                                "coordinates": [],
                            },
                        },
                    }
                )
            return detail, intersections, []

        with patch.object(
            main,
            "resolve_onoffice_selection_feature",
            side_effect=fake_resolver,
        ):
            with self.assertRaises(HTTPException) as raised:
                main.api_v1_onoffice_selection_payload(
                    access=self.access,
                    payload={
                        "features": requested,
                        "expand_intersections": True,
                    },
                )
        self.assertEqual(422, raised.exception.status_code)
        self.assertEqual(
            "expanded selection is too large",
            raised.exception.detail,
        )

    def test_contract_keeps_feature_read_scope_and_boolean_flag(self) -> None:
        route = next(
            route
            for route in main.app.routes
            if getattr(route, "path", "")
            == "/api/v1/integrations/onoffice/selection-payload"
        )
        scope_dependencies = [
            dependency.call
            for dependency in route.dependant.dependencies
            if isinstance(dependency.call, main.RequireScopes)
        ]
        self.assertEqual(1, len(scope_dependencies))
        self.assertEqual({"feature:read"}, scope_dependencies[0].required)

        with self.assertRaises(HTTPException) as raised:
            main.api_v1_onoffice_selection_payload(
                access=self.access,
                payload={
                    "features": [],
                    "expand_intersections": "true",
                },
            )
        self.assertEqual(422, raised.exception.status_code)


if __name__ == "__main__":
    unittest.main()
