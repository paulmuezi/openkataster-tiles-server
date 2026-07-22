from __future__ import annotations

import unittest

from openkataster_tiles import main


class BayernLod2FeatureNormalizationTests(unittest.TestCase):
    def test_public_state_metadata_contains_lod2_source_and_license(self) -> None:
        rows = main._merge_local_state_metadata([])
        bayern = next(row for row in rows if row.get("bundesland") == "Bayern")

        self.assertEqual(bayern["datenstand"], "14.07.2026")
        self.assertEqual(bayern["lizenz"], "CC BY 4.0")
        self.assertIn("Bayerische Vermessungsverwaltung", bayern["quellenvermerk"])

    def test_building_keeps_user_facts_and_hides_citygml_provenance_columns(self) -> None:
        normalized = main.normalize_feature_properties_for_response(
            "",
            "building",
            {
                "id": "DEBYvAAAAABTyw6x",
                "source_db": "bayern-lod2",
                "gml_id": "DEBYvAAAAABTyw6x",
                "gebaeudefunktion_text": "Wohngebäude",
                "name": "",
                "geschosse_oberirdisch": 4,
                "dachform_text": "Satteldach",
                "objekthoehe_m": 16.2,
                "geometrische_flaeche_m2": 312.4,
                "addresses": [{"label": "Alter Hof 5, 80331 München"}],
                "source_file": "690_5334.gml.gz",
                "source_epsg": 25832,
                "lod2_id": "DENW123",
                "source_2d_id": "DEBYvAAAAABTyw6x",
                "objektart_code": "31001",
                "gebaeudefunktion_code": "31001_1000",
                "ags": "09162000",
                "datenquelle_lage": "1000",
                "geometrietyp_2d_referenz": "1000",
            },
        )

        self.assertEqual(normalized["gebaeudefunktion_text"], "Wohngebäude")
        self.assertEqual(normalized["geschosse_oberirdisch"], 4)
        self.assertEqual(normalized["dachform_text"], "Satteldach")
        self.assertEqual(normalized["addresses"][0]["label"], "Alter Hof 5, 80331 München")
        self.assertNotIn("name", normalized)
        self.assertNotIn("objekthoehe_m", normalized)
        self.assertFalse(
            {
                "source_file",
                "source_epsg",
                "lod2_id",
                "source_2d_id",
                "objektart_code",
                "gebaeudefunktion_code",
                "ags",
                "datenquelle_lage",
                "geometrietyp_2d_referenz",
            }
            & normalized.keys()
        )

    def test_object_height_remains_available_for_non_lod2_sources(self) -> None:
        for state, source_db in (
            ("sachsen-anhalt", "sachsen-anhalt"),
            ("bayern", "trusted-bayern-source"),
        ):
            with self.subTest(state=state, source_db=source_db):
                normalized = main.normalize_feature_properties_for_response(
                    state,
                    "building",
                    {
                        "source_db": source_db,
                        "gml_id": "height",
                        "objekthoehe_m": 12.4,
                    },
                )

                self.assertEqual(normalized["objekthoehe_m"], 12.4)

    def test_official_address_without_house_number_remains_visible(self) -> None:
        grouped = main.group_addresses_for_display(
            [
                {
                    "street": "Am Gammertshof o.Nr.",
                    "house_number": "",
                    "post_code": "97285",
                    "city": "Röttingen",
                    "street_house": "Am Gammertshof o.Nr.",
                    "label": "Am Gammertshof o.Nr., 97285 Röttingen",
                    "address_source": "bayern-lod2",
                }
            ]
        )

        self.assertEqual(len(grouped), 1)
        self.assertEqual(grouped[0]["street"], "Am Gammertshof o.Nr.")
        self.assertEqual(grouped[0]["house_number"], "")
        self.assertEqual(grouped[0]["street_house"], "Am Gammertshof o.Nr.")
        self.assertEqual(grouped[0]["label"], "Am Gammertshof o.Nr., 97285 Röttingen")
        self.assertEqual(grouped[0]["post_code"], "97285")


if __name__ == "__main__":
    unittest.main()
