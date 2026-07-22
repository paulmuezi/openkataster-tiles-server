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
