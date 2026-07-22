from __future__ import annotations

import tempfile
import unittest
import urllib.parse
import json
import shutil
from argparse import Namespace
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from producer.aerial import dop20_pilot


class _Response(BytesIO):
    def __init__(self, payload: bytes, content_type: str = "image/jpeg") -> None:
        super().__init__(payload)
        self.headers = {"Content-Type": content_type}

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


class Dop20PilotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = dop20_pilot.load_source("bayern")

    def test_tile_id_uses_north_edge_convention(self) -> None:
        bounds = dop20_pilot.parse_tile_id("E624N5306")
        self.assertEqual(bounds.min_easting, 624000)
        self.assertEqual(bounds.max_easting, 625000)
        self.assertEqual(bounds.min_northing, 5305000)
        self.assertEqual(bounds.max_northing, 5306000)
        self.assertEqual(bounds.wms_bbox(), "624000,5305000,625000,5306000")

    def test_invalid_tile_id_is_rejected(self) -> None:
        for tile_id in ("624_5306", "E62N5306", "E624N53060", "E624Nabcd"):
            with self.subTest(tile_id=tile_id), self.assertRaises(ValueError):
                dop20_pilot.parse_tile_id(tile_id)

    def test_twenty_centimetres_produces_exact_dop20_request(self) -> None:
        bounds = dop20_pilot.parse_tile_id("E624N5306")
        params = dop20_pilot.build_getmap_params(
            self.source, bounds, 0.2, "image/jpeg"
        )
        self.assertEqual(params["BBOX"], "624000,5305000,625000,5306000")
        self.assertEqual(params["WIDTH"], "5000")
        self.assertEqual(params["HEIGHT"], "5000")
        self.assertEqual(params["CRS"], "EPSG:25832")
        self.assertEqual(params["LAYERS"], "by_dop20c")

    def test_source_pixel_limit_is_enforced(self) -> None:
        with self.assertRaisesRegex(ValueError, "source limit"):
            dop20_pilot.request_dimensions(self.source, 0.1)

    def test_acquisition_date_is_normalized(self) -> None:
        payload = "Layer 'by_dop20_info'\n ua = '25.06.2025'\n"
        self.assertEqual(dop20_pilot.parse_acquisition_date(payload), "2025-06-25")
        self.assertIsNone(dop20_pilot.parse_acquisition_date("no result"))

    def test_atomic_download_validates_content_type(self) -> None:
        image = Image.new("RGB", (4, 4), "red")
        payload = BytesIO()
        image.save(payload, "JPEG")
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "tile.jpg"
            with patch.object(
                dop20_pilot.urllib.request,
                "urlopen",
                return_value=_Response(payload.getvalue()),
            ):
                content_type = dop20_pilot.download_atomic(
                    "https://example.invalid/tile", destination
                )
            self.assertEqual(content_type, "image/jpeg")
            self.assertEqual(dop20_pilot.validate_image(destination, (4, 4))["format"], "JPEG")

    def test_dry_run_does_not_create_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "missing"
            result = dop20_pilot.run(
                Namespace(
                    source="bayern",
                    tile="E624N5306",
                    output_dir=output_dir,
                    pixel_size=0.2,
                    format="image/jpeg",
                    skip_cog=False,
                    dry_run=True,
                )
            )
            query = urllib.parse.parse_qs(
                urllib.parse.urlsplit(result["request_url"]).query
            )
            self.assertEqual(query["BBOX"], ["624000,5305000,625000,5306000"])
            self.assertFalse(output_dir.exists())

    @unittest.skipUnless(
        shutil.which("gdal_translate") and shutil.which("gdalinfo"),
        "GDAL command-line tools are required",
    )
    def test_full_bundle_is_validated_and_published_together(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "published-bundle"

            def fake_download(_url: str, destination: Path, attempts: int = 3) -> str:
                del attempts
                Image.new("RGB", (1000, 1000), "green").save(destination, "JPEG")
                return "image/jpeg"

            with (
                patch.object(dop20_pilot, "download_atomic", side_effect=fake_download),
                patch.object(
                    dop20_pilot,
                    "fetch_acquisition_date",
                    return_value={
                        "sample": "tile_center",
                        "status": "ok",
                        "date": "2025-06-25",
                        "error_type": None,
                    },
                ),
            ):
                result = dop20_pilot.run(
                    Namespace(
                        source="bayern",
                        tile="E624N5306",
                        output_dir=output_dir,
                        pixel_size=1.0,
                        format="image/jpeg",
                        skip_cog=False,
                        dry_run=False,
                    )
                )

            self.assertTrue(output_dir.is_dir())
            manifest_path = Path(result["manifest"])
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["request"]["format"], "image/jpeg")
            self.assertEqual(manifest["cog_build"]["validation"]["layout"], "COG")
            self.assertIn("GDAL", manifest["cog_build"]["gdal_version"])
            self.assertIn("_jpg.source.jpg", manifest["image"]["name"])
            self.assertTrue((output_dir / manifest["cog"]["name"]).is_file())

            with self.assertRaisesRegex(RuntimeError, "already exists"):
                dop20_pilot.run(
                    Namespace(
                        source="bayern",
                        tile="E624N5306",
                        output_dir=output_dir,
                        pixel_size=1.0,
                        format="image/jpeg",
                        skip_cog=True,
                        dry_run=False,
                    )
                )


if __name__ == "__main__":
    unittest.main()
