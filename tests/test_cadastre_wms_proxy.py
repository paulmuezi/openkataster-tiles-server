from __future__ import annotations

import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from openkataster_tiles import main


class _FakeResponse:
    def __init__(self, data: bytes = b"\x89PNG\r\n\x1a\nOpenKataster", content_type: str = "image/png") -> None:
        self.data = data
        self.headers = {"Content-Type": content_type}

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.data


class CadastreWmsProxyTests(unittest.TestCase):
    def test_saxony_anhalt_request_is_fully_server_configured_and_cached(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch.object(main, "LUFTBILD_CACHE_DIR", Path(temp_dir)),
                patch.object(main, "LUFTBILD_MIN_FREE_BYTES", 0),
                patch.object(main, "_LUFTBILD_CACHE_LAST_CLEANUP", 0.0),
                patch.object(main.urllib.request, "urlopen", return_value=_FakeResponse()) as urlopen,
            ):
                miss = main.katasterbild_tile("sachsen-anhalt", 18, 139538, 86447)
                hit = main.katasterbild_tile("sachsen-anhalt", 18, 139538, 86447)

        self.assertEqual(miss.status_code, 200)
        self.assertEqual(miss.headers["x-openkataster-cache"], "MISS")
        self.assertEqual(hit.headers["x-openkataster-cache"], "HIT")
        self.assertEqual(urlopen.call_count, 1)

        request = urlopen.call_args.args[0]
        params = urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query)
        self.assertEqual(params["CRS"], ["EPSG:3857"])
        self.assertEqual(params["WIDTH"], ["512"])
        self.assertEqual(params["HEIGHT"], ["512"])
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

    def test_bavaria_request_uses_larger_screen_labels_without_changing_tile_geometry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch.object(main, "LUFTBILD_CACHE_DIR", Path(temp_dir)),
                patch.object(main, "LUFTBILD_MIN_FREE_BYTES", 0),
                patch.object(main, "_LUFTBILD_CACHE_LAST_CLEANUP", 0.0),
                patch.object(main.urllib.request, "urlopen", return_value=_FakeResponse()) as urlopen,
            ):
                response = main.katasterbild_tile("bayern", 18, 139440, 90047)

        self.assertEqual(response.status_code, 200)
        request = urlopen.call_args.args[0]
        params = urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query)
        self.assertEqual(params["CRS"], ["EPSG:3857"])
        self.assertEqual(params["WIDTH"], ["512"])
        self.assertEqual(params["HEIGHT"], ["512"])
        self.assertEqual(params["DPI"], ["120"])
        self.assertEqual(params["LAYERS"], ["by_alkis_parzellarkarte_farbe"])
        self.assertEqual(params["STYLES"], ["Farbe"])
        self.assertIn("screen-dpi120", main.KATASTER_WMS_CONFIGS["bayern"]["revision"])
        self.assertIn("screen-dpi120", main.KATASTER_WMS_CONFIGS["sachsen-anhalt"]["revision"])

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
                patch.object(main.urllib.request, "urlopen", return_value=_FakeResponse(b"<ServiceException/>", "text/xml")),
            ):
                with self.assertRaises(HTTPException) as raised:
                    main.katasterbild_tile("bayern", 18, 139501, 90975)
            self.assertFalse(any(Path(temp_dir).rglob("*.png")))

        self.assertEqual(raised.exception.status_code, 502)


if __name__ == "__main__":
    unittest.main()
