from __future__ import annotations

import asyncio
import gzip
import os
import sqlite3
import tempfile
import time
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException, Response
from shapely import wkb
from shapely.geometry import Point, Polygon

from openkataster_tiles import main


# A valid MVT containing one empty layer named ``gst``.
VALID_MVT = bytes.fromhex("1a0a78020a03677374288020")


class _FakeResponse:
    def __init__(
        self,
        data: bytes,
        *,
        content_type: str = "",
        content_encoding: str = "",
        status: int = 200,
    ) -> None:
        self.data = data
        self.status = status
        self.headers = {
            "Content-Type": content_type,
            "Content-Encoding": content_encoding,
        }

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, limit: int = -1) -> bytes:
        return self.data if limit < 0 else self.data[:limit]


class BevVectorProxyTests(unittest.TestCase):
    def _austria_search_fixture(self, directory: Path) -> main.FeatureDbEntry:
        path = directory / "oesterreich.search.sqlite"
        connection = sqlite3.connect(path)
        connection.executescript(
            """
            CREATE TABLE address_lookup (
                id INTEGER PRIMARY KEY,
                feature_kind TEXT NOT NULL,
                source_db TEXT NOT NULL,
                gml_id TEXT NOT NULL,
                street_norm TEXT NOT NULL,
                street_label TEXT NOT NULL,
                house_number_norm TEXT NOT NULL,
                house_number_label TEXT NOT NULL,
                city_norm TEXT NOT NULL,
                city_label TEXT NOT NULL,
                post_code TEXT NOT NULL,
                label TEXT NOT NULL,
                derivation TEXT NOT NULL,
                lon REAL,
                lat REAL,
                min_lon REAL NOT NULL,
                max_lon REAL NOT NULL,
                min_lat REAL NOT NULL,
                max_lat REAL NOT NULL
            );
            CREATE INDEX idx_address_exact
                ON address_lookup(city_norm, street_norm, house_number_norm);
            CREATE INDEX idx_address_no_city
                ON address_lookup(street_norm, house_number_norm);

            CREATE TABLE street_lookup (
                id INTEGER PRIMARY KEY,
                street_norm TEXT NOT NULL,
                street_label TEXT NOT NULL,
                city_norm TEXT NOT NULL,
                city_label TEXT NOT NULL,
                post_code TEXT NOT NULL,
                label TEXT NOT NULL,
                address_count INTEGER NOT NULL,
                feature_count INTEGER NOT NULL,
                lon REAL,
                lat REAL,
                min_lon REAL NOT NULL,
                max_lon REAL NOT NULL,
                min_lat REAL NOT NULL,
                max_lat REAL NOT NULL
            );
            CREATE INDEX idx_street_exact
                ON street_lookup(city_norm, street_norm);
            CREATE INDEX idx_street_no_city
                ON street_lookup(street_norm);

            CREATE TABLE parcel_lookup (
                id INTEGER PRIMARY KEY,
                source_db TEXT NOT NULL,
                gml_id TEXT NOT NULL,
                gemarkung_norm TEXT NOT NULL,
                gemarkung_label TEXT NOT NULL,
                gemarkungsnummer TEXT NOT NULL,
                flur_norm TEXT NOT NULL,
                flur_label TEXT NOT NULL,
                flurstueck_norm TEXT NOT NULL,
                flurstueck_label TEXT NOT NULL,
                zaehler TEXT NOT NULL,
                nenner TEXT NOT NULL,
                amtliche_flaeche_m2 REAL,
                lon REAL,
                lat REAL,
                min_lon REAL NOT NULL,
                max_lon REAL NOT NULL,
                min_lat REAL NOT NULL,
                max_lat REAL NOT NULL
            );
            CREATE INDEX idx_parcel_exact
                ON parcel_lookup(gemarkung_norm, flur_norm, flurstueck_norm);
            CREATE INDEX idx_parcel_without_flur
                ON parcel_lookup(gemarkung_norm, flurstueck_norm, flur_norm);
            CREATE INDEX idx_parcel_gemarkung
                ON parcel_lookup(gemarkung_norm);
            """
        )
        connection.execute(
            """
            INSERT INTO address_lookup (
                feature_kind, source_db, gml_id, street_norm, street_label,
                house_number_norm, house_number_label, city_norm, city_label,
                post_code, label, derivation, lon, lat,
                min_lon, max_lon, min_lat, max_lat
            ) VALUES (
                'address', 'austria-bev', 'address:6833147',
                'eyzinggasse', 'Eyzinggasse', '27', '27',
                'wien', 'Wien', '1110', 'Eyzinggasse 27, 1110 Wien',
                'official-address-point', 16.420647, 48.182460,
                16.420647, 16.420647, 48.182460, 48.182460
            )
            """
        )
        connection.execute(
            """
            INSERT INTO street_lookup (
                street_norm, street_label, city_norm, city_label, post_code,
                label, address_count, feature_count, lon, lat,
                min_lon, max_lon, min_lat, max_lat
            ) VALUES (
                'eyzinggasse', 'Eyzinggasse', 'wien', 'Wien', '1110',
                'Eyzinggasse, Wien', 1, 1, 16.420647, 48.182460,
                16.420647, 16.420647, 48.182460, 48.182460
            )
            """
        )
        connection.execute(
            """
            INSERT INTO parcel_lookup (
                source_db, gml_id, gemarkung_norm, gemarkung_label,
                gemarkungsnummer, flur_norm, flur_label, flurstueck_norm,
                flurstueck_label, zaehler, nenner, amtliche_flaeche_m2,
                lon, lat, min_lon, max_lon, min_lat, max_lat
            ) VALUES (
                'austria-bev', 'AT.BEV.GST.01107..1023/1',
                'simmering', 'Simmering (01107)', '01107', '', '',
                '.1023/1', '.1023/1', '.1023', '1', NULL,
                16.420599, 48.182759,
                16.420497, 16.420641, 48.182714, 48.182866
            )
            """
        )
        connection.commit()
        connection.close()
        return main.FeatureDbEntry(name="oesterreich", path=path)

    def _search_austria(self, query: str) -> dict:
        with tempfile.TemporaryDirectory() as temp_dir:
            entry = self._austria_search_fixture(Path(temp_dir))
            entries = (entry,)
            signature = (
                (
                    entry.name,
                    str(entry.path),
                    *main.sqlite_file_signature(entry.path),
                ),
            )
            for cached_function in (
                main.search_street_suggestions_cached,
                main.search_gemarkung_suggestions_cached,
                main.search_contextual_parcel_suggestions_cached,
            ):
                cached_function.cache_clear()
            try:
                with (
                    patch.object(
                        main,
                        "search_suggestion_states_for_dataset",
                        return_value={"oesterreich"},
                    ),
                    patch.object(
                        main,
                        "active_bucket_state_keys",
                        return_value=("oesterreich",),
                    ),
                    patch.object(
                        main,
                        "search_db_entries_for_states",
                        return_value=entries,
                    ),
                    patch.object(
                        main,
                        "search_db_signature_for_states",
                        return_value=signature,
                    ),
                    patch.object(main, "openplz_signature", return_value=(0, 0)),
                    patch.object(main, "states_for_place_context", return_value=tuple()),
                    patch.object(main, "search_poi_suggestions", return_value=[]),
                ):
                    return main.search_unified_suggestions_for_dataset(
                        "oesterreich",
                        query,
                        8,
                    )
            finally:
                for cached_function in (
                    main.search_street_suggestions_cached,
                    main.search_gemarkung_suggestions_cached,
                    main.search_contextual_parcel_suggestions_cached,
                ):
                    cached_function.cache_clear()
                cached = main._SEARCH_DB_CONNECTIONS.pop(str(entry.path), None)
                if cached:
                    cached[1].close()

    def test_austrian_address_parser_recognizes_four_digit_postcodes(self) -> None:
        parsed = main.parse_unified_address_query(
            "Stephansplatz 1, 1010 Wien",
            {"oesterreich"},
        )

        self.assertEqual(parsed["postcode"], "1010")
        self.assertNotIn("1010", parsed["street"])
        german = main.parse_unified_address_query(
            "Musterstraße 1, 1234 Musterstadt",
            {"niedersachsen"},
        )
        self.assertEqual(german["postcode"], "")

    def test_austrian_address_search_uses_local_four_digit_postcode(self) -> None:
        result = self._search_austria("Eyzinggasse 27, 1110 Wien")

        self.assertEqual(result["count"], 1)
        address = result["results"][0]
        self.assertEqual(address["result_type"], "address")
        self.assertEqual(address["label"], "Eyzinggasse 27, 1110 Wien")
        self.assertEqual(address["address"]["country"], "Österreich")
        self.assertEqual(address["address"]["address_id"], "6833147")
        self.assertEqual(address["feature"]["address_id"], "6833147")

    def test_address_selection_uses_trusted_building_relation_outside_footprint(
        self,
    ) -> None:
        parcel = Polygon(
            (
                (16.4200, 48.1820),
                (16.4210, 48.1820),
                (16.4210, 48.1830),
                (16.4200, 48.1830),
                (16.4200, 48.1820),
            )
        )
        trusted_building = Polygon(
            (
                (16.42070, 48.18270),
                (16.42090, 48.18270),
                (16.42090, 48.18290),
                (16.42070, 48.18290),
                (16.42070, 48.18270),
            )
        )
        nearer_unrelated_building = Polygon(
            (
                (16.42020, 48.18220),
                (16.42030, 48.18220),
                (16.42030, 48.18230),
                (16.42020, 48.18230),
                (16.42020, 48.18220),
            )
        )
        address_point = Point(16.42010, 48.18210)
        address = {
            "street": "Eyzinggasse",
            "house_number": "27",
            "label": "Eyzinggasse 27, 1110 Wien",
            "address_id": "6833147",
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "oesterreich.features.sqlite"
            con = sqlite3.connect(path)
            con.executescript(
                """
                CREATE TABLE features (
                    id INTEGER PRIMARY KEY,
                    state_key TEXT,
                    kind TEXT NOT NULL,
                    source_db TEXT NOT NULL,
                    gml_id TEXT NOT NULL,
                    properties_json TEXT NOT NULL,
                    geometry_wkb BLOB NOT NULL,
                    center_lon REAL,
                    center_lat REAL,
                    min_lon REAL NOT NULL,
                    max_lon REAL NOT NULL,
                    min_lat REAL NOT NULL,
                    max_lat REAL NOT NULL
                );
                CREATE VIRTUAL TABLE feature_index USING rtree(
                    id, min_lon, max_lon, min_lat, max_lat
                );
                CREATE TABLE address_points (
                    id INTEGER PRIMARY KEY,
                    source_db TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    properties_json TEXT NOT NULL,
                    geometry_wkb BLOB NOT NULL,
                    lon REAL NOT NULL,
                    lat REAL NOT NULL
                );
                CREATE VIRTUAL TABLE address_index USING rtree(
                    id, min_lon, max_lon, min_lat, max_lat
                );
                CREATE TABLE feature_addresses (
                    id INTEGER PRIMARY KEY,
                    source_db TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    gml_id TEXT NOT NULL,
                    properties_json TEXT NOT NULL
                );
                CREATE UNIQUE INDEX idx_feature_addresses_unique
                    ON feature_addresses(source_db, kind, gml_id, properties_json);
                """
            )
            rows = (
                (
                    1,
                    "parcel",
                    "AT.BEV.GST.01107..1024",
                    {"flurstueck": ".1024", "gemarkung": "Simmering"},
                    parcel,
                ),
                (
                    2,
                    "building",
                    "AT.BEV.BLD.TRUSTED",
                    {"gebaeudefunktion_text": "Gebäude"},
                    trusted_building,
                ),
                (
                    3,
                    "building",
                    "AT.BEV.BLD.NEARER",
                    {"gebaeudefunktion_text": "Nebengebäude"},
                    nearer_unrelated_building,
                ),
            )
            for feature_id, kind, gml_id, properties, geometry in rows:
                min_lon, min_lat, max_lon, max_lat = geometry.bounds
                center = geometry.representative_point()
                con.execute(
                    """
                    INSERT INTO features VALUES (
                        ?, 'oesterreich', ?, 'austria-bev', ?, ?, ?, ?, ?,
                        ?, ?, ?, ?
                    )
                    """,
                    (
                        feature_id,
                        kind,
                        gml_id,
                        main.json.dumps(properties),
                        wkb.dumps(geometry),
                        center.x,
                        center.y,
                        min_lon,
                        max_lon,
                        min_lat,
                        max_lat,
                    ),
                )
                con.execute(
                    "INSERT INTO feature_index VALUES (?, ?, ?, ?, ?)",
                    (feature_id, min_lon, max_lon, min_lat, max_lat),
                )
            con.execute(
                """
                INSERT INTO address_points VALUES (
                    1, 'austria-bev', 'address:6833147', ?, ?, ?, ?
                )
                """,
                (
                    main.json.dumps(address),
                    wkb.dumps(address_point),
                    address_point.x,
                    address_point.y,
                ),
            )
            con.execute(
                "INSERT INTO address_index VALUES (1, ?, ?, ?, ?)",
                (
                    address_point.x,
                    address_point.x,
                    address_point.y,
                    address_point.y,
                ),
            )
            con.execute(
                """
                INSERT INTO feature_addresses(
                    source_db, kind, gml_id, properties_json
                ) VALUES ('austria-bev', 'building', 'AT.BEV.BLD.TRUSTED', ?)
                """,
                (main.json.dumps(address),),
            )
            con.commit()
            con.close()

            without_hint = main.query_features_in_index(
                path,
                address_point.x,
                address_point.y,
            )
            with patch.object(
                main,
                "enrich_addresses_with_postcode",
                side_effect=lambda addresses, *_args, **_kwargs: addresses,
            ):
                with_hint = main.query_features_in_index(
                    path,
                    address_point.x,
                    address_point.y,
                    address_hint=address,
                )

            con = sqlite3.connect(path)
            con.execute("DELETE FROM feature_addresses")
            con.commit()
            con.close()
            with patch.object(
                main,
                "enrich_addresses_with_postcode",
                side_effect=lambda addresses, *_args, **_kwargs: addresses,
            ):
                without_building_relation = main.query_features_in_index(
                    path,
                    address_point.x,
                    address_point.y,
                    address_hint=address,
                )
            with (
                patch.object(
                    main,
                    "enrich_addresses_with_postcode",
                    side_effect=lambda addresses, *_args, **_kwargs: addresses,
                ),
                patch.object(
                    main,
                    "building_label_points_for_parcel",
                    return_value=[trusted_building.representative_point()],
                ),
            ):
                with_building_label = main.query_features_in_index(
                    path,
                    address_point.x,
                    address_point.y,
                    address_hint=address,
                )

        self.assertEqual(len(without_hint[0]), 1)
        self.assertEqual(without_hint[1], [])
        self.assertEqual(
            [building["gml_id"] for building in with_hint[1]],
            ["AT.BEV.BLD.TRUSTED"],
        )
        self.assertEqual(len(without_building_relation[0]), 1)
        self.assertEqual(without_building_relation[1], [])
        self.assertEqual(
            [building["gml_id"] for building in with_building_label[1]],
            ["AT.BEV.BLD.TRUSTED"],
        )

    def test_austrian_parcel_search_preserves_leading_dot(self) -> None:
        result = self._search_austria(
            "Grundstück .1023/1 in Katastralgemeinde Simmering"
        )

        self.assertEqual(result["count"], 1)
        parcel = result["results"][0]
        self.assertEqual(parcel["kind"], "parcel")
        self.assertEqual(parcel["cadastre"]["flurstueck"], ".1023/1")
        self.assertEqual(
            parcel["label"],
            "Grundstück .1023/1, Simmering (01107)",
        )
        self.assertIn("Katastralgemeinde", parcel["subtitle"])

    def test_valid_official_tile_without_content_type_is_cached(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch.object(main, "BEV_VECTOR_CACHE_DIR", Path(temp_dir)),
                patch.object(
                    main.urllib.request,
                    "urlopen",
                    return_value=_FakeResponse(VALID_MVT),
                ) as urlopen,
            ):
                miss = main._bev_vector_tile("kataster", 16, 35748, 22724)
                hit = main._bev_vector_tile("kataster", 16, 35748, 22724)

        self.assertEqual(miss.status_code, 200)
        self.assertEqual(miss.body, VALID_MVT)
        self.assertEqual(miss.headers["x-openkataster-cache"], "MISS")
        self.assertEqual(hit.headers["x-openkataster-cache"], "HIT")
        self.assertEqual(urlopen.call_count, 1)

    def test_layer_zoom_limits_are_enforced_before_network_access(self) -> None:
        with patch.object(main.urllib.request, "urlopen") as urlopen:
            with self.assertRaises(HTTPException) as below_minimum:
                main._bev_vector_tile("symbole", 12, 2200, 1400)
            with self.assertRaises(HTTPException) as above_maximum:
                main._bev_vector_tile("kataster", 17, 71500, 45400)

        self.assertEqual(below_minimum.exception.status_code, 404)
        self.assertEqual(above_maximum.exception.status_code, 404)
        urlopen.assert_not_called()

    def test_public_v1_tile_route_never_falls_back_to_germany_for_austria(self) -> None:
        expected = Response(content=VALID_MVT, media_type="application/vnd.mapbox-vector-tile")
        with (
            patch.object(main, "active_bucket_state_keys", return_value=("oesterreich",)),
            patch.object(main, "_bev_vector_tile", return_value=expected) as bev_tile,
        ):
            response = main.api_v1_tile_mvt("Österreich", 16, 35748, 22724)

        self.assertIs(response, expected)
        bev_tile.assert_called_once_with("kataster", 16, 35748, 22724)

    def test_edge_safe_bev_route_and_tilejson_share_api_v1_namespace(self) -> None:
        route_paths = {
            getattr(route, "path", "")
            for route in main.app.routes
        }
        self.assertIn(
            "/api/v1/bev/tiles/{layer}/{z:int}/{x:int}/{y:int}.pbf",
            route_paths,
        )

        request = object()
        with (
            patch.object(main, "normalize_state_key", return_value="oesterreich"),
            patch.object(
                main,
                "public_base_url",
                return_value="https://tiles.openkataster.de",
            ),
        ):
            payload = asyncio.run(main.api_v1_tilejson("oesterreich", request))

        self.assertEqual(
            payload["tiles"],
            [
                "https://tiles.openkataster.de/api/v1/bev/tiles/kataster/"
                "{z}/{x}/{y}.pbf"
            ],
        )
        catalog = main._austria_region_row()
        source_templates = {
            source["tile_template"]
            for source in catalog["rendering"]["cadastre_vector"]["sources"]
        }
        self.assertEqual(
            source_templates,
            {
                "/api/v1/bev/tiles/kataster/{z}/{x}/{y}.pbf",
                "/api/v1/bev/tiles/symbole/{z}/{x}/{y}.pbf",
            },
        )

    def test_text_error_document_is_never_cached_as_vector_tile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir)
            with (
                patch.object(main, "BEV_VECTOR_CACHE_DIR", cache_dir),
                patch.object(
                    main.urllib.request,
                    "urlopen",
                    return_value=_FakeResponse(
                        b"<html>temporary upstream error</html>",
                        content_type="text/html",
                    ),
                ),
            ):
                with self.assertRaises(HTTPException) as raised:
                    main._bev_vector_tile("kataster", 16, 35748, 22724)

        self.assertEqual(raised.exception.status_code, 502)
        self.assertFalse(any(cache_dir.rglob("*.pbf")))

    def test_gzip_expansion_is_bounded(self) -> None:
        compressed = gzip.compress(b"A" * 129)
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir)
            with (
                patch.object(main, "BEV_VECTOR_CACHE_DIR", cache_dir),
                patch.object(main, "BEV_VECTOR_MAX_UNCOMPRESSED_BYTES", 128),
                patch.object(
                    main.urllib.request,
                    "urlopen",
                    return_value=_FakeResponse(
                        compressed,
                        content_type="application/x-protobuf",
                        content_encoding="gzip",
                    ),
                ),
            ):
                with self.assertRaises(HTTPException) as raised:
                    main._bev_vector_tile("kataster", 16, 35748, 22724)

        self.assertEqual(raised.exception.status_code, 502)
        self.assertFalse(any(cache_dir.rglob("*.pbf")))

    def test_recent_stale_tile_is_used_during_upstream_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch.object(main, "BEV_VECTOR_CACHE_DIR", Path(temp_dir)),
                patch.object(main, "BEV_VECTOR_CACHE_TTL_SECONDS", 1),
                patch.object(main, "BEV_VECTOR_CACHE_STALE_SECONDS", 3600),
            ):
                cache_path = main._bev_vector_cache_path("kataster", 16, 35748, 22724)
                cache_path.parent.mkdir(parents=True)
                cache_path.write_bytes(VALID_MVT)
                stale_timestamp = time.time() - 2
                os.utime(cache_path, (stale_timestamp, stale_timestamp))
                with patch.object(
                    main.urllib.request,
                    "urlopen",
                    side_effect=urllib.error.URLError("offline"),
                ):
                    response = main._bev_vector_tile("kataster", 16, 35748, 22724)

        self.assertEqual(response.headers["x-openkataster-cache"], "STALE")
        self.assertIn("stale", response.headers["warning"].lower())


if __name__ == "__main__":
    unittest.main()
