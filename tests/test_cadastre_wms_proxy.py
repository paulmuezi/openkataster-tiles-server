from __future__ import annotations

import tempfile
import unittest
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from PIL import Image

from openkataster_tiles import main


def _png_bytes(
    size: int,
    color: tuple[int, int, int, int] = (220, 230, 240, 255),
) -> bytes:
    output = BytesIO()
    Image.new("RGBA", (size, size), color).save(output, format="PNG")
    return output.getvalue()


class _FakeResponse:
    def __init__(self, data: bytes, content_type: str = "image/png") -> None:
        self.data = data
        self.headers = {"Content-Type": content_type}

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.data


class CadastreWmsProxyTests(unittest.TestCase):
    def test_parallel_sibling_requests_share_one_metatile_render(self) -> None:
        config = main.KATASTER_WMS_CONFIGS["bayern"]
        request_size = (
            int(config["tile_size"]) * int(config["metatile_size"])
            + 2 * int(config["bleed_pixels"])
        )
        coordinates = (
            (139440, 90046),
            (139441, 90046),
            (139440, 90047),
            (139441, 90047),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch.object(main, "LUFTBILD_CACHE_DIR", Path(temp_dir)),
                patch.object(main, "LUFTBILD_MIN_FREE_BYTES", 0),
                patch.object(main, "_LUFTBILD_CACHE_LAST_CLEANUP", 0.0),
                patch.object(
                    main.urllib.request,
                    "urlopen",
                    return_value=_FakeResponse(_png_bytes(request_size)),
                ) as urlopen,
                ThreadPoolExecutor(max_workers=4) as executor,
            ):
                responses = list(
                    executor.map(
                        lambda coordinate: main.katasterbild_tile(
                            "bayern",
                            18,
                            coordinate[0],
                            coordinate[1],
                        ),
                        coordinates,
                    )
                )

        self.assertEqual(urlopen.call_count, 1)
        cache_states = [response.headers["x-openkataster-cache"] for response in responses]
        self.assertEqual(cache_states.count("MISS"), 1)
        self.assertEqual(cache_states.count("HIT"), 3)

    def test_saxony_anhalt_request_is_fully_server_configured_and_cached(self) -> None:
        config = main.KATASTER_WMS_CONFIGS["sachsen-anhalt"]
        tile_size = int(config["tile_size"])
        metatile_size = int(config["metatile_size"])
        bleed_pixels = int(config["bleed_pixels"])
        request_size = tile_size * metatile_size + 2 * bleed_pixels
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch.object(main, "LUFTBILD_CACHE_DIR", Path(temp_dir)),
                patch.object(main, "LUFTBILD_MIN_FREE_BYTES", 0),
                patch.object(main, "_LUFTBILD_CACHE_LAST_CLEANUP", 0.0),
                patch.object(
                    main.urllib.request,
                    "urlopen",
                    return_value=_FakeResponse(_png_bytes(request_size)),
                ) as urlopen,
            ):
                miss = main.katasterbild_tile("sachsen-anhalt", 18, 139538, 86447)
                sibling_hit = main.katasterbild_tile("sachsen-anhalt", 18, 139539, 86447)
                hit = main.katasterbild_tile("sachsen-anhalt", 18, 139538, 86447)

        self.assertEqual(miss.status_code, 200)
        self.assertEqual(miss.headers["x-openkataster-cache"], "MISS")
        self.assertEqual(miss.headers["x-openkataster-metatile"], "2")
        self.assertEqual(miss.headers["x-openkataster-scale"], "1")
        self.assertEqual(sibling_hit.headers["x-openkataster-cache"], "HIT")
        self.assertEqual(hit.headers["x-openkataster-cache"], "HIT")
        self.assertEqual(urlopen.call_count, 1)

        request = urlopen.call_args.args[0]
        params = urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query)
        self.assertEqual(params["CRS"], ["EPSG:3857"])
        self.assertEqual(params["WIDTH"], [str(request_size)])
        self.assertEqual(params["HEIGHT"], [str(request_size)])
        self.assertEqual(params["DPI"], ["120"])
        self.assertEqual(params["TRANSPARENT"], ["true"])
        self.assertEqual(
            params["LAYERS"],
            [
                "adv_alkis_tatsaechliche_nutzung,adv_alkis_gesetzl_festlegungen,"
                "adv_alkis_weiteres,adv_alkis_gebaeude,adv_alkis_flurstuecke"
            ],
        )
        self.assertEqual(params["STYLES"], ["Farbe,Farbe,Farbe,Farbe,Farbe"])
        with Image.open(BytesIO(miss.body)) as cropped:
            self.assertEqual(cropped.size, (512, 512))

    def test_bavaria_high_density_request_preserves_logical_map_geometry(self) -> None:
        config = main.KATASTER_WMS_CONFIGS["bayern"]
        tile_size = int(config["tile_size"]) * 2
        metatile_size = int(config["metatile_size"])
        bleed_pixels = int(config["bleed_pixels"]) * 2
        request_size = tile_size * metatile_size + 2 * bleed_pixels
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch.object(main, "LUFTBILD_CACHE_DIR", Path(temp_dir)),
                patch.object(main, "LUFTBILD_MIN_FREE_BYTES", 0),
                patch.object(main, "_LUFTBILD_CACHE_LAST_CLEANUP", 0.0),
                patch.object(
                    main.urllib.request,
                    "urlopen",
                    return_value=_FakeResponse(_png_bytes(request_size)),
                ) as urlopen,
            ):
                response = main.katasterbild_tile_2x("bayern", 18, 139440, 90047)

        self.assertEqual(response.status_code, 200)
        request = urlopen.call_args.args[0]
        params = urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query)
        self.assertEqual(params["CRS"], ["EPSG:3857"])
        self.assertEqual(params["WIDTH"], [str(request_size)])
        self.assertEqual(params["HEIGHT"], [str(request_size)])
        self.assertEqual(params["DPI"], ["240"])
        self.assertEqual(params["LAYERS"], ["by_alkis_parzellarkarte_farbe"])
        self.assertEqual(params["STYLES"], ["Farbe"])
        self.assertIn("metatile2", config["revision"])
        self.assertIn("hidpi", config["revision"])

        request_bbox = [float(value) for value in params["BBOX"][0].split(",")]
        anchor_x = 139440
        anchor_y = 90046
        top_left_bbox = main._tile_webmercator_bounds(18, anchor_x, anchor_y)
        bottom_right_bbox = main._tile_webmercator_bounds(
            18,
            anchor_x + metatile_size - 1,
            anchor_y + metatile_size - 1,
        )
        metatile_bbox = [
            top_left_bbox[0],
            bottom_right_bbox[1],
            bottom_right_bbox[2],
            top_left_bbox[3],
        ]
        expected_bbox, expected_size, _ = main._buffered_wms_frame(
            metatile_bbox,
            tile_size * metatile_size,
            bleed_pixels,
        )
        self.assertEqual(expected_size, request_size)
        for actual, expected in zip(request_bbox, expected_bbox, strict=True):
            self.assertAlmostEqual(actual, expected, places=3)

        with Image.open(BytesIO(response.body)) as cropped:
            self.assertEqual(cropped.size, (1024, 1024))

    def test_metatile_split_restores_every_exact_xyz_pixel_extent(self) -> None:
        tile_size = 10
        metatile_size = 2
        bbox = [100.0, 200.0, 120.0, 220.0]
        buffered_bbox, request_size, bleed_pixels = main._buffered_wms_frame(
            bbox,
            tile_size * metatile_size,
            2,
        )
        self.assertEqual(buffered_bbox, [98.0, 198.0, 122.0, 222.0])
        self.assertEqual(request_size, 24)
        self.assertEqual(bleed_pixels, 2)

        source = Image.new("RGBA", (request_size, request_size), (255, 0, 0, 255))
        colors = {
            (0, 0): (0, 128, 255, 255),
            (1, 0): (0, 180, 100, 255),
            (0, 1): (250, 180, 0, 255),
            (1, 1): (140, 60, 220, 255),
        }
        for (column, row), color in colors.items():
            for x in range(
                bleed_pixels + column * tile_size,
                bleed_pixels + (column + 1) * tile_size,
            ):
                for y in range(
                    bleed_pixels + row * tile_size,
                    bleed_pixels + (row + 1) * tile_size,
                ):
                    source.putpixel((x, y), color)
        raw = BytesIO()
        source.save(raw, format="PNG")
        cropped_tiles = main._split_wms_metatile(
            raw.getvalue(),
            request_size=request_size,
            tile_size=tile_size,
            metatile_size=metatile_size,
            bleed_pixels=bleed_pixels,
            media_type="image/png",
        )
        self.assertEqual(set(cropped_tiles), set(colors))
        for position, color in colors.items():
            with Image.open(BytesIO(cropped_tiles[position])) as cropped:
                self.assertEqual(cropped.size, (tile_size, tile_size))
                self.assertEqual(cropped.getpixel((0, 0)), color)
                self.assertEqual(cropped.getpixel((9, 9)), color)

    def test_invalid_metatile_image_is_rejected_without_being_cached(self) -> None:
        invalid_images = (
            _png_bytes(512),
            b"\x89PNG\r\n\x1a\nnot-a-decodable-image",
        )
        for invalid_image in invalid_images:
            with self.subTest(size=len(invalid_image)), tempfile.TemporaryDirectory() as temp_dir:
                cache_dir = Path(temp_dir)
                with (
                    patch.object(main, "LUFTBILD_CACHE_DIR", cache_dir),
                    patch.object(
                        main.urllib.request,
                        "urlopen",
                        return_value=_FakeResponse(invalid_image),
                    ),
                ):
                    with self.assertRaises(HTTPException) as raised:
                        main.katasterbild_tile("bayern", 18, 139440, 90047)

                self.assertEqual(raised.exception.status_code, 502)
                self.assertEqual(
                    raised.exception.headers,
                    {"Cache-Control": "no-store"},
                )
                self.assertFalse(any(cache_dir.rglob("*.png")))

    def test_unknown_state_and_out_of_range_zoom_never_reach_upstream(self) -> None:
        with patch.object(main.urllib.request, "urlopen") as urlopen:
            with self.assertRaises(HTTPException) as unknown:
                main.katasterbild_tile("niedersachsen", 18, 1, 1)
            with self.assertRaises(HTTPException) as zoom:
                main.katasterbild_tile("sachsen-anhalt", 16, 1, 1)

        self.assertEqual(unknown.exception.status_code, 404)
        self.assertEqual(zoom.exception.status_code, 400)
        urlopen.assert_not_called()

    def test_visual_capability_is_exposed_without_claiming_local_data(self) -> None:
        with (
            patch.object(main, "active_bucket_state_keys", return_value=("sachsen-anhalt",)),
            patch.object(main, "_state_metadata_cache", return_value=[]),
        ):
            rows = main._api_v1_state_rows()

        self.assertEqual([row["slug"] for row in rows], ["bayern", "sachsen-anhalt"])
        saxony_anhalt = next(row for row in rows if row["slug"] == "sachsen-anhalt")
        self.assertTrue(saxony_anhalt["active"])
        self.assertTrue(saxony_anhalt["interactive"])
        capability = saxony_anhalt["rendering"]["cadastre_raster"]
        self.assertEqual(capability["profile"], "official-wms-full-v1")
        self.assertEqual(capability["tile_size"], 512)
        self.assertEqual(capability["max_scale"], 2)
        self.assertIn("{ratio}.png", capability["tile_template"])
        self.assertIn("/katasterbild/sachsen-anhalt/", capability["tile_template"])

        with (
            patch.object(main, "active_bucket_state_keys", return_value=("niedersachsen",)),
            patch.object(main, "_state_metadata_cache", return_value=[]),
        ):
            mixed_rows = main._api_v1_state_rows()
        by_slug = {row["slug"]: row for row in mixed_rows}
        self.assertNotIn(
            "cadastre_raster",
            by_slug["niedersachsen"].get("rendering", {}),
        )
        self.assertTrue(by_slug["niedersachsen"]["active"])
        self.assertFalse(by_slug["bayern"]["active"])
        self.assertFalse(by_slug["bayern"]["interactive"])
        self.assertTrue(by_slug["bayern"]["visual_active"])
        self.assertIn("cadastre_raster", by_slug["bayern"]["rendering"])

    def test_xml_service_exception_is_rejected_and_not_cached(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch.object(main, "LUFTBILD_CACHE_DIR", Path(temp_dir)),
                patch.object(
                    main.urllib.request,
                    "urlopen",
                    return_value=_FakeResponse(b"<ServiceException/>", "text/xml"),
                ),
            ):
                with self.assertRaises(HTTPException) as raised:
                    main.katasterbild_tile("bayern", 18, 139501, 90975)
            self.assertFalse(any(Path(temp_dir).rglob("*.png")))

        self.assertEqual(raised.exception.status_code, 502)


if __name__ == "__main__":
    unittest.main()
