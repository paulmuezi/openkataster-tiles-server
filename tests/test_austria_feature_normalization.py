from __future__ import annotations

import copy
import unittest
from unittest import mock

from openkataster_tiles import main


class AustriaFeatureNormalizationTests(unittest.TestCase):
    def test_building_keeps_user_facts_and_hides_bev_provenance(self) -> None:
        properties = {
            "id": "{BUILDING}",
            "source_db": "austria-bev",
            "gml_id": "{BUILDING}",
            "gebaeudefunktion_text": "Wohngebäude",
            "geometrische_flaeche_m2": 184.2,
            "addresses": [{"label": "Eyzinggasse 29, 1110 Wien"}],
            "objektart_code": "8101",
            "objektart_text": "Bauwerk",
            "agwr_objektnummer": "123",
            "agwr_typ": "Bestand",
            "erfassungsart": "Photogrammetrie",
            "datenquelle": "BEV",
            "datenquelle_ext_id": "raw-1",
            "erstellungsdatum": "2024-01-01",
            "bearbeitungsdatum": "2026-03-23",
            "country_code": "AT",
            "future_raw_bev_field": "must never become a table column",
        }
        original = copy.deepcopy(properties)

        normalized = main.normalize_feature_properties_for_response(
            "",
            "building",
            properties,
        )

        self.assertEqual(
            set(normalized),
            {
                "id",
                "source_db",
                "gml_id",
                "gebaeudefunktion_text",
                "geometrische_flaeche_m2",
                "addresses",
            },
        )
        self.assertEqual(normalized["addresses"][0]["country"], "Österreich")
        self.assertEqual(properties, original)

    def test_parcel_keeps_cadastral_facts_and_hides_admin_duplicates(self) -> None:
        normalized = main.normalize_feature_properties_for_response(
            "oesterreich",
            "parcel",
            {
                "id": "AT.BEV.GST.01107.1652",
                "source_db": "austria-bev",
                "gml_id": "AT.BEV.GST.01107.1652",
                "gemarkung": "Simmering",
                "gemarkungsnummer": "01107",
                "katastralgemeindenummer": "01107",
                "flur": "",
                "flurstueck": "1652",
                "grundstuecksnummer": "1652",
                "zaehler": "1652",
                "nenner": "",
                "rechtsstatus": "G",
                "rechtsstatus_text": "Grenzkataster",
                "flaechenindikator": "",
                "flaechenbestimmung": "grafisch bestimmt",
                "amtliche_flaeche_m2": 742.0,
                "gemeinde": "Wien",
                "gemeindenummer": "90001",
                "bezirk": "Wien",
                "bezirksnummer": "900",
                "bundesland": "Wien",
                "bundeslandnummer": "9",
                "anlegungsmassstab": "1:1000",
                "country_code": "AT",
                "addresses": [{"label": "Eyzinggasse 27, 1110 Wien"}],
            },
        )

        self.assertEqual(normalized["gemarkung"], "Simmering")
        self.assertEqual(normalized["flurstueck"], "1652")
        self.assertEqual(normalized["rechtsstatus_text"], "Grenzkataster")
        self.assertEqual(normalized["flaechenbestimmung"], "grafisch bestimmt")
        self.assertEqual(normalized["addresses"][0]["country"], "Österreich")
        self.assertFalse(
            {
                "flur",
                "rechtsstatus",
                "flaechenindikator",
                "gemeinde",
                "gemeindenummer",
                "bezirk",
                "bezirksnummer",
                "bundesland",
                "bundeslandnummer",
                "anlegungsmassstab",
                "country_code",
            }
            & normalized.keys()
        )

    def test_parcel_repairs_legacy_bev_kg_encoding(self) -> None:
        normalized = main.normalize_feature_properties_for_response(
            "oesterreich",
            "parcel",
            {
                "source_db": "austria-bev",
                "gml_id": "AT.BEV.GST.19544.1543/14",
                "gemarkung": "St. PÃ¶lten",
                "bundesland": "NiederÃ¶sterreich",
                "flurstueck": "1543/14",
                "addresses": [
                    {
                        "label": "Mühlweg 14, 3100 St.Pölten",
                        "city": "St.Pölten",
                    }
                ],
            },
        )

        self.assertEqual(normalized["gemarkung"], "St. Pölten")
        self.assertEqual(
            normalized["addresses"][0]["label"],
            "Mühlweg 14, 3100 St. Pölten",
        )
        self.assertEqual(normalized["addresses"][0]["city"], "St. Pölten")
        self.assertNotIn("bundesland", normalized)

    def test_search_variants_keep_current_and_legacy_kg_spellings(self) -> None:
        variants = main.normalize_geocoder_text_variants("St. Pölten")

        self.assertIn("st polten", variants)
        self.assertIn("st pa lten", variants)
        self.assertEqual(
            main.repair_utf8_decoded_as_cp1252("NiederÃ¶sterreich"),
            "Niederösterreich",
        )

    def test_explicit_region_filter_cannot_escape_dataset(self) -> None:
        with (
            mock.patch.object(
                main,
                "all_local_search_db_entries",
                return_value=(
                    main.FeatureDbEntry(
                        name="niedersachsen",
                        path=main.DATA_DIR / "niedersachsen.search.sqlite",
                    ),
                    main.FeatureDbEntry(
                        name="oesterreich",
                        path=main.DATA_DIR / "oesterreich.search.sqlite",
                    ),
                ),
            ),
            mock.patch.object(
                main,
                "dataset_region_keys",
                side_effect=lambda dataset: (
                    {"niedersachsen"}
                    if dataset == "deutschland"
                    else {"oesterreich"}
                ),
            ),
        ):
            self.assertEqual(
                main.search_suggestion_states_for_dataset(
                    "deutschland",
                    "oesterreich",
                ),
                set(),
            )
            self.assertEqual(
                main.search_suggestion_states_for_dataset(
                    "oesterreich",
                    "niedersachsen",
                ),
                set(),
            )
            self.assertEqual(
                main.search_suggestion_states_for_dataset(
                    "oesterreich",
                    "oesterreich",
                ),
                {"oesterreich"},
            )

    def test_point_features_keep_region_for_persisted_selection_references(self) -> None:
        entry = main.FeatureDbEntry(
            name="oesterreich",
            path=main.DATA_DIR / "oesterreich.features.sqlite",
        )
        parcel = {
            "source_db": "austria-bev",
            "gml_id": "AT.BEV.GST.19544.1543/14",
            "geometry": {"type": "Polygon", "coordinates": []},
        }
        building = {
            "source_db": "austria-bev",
            "gml_id": "AT.BEV.BWG.42",
            "geometry": {"type": "Polygon", "coordinates": []},
        }
        with (
            mock.patch.object(
                main,
                "feature_db_entries_for_dataset",
                return_value=(entry,),
            ),
            mock.patch.object(
                main,
                "query_features_in_index",
                return_value=([parcel], [building]),
            ),
            mock.patch.object(
                main,
                "_enrich_onoffice_land_register_features",
                return_value=None,
            ),
        ):
            result = main.features_at_point_for_dataset(
                "oesterreich",
                15.6,
                48.2,
            )

        self.assertEqual(result["parcels"][0]["state"], "oesterreich")
        self.assertEqual(result["buildings"][0]["state"], "oesterreich")

    def test_other_datasets_remain_unfiltered(self) -> None:
        properties = {
            "source_db": "niedersachsen.gml",
            "custom_field": "bleibt erhalten",
        }

        self.assertEqual(
            main.normalize_feature_properties_for_response(
                "niedersachsen",
                "building",
                properties,
            ),
            properties,
        )


if __name__ == "__main__":
    unittest.main()
