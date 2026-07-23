from __future__ import annotations

import copy
import unittest

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
