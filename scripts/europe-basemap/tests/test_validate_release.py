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


LEGACY_HEADER = {
    "tile_compression": "gzip",
    "tile_type": "mvt",
    "minzoom": 0,
    "maxzoom": 15,
    "bounds": [-25, 34, 45, 72],
    "center": [10, 52, 5],
}
REGIONAL_HEADER = {
    **LEGACY_HEADER,
    "bounds": [5, 45.5, 18, 55.75],
    "center": [11.5, 51, 5],
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
    def fixture(
        self,
        root: Path,
        *,
        version: str = "europe-20260723-z15",
        header_payload: dict[str, object] | None = None,
    ) -> tuple[Path, Path, Path]:
        release = root / version
        release.mkdir()
        archive = release / "basemap.pmtiles"
        archive.write_bytes(b"PMTiles production contract fixture")
        header = root / "header.json"
        metadata = root / "metadata.json"
        if header_payload is None:
            expected_bounds = MODULE._version_contract(version)[2]
            header_payload = {
                **LEGACY_HEADER,
                "bounds": list(expected_bounds),
            }
        header.write_text(json.dumps(header_payload), encoding="utf-8")
        metadata.write_text(json.dumps(METADATA), encoding="utf-8")
        return release, header, metadata

    @staticmethod
    def manifest_args(
        release: Path,
        header: Path,
        metadata: Path,
    ) -> object:
        return type(
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

    def test_create_and_check_legacy_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            release, header, metadata = self.fixture(root)
            MODULE.create_manifest(self.manifest_args(release, header, metadata))
            checked = MODULE._validated_manifest(release, skip_hash=False)

            self.assertEqual(checked["schema_version"], 1)
            self.assertEqual(checked["version"], release.name)
            self.assertEqual(checked["bounds"], [-25.0, 34.0, 45.0, 72.0])
            self.assertEqual(
                checked["provenance"]["coverage_profile"],
                MODULE.LEGACY_COVERAGE_PROFILE,
            )
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

    def test_create_and_check_regional_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            release, header, metadata = self.fixture(
                root,
                version="europe-de-at-20260723-z15",
            )
            MODULE.create_manifest(self.manifest_args(release, header, metadata))
            checked = MODULE._validated_manifest(release, skip_hash=False)

            self.assertEqual(checked["bounds"], [5.0, 45.5, 18.0, 55.75])
            self.assertEqual(
                checked["provenance"]["coverage_profile"],
                MODULE.DE_AT_COVERAGE_PROFILE,
            )
            self.assertEqual(
                checked["provenance"]["extract_bbox"],
                [5.0, 45.5, 18.0, 55.75],
            )

    def test_create_rejects_bounds_from_the_other_version_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            release, header, metadata = self.fixture(
                root,
                version="europe-de-at-20260723-z15",
                header_payload=LEGACY_HEADER,
            )
            with self.assertRaises(MODULE.ValidationError):
                MODULE.create_manifest(
                    self.manifest_args(release, header, metadata)
                )

    def test_existing_legacy_manifest_without_profile_remains_valid(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            release, header, metadata = self.fixture(root)
            MODULE.create_manifest(self.manifest_args(release, header, metadata))
            manifest_path = release / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["provenance"].pop("coverage_profile")
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            checked = MODULE._validated_manifest(release, skip_hash=False)
            self.assertNotIn("coverage_profile", checked["provenance"])

    def test_regional_manifest_requires_explicit_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            release, header, metadata = self.fixture(
                root,
                version="europe-de-at-20260723-z15",
            )
            MODULE.create_manifest(self.manifest_args(release, header, metadata))
            manifest_path = release / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["provenance"].pop("coverage_profile")
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaises(MODULE.ValidationError):
                MODULE._validated_manifest(release, skip_hash=False)

    def test_tampered_archive_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            release, header, metadata = self.fixture(root)
            MODULE.create_manifest(self.manifest_args(release, header, metadata))
            with (release / "basemap.pmtiles").open("ab") as stream:
                stream.write(b"tampered")
            with self.assertRaises(MODULE.ValidationError):
                MODULE._validated_manifest(release, skip_hash=False)

    def test_version_contracts_are_immutable_and_disjoint(self) -> None:
        self.assertEqual(
            MODULE._version_contract("europe-20260723-z15"),
            (
                "20260723",
                MODULE.LEGACY_COVERAGE_PROFILE,
                MODULE.LEGACY_BOUNDS,
            ),
        )
        self.assertEqual(
            MODULE._version_contract("europe-de-at-20260723-z15"),
            (
                "20260723",
                MODULE.DE_AT_COVERAGE_PROFILE,
                MODULE.DE_AT_BOUNDS,
            ),
        )
        for invalid in (
            "europe-de-20260723-z15",
            "europe-at-de-20260723-z15",
            "europe-de-at-20260723-z14",
            "europe-de-at-20260723-z15/../../outside",
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(MODULE.ValidationError):
                    MODULE._version_contract(invalid)

    def test_schema_without_buildings_is_rejected(self) -> None:
        metadata = dict(METADATA)
        metadata["vector_layers"] = [
            layer
            for layer in METADATA["vector_layers"]
            if layer["id"] != "buildings"
        ]
        with self.assertRaises(MODULE.ValidationError):
            MODULE._validated_archive(
                LEGACY_HEADER,
                metadata,
                expected_bounds=MODULE.LEGACY_BOUNDS,
            )

    def test_unreviewed_vector_layer_is_rejected(self) -> None:
        metadata = dict(METADATA)
        metadata["vector_layers"] = [
            *METADATA["vector_layers"],
            {"id": "new_upstream_layer", "fields": {}, "minzoom": 0, "maxzoom": 15},
        ]
        with self.assertRaises(MODULE.ValidationError):
            MODULE._validated_archive(
                LEGACY_HEADER,
                metadata,
                expected_bounds=MODULE.LEGACY_BOUNDS,
            )

    def test_bounds_must_match_the_exact_configured_extract(self) -> None:
        header = dict(LEGACY_HEADER)
        header["bounds"] = [-10, 40, 30, 60]
        with self.assertRaises(MODULE.ValidationError):
            MODULE._validated_archive(
                header,
                METADATA,
                expected_bounds=MODULE.LEGACY_BOUNDS,
            )

        oversized = dict(LEGACY_HEADER)
        oversized["bounds"] = [-180, -85, 180, 85]
        with self.assertRaises(MODULE.ValidationError):
            MODULE._validated_archive(
                oversized,
                METADATA,
                expected_bounds=MODULE.LEGACY_BOUNDS,
            )

        with self.assertRaises(MODULE.ValidationError):
            MODULE._validated_archive(
                LEGACY_HEADER,
                METADATA,
                expected_bounds=MODULE.DE_AT_BOUNDS,
            )

        MODULE._validated_archive(
            REGIONAL_HEADER,
            METADATA,
            expected_bounds=MODULE.DE_AT_BOUNDS,
        )


if __name__ == "__main__":
    unittest.main()
