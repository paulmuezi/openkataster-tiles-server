from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from openkataster_tiles import main


LIVE_REFERENCES_AVAILABLE = (
    main.OPENPLZ_DB.exists()
    and (main.DATA_DIR / "sachsen.search.sqlite").exists()
    and (main.DATA_DIR / "baden-wurttemberg.search.sqlite").exists()
    and (main.DATA_DIR / "nordrhein-westfalen.search.sqlite").exists()
)

ADDRESS_FALLBACK_REFERENCES_AVAILABLE = (
    main.OPENPLZ_DB.exists()
    and (main.DATA_DIR / "baden-wurttemberg.search.sqlite").exists()
    and (main.DATA_DIR / "berlin.search.sqlite").exists()
    and (main.DATA_DIR / "bremen.search.sqlite").exists()
    and (main.DATA_DIR / "rheinland-pfalz.search.sqlite").exists()
)

GEMARKUNG_REFERENCES_AVAILABLE = all(
    (main.DATA_DIR / f"{state}.search.sqlite").exists()
    for state in (
        "baden-wurttemberg",
        "bremen",
        "rheinland-pfalz",
        "schleswig-holstein",
    )
)

CENTRAL_ADDRESS_REFERENCES_AVAILABLE = (
    main.OPENPLZ_DB.exists()
    and all(
        (main.DATA_DIR / f"{state}.search.sqlite").exists()
        for state in (
            "baden-wurttemberg",
            "berlin",
            "brandenburg",
            "bremen",
            "mecklenburg-vorpommern",
            "niedersachsen",
            "nordrhein-westfalen",
            "rheinland-pfalz",
            "saarland",
            "schleswig-holstein",
            "thueringen",
        )
    )
)


