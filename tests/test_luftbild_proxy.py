from __future__ import annotations

import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from openkataster_tiles import main


class _FakeUpstreamResponse:
    def __init__(self, data: bytes, content_type: str) -> None:
        self._data = data
        self.headers = {"Content-Type": content_type}

    def __enter__(self) -> "_FakeUpstreamResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._data


class LuftbildProxyTests(unittest.TestCase):
    def test_niedersachsen_uses_direct_wms_jpeg_and_format_aware_cache(self) -> None:
        config = main.LUFTBILD_WMS_CONFIGS["niedersachsen"]
        self.assertEqual(config["url"], "https://opendata.geoservices.lgln.niedersachsen.de/dop_wms")
        self.assertEqual(config["layer"], "ni_dop")
        self.assertEqual(config["format"], "image/jpeg")
        self.assertEqual(config["tile_size"], 512)
        self.assertEqual(config["attempts"], 2)

        jpeg = b"\xff\xd8\xff\xe0OpenKataster-test\xff\xd9"
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir)
            fake_response = _FakeUpstreamResponse(jpeg, "image/jpeg")
            with (
                patch.object(main, "LUFTBILD_CACHE_DIR", cache_dir),
                patch.object(main, "LUFTBILD_MIN_FREE_BYTES", 0),
                patch.object(main, "_LUFTBILD_CACHE_LAST_CLEANUP", 0.0),
                patch.object(main.urllib.request, "urlopen", return_value=fake_response) as urlopen,
            ):
                miss = main.luftbild_tile("niedersachsen", 18, 138243, 86198)
                hit = main.luftbild_tile("niedersachsen", 18, 138243, 86198)

            self.assertEqual(miss.media_type, "image/jpeg")
            self.assertEqual(miss.headers["x-openkataster-cache"], "MISS")
            self.assertEqual(hit.media_type, "image/jpeg")
            self.assertEqual(hit.headers["x-openkataster-cache"], "HIT")
            self.assertEqual(urlopen.call_count, 1)

            cache_path = (
                cache_dir
                / "512"
                / "niedersachsen"
                / "ni_dop"
                / "EPSG_3857"
                / "18"
                / "138243"
                / "86198.jpg"
            )
            self.assertEqual(cache_path.read_bytes(), jpeg)

            request = urlopen.call_args.args[0]
            params = urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query)
            self.assertEqual(params["LAYERS"], ["ni_dop"])
            self.assertEqual(params["FORMAT"], ["image/jpeg"])
            self.assertEqual(params["CRS"], ["EPSG:3857"])
            self.assertEqual(params["WIDTH"], ["512"])
            self.assertEqual(params["HEIGHT"], ["512"])
            self.assertEqual(urlopen.call_args.kwargs["timeout"], 8.0)

    def test_png_and_jpeg_cache_paths_do_not_collide(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(main, "LUFTBILD_CACHE_DIR", Path(temp_dir)):
                png = main._luftbild_cache_path(
                    "niedersachsen",
                    "ni_dop",
                    "EPSG:3857",
                    18,
                    1,
                    2,
                    tile_size=1024,
                    image_format="image/png",
                )
                jpeg = main._luftbild_cache_path(
                    "niedersachsen",
                    "ni_dop",
                    "EPSG:3857",
                    18,
                    1,
                    2,
                    tile_size=512,
                    image_format="image/jpeg",
                )

        self.assertEqual(png.suffix, ".png")
        self.assertEqual(jpeg.suffix, ".jpg")
        self.assertNotEqual(png, jpeg)

    def test_timeout_is_returned_as_non_cacheable_502(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch.object(main, "LUFTBILD_CACHE_DIR", Path(temp_dir)),
                patch.object(
                    main.urllib.request,
                    "urlopen",
                    side_effect=TimeoutError("read timed out"),
                ) as urlopen,
            ):
                with self.assertRaises(HTTPException) as raised:
                    main.luftbild_tile("niedersachsen", 18, 138244, 86198)

        self.assertEqual(raised.exception.status_code, 502)
        self.assertEqual(raised.exception.headers, {"Cache-Control": "no-store"})
        self.assertEqual(urlopen.call_count, 2)

    def test_transient_timeout_is_retried_once(self) -> None:
        jpeg = b"\xff\xd8\xff\xe0OpenKataster-retry\xff\xd9"
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch.object(main, "LUFTBILD_CACHE_DIR", Path(temp_dir)),
                patch.object(main, "LUFTBILD_MIN_FREE_BYTES", 0),
                patch.object(main, "_LUFTBILD_CACHE_LAST_CLEANUP", 0.0),
                patch.object(
                    main.urllib.request,
                    "urlopen",
                    side_effect=[
                        TimeoutError("read timed out"),
                        _FakeUpstreamResponse(jpeg, "image/jpeg"),
                    ],
                ) as urlopen,
            ):
                response = main.luftbild_tile("niedersachsen", 18, 138244, 86198)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.media_type, "image/jpeg")
        self.assertEqual(urlopen.call_count, 2)

    def test_cache_usage_includes_jpeg_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir)
            (cache_dir / "tile.png").write_bytes(b"png")
            (cache_dir / "tile.jpg").write_bytes(b"jpeg")
            with patch.object(main, "LUFTBILD_CACHE_DIR", cache_dir):
                total, files = main._luftbild_cache_usage()

        self.assertEqual(total, 7)
        self.assertEqual(len(files), 2)


if __name__ == "__main__":
    unittest.main()
