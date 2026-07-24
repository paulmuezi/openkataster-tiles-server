from __future__ import annotations

import gzip
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from openkataster_tiles import main


class _FakeDataset:
    def __init__(self, tile_data: bytes | None = None) -> None:
        self.min_zoom = 0
        self.max_zoom = 15
        self.is_gzip = True
        self.header = {"tile_type": 1}
        self.tile_data = tile_data
        self.requested_tiles: list[tuple[int, int, int]] = []
        self.closed = False

    def tile(self, z: int, x: int, y: int) -> bytes | None:
        self.requested_tiles.append((z, x, y))
        return self.tile_data

    def close(self) -> None:
        self.closed = True


def _request(*, if_none_match: str = "") -> Request:
    headers = [(b"host", b"tiles.openkataster.de")]
    if if_none_match:
        headers.append((b"if-none-match", if_none_match.encode("ascii")))
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "https",
            "path": "/api/v1/basemap/config",
            "raw_path": b"/api/v1/basemap/config",
            "query_string": b"",
            "root_path": "",
            "headers": headers,
            "server": ("tiles.openkataster.de", 443),
            "client": ("127.0.0.1", 12345),
        }
    )


class EuropeBasemapTests(unittest.TestCase):
    version = "europe-20260723-z15"

    def _runtime_fixture(
        self,
        root: Path,
        dataset: _FakeDataset,
    ) -> main.EuropeBasemapRuntime:
        version_dir = root / "versions" / self.version
        version_dir.mkdir(parents=True)
        pmtiles_path = version_dir / "basemap.pmtiles"
        pmtiles_path.write_bytes(b"PMTiles test fixture")
        manifest = {
            "schema_version": 1,
            "version": self.version,
            "pmtiles": "basemap.pmtiles",
            "sha256": "a" * 64,
            "size_bytes": pmtiles_path.stat().st_size,
            "minzoom": 0,
            "maxzoom": 15,
            "bounds": [-25.0, 34.0, 45.0, 72.0],
            "attribution": main.EUROPE_BASEMAP_ATTRIBUTION,
            "source": "Protomaps Basemap v4",
            "provenance": {
                "data_licenses": main.EUROPE_BASEMAP_DATA_LICENSES,
            },
        }
        (version_dir / "manifest.json").write_text(
            json.dumps(manifest),
            encoding="utf-8",
        )
        (root / "active").symlink_to(
            Path("versions") / self.version,
            target_is_directory=True,
        )
        with (
            patch.object(main, "EUROPE_BASEMAP_ROOT", root),
            patch.object(main, "load_europe_basemap_dataset", return_value=dataset),
        ):
            return main._resolve_europe_basemap_runtime()

    def test_mode_file_overrides_environment_and_invalid_file_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with (
                patch.object(main, "EUROPE_BASEMAP_ROOT", root),
                patch.object(main, "EUROPE_BASEMAP_ENV_MODE", "on"),
            ):
                self.assertEqual(main.europe_basemap_mode_details(), ("on", "environment"))
                (root / "mode").write_text("preview\n", encoding="utf-8")
                self.assertEqual(main.europe_basemap_mode_details(), ("preview", "mode-file"))
                (root / "mode").write_text("enabled", encoding="utf-8")
                self.assertEqual(
                    main.europe_basemap_mode_details(),
                    ("off", "mode-file-invalid"),
                )

    def test_symlinked_mode_file_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            outside = root / "outside-mode"
            outside.write_text("on", encoding="utf-8")
            (root / "mode").symlink_to(outside)
            with (
                patch.object(main, "EUROPE_BASEMAP_ROOT", root),
                patch.object(main, "EUROPE_BASEMAP_ENV_MODE", "on"),
            ):
                self.assertEqual(
                    main.europe_basemap_mode_details(),
                    ("off", "mode-file-invalid"),
                )

    def test_runtime_requires_active_version_below_versions_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "europe"
            outside = base / "outside"
            (root / "versions").mkdir(parents=True)
            outside.mkdir()
            (root / "active").symlink_to(outside, target_is_directory=True)
            with patch.object(main, "EUROPE_BASEMAP_ROOT", root):
                with self.assertRaises(ValueError):
                    main._resolve_europe_basemap_runtime()
                self.assertIsNone(main.europe_basemap_runtime())

    def test_runtime_rejects_pmtiles_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            dataset = _FakeDataset()
            runtime = self._runtime_fixture(root, dataset)
            self.assertEqual(runtime.version, self.version)
            runtime.path.unlink()
            outside = root / "outside.pmtiles"
            outside.write_bytes(b"PMTiles test fixture")
            runtime.path.symlink_to(outside)
            with patch.object(main, "EUROPE_BASEMAP_ROOT", root):
                with self.assertRaises(ValueError):
                    main._resolve_europe_basemap_runtime()

    def test_config_contract_is_nested_and_never_exposes_server_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = self._runtime_fixture(Path(temp_dir), _FakeDataset())
            with (
                patch.object(main, "europe_basemap_mode_details", return_value=("preview", "mode-file")),
                patch.object(main, "europe_basemap_runtime", return_value=runtime),
            ):
                payload = main.europe_basemap_config_payload(_request())

        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["mode"], "preview")
        self.assertEqual(payload["fallback"], "national")
        self.assertTrue(payload["europe"]["available"])
        self.assertEqual(payload["europe"]["version"], self.version)
        self.assertEqual(
            payload["europe"]["style_url"],
            "/viewer-assets/europe-basemap-style-20260724-bkg2/style.json",
        )
        self.assertEqual(
            payload["europe"]["tile_template"],
            (
                "https://tiles.openkataster.de/api/v1/basemap/europe/"
                "{z}/{x}/{y}.mvt"
            ),
        )
        self.assertEqual(
            payload["europe"]["attribution"],
            main.EUROPE_BASEMAP_ATTRIBUTION,
        )
        self.assertEqual(
            payload["europe"]["licenses"],
            main.EUROPE_BASEMAP_DATA_LICENSES,
        )
        self.assertNotIn(str(runtime.path), json.dumps(payload))

    def test_unavailable_runtime_forces_effective_mode_off(self) -> None:
        with (
            patch.object(main, "europe_basemap_mode_details", return_value=("on", "environment")),
            patch.object(main, "europe_basemap_runtime", return_value=None),
        ):
            payload = main.europe_basemap_config_payload(_request())

        self.assertEqual(payload["configured_mode"], "on")
        self.assertEqual(payload["mode"], "off")
        self.assertEqual(payload["status"], "unavailable")
        self.assertFalse(payload["europe"]["available"])

    def test_versioned_tile_has_gzip_etag_and_immutable_cache(self) -> None:
        compressed_tile = gzip.compress(b"vector tile")
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = self._runtime_fixture(
                Path(temp_dir),
                _FakeDataset(compressed_tile),
            )
            with (
                patch.object(main, "europe_basemap_mode", return_value="preview"),
                patch.object(main, "europe_basemap_runtime", return_value=runtime),
            ):
                response = main.api_v1_europe_basemap_tile(
                    _request(),
                    0,
                    0,
                    0,
                    version=self.version,
                )
                conditional = main.api_v1_europe_basemap_tile(
                    _request(if_none_match=response.headers["etag"]),
                    0,
                    0,
                    0,
                    version=self.version,
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.body, compressed_tile)
        self.assertEqual(response.media_type, "application/vnd.mapbox-vector-tile")
        self.assertEqual(response.headers["content-encoding"], "gzip")
        self.assertEqual(
            response.headers["cache-control"],
            "public, max-age=31536000, immutable",
        )
        self.assertIn(
            "https://www.openstreetmap.org/copyright",
            response.headers["link"],
        )
        self.assertIn(
            "https://creativecommons.org/licenses/by/4.0/",
            response.headers["link"],
        )
        self.assertEqual(conditional.status_code, 304)
        self.assertEqual(conditional.headers["etag"], response.headers["etag"])

    def test_unversioned_tile_is_short_cached_and_stale_version_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = self._runtime_fixture(
                Path(temp_dir),
                _FakeDataset(gzip.compress(b"vector tile")),
            )
            with (
                patch.object(main, "europe_basemap_mode", return_value="on"),
                patch.object(main, "europe_basemap_runtime", return_value=runtime),
            ):
                response = main.api_v1_europe_basemap_tile(
                    _request(),
                    0,
                    0,
                    0,
                    version=None,
                )
                with self.assertRaises(HTTPException) as raised:
                    main.api_v1_europe_basemap_tile(
                        _request(),
                        0,
                        0,
                        0,
                        version="europe-old",
                    )

        self.assertEqual(
            response.headers["cache-control"],
            "public, max-age=300, must-revalidate",
        )
        self.assertEqual(raised.exception.status_code, 404)
        self.assertEqual(raised.exception.headers, {"Cache-Control": "no-store"})

    def test_disabled_and_unavailable_modes_degrade_without_serving_tiles(self) -> None:
        with patch.object(main, "europe_basemap_mode", return_value="off"):
            with self.assertRaises(HTTPException) as disabled:
                main.api_v1_europe_basemap_tile(_request(), 0, 0, 0, version=None)
        self.assertEqual(disabled.exception.status_code, 404)

        with (
            patch.object(main, "europe_basemap_mode", return_value="preview"),
            patch.object(main, "europe_basemap_runtime", return_value=None),
        ):
            with self.assertRaises(HTTPException) as unavailable:
                main.api_v1_europe_basemap_tile(_request(), 0, 0, 0, version=None)
        self.assertEqual(unavailable.exception.status_code, 503)
        self.assertEqual(unavailable.exception.headers["Retry-After"], "60")

    def test_pbf_media_type_and_fingerprinted_asset_cache(self) -> None:
        self.assertEqual(
            main._viewer_media_type(Path("0-255.pbf")),
            "application/x-protobuf",
        )
        self.assertEqual(
            main._viewer_asset_cache_control("europe-basemap-assets-protomaps-028c18f7"),
            "public, max-age=31536000, immutable",
        )
        self.assertEqual(
            main._viewer_asset_cache_control("viewer-app"),
            "public, max-age=300",
        )

    def test_http_route_serves_maplibre_fontstack_assets_with_spaces(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            font_file = (
                root
                / "europe-basemap-assets-protomaps-028c18f7"
                / "fonts"
                / "Noto Sans Devanagari Regular v1"
                / "0-255.pbf"
            )
            font_file.parent.mkdir(parents=True)
            font_file.write_bytes(b"glyph fixture")
            sprite_root = (
                root
                / "europe-basemap-assets-protomaps-028c18f7"
                / "sprites"
                / "v4"
            )
            sprite_root.mkdir(parents=True)
            (sprite_root / "light@2x.json").write_bytes(b'{"sprite": true}')
            (sprite_root / "light@2x.png").write_bytes(b"png fixture")
            with patch.object(main, "VIEWER_ROOT", root):
                client = TestClient(main.app)
                response = client.get(
                    (
                        "/viewer-assets/europe-basemap-assets-protomaps-028c18f7/"
                        "fonts/Noto%20Sans%20Devanagari%20Regular%20v1/0-255.pbf"
                    )
                )
                sprite_json = client.get(
                    (
                        "/viewer-assets/europe-basemap-assets-protomaps-028c18f7/"
                        "sprites/v4/light@2x.json"
                    )
                )
                sprite_png = client.get(
                    (
                        "/viewer-assets/europe-basemap-assets-protomaps-028c18f7/"
                        "sprites/v4/light@2x.png"
                    )
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"glyph fixture")
        self.assertEqual(response.headers["content-type"], "application/x-protobuf")
        self.assertEqual(
            response.headers["cache-control"],
            "public, max-age=31536000, immutable",
        )
        self.assertEqual(sprite_json.status_code, 200)
        self.assertEqual(sprite_json.json(), {"sprite": True})
        self.assertEqual(sprite_png.status_code, 200)
        self.assertEqual(sprite_png.content, b"png fixture")
        self.assertEqual(sprite_png.headers["content-type"], "image/png")

    def test_dataset_cache_is_identity_keyed_bounded_and_explicitly_closed(self) -> None:
        created = [_FakeDataset(), _FakeDataset(), _FakeDataset()]
        main.clear_europe_basemap_dataset_cache()
        try:
            with patch.object(main, "Dataset", side_effect=created) as dataset_class:
                first = main.load_europe_basemap_dataset("/tmp/europe.pmtiles", 1, 10, 100)
                same = main.load_europe_basemap_dataset("/tmp/europe.pmtiles", 1, 10, 100)
                second = main.load_europe_basemap_dataset("/tmp/europe.pmtiles", 2, 10, 100)
                third = main.load_europe_basemap_dataset("/tmp/europe.pmtiles", 3, 10, 100)

            self.assertIs(first, same)
            self.assertEqual(dataset_class.call_count, 3)
            self.assertTrue(first.closed)
            self.assertFalse(second.closed)
            self.assertFalse(third.closed)
        finally:
            main.clear_europe_basemap_dataset_cache()

        self.assertTrue(second.closed)
        self.assertTrue(third.closed)

    def test_dataset_close_releases_the_actual_mmap_and_file_descriptor(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            archive = Path(temp_dir) / "basemap.pmtiles"
            archive.write_bytes(b"not-empty")
            reader = Mock()
            reader.header.return_value = {"tile_type": 1}
            reader.metadata.return_value = {}

            with patch.object(main, "Reader", return_value=reader):
                dataset = main.Dataset(archive)

            mapping = dataset._mapping
            handle = dataset.file
            self.assertIsNotNone(mapping)
            self.assertFalse(mapping.closed)
            self.assertFalse(handle.closed)

            dataset.close()

            self.assertIsNone(dataset._mapping)
            self.assertTrue(mapping.closed)
            self.assertTrue(handle.closed)
            with self.assertRaises(ValueError):
                dataset._read_mapped_bytes(0, 1)


if __name__ == "__main__":
    unittest.main()
