from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from producer import extract_alkis_building_relation_patch as relation_patch


class BuildingRelationPatchTests(unittest.TestCase):
    def test_newest_catalog_object_wins_for_reused_key(self) -> None:
        key = ("03", "4", "52", "023", "0715008004")
        resolved, collisions = relation_patch.resolve_catalog_candidates(
            {
                key: [
                    {
                        "street": "Die Grasen",
                        "beginnt": "2026-03-20T09:49:39Z",
                        "endet": "",
                        "gml_id": "old",
                    },
                    {
                        "street": "Bullenkamp",
                        "beginnt": "2026-03-23T08:45:22Z",
                        "endet": "",
                        "gml_id": "new",
                    },
                ]
            }
        )
        self.assertEqual(collisions, 1)
        self.assertEqual(resolved[key], "Bullenkamp")

    def test_same_timestamp_catalog_ambiguity_still_aborts(self) -> None:
        key = ("03", "4", "52", "023", "0715008004")
        with self.assertRaisesRegex(RuntimeError, "Mehrdeutiger aktueller"):
            relation_patch.resolve_catalog_candidates(
                {
                    key: [
                        {"street": "A", "beginnt": "2026-01-01", "endet": "", "gml_id": "1"},
                        {"street": "B", "beginnt": "2026-01-01", "endet": "", "gml_id": "2"},
                    ]
                }
            )

    def test_relation_is_resolved_across_source_shards(self) -> None:
        con = sqlite3.connect(":memory:")
        relation_patch.create_schema(con)
        address = {
            "street": "Feldstraße",
            "house_number": "18",
            "city": "Hildesheim",
            "land": "03",
            "regierungsbezirk": "2",
            "kreis": "54",
            "gemeinde": "021",
        }
        signature = relation_patch.address_signature(address)
        con.execute(
            """
            INSERT INTO building_ref_candidates(building_gml_id, lage_gml_id, source_db)
            VALUES ('DENIAL5600001uNN', 'DENIAL5600002QWv', 'alkis_niedersachsen_11')
            """
        )
        con.execute(
            """
            INSERT INTO lage_candidates(lage_gml_id, address_signature, source_db, address_json)
            VALUES ('DENIAL5600002QWv', ?, 'alkis_niedersachsen_14', ?)
            """,
            (signature, json.dumps(address, ensure_ascii=False)),
        )

        self.assertEqual(relation_patch.resolve_relations_globally(con), 1)
        row = con.execute(
            "SELECT building_gml_id, street, house_number, city FROM building_relations"
        ).fetchone()
        self.assertEqual(
            row,
            ("DENIAL5600001uNN", "Feldstraße", "18", "Hildesheim"),
        )

    def test_interrupted_extract_removes_temporary_database(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            dump = root / "alkis_test_1.dump"
            dump.touch()
            output = root / "relations.sqlite"
            with mock.patch.object(
                relation_patch,
                "extract_dump",
                side_effect=KeyboardInterrupt,
            ):
                with self.assertRaises(KeyboardInterrupt):
                    relation_patch.extract([dump], output)

            self.assertFalse(output.exists())
            self.assertEqual(list(root.glob(f".{output.name}.*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
