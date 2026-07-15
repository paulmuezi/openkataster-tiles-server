from __future__ import annotations

import json
import logging
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from openkataster_tiles.search_analytics import (
    SearchAnalytics,
    QuerylessUvicornAccessFilter,
    public_result_summary,
    query_key_for,
    sanitize_text,
    valid_analytics_marker,
)


class SearchAnalyticsTests(unittest.TestCase):
    def make_store(self, root: Path, **kwargs) -> SearchAnalytics:
        return SearchAnalytics(root / "search_analytics.sqlite", busy_timeout_ms=50, **kwargs)

    def test_marker_requires_explicit_valid_get(self) -> None:
        marker = "019f6e96-40aa-7a8e-a8ee-4f17fb38ed90"
        self.assertTrue(valid_analytics_marker("GET", marker, "address"))
        self.assertFalse(valid_analytics_marker("get", "01J1X7J8M0JX8RJQWVBWZ0H33H", "map_selection"))
        self.assertFalse(valid_analytics_marker("HEAD", marker, "address"))
        self.assertFalse(valid_analytics_marker("GET", "too-short", "address"))
        self.assertFalse(valid_analytics_marker("GET", marker, "autocomplete"))
        self.assertFalse(valid_analytics_marker("GET", None, "street"))

    def test_uvicorn_access_filter_removes_only_the_query_string(self) -> None:
        record = logging.LogRecord(
            "uvicorn.access",
            logging.INFO,
            __file__,
            1,
            '%s - "%s %s HTTP/%s" %d',
            (
                "127.0.0.1:1234",
                "GET",
                "/api/v1/search/address?q=Hauptstra%C3%9Fe&token=secret",
                "1.1",
                200,
            ),
            None,
        )
        self.assertTrue(QuerylessUvicornAccessFilter().filter(record))
        self.assertEqual(record.args[2], "/api/v1/search/address")
        self.assertEqual(record.args[0], "127.0.0.1:1234")
        self.assertEqual(record.args[4], 200)

    def test_query_sanitizing_is_bounded_and_stable(self) -> None:
        cleaned = sanitize_text("  Haupt\x00straße\n  Köln  " + "x" * 300)
        self.assertNotIn("\x00", cleaned)
        self.assertNotIn("\n", cleaned)
        self.assertLessEqual(len(cleaned), 240)
        self.assertEqual(query_key_for("Hauptstraße Köln"), "hauptstrasse koln")

    def test_public_summary_never_traverses_private_feature_fields(self) -> None:
        payload = {
            "lon": 8.123456,
            "lat": 50.123456,
            "parcels": [
                {
                    "label": "Flur 4, Flurstück 12/3",
                    "gml_id": "DE_PRIVATE_GML_ID",
                    "geometry": {"coordinates": [8.123456, 50.123456]},
                }
            ],
            "buildings": [{"address": "Hauptstraße 1", "source_db": "secret.sqlite"}],
        }
        result_count, counts, labels, types = public_result_summary(payload, "map_selection")
        self.assertEqual(result_count, 2)
        self.assertEqual(counts, {"total": 2, "building": 1, "parcel": 1})
        self.assertEqual(labels, ["Flur 4, Flurstück 12/3", "Hauptstraße 1"])
        self.assertEqual(types, ["parcel", "building"])
        serialized = json.dumps([counts, labels, types])
        self.assertNotIn("DE_PRIVATE_GML_ID", serialized)
        self.assertNotIn("8.123456", serialized)
        self.assertNotIn("secret.sqlite", serialized)

    def test_records_categories_results_and_privacy_safe_recent_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = self.make_store(root)
            now = time.time()
            self.assertTrue(
                store.record_response(
                    scope="address",
                    query_text="  Hauptstraße 1\nKöln ",
                    state="Nordrhein-Westfalen",
                    payload={
                        "results": [
                            {"label": "Hauptstraße 1, Köln", "kind": "address", "gml_id": "DO_NOT_STORE"},
                            {"label": "Hauptstraße 1a, Köln", "kind": "address"},
                            {"label": "Hauptstraße 1b, Köln", "kind": "address"},
                            {"label": "Hauptstraße 1c, Köln", "kind": "address"},
                        ]
                    },
                    access_mode="pro",
                    latency_ms=12.6,
                    occurred_at=now,
                )
            )
            self.assertTrue(
                store.record_response(
                    scope="parcel",
                    query_text="Köln 4 999/1",
                    state="Nordrhein-Westfalen",
                    payload={"results": []},
                    access_mode="free",
                    latency_ms=7,
                    occurred_at=now,
                )
            )
            self.assertFalse(
                store.record_response(
                    scope="map_selection",
                    query_text="8.123456, 50.123456 DE_GML_SECRET",
                    state="",
                    payload={
                        "lon": 8.123456,
                        "lat": 50.123456,
                        "count": 2,
                        "parcels": [{"label": "Flurstück 12/3", "gml_id": "DE_GML_SECRET"}],
                        "buildings": [{"label": "Gebäude", "gml_id": "DE_GML_SECRET_2"}],
                    },
                    access_mode="partner",
                    latency_ms=20,
                    occurred_at=now,
                )
            )

            # Historical map-selection rows may remain in the database, but
            # they are no longer part of search-quality totals or lists.
            day = time.strftime("%Y-%m-%d", time.gmtime(now))
            with closing(sqlite3.connect(store.db_path)) as connection:
                connection.execute(
                    """
                    INSERT INTO search_events(
                      occurred_at, day, category, scope, query_text, query_key,
                      state, outcome, result_count, counts_json, labels_json,
                      types_json, access_mode, latency_ms
                    ) VALUES (?, ?, 'object', 'map_selection', 'Kartenauswahl',
                      'map_selection', '', 'found', 2, '{}', '[]', '[]', 'pro', 3)
                    """,
                    (int(now), day),
                )
                connection.execute(
                    """
                    INSERT INTO search_daily(
                      day, category, scope, outcome, searches, result_count_sum,
                      latency_ms_sum, updated_at
                    ) VALUES (?, 'object', 'map_selection', 'found', 1, 2, 3, ?)
                    """,
                    (day, int(now)),
                )

            dashboard = store.dashboard("1d")
            self.assertTrue(dashboard["available"])
            self.assertEqual(dashboard["period"]["window"], "rolling")
            self.assertEqual(dashboard["stats"]["total_searches"], 2)
            self.assertEqual(dashboard["stats"]["found"], 1)
            self.assertEqual(dashboard["stats"]["not_found"], 1)
            by_category = {row["category"]: row for row in dashboard["stats"]["by_category"]}
            self.assertEqual(by_category["address"]["searches"], 1)
            self.assertEqual(by_category["parcel"]["searches"], 1)
            self.assertNotIn("object", by_category)
            self.assertTrue(all(row["category"] != "object" for row in dashboard["recent"]))
            self.assertEqual(dashboard["pagination"]["total_items"], 2)
            self.assertEqual(dashboard["retention"]["raw_rows"], 2)
            self.assertEqual(dashboard["retention"]["aggregate_rows"], 2)
            daily = dashboard["stats"]["daily"][0]
            self.assertEqual(daily["searches"], 2)
            self.assertEqual(daily["address_searches"], 1)
            self.assertEqual(daily["parcel_searches"], 1)
            self.assertEqual(len(next(row for row in dashboard["recent"] if row["category"] == "address")["labels"]), 3)
            serialized = json.dumps(dashboard, ensure_ascii=False)
            self.assertNotIn("DE_GML_SECRET", serialized)
            self.assertNotIn("8.123456", serialized)

            with closing(sqlite3.connect(store.db_path)) as connection:
                daily_columns = {row[1] for row in connection.execute("PRAGMA table_info(search_daily)")}
                self.assertEqual(
                    daily_columns,
                    {
                        "day",
                        "category",
                        "scope",
                        "outcome",
                        "searches",
                        "result_count_sum",
                        "latency_ms_sum",
                        "updated_at",
                    },
                )
                raw_dump = " ".join(str(value) for row in connection.execute("SELECT * FROM search_events") for value in row)
                self.assertNotIn("DE_GML_SECRET", raw_dump)
                self.assertNotIn("8.123456", raw_dump)

    def test_recent_searches_use_server_side_numbered_pages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(Path(tmp))
            now = time.time()
            for index in range(28):
                self.assertTrue(
                    store.record_response(
                        scope="address",
                        query_text=f"Teststraße {index}",
                        state="hessen",
                        payload={"results": [{"label": f"Teststraße {index}"}]},
                        access_mode="free",
                        latency_ms=1,
                        occurred_at=now + index,
                    )
                )

            payload = store.dashboard("1d", page=2, per_page=25)
            self.assertEqual(payload["pagination"], {
                "page": 2,
                "per_page": 25,
                "total_items": 28,
                "total_pages": 2,
            })
            self.assertEqual(len(payload["recent"]), 3)

    def test_hourly_timeline_keeps_search_categories_separate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(Path(tmp))
            now = int(time.time())
            for scope in ("address", "parcel"):
                self.assertTrue(
                    store.record_response(
                        scope=scope,
                        query_text=f"Suche {scope}",
                        state="hessen",
                        payload={"results": []},
                        access_mode="free",
                        latency_ms=1,
                        occurred_at=now,
                    )
                )

            row = store.dashboard("1d", bucket="hour")["stats"]["daily"][0]
            self.assertRegex(row["timestamp"], r"T\d{2}:00:00Z$")
            self.assertEqual(row["searches"], 2)
            self.assertEqual(row["address_searches"], 1)
            self.assertEqual(row["parcel_searches"], 1)

    def test_long_range_timeline_uses_start_of_day(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(Path(tmp))
            self.assertTrue(
                store.record_response(
                    scope="address",
                    query_text="Morgensuche",
                    state="hessen",
                    payload={"results": []},
                    access_mode="free",
                    latency_ms=1,
                )
            )

            row = store.dashboard("90d", bucket="day")["stats"]["daily"][-1]
            self.assertRegex(row["timestamp"], r"T00:00:00Z$")

    def test_rolling_day_excludes_older_raw_but_long_aggregate_keeps_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(Path(tmp))
            now = time.time()
            old = now - 25 * 3_600
            store.record_response(
                scope="address",
                query_text="Gesternstraße 1",
                state="hessen",
                payload={"results": []},
                access_mode="free",
                latency_ms=1,
                occurred_at=old,
            )
            store.record_response(
                scope="address",
                query_text="Heuteweg 1",
                state="hessen",
                payload={"results": [{"label": "Heuteweg 1", "kind": "address"}]},
                access_mode="free",
                latency_ms=1,
                occurred_at=now,
            )
            self.assertEqual(store.dashboard("1d")["stats"]["total_searches"], 1)
            self.assertEqual(store.dashboard("90d")["stats"]["total_searches"], 2)

    def test_top_misses_are_limited_to_raw_retention(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(Path(tmp), raw_days=30, aggregate_days=400)
            now = time.time()
            old = now - 45 * 86_400
            store.record_response(
                scope="parcel",
                query_text="Alte Gemarkung 1 999/9",
                state="bayern",
                payload={"results": []},
                access_mode="free",
                latency_ms=4,
                occurred_at=old,
            )
            store._last_cleanup = 0.0
            with patch("openkataster_tiles.search_analytics.time.monotonic", return_value=5.0):
                store.record_response(
                    scope="parcel",
                    query_text="Neue Gemarkung 1 888/8",
                    state="bayern",
                    payload={"results": []},
                    access_mode="free",
                    latency_ms=4,
                    occurred_at=now,
                )
            dashboard = store.dashboard("90d")
            self.assertEqual(dashboard["stats"]["total_searches"], 2)
            misses = {row["query"] for row in dashboard["top_misses"]}
            self.assertIn("Neue Gemarkung 1 888/8", misses)
            self.assertNotIn("Alte Gemarkung 1 999/9", misses)
            self.assertEqual(dashboard["retention"]["raw_rows"], 1)

    def test_raw_hard_cap_and_fail_open(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = self.make_store(root, raw_hard_cap=100)
            now = time.time()
            for index in range(105):
                self.assertTrue(
                    store.record_response(
                        scope="place",
                        query_text=f"Ort {index}",
                        state="",
                        payload={"results": []},
                        access_mode="free",
                        latency_ms=1,
                        occurred_at=now + index,
                    )
                )
            self.assertEqual(store.dashboard("1d")["retention"]["raw_rows"], 100)

            invalid_path = root / "is-a-directory.sqlite"
            invalid_path.mkdir()
            broken = SearchAnalytics(invalid_path)
            self.assertFalse(
                broken.record_response(
                    scope="address",
                    query_text="Teststraße 1",
                    state="",
                    payload={"results": []},
                    access_mode="free",
                    latency_ms=1,
                )
            )
            self.assertFalse(broken.dashboard("30d")["available"])


if __name__ == "__main__":
    unittest.main()