class SearchRuntimeFixTests(unittest.TestCase):
    def gemarkung_suggestions_from_fixture(
        self,
        query: str,
        limit: int,
        rows_by_state: dict[str, list[tuple[str, str, str, int]]],
    ) -> tuple[dict, ...]:
        with TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            entries = []
            for state, fixture_rows in rows_by_state.items():
                path = directory / f"{state}.search.sqlite"
                connection = sqlite3.connect(path)
                connection.execute(
                    """
                    CREATE TABLE parcel_lookup (
                        gemarkung_norm TEXT NOT NULL,
                        gemarkung_label TEXT NOT NULL,
                        gemarkungsnummer TEXT NOT NULL
                    )
                    """
                )
                for gemarkung_norm, label, number, parcel_count in fixture_rows:
                    connection.executemany(
                        "INSERT INTO parcel_lookup VALUES (?, ?, ?)",
                        [(gemarkung_norm, label, number)] * parcel_count,
                    )
                connection.commit()
                connection.close()
                entries.append(main.FeatureDbEntry(name=state, path=path))
            entries.sort(key=lambda entry: entry.name)
            signature = tuple(
                (entry.name, str(entry.path), *main.sqlite_file_signature(entry.path))
                for entry in entries
            )
            main.search_gemarkung_suggestions_cached.cache_clear()
            try:
                with patch.object(main, "search_db_entries_for_states", return_value=tuple(entries)):
                    return main.search_gemarkung_suggestions_cached(
                        query,
                        limit,
                        tuple(entry.name for entry in entries),
                        signature,
                    )
            finally:
                main.search_gemarkung_suggestions_cached.cache_clear()
                for entry in entries:
                    cached = main._SEARCH_DB_CONNECTIONS.pop(str(entry.path), None)
                    if cached:
                        cached[1].close()

    def test_search_db_errors_are_structured_503s(self) -> None:
        with patch.object(
            main,
            "search_db_connection",
            side_effect=sqlite3.OperationalError("forced test failure"),
        ):
            with self.assertRaises(main.HTTPException) as raised:
                main.search_db_fetchall(Path("/tmp/test.search.sqlite"), "SELECT 1")
        error = raised.exception
        self.assertEqual(503, error.status_code)
        self.assertEqual("search_database_unavailable", error.detail["code"])
        self.assertTrue(error.detail["request_id"].startswith("search-"))
        self.assertEqual(error.detail["request_id"], error.headers["X-Request-ID"])

    def test_search_db_errors_do_not_fill_response_cache(self) -> None:
        saved_cache = dict(main._SEARCH_RESPONSE_CACHE)
        main._SEARCH_RESPONSE_CACHE.clear()
        error = main.HTTPException(
            status_code=503,
            detail={"code": "search_database_unavailable", "request_id": "search-test"},
        )
        try:
            with patch.object(
                main,
                "search_direct_geocoder_for_dataset",
                side_effect=error,
            ):
                with self.assertRaises(main.HTTPException):
                    main.cached_search_features_for_dataset(
                        "deutschland",
                        "Gelnhaarer Strasse 6 Kefenrod",
                        12,
                        "address",
                        state="hessen",
                    )
            self.assertEqual({}, main._SEARCH_RESPONSE_CACHE)
        finally:
            main._SEARCH_RESPONSE_CACHE.clear()
            main._SEARCH_RESPONSE_CACHE.update(saved_cache)

    def test_parcel_api_requires_only_gemarkung_and_flurstueck(self) -> None:
        parameters = main.app.openapi()["paths"]["/api/v1/search/parcel"]["get"]["parameters"]
        query_requirements = {
            parameter["name"]: parameter["required"]
            for parameter in parameters
            if parameter["in"] == "query"
        }
        self.assertTrue(query_requirements["gemarkung"])
        self.assertTrue(query_requirements["flurstueck"])
        self.assertFalse(query_requirements["flur"])

    def test_parcel_number_normalization_preserves_slash(self) -> None:
        self.assertEqual("1/11", main.fast_parcel_number_norm(" 1 / 11 "))
        self.assertNotEqual(
            main.fast_parcel_number_norm("1/11"),
            main.fast_parcel_number_norm("11/1"),
        )

    def test_parcel_label_omits_empty_flur(self) -> None:
        row = {
            "lon": 9.0,
            "lat": 48.0,
            "flur_label": "",
            "flurstueck_label": "1066",
            "gemarkung_label": "Hofen (0976)",
            "source_db": "alkis.sqlite",
            "gml_id": "parcel-1",
            "gemarkungsnummer": "0976",
            "zaehler": "1066",
            "nenner": "",
            "amtliche_flaeche_m2": None,
            "min_lon": 8.9,
            "min_lat": 47.9,
            "max_lon": 9.1,
            "max_lat": 48.1,
        }
        self.assertEqual(
            "Flurstück 1066, Hofen (0976)",
            main.search_parcel_result_from_row(row, "baden-wurttemberg")["label"],
        )

    def test_city_norms_accept_prefixed_and_plain_city(self) -> None:
        self.assertIn("stadt dresden", main.city_norms_for_state_context("Dresden", "sachsen"))
        self.assertIn("dresden", main.city_norms_for_state_context("Stadt Dresden", "sachsen"))

    def test_city_norms_accept_bremen_municipality_alias(self) -> None:
        self.assertIn(
            "stadtgemeinde bremen",
            main.city_norms_for_state_context("Bremen", "bremen"),
        )

    def test_umlaut_transliteration_does_not_collapse_ordinary_ue(self) -> None:
        variants = main.normalize_geocoder_text_variants("Suederquerweg")
        self.assertIn("suderquerweg", variants)
        self.assertIn("suederquerweg", variants)

    def test_postcode_city_is_replaced_by_requested_city(self) -> None:
        self.assertEqual(
            "Stuttgart",
            main.search_result_city_label("70184", "70184", "baden-wurttemberg", "Stuttgart"),
        )

    def test_spaced_house_suffix_stays_with_house_number(self) -> None:
        candidates = main.geocoder_direct_candidates("Mühlenweg 8 a Loose")
        self.assertEqual(
            ("address", "Mühlenweg", "8 a", "Loose"),
            candidates[0],
        )

    def test_spaced_house_range_stays_with_house_number(self) -> None:
        candidates = main.geocoder_direct_candidates("Hauptstraße 18 - 20 Dresden")
        self.assertEqual(
            ("address", "Hauptstraße", "18-20", "Dresden"),
            candidates[0],
        )
        self.assertEqual(
            "",
            main.search_result_city_label("70184", "70184", "baden-wurttemberg"),
        )

    def test_house_number_semantics_preserve_meaningful_separators(self) -> None:
        self.assertEqual(
            main.normalize_house_number_semantic("17 B7"),
            main.normalize_house_number_semantic("17 b7"),
        )
        self.assertEqual(
            main.normalize_house_number_semantic("1 1⁄10"),
            main.normalize_house_number_semantic("1 1/10"),
        )
        for typed, stored in (
            ("101", "10/1"),
            ("1719", "17/19"),
            ("1ad", "1A-D"),
            ("33a1", "33 a - 1"),
        ):
            with self.subTest(typed=typed, stored=stored):
                self.assertNotEqual(
                    main.normalize_house_number_semantic(typed),
                    main.normalize_house_number_semantic(stored),
                )

    def test_fallback_place_context_rejects_neighbor_rows(self) -> None:
        rows = [
            {
                "lon": 7.5,
                "lat": 48.5,
                "post_code": "12345",
                "city_label": "12345",
                "city_norm": "12345",
            },
            {
                "lon": 7.5,
                "lat": 48.5,
                "post_code": "12345",
                "city_label": "Nachbarort",
                "city_norm": "nachbarort",
            },
            {
                "lon": 8.5,
                "lat": 48.5,
                "post_code": "12345",
                "city_label": "Zielort",
                "city_norm": "zielort",
            },
            {
                "lon": 7.6,
                "lat": 48.6,
                "post_code": "12345",
                "city_label": "Veralteter Ort",
                "city_norm": "veralteter ort",
            },
            {
                "lon": 7.5,
                "lat": 48.5,
                "post_code": "12345",
                "city_label": "",
                "city_norm": "nachbarort",
            },
        ]
        with (
            patch.object(
                main,
                "openplz_place_comparison_norms",
                return_value=("zielort",),
            ),
            patch.object(
                main,
                "city_norms_for_state_context",
                side_effect=lambda value, _state: (main.normalize_geocoder_text(value),),
            ),
            patch.object(
                main,
                "gn250_place_bboxes_for_state_context",
                side_effect=lambda value, _state, _signature: (
                    ((7.0, 48.0, 8.0, 49.0),)
                    if main.normalize_geocoder_text(value) == "nachbarort"
                    else (((9.0, 50.0, 10.0, 51.0),) if value else tuple())
                ),
            ),
            patch.object(main, "postcode_area_lookup", return_value="12345"),
        ):
            accepted = main.filter_address_rows_by_place_context(
                rows,
                "Zielort",
                "test-state",
                ((7.0, 48.0, 8.0, 49.0),),
                (1, 1),
            )
        self.assertEqual([rows[0], rows[3]], accepted)

    def test_structured_address_fields_do_not_round_trip_through_free_text(self) -> None:
        self.assertEqual(
            (("address", "Altenkesseler Straße", "17 B7", "Saarbrücken"),),
            main.structured_geocoder_candidates(
                "Altenkesseler Straße", "17 B7", "Saarbrücken"
            ),
        )

    def test_free_text_parser_keeps_multi_part_house_numbers_together(self) -> None:
        cases = (
            (
                "Altenkesseler Straße 17 B7 Saarbrücken",
                ("address", "Altenkesseler Straße", "17 B7", "Saarbrücken"),
            ),
            (
                "Östliche Ringstraße 1 1/10 Karben",
                ("address", "Östliche Ringstraße", "1 1/10", "Karben"),
            ),
            (
                "Chausseestraße 33 a - 1 Beetzsee",
                ("address", "Chausseestraße", "33 a-1", "Beetzsee"),
            ),
        )
        for query, expected in cases:
            with self.subTest(query=query):
                self.assertEqual(expected, main.geocoder_direct_candidates(query)[0])

    def test_city_context_variants_handle_ot_and_kurort_generically(self) -> None:
        kindelbrueck = main.city_norms_for_state_context(
            "Kindelbrück OT Düppel", "thueringen"
        )
        self.assertIn("kindelbruck", kindelbrueck)
        self.assertIn("duppel", kindelbrueck)
        self.assertIn(
            "schmalkalden kurort",
            main.city_norms_for_state_context("Schmalkalden", "thueringen"),
        )
        self.assertIn(
            "stadtgemeinde bremerhaven",
            main.city_norms_for_state_context("Bremerhaven", "bremen"),
        )
        self.assertIn(
            "Oldenburg",
            main.gn250_place_name_aliases("Oldenburg (Oldb)", "niedersachsen"),
        )

    @unittest.skipUnless(LIVE_REFERENCES_AVAILABLE, "requires mounted OpenKataster reference databases")
    def test_sachsen_suggestions_use_plain_place_name(self) -> None:
        result = main.search_street_suggestions_for_dataset(
            "deutschland", "Dresden", "Pra", 8, state="sachsen"
        )
        self.assertIn("Prager Straße", [item["label"] for item in result["results"]])

    @unittest.skipUnless(LIVE_REFERENCES_AVAILABLE, "requires mounted OpenKataster reference databases")
    def test_bw_suggestions_use_openplz_and_selected_place(self) -> None:
        result = main.search_street_suggestions_for_dataset(
            "deutschland", "Stuttgart", "Aach", 8, state="baden-wurttemberg"
        )
        self.assertIn(
            ("Aachener Straße", "Stuttgart"),
            [(item["label"], item["subtitle"]) for item in result["results"]],
        )

    @unittest.skipUnless(LIVE_REFERENCES_AVAILABLE, "requires mounted OpenKataster reference databases")
    def test_bw_parcel_search_accepts_empty_flur_and_keeps_gemarkung_code(self) -> None:
        results = main.search_fast_cadastre_parcels_for_dataset(
            "Hofen (0976)", "", "1066", 12, {"baden-wurttemberg"}
        )
        self.assertTrue(results)
        self.assertTrue(all(item["feature"]["gemarkungsnummer"] == "0976" for item in results))
        self.assertEqual("Flurstück 1066, Hofen (0976)", results[0]["label"])

    @unittest.skipUnless(LIVE_REFERENCES_AVAILABLE, "requires mounted OpenKataster reference databases")
    def test_supplied_flur_is_strict(self) -> None:
        valid = main.search_fast_cadastre_parcels_for_dataset(
            "Bietigheim (1000)", "1", "771/1", 12, {"baden-wurttemberg"}
        )
        invalid = main.search_fast_cadastre_parcels_for_dataset(
            "Bietigheim (1000)", "999999", "771/1", 12, {"baden-wurttemberg"}
        )
        self.assertTrue(valid)
        self.assertEqual([], invalid)

    @unittest.skipUnless(LIVE_REFERENCES_AVAILABLE, "requires mounted OpenKataster reference databases")
    def test_legacy_compact_parcel_keys_do_not_mix_slash_positions(self) -> None:
        for number in ("1/11", "11/1", "111"):
            with self.subTest(number=number):
                results = main.search_fast_cadastre_parcels_for_dataset(
                    "Reicholzheim (0021)", "", number, 12, {"baden-wurttemberg"}
                )
                self.assertEqual([number], [item["feature"]["flurstueck"] for item in results])

    @unittest.skipUnless(LIVE_REFERENCES_AVAILABLE, "requires mounted OpenKataster reference databases")
    def test_optional_flur_can_return_multiple_disambiguated_results(self) -> None:
        results = main.search_fast_cadastre_parcels_for_dataset(
            "Elberfeld (3135)", "", "16", 12, {"nordrhein-westfalen"}
        )
        self.assertGreater(len(results), 1)
        self.assertTrue(all(item["feature"]["flur"] for item in results))
        self.assertGreater(len({item["feature"]["flur"] for item in results}), 1)

    @unittest.skipUnless(LIVE_REFERENCES_AVAILABLE, "requires mounted OpenKataster reference databases")
    def test_gemarkung_suggestions_keep_homonyms_with_distinct_codes(self) -> None:
        result = main.search_gemarkung_suggestions_for_dataset(
            "deutschland", "Hofen", 8, state="baden-wurttemberg"
        )
        codes = {item["gemarkungsnummer"] for item in result["results"]}
        self.assertIn("0976", codes)
        self.assertIn("2384", codes)

    def test_gemarkung_suggestions_use_producer_umlaut_normalization(self) -> None:
        rows = self.gemarkung_suggestions_from_fixture(
            "Überseehafen",
            8,
            {
                "baden-wurttemberg": [
                    ("uberseehafener feld", "Überseehafener Feld (1000)", "1000", 50),
                ],
                "bremen": [
                    ("uberseehafen", "Überseehafen (0009)", "0009", 5),
                ],
            },
        )
        self.assertEqual(
            [("bremen", "0009")],
            [(item["state"], item["gemarkungsnummer"]) for item in rows],
        )

    def test_exact_gemarkung_outranks_prefixes_from_earlier_states(self) -> None:
        rows = self.gemarkung_suggestions_from_fixture(
            "Hemme",
            8,
            {
                "baden-wurttemberg": [
                    (f"hemmendorf {index}", f"Hemmendorf {index} ({index:04d})", f"{index:04d}", 20)
                    for index in range(1, 9)
                ],
                "schleswig-holstein": [
                    ("hemme", "Hemme (3324)", "3324", 1),
                ],
            },
        )
        self.assertEqual(
            [("schleswig-holstein", "3324")],
            [(item["state"], item["gemarkungsnummer"]) for item in rows],
        )

    def test_full_gemarkung_label_filters_by_displayed_code(self) -> None:
        rows = self.gemarkung_suggestions_from_fixture(
            "Hausen (5933)",
            8,
            {
                "baden-wurttemberg": [
                    ("hausen", "Hausen (1000)", "1000", 100),
                    ("hausen", "Hausen (5933)", "5933", 1),
                ],
            },
        )
        self.assertEqual(
            ["5933"],
            [item["gemarkungsnummer"] for item in rows],
        )

    def test_primary_gemarkung_prefix_outranks_digraph_fallback(self) -> None:
        rows = self.gemarkung_suggestions_from_fixture(
            "Neuenk",
            8,
            {
                "saarland": [
                    ("neunkirchen", "Neunkirchen (0001)", "0001", 100),
                    ("neuenkirchen", "Neuenkirchen (0002)", "0002", 1),
                ],
            },
        )
        self.assertEqual(
            ["0002", "0001"],
            [item["gemarkungsnummer"] for item in rows],
        )

    def test_viewer_limit_keeps_all_current_hausen_homonyms_selectable(self) -> None:
        baden_codes = [f"{index:04d}" for index in range(1, 17)] + ["5933"]
        rheinland_codes = ["1238"] + [f"9{index:03d}" for index in range(1, 12)]
        rows = self.gemarkung_suggestions_from_fixture(
            "Hausen",
            50,
            {
                "baden-wurttemberg": [
                    ("hausen", f"Hausen ({code})", code, len(baden_codes) - index)
                    for index, code in enumerate(baden_codes)
                ],
                "rheinland-pfalz": [
                    ("hausen", f"Hausen ({code})", code, len(rheinland_codes) - index)
                    for index, code in enumerate(rheinland_codes)
                ],
            },
        )
        identities = {(item["state"], item["gemarkungsnummer"]) for item in rows}
        self.assertEqual(29, len(rows))
        self.assertIn(("baden-wurttemberg", "5933"), identities)
        self.assertIn(("rheinland-pfalz", "1238"), identities)

    def test_gemarkung_suggestion_api_accepts_viewer_limit(self) -> None:
        parameters = main.app.openapi()["paths"]["/api/v1/suggest/gemarkungen"]["get"]["parameters"]
        limit_parameter = next(
            parameter for parameter in parameters if parameter["name"] == "limit"
        )
        self.assertEqual(50, limit_parameter["schema"]["maximum"])

    @unittest.skipUnless(
        GEMARKUNG_REFERENCES_AVAILABLE,
        "requires mounted OpenKataster Gemarkung databases",
    )
    def test_live_gemarkung_edge_cases_are_selectable(self) -> None:
        cases = (
            ("Überseehafen", "bremen", "0009"),
            ("Hemme", "schleswig-holstein", "3324"),
            ("Hausen", "baden-wurttemberg", "5933"),
            ("Hausen", "rheinland-pfalz", "1238"),
        )
        for query, state, number in cases:
            with self.subTest(query=query, state=state, number=number):
                result = main.search_gemarkung_suggestions_for_dataset(
                    "deutschland", query, 50
                )
                identities = {
                    (item["state"], item["gemarkungsnummer"])
                    for item in result["results"]
                }
                self.assertIn((state, number), identities)

    @unittest.skipUnless(
        CENTRAL_ADDRESS_REFERENCES_AVAILABLE,
        "requires mounted central address reference databases",
    )
    def test_legacy_house_keys_never_return_separator_collisions(self) -> None:
        cases = (
            (
                "Am Hang 101 69181",
                "Am Hang 10/1 69181",
                "baden-wurttemberg",
                "Am Hang 10/1",
            ),
            (
                "Schlossweiherstraße 1719 Aachen",
                "Schlossweiherstraße 17/19 Aachen",
                "nordrhein-westfalen",
                "Schlossweiherstraße 17/19",
            ),
        )
        for false_query, true_query, state, expected in cases:
            with self.subTest(state=state):
                self.assertEqual(
                    [],
                    main.search_direct_geocoder_for_dataset(
                        false_query, 12, {state}
                    ),
                )
                positive = main.search_direct_geocoder_for_dataset(
                    true_query, 12, {state}
                )
                self.assertTrue(positive)
                self.assertTrue(all(expected in item["label"] for item in positive))

    @unittest.skipUnless(
        CENTRAL_ADDRESS_REFERENCES_AVAILABLE,
        "requires mounted central address reference databases",
    )
    def test_street_postcode_fallback_never_relabels_neighbor_addresses(self) -> None:
        cases = (
            ("brandenburg", "Lindenstraße", "11", "Alt Tucheband"),
            (
                "schleswig-holstein",
                "Massower Straße",
                "19",
                "Klein Pampau",
            ),
        )
        for state, street, house, city in cases:
            with self.subTest(state=state, city=city):
                results = main.search_direct_geocoder_for_dataset(
                    " ",
                    12,
                    {state},
                    candidate_override=main.structured_geocoder_candidates(
                        street,
                        house,
                        city,
                    ),
                )
                self.assertEqual([], results)

    @unittest.skipUnless(
        CENTRAL_ADDRESS_REFERENCES_AVAILABLE,
        "requires mounted central address reference databases",
    )
    def test_same_city_addresses_remain_visible_without_a_postcode(self) -> None:
        results = main.search_direct_geocoder_for_dataset(
            " ",
            12,
            {"saarland"},
            candidate_override=main.structured_geocoder_candidates(
                "Pfählerstraße",
                "14",
                "Saarbrücken",
            ),
        )
        labels = {item["label"] for item in results}
        self.assertIn("Pfählerstraße 14, 66125 Saarbrücken", labels)
        self.assertIn("Pfählerstraße 14, 66128 Saarbrücken", labels)

    @unittest.skipUnless(
        CENTRAL_ADDRESS_REFERENCES_AVAILABLE,
        "requires mounted central address reference databases",
    )
    def test_context_recovery_keeps_official_titles_and_stale_city_rows(self) -> None:
        cases = (
            (
                "niedersachsen",
                "Theodor-Francksen-Straße",
                "90",
                "Oldenburg",
                "Theodor-Francksen-Straße 90, 26123 Oldenburg",
            ),
            (
                "bremen",
                "Anton-Schumacher-Straße",
                "20",
                "Bremerhaven",
                "Anton-Schumacher-Straße 20, 27568 Bremerhaven",
            ),
            (
                "rheinland-pfalz",
                "Karlstraße",
                "31",
                "Wörth am Rhein",
                "Karlstraße 31, 76744 Wörth am Rhein",
            ),
            (
                "brandenburg",
                "Klein Jamno Nr.",
                "25",
                "Forst (Lausitz)",
                "Klein Jamno Nr. 25, 03149 Forst (Lausitz)",
            ),
            (
                "mecklenburg-vorpommern",
                "Neue Straße",
                "6",
                "Wustrow",
                "Neue Str. 6, 18347 Wustrow",
            ),
        )
        for state, street, house, city, expected in cases:
            with self.subTest(state=state, city=city):
                results = main.search_direct_geocoder_for_dataset(
                    " ",
                    12,
                    {state},
                    candidate_override=main.structured_geocoder_candidates(
                        street,
                        house,
                        city,
                    ),
                )
                self.assertIn(expected, [item["label"] for item in results])

    @unittest.skipUnless(
        CENTRAL_ADDRESS_REFERENCES_AVAILABLE,
        "requires mounted central address reference databases",
    )
    def test_structured_address_edge_cases_resolve_centrally(self) -> None:
        cases = (
            (
                "Altenkesseler Straße", "17 B7", "Saarbrücken", "saarland",
                "Altenkesseler Straße 17 b7, 66115 Saarbrücken",
            ),
            (
                "Bergstraße", "28a", "Schmalkalden", "thueringen",
                "Bergstraße 28a, 98574 Schmalkalden",
            ),
            (
                "Mittelgasse", "1", "Schönbrunn", "thueringen",
                "Mittelgasse 1, 98667 Schönbrunn",
            ),
            (
                "Röblingstraße", "7", "Mühlhausen", "thueringen",
                "Röblingstraße 7, 99974 Mühlhausen",
            ),
            (
                "Dorfstraße", "21", "Kindelbrück OT Düppel", "thueringen",
                "Dorfstraße 21, 99638 Kindelbrück OT Düppel",
            ),
            (
                "Guldengasse", "35", "Wyhl am Kaiserstuhl", "baden-wurttemberg",
                "Guldengasse 35, 79369 Wyhl am Kaiserstuhl",
            ),
            (
                "Hauptstraße", "44", "Endingen am Kaiserstuhl", "baden-wurttemberg",
                "Hauptstraße 44, 79346 Endingen am Kaiserstuhl",
            ),
            (
                "Feriendorf Freizeitcenter", "33", "Rheinmünster", "baden-wurttemberg",
                "Feriendorf Freizeitcenter 33, 77836 Rheinmünster",
            ),
        )
        for street, house, city, state, expected in cases:
            with self.subTest(street=street, city=city):
                results = main.search_direct_geocoder_for_dataset(
                    " ",
                    12,
                    {state},
                    candidate_override=main.structured_geocoder_candidates(
                        street, house, city
                    ),
                )
                self.assertIn(expected, [item["label"] for item in results])

    @unittest.skipUnless(
        CENTRAL_ADDRESS_REFERENCES_AVAILABLE,
        "requires mounted central address reference databases",
    )
    def test_place_and_street_suggestions_keep_context_aliases_selectable(self) -> None:
        places = main.search_place_suggestions_for_dataset(
            "deutschland", "Mühlhausen", 8
        )["results"]
        self.assertIn(
            ("Mühlhausen/Thüringen", "thueringen"),
            [(item["label"], item["state"]) for item in places],
        )
        cases = (
            ("Mühlhausen", "Röbl", "thueringen", "Röblingstraße"),
            ("Schönbrunn", "Mitt", "thueringen", "Mittelgasse"),
            ("Wyhl am Kaiserstuhl", "Guld", "baden-wurttemberg", "Guldengasse"),
            ("Endingen am Kaiserstuhl", "Haupt", "baden-wurttemberg", "Hauptstraße"),
            ("Rheinmünster", "Feri", "baden-wurttemberg", "Feriendorf Freizeitcenter"),
        )
        for place, query, state, expected in cases:
            with self.subTest(place=place, query=query):
                result = main.search_street_suggestions_for_dataset(
                    "deutschland", place, query, 8, state=state
                )
                self.assertIn(expected, [item["label"] for item in result["results"]])

    @unittest.skipUnless(
        CENTRAL_ADDRESS_REFERENCES_AVAILABLE,
        "requires mounted central address reference databases",
    )
    def test_openplz_state_slug_aliases_are_resolved_from_the_database(self) -> None:
        self.assertIn("thuringen", main.openplz_storage_state_keys("thueringen"))

    @unittest.skipUnless(LIVE_REFERENCES_AVAILABLE, "requires mounted OpenKataster reference databases")
    def test_direct_search_is_place_scoped_and_labels_are_not_duplicated(self) -> None:
        dresden = main.search_direct_geocoder_for_dataset(
            "Hauptstraße 1 Dresden", 20, {"sachsen"}
        )
        self.assertTrue(dresden)
        self.assertTrue(all("Dresden" in item["label"] for item in dresden))

        stuttgart = main.search_direct_geocoder_for_dataset(
            "Alexanderstraße 1 Stuttgart", 12, {"baden-wurttemberg"}
        )
        self.assertEqual(
            ["Alexanderstraße 1, 70184 Stuttgart"],
            [item["label"] for item in stuttgart],
        )

    @unittest.skipUnless(
        ADDRESS_FALLBACK_REFERENCES_AVAILABLE,
        "requires mounted OpenKataster address and OpenPLZ databases",
    )
    def test_unique_postcode_proof_is_state_and_locality_scoped(self) -> None:
        self.assertEqual(
            ("74219",),
            main.openplz_unique_postcodes_for_place(
                ("74219",), "Möckmühl", "baden-wurttemberg"
            ),
        )
        self.assertEqual(
            ("56075",),
            main.openplz_unique_postcodes_for_place(
                ("56075",), "Koblenz", "rheinland-pfalz"
            ),
        )
        self.assertEqual(
            (),
            main.openplz_unique_postcodes_for_place(
                ("15537",), "Treptow-Köpenick", "berlin"
            ),
        )

    @unittest.skipUnless(
        ADDRESS_FALLBACK_REFERENCES_AVAILABLE,
        "requires mounted OpenKataster address and OpenPLZ databases",
    )
    def test_exact_address_recovery_remains_building_only(self) -> None:
        cases = (
            ("Raiffeisenweg 4 Möckmühl", {"baden-wurttemberg"}),
            ("Vosshaller Weg 18 Bremen", {"bremen"}),
            ("Zum Domherrenwald 1 A Koblenz", {"rheinland-pfalz"}),
        )
        for query, states in cases:
            with self.subTest(query=query):
                results = main.search_direct_geocoder_for_dataset(query, 12, states)
                self.assertTrue(results)
                self.assertTrue(all(item["kind"] == "building" for item in results))

    @unittest.skipUnless(
        ADDRESS_FALLBACK_REFERENCES_AVAILABLE,
        "requires mounted OpenKataster address and OpenPLZ databases",
    )
    def test_street_suggestion_recovery_is_locality_scoped(self) -> None:
        cases = (
            ("Möckmühl", "Raiff", "baden-wurttemberg", "Raiffeisenweg"),
            ("Bremen", "Vossh", "bremen", "Vosshaller Weg"),
            ("Koblenz", "Zum Dom", "rheinland-pfalz", "Zum Domherrenwald"),
        )
        for place, query, state, expected in cases:
            with self.subTest(place=place, query=query):
                result = main.search_street_suggestions_for_dataset(
                    "deutschland", place, query, 8, state=state
                )
                self.assertIn(expected, [item["label"] for item in result["results"]])

    @unittest.skipUnless(
        ADDRESS_FALLBACK_REFERENCES_AVAILABLE,
        "requires mounted OpenKataster address and OpenPLZ databases",
    )
    def test_inconsistent_berlin_postcode_is_not_recovered(self) -> None:
        self.assertEqual(
            [],
            main.search_direct_geocoder_for_dataset(
                "Am Zwiebusch 57 Treptow-Köpenick", 12, {"berlin"}
            ),
        )


if __name__ == "__main__":
    unittest.main()
