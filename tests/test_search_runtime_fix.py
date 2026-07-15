from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path
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


class SearchRuntimeFixTests(unittest.TestCase):
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
