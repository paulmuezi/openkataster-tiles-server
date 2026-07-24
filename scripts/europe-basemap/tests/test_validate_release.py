from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "validate-release.py"
SPEC = importlib.util.spec_from_file_location("validate_release", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


HEADER = {
    "tile_compression": "gzip",
    "tile_type": "mvt",
    "minzoom": 0,
    "maxzoom": 15,
    "bounds": [-25, 34, 45, 72],
    "center": [10, 52, 5],
}
METADATA = {
    "name": "Protomaps Basemap",
    "version": "4.15.1",
    "attribution": "© OpenStreetMap",
    "planetiler:osm:osmosisreplicationtime": "2026-07-23T04:00:00Z",
    "vector_layers": [
        {"id": layer_id, "fields": {}, "minzoom": 0, "maxzoom": 15}
        for layer_id in sorted(MODULE.EXPECTED_VECTOR_LAYERS)
    ],
}


class ManifestContractTests(unittest.TestCase):
    def fixture(self, root: Path) -> tuple[Path, Path, Path]:
        release = root / "europe-20260723-z15"
        release.mkdir()
        archive = release / "basemap.pmtiles"
        archive.write_bytes(b"PMTiles production contract fixture")
        header = root / "header.json"
        metadata = root / "metadata.json"
        header.write_text(json.dumps(HEADER), encoding="utf-8")
        metadata.write_text(json.dumps(METADATA), encoding="utf-8")
        return release, header, metadata

    def test_create_and_check_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            release, header, metadata = self.fixture(root)
            args = type(
                "Args",
                (),
                {
                    "pmtiles": release / "basemap.pmtiles",
                    "header_json": header,
                    "metadata_json": metadata,
                    "manifest": release / "manifest.json",
                    "version": release.name,
                    "build_date": "20260723",
                    "source_url": "https://build.protomaps.com/20260723.pmtiles",
                    "pmtiles_cli_version": "1.31.2",
                    "pmtiles_cli_sha256": (
                        "a7e9ae10184d109c83f456ccdf6df4f3e2a64ba6cf69d9ed0f9f1840305055c1"
                    ),
                },
            )()
            MODULE.create_manifest(args)
            checked = MODULE._validated_manifest(release, skip_hash=False)

            self.assertEqual(checked["schema_version"], 1)
            self.assertEqual(checked["version"], release.name)
            self.assertEqual(checked["bounds"], [-25.0, 34.0, 45.0, 72.0])
            self.assertEqual(
                checked["provenance"]["source_url"],
                "https://build.protomaps.com/20260723.pmtiles",
            )
            self.assertEqual(
                checked["provenance"]["vector_layers"],
                sorted(MODULE.EXPECTED_VECTOR_LAYERS),
            )
            self.assertEqual(
                checked["provenance"]["data_licenses"],
                MODULE.DATA_LICENSES,
            )
            self.assertIn("ESA WorldCover project 2020", checked["attribution"])

    def test_tampered_archive_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            release, header, metadata = self.fixture(root)
            args = type(
                "Args",
                (),
                {
                    "pmtiles": release / "basemap.pmtiles",
                    "header_json": header,
                    "metadata_json": metadata,
                    "manifest": release / "manifest.json",
                    "version": release.name,
                    "build_date": "20260723",
                    "source_url": "https://build.protomaps.com/20260723.pmtiles",
                    "pmtiles_cli_version": "1.31.2",
                    "pmtiles_cli_sha256": (
                        "a7e9ae10184d109c83f456ccdf6df4f3e2a64ba6cf69d9ed0f9f1840305055c1"
                    ),
                },
            )()
            MODULE.create_manifest(args)
            with (release / "basemap.pmtiles").open("ab") as stream:
                stream.write(b"tampered")
            with self.assertRaises(MODULE.ValidationError):
                MODULE._validated_manifest(release, skip_hash=False)

    def test_schema_without_buildings_is_rejected(self) -> None:
        metadata = dict(METADATA)
        metadata["vector_layers"] = [
            layer
            for layer in METADATA["vector_layers"]
            if layer["id"] != "buildings"
        ]
        with self.assertRaises(MODULE.ValidationError):
            MODULE._validated_archive(HEADER, metadata)

    def test_unreviewed_vector_layer_is_rejected(self) -> None:
        metadata = dict(METADATA)
        metadata["vector_layers"] = [
            *METADATA["vector_layers"],
            {"id": "new_upstream_layer", "fields": {}, "minzoom": 0, "maxzoom": 15},
        ]
        with self.assertRaises(MODULE.ValidationError):
            MODULE._validated_archive(HEADER, metadata)

    def test_bounds_must_match_the_exact_configured_extract(self) -> None:
        header = dict(HEADER)
        header["bounds"] = [-10, 40, 30, 60]
        with self.assertRaises(MODULE.ValidationError):
            MODULE._validated_archive(header, METADATA)

        oversized = dict(HEADER)
        oversized["bounds"] = [-180, -85, 180, 85]
        with self.assertRaises(MODULE.ValidationError):
            MODULE._validated_archive(oversized, METADATA)


if __name__ == "__main__":
    unittest.main()
