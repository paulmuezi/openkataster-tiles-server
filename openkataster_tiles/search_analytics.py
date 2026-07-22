from __future__ import annotations

import json
import logging
import math
import os
import re
import sqlite3
import threading
import time
import unicodedata
from collections import Counter
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping


ANALYTICS_CATEGORIES = ("address", "parcel", "poi")
ANALYTICS_SCOPES = ("place", "street", "address", "parcel", "poi")
SCOPE_CATEGORY = {
    "place": "address",
    "street": "address",
    "address": "address",
    "parcel": "parcel",
    "poi": "poi",
}
_ANALYTICS_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{15,79}$")
_STATE_RE = re.compile(r"[^a-z0-9_-]+")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]+")
_WHITESPACE_RE = re.compile(r"\s+")
_QUERY_KEY_RE = re.compile(r"[^a-z0-9]+")
_PUBLIC_TYPES = {
    "address",
    "building",
    "feature",
    "object",
    "parcel",
    "place",
    "poi",
    "street",
}


class QuerylessUvicornAccessFilter(logging.Filter):
    """Remove query strings before Uvicorn formats an access-log record.

    Search terms and temporary viewer tokens can otherwise end up in the
    general-purpose container logs.  The method, route path, client address
    and status code stay available for operations and incident diagnosis.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            path = args[2].split("?", 1)[0]
            if path != args[2]:
                record.args = (*args[:2], path, *args[3:])
        return True


def install_queryless_uvicorn_access_logging() -> None:
    logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(item, QuerylessUvicornAccessFilter) for item in logger.filters):
        logger.addFilter(QuerylessUvicornAccessFilter())


def _environment_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def valid_analytics_marker(method: str, analytics_id: str | None, scope: str | None) -> bool:
    """Return true only for explicitly marked, completed GET interactions.

    The id is deliberately only a request marker. It is validated here but is
    never written to the analytics database.
    """

    return (
        method.upper() == "GET"
        and isinstance(analytics_id, str)
        and bool(_ANALYTICS_ID_RE.fullmatch(analytics_id))
        and scope in SCOPE_CATEGORY
    )


def sanitize_text(value: Any, max_length: int = 240) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = _CONTROL_RE.sub(" ", text)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text[:max_length].rstrip()


def query_key_for(value: Any) -> str:
    text = sanitize_text(value)
    folded = unicodedata.normalize("NFKD", text.casefold())
    folded = "".join(char for char in folded if not unicodedata.combining(char))
    return _QUERY_KEY_RE.sub(" ", folded).strip()[:240]


def sanitize_state(value: Any) -> str:
    folded = unicodedata.normalize("NFKD", sanitize_text(value, 80).casefold())
    folded = "".join(char for char in folded if not unicodedata.combining(char))
    return _STATE_RE.sub("-", folded).strip("-_")[:80]


def sanitize_access_mode(value: Any) -> str:
    mode = sanitize_text(value, 24).casefold()
    return mode if mode in {"free", "pro", "partner", "api"} else "unknown"


def parse_period_days(value: Any, maximum: int = 400) -> int:
    raw = sanitize_text(value, 12).casefold() or "30d"
    if raw.endswith("d"):
        raw = raw[:-1]
    try:
        days = int(raw)
    except (TypeError, ValueError):
        days = 30
    return max(1, min(maximum, days))


def _public_type(item: Mapping[str, Any], fallback: str) -> str:
    for key in ("result_type", "kind", "type"):
        value = sanitize_text(item.get(key), 24).casefold()
        if value in _PUBLIC_TYPES:
            return value
    return fallback if fallback in _PUBLIC_TYPES else "object"


def _public_label(item: Mapping[str, Any], fallback: str) -> str:
    for key in ("label", "value", "name", "address"):
        value = sanitize_text(item.get(key), 160)
        if value:
            return value
    return fallback


def public_result_summary(payload: Mapping[str, Any], scope: str) -> tuple[int, dict[str, int], list[str], list[str]]:
    """Extract only fields already intended for display in the public viewer.

    In particular, this function never traverses geometry, feature references,
    GML ids, coordinates, tokens, or request metadata.
    """

    entries: list[tuple[Mapping[str, Any], str, str]] = []
    if scope == "map_selection":
        parcels = payload.get("parcels")
        buildings = payload.get("buildings")
        for item in parcels if isinstance(parcels, list) else []:
            if isinstance(item, Mapping):
                entries.append((item, "parcel", "Flurstück"))
        for item in buildings if isinstance(buildings, list) else []:
            if isinstance(item, Mapping):
                entries.append((item, "building", "Gebäude"))
    else:
        results = payload.get("results")
        fallback_type = {
            "place": "place",
            "street": "street",
            "address": "address",
            "parcel": "parcel",
            "poi": "poi",
        }.get(scope, "object")
        fallback_label = {
            "place": "Ort",
            "street": "Straße",
            "address": "Adresse",
            "parcel": "Flurstück",
            "poi": "Ort von Interesse",
        }.get(scope, "Treffer")
        for item in results if isinstance(results, list) else []:
            if isinstance(item, Mapping):
                entries.append((item, fallback_type, fallback_label))

    type_counts: Counter[str] = Counter()
    labels: list[str] = []
    types: list[str] = []
    for item, fallback_type, fallback_label in entries:
        item_type = _public_type(item, fallback_type)
        type_counts[item_type] += 1
        if len(labels) < 3:
            labels.append(_public_label(item, fallback_label))
            types.append(item_type)

    explicit_count = payload.get("count")
    if isinstance(explicit_count, int) and explicit_count >= 0:
        result_count = explicit_count
    else:
        result_count = len(entries)
    counts = {"total": result_count, **dict(sorted(type_counts.items()))}
    return result_count, counts, labels, types


def infer_public_state(payload: Mapping[str, Any]) -> str:
    """Infer a state only when all public results identify the same state."""

    states: set[str] = set()
    result_groups = [payload.get("results"), payload.get("parcels"), payload.get("buildings")]
    for group in result_groups:
        for item in group if isinstance(group, list) else []:
            if not isinstance(item, Mapping):
                continue
            state = sanitize_state(item.get("state"))
            if state:
                states.add(state)
            if len(states) > 1:
                return ""
    return next(iter(states), "")


class SearchAnalytics:
    def __init__(
        self,
        db_path: str | Path,
        *,
        raw_days: int = 30,
        aggregate_days: int = 400,
        raw_hard_cap: int = 250_000,
        aggregate_hard_cap: int = 500_000,
        busy_timeout_ms: int = 350,
    ) -> None:
        self.db_path = Path(db_path)
        self.raw_days = max(1, int(raw_days))
        self.aggregate_days = max(self.raw_days, int(aggregate_days))
        self.raw_hard_cap = max(100, int(raw_hard_cap))
        self.aggregate_hard_cap = max(100, int(aggregate_hard_cap))
        self.busy_timeout_ms = max(1, min(5_000, int(busy_timeout_ms)))
        self._initialized = False
        self._initialize_lock = threading.Lock()
        self._cleanup_lock = threading.Lock()
        self._last_cleanup = 0.0

    @classmethod
    def from_environment(cls, data_dir: str | Path) -> "SearchAnalytics":
        default_path = Path(data_dir) / "search_analytics.sqlite"
        return cls(
            os.environ.get("OPENKATASTER_SEARCH_ANALYTICS_DB") or str(default_path),
            raw_days=_environment_int("OPENKATASTER_SEARCH_ANALYTICS_RAW_DAYS", 30),
            aggregate_days=_environment_int("OPENKATASTER_SEARCH_ANALYTICS_AGGREGATE_DAYS", 400),
            raw_hard_cap=_environment_int("OPENKATASTER_SEARCH_ANALYTICS_RAW_HARD_CAP", 250_000),
            aggregate_hard_cap=_environment_int("OPENKATASTER_SEARCH_ANALYTICS_AGGREGATE_HARD_CAP", 500_000),
            busy_timeout_ms=_environment_int("OPENKATASTER_SEARCH_ANALYTICS_BUSY_TIMEOUT_MS", 350),
        )

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            self.db_path,
            timeout=self.busy_timeout_ms / 1_000,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
        return connection

    @staticmethod
    def _table_sql(connection: sqlite3.Connection, table: str) -> str:
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        return str(row[0] or "") if row is not None else ""

    @staticmethod
    def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
        return {
            str(row[1])
            for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }

    @staticmethod
    def _schema_supports_poi(sql: str) -> bool:
        normalized = _WHITESPACE_RE.sub(" ", sql.casefold())
        for column in ("category", "scope"):
            match = re.search(
                rf"check\s*\(\s*[`\"\[]?{column}[`\"\]]?\s+in\s*\(([^)]*)\)",
                normalized,
            )
            if match is None or re.search(r"['\"]poi['\"]", match.group(1)) is None:
                return False
        return True

    @staticmethod
    def _create_events_table(connection: sqlite3.Connection, table: str) -> None:
        if table not in {"search_events", "search_events__poi_migration"}:
            raise ValueError("unexpected analytics event table")
        connection.execute(
            f"""
            CREATE TABLE {table} (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              occurred_at INTEGER NOT NULL,
              day TEXT NOT NULL,
              category TEXT NOT NULL CHECK(category IN ('address', 'parcel', 'object', 'poi')),
              scope TEXT NOT NULL CHECK(scope IN ('place', 'street', 'address', 'parcel', 'map_selection', 'poi')),
              query_text TEXT NOT NULL,
              query_key TEXT NOT NULL,
              state TEXT NOT NULL DEFAULT '',
              outcome TEXT NOT NULL CHECK(outcome IN ('found', 'not_found')),
              result_count INTEGER NOT NULL DEFAULT 0,
              counts_json TEXT NOT NULL DEFAULT '{{}}',
              labels_json TEXT NOT NULL DEFAULT '[]',
              types_json TEXT NOT NULL DEFAULT '[]',
              access_mode TEXT NOT NULL,
              latency_ms INTEGER NOT NULL DEFAULT 0
            )
            """
        )

    @staticmethod
    def _create_daily_table(connection: sqlite3.Connection, table: str) -> None:
        if table not in {"search_daily", "search_daily__poi_migration"}:
            raise ValueError("unexpected analytics daily table")
        connection.execute(
            f"""
            CREATE TABLE {table} (
              day TEXT NOT NULL,
              category TEXT NOT NULL CHECK(category IN ('address', 'parcel', 'object', 'poi')),
              scope TEXT NOT NULL CHECK(scope IN ('place', 'street', 'address', 'parcel', 'map_selection', 'poi')),
              outcome TEXT NOT NULL CHECK(outcome IN ('found', 'not_found')),
              searches INTEGER NOT NULL DEFAULT 0,
              result_count_sum INTEGER NOT NULL DEFAULT 0,
              latency_ms_sum INTEGER NOT NULL DEFAULT 0,
              updated_at INTEGER NOT NULL,
              PRIMARY KEY(day, category, scope, outcome)
            ) WITHOUT ROWID
            """
        )

    def _migrate_events_for_poi(self, connection: sqlite3.Connection) -> None:
        required = {
            "id",
            "occurred_at",
            "day",
            "category",
            "scope",
            "query_text",
            "query_key",
            "state",
            "outcome",
            "result_count",
            "counts_json",
            "labels_json",
            "types_json",
            "access_mode",
            "latency_ms",
        }
        if not required.issubset(self._table_columns(connection, "search_events")):
            raise sqlite3.DatabaseError("unsupported search_events schema")
        connection.execute("DROP TABLE IF EXISTS search_events__poi_migration")
        self._create_events_table(connection, "search_events__poi_migration")
        connection.execute(
            """
            INSERT INTO search_events__poi_migration(
              id, occurred_at, day, category, scope, query_text, query_key, state,
              outcome, result_count, counts_json, labels_json, types_json,
              access_mode, latency_ms
            )
            SELECT id, occurred_at, day, category, scope, query_text, query_key, state,
              outcome, result_count, counts_json, labels_json, types_json,
              access_mode, latency_ms
            FROM search_events
            ORDER BY id
            """
        )
        connection.execute("DROP TABLE search_events")
        connection.execute(
            "ALTER TABLE search_events__poi_migration RENAME TO search_events"
        )

    def _migrate_daily_for_poi(self, connection: sqlite3.Connection) -> None:
        required = {
            "day",
            "category",
            "scope",
            "outcome",
            "searches",
            "result_count_sum",
            "latency_ms_sum",
            "updated_at",
        }
        if not required.issubset(self._table_columns(connection, "search_daily")):
            raise sqlite3.DatabaseError("unsupported search_daily schema")
        connection.execute("DROP TABLE IF EXISTS search_daily__poi_migration")
        self._create_daily_table(connection, "search_daily__poi_migration")
        # Early development databases used query-level aggregate keys. Grouping
        # here both preserves every numeric counter and removes those raw fields
        # from the long-lived aggregate schema.
        connection.execute(
            """
            INSERT INTO search_daily__poi_migration(
              day, category, scope, outcome, searches, result_count_sum,
              latency_ms_sum, updated_at
            )
            SELECT day, category, scope, outcome, SUM(searches),
              SUM(result_count_sum), SUM(latency_ms_sum), MAX(updated_at)
            FROM search_daily
            GROUP BY day, category, scope, outcome
            """
        )
        connection.execute("DROP TABLE search_daily")
        connection.execute(
            "ALTER TABLE search_daily__poi_migration RENAME TO search_daily"
        )

    def initialize(self) -> bool:
        if self._initialized:
            return True
        with self._initialize_lock:
            if self._initialized:
                return True
            try:
                with closing(self._connect()) as connection:
                    connection.execute("PRAGMA journal_mode=WAL")
                    connection.execute("PRAGMA synchronous=NORMAL")
                    # CHECK constraints cannot be widened in place in SQLite.
                    # An immediate transaction serializes this migration across
                    # API worker processes. The table swaps are transactional,
                    # so another worker sees either the complete old schema or
                    # the complete new schema, never a partially copied table.
                    connection.execute("BEGIN IMMEDIATE")
                    try:
                        events_sql = self._table_sql(connection, "search_events")
                        if not events_sql:
                            self._create_events_table(connection, "search_events")
                        elif not self._schema_supports_poi(events_sql):
                            self._migrate_events_for_poi(connection)

                        daily_sql = self._table_sql(connection, "search_daily")
                        daily_columns = self._table_columns(connection, "search_daily")
                        private_daily_columns = {
                            "query_text",
                            "query_key",
                            "state",
                            "access_mode",
                            "labels_json",
                            "types_json",
                        }
                        if not daily_sql:
                            self._create_daily_table(connection, "search_daily")
                        elif (
                            not self._schema_supports_poi(daily_sql)
                            or bool(daily_columns & private_daily_columns)
                        ):
                            self._migrate_daily_for_poi(connection)

                        connection.execute(
                            """
                            CREATE INDEX IF NOT EXISTS search_events_time_idx
                              ON search_events(occurred_at DESC)
                            """
                        )
                        connection.execute(
                            """
                            CREATE INDEX IF NOT EXISTS search_events_miss_idx
                              ON search_events(outcome, query_key, occurred_at DESC)
                            """
                        )
                        connection.execute(
                            """
                            CREATE INDEX IF NOT EXISTS search_daily_day_idx
                              ON search_daily(day DESC)
                            """
                        )
                        connection.execute(
                            """
                            CREATE INDEX IF NOT EXISTS search_daily_miss_idx
                              ON search_daily(outcome, day DESC, searches DESC)
                            """
                        )
                        connection.execute("COMMIT")
                    except BaseException:
                        connection.execute("ROLLBACK")
                        raise
                self._initialized = True
                return True
            except (OSError, sqlite3.Error, ValueError):
                return False

    def record_response(
        self,
        *,
        scope: str,
        query_text: str,
        state: str,
        payload: Mapping[str, Any],
        access_mode: str,
        latency_ms: float,
        occurred_at: float | None = None,
    ) -> bool:
        category = SCOPE_CATEGORY.get(scope)
        if not category or not isinstance(payload, Mapping):
            return False
        if not self.initialize():
            return False

        # A point lookup is represented by a fixed semantic label. Coordinates
        # and feature identifiers are never accepted as analytics input.
        cleaned_query = "Kartenauswahl" if scope == "map_selection" else sanitize_text(query_text)
        query_key = "map_selection" if scope == "map_selection" else query_key_for(cleaned_query)
        if not cleaned_query or not query_key:
            return False
        result_count, counts, labels, types = public_result_summary(payload, scope)
        cleaned_state = sanitize_state(state) or infer_public_state(payload)
        timestamp = max(0, int(occurred_at if occurred_at is not None else time.time()))
        day = datetime.fromtimestamp(timestamp, timezone.utc).date().isoformat()
        outcome = "found" if result_count > 0 else "not_found"
        latency = max(0, min(300_000, int(round(float(latency_ms or 0)))))
        mode = sanitize_access_mode(access_mode)
        counts_json = json.dumps(counts, ensure_ascii=False, separators=(",", ":"))
        labels_json = json.dumps(labels[:3], ensure_ascii=False, separators=(",", ":"))
        types_json = json.dumps(types[:3], ensure_ascii=False, separators=(",", ":"))

        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                event_cursor = connection.execute(
                    """
                    INSERT INTO search_events(
                      occurred_at, day, category, scope, query_text, query_key, state,
                      outcome, result_count, counts_json, labels_json, types_json,
                      access_mode, latency_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        timestamp,
                        day,
                        category,
                        scope,
                        cleaned_query,
                        query_key,
                        cleaned_state,
                        outcome,
                        result_count,
                        counts_json,
                        labels_json,
                        types_json,
                        mode,
                        latency,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO search_daily(
                      day, category, scope, outcome, searches, result_count_sum,
                      latency_ms_sum, updated_at
                    ) VALUES (?, ?, ?, ?, 1, ?, ?, ?)
                    ON CONFLICT(day, category, scope, outcome)
                    DO UPDATE SET
                      searches = search_daily.searches + 1,
                      result_count_sum = search_daily.result_count_sum + excluded.result_count_sum,
                      latency_ms_sum = search_daily.latency_ms_sum + excluded.latency_ms_sum,
                      updated_at = excluded.updated_at
                    """,
                    (
                        day,
                        category,
                        scope,
                        outcome,
                        result_count,
                        latency,
                        timestamp,
                    ),
                )
                # Always enforce caps in the same transaction. Retention cleanup
                # below may run less often, but the database cannot grow past them.
                raw_id_cutoff = int(event_cursor.lastrowid or 0) - self.raw_hard_cap
                if raw_id_cutoff > 0:
                    # AUTOINCREMENT ids have no more than one live row per id;
                    # retaining the newest `cap` id range therefore enforces a
                    # strict upper bound without an O(cap) OFFSET scan per hit.
                    connection.execute("DELETE FROM search_events WHERE id <= ?", (raw_id_cutoff,))
                aggregate_count = int(connection.execute("SELECT COUNT(*) FROM search_daily").fetchone()[0])
                if aggregate_count > self.aggregate_hard_cap:
                    connection.execute(
                        """
                        DELETE FROM search_daily
                        WHERE (day, category, scope, outcome) IN (
                          SELECT day, category, scope, outcome
                          FROM search_daily
                          ORDER BY day ASC, updated_at ASC
                          LIMIT ?
                        )
                        """,
                        (aggregate_count - self.aggregate_hard_cap,),
                    )
                cleanup_clock = time.monotonic()
                cleanup_due = self._last_cleanup <= 0.0 or cleanup_clock - self._last_cleanup >= 3600
                if cleanup_due and self._cleanup_lock.acquire(blocking=False):
                    try:
                        raw_cutoff = timestamp - self.raw_days * 86_400
                        aggregate_cutoff = (datetime.fromtimestamp(timestamp, timezone.utc).date() - timedelta(days=self.aggregate_days - 1)).isoformat()
                        connection.execute("DELETE FROM search_events WHERE occurred_at < ?", (raw_cutoff,))
                        connection.execute("DELETE FROM search_daily WHERE day < ?", (aggregate_cutoff,))
                        self._last_cleanup = cleanup_clock
                    finally:
                        self._cleanup_lock.release()
                connection.execute("COMMIT")
            return True
        except (OSError, sqlite3.Error, TypeError, ValueError):
            return False

    def _empty_dashboard(
        self,
        days: int,
        *,
        available: bool,
        page: int = 1,
        per_page: int = 100,
    ) -> dict[str, Any]:
        today = datetime.now(timezone.utc).date()
        start = today - timedelta(days=days - 1)
        category_rows = [self._stat_row("category", value, None) for value in ANALYTICS_CATEGORIES]
        scope_rows = [self._stat_row("scope", value, SCOPE_CATEGORY[value]) for value in ANALYTICS_SCOPES]
        return {
            "available": available,
            "period": {"days": days, "from": start.isoformat(), "to": today.isoformat()},
            "stats": {
                "total_searches": 0,
                "found": 0,
                "not_found": 0,
                "success_rate": 0.0,
                "avg_latency_ms": 0.0,
                "avg_results": 0.0,
                "by_category": category_rows,
                "by_scope": scope_rows,
                "daily": [],
            },
            "recent": [],
            "top_misses": [],
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total_items": 0,
                "total_pages": 1,
            },
            "retention": {
                "raw_days": self.raw_days,
                "aggregate_days": self.aggregate_days,
                "raw_hard_cap": self.raw_hard_cap,
                "aggregate_hard_cap": self.aggregate_hard_cap,
                "raw_rows": 0,
                "raw_oldest": None,
                "raw_newest": None,
                "aggregate_rows": 0,
                "aggregate_oldest": None,
                "aggregate_newest": None,
            },
        }

    @staticmethod
    def _stat_row(name: str, value: str, category: str | None, row: Mapping[str, Any] | None = None) -> dict[str, Any]:
        row = dict(row) if row is not None else {}
        searches = int(row.get("searches") or 0)
        found = int(row.get("found") or 0)
        latency_sum = int(row.get("latency_sum") or 0)
        result_sum = int(row.get("result_sum") or 0)
        result = {
            name: value,
            "searches": searches,
            "found": found,
            "not_found": searches - found,
            "success_rate": round(found / searches, 4) if searches else 0.0,
            "avg_latency_ms": round(latency_sum / searches, 1) if searches else 0.0,
            "avg_results": round(result_sum / searches, 2) if searches else 0.0,
        }
        if category is not None:
            result["category"] = category
        return result

    @staticmethod
    def _iso_timestamp(value: Any) -> str | None:
        if value is None:
            return None
        return datetime.fromtimestamp(int(value), timezone.utc).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _json_list(value: Any) -> list[Any]:
        try:
            parsed = json.loads(str(value or "[]"))
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
        return parsed[:3] if isinstance(parsed, list) else []

    @staticmethod
    def _json_dict(value: Any) -> dict[str, Any]:
        try:
            parsed = json.loads(str(value or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def dashboard(
        self,
        period: Any = "30d",
        *,
        page: int = 1,
        per_page: int = 100,
        bucket: str = "day",
        timeline_from: int | None = None,
    ) -> dict[str, Any]:
        days = parse_period_days(period, self.aggregate_days)
        safe_page = max(1, int(page or 1))
        safe_per_page = max(1, min(100, int(per_page or 100)))
        safe_bucket = "hour" if bucket == "hour" else "day"
        if not self.initialize():
            return self._empty_dashboard(
                days,
                available=False,
                page=safe_page,
                per_page=safe_per_page,
            )
        today = datetime.now(timezone.utc).date()
        start = today - timedelta(days=days - 1)
        start_day = start.isoformat()
        base = self._empty_dashboard(
            days,
            available=True,
            page=safe_page,
            per_page=safe_per_page,
        )
        now_timestamp = int(time.time())
        raw_cutoff = now_timestamp - days * 86_400
        timeline_cutoff = (
            max(0, int(timeline_from))
            if timeline_from is not None
            else raw_cutoff
        )
        use_raw_stats = days <= self.raw_days
        base["period"]["window"] = "rolling" if use_raw_stats else "utc_days"
        if use_raw_stats:
            base["period"]["from_timestamp"] = self._iso_timestamp(raw_cutoff)
        try:
            with closing(self._connect()) as connection:
                if use_raw_stats:
                    totals_sql = """
                        SELECT COUNT(*) AS searches,
                          COALESCE(SUM(CASE WHEN outcome = 'found' THEN 1 ELSE 0 END), 0) AS found,
                          COALESCE(SUM(result_count), 0) AS result_sum,
                          COALESCE(SUM(latency_ms), 0) AS latency_sum
                        FROM search_events
                        WHERE occurred_at >= ? AND category IN ('address', 'parcel', 'poi')
                    """
                    category_sql = """
                        SELECT category, COUNT(*) AS searches,
                          SUM(CASE WHEN outcome = 'found' THEN 1 ELSE 0 END) AS found,
                          SUM(result_count) AS result_sum, SUM(latency_ms) AS latency_sum
                        FROM search_events
                        WHERE occurred_at >= ? AND category IN ('address', 'parcel', 'poi')
                        GROUP BY category
                    """
                    scope_sql = """
                        SELECT scope, COUNT(*) AS searches,
                          SUM(CASE WHEN outcome = 'found' THEN 1 ELSE 0 END) AS found,
                          SUM(result_count) AS result_sum, SUM(latency_ms) AS latency_sum
                        FROM search_events
                        WHERE occurred_at >= ? AND category IN ('address', 'parcel', 'poi')
                        GROUP BY scope
                    """
                    timeline_key = (
                        "strftime('%Y-%m-%dT%H:00:00Z', occurred_at, 'unixepoch')"
                        if safe_bucket == "hour"
                        else "day || 'T00:00:00Z'"
                    )
                    daily_sql = f"""
                        SELECT {timeline_key} AS timestamp, COUNT(*) AS searches,
                          SUM(CASE WHEN category = 'address' THEN 1 ELSE 0 END) AS address_searches,
                          SUM(CASE WHEN category = 'parcel' THEN 1 ELSE 0 END) AS parcel_searches,
                          SUM(CASE WHEN category = 'poi' THEN 1 ELSE 0 END) AS poi_searches,
                          SUM(CASE WHEN outcome = 'found' THEN 1 ELSE 0 END) AS found
                        FROM search_events
                        WHERE occurred_at >= ? AND occurred_at <= ?
                          AND category IN ('address', 'parcel', 'poi')
                        GROUP BY timestamp ORDER BY timestamp
                    """
                    stats_parameter: str | int = raw_cutoff
                    timeline_parameters: tuple[str | int, ...] = (
                        timeline_cutoff,
                        now_timestamp,
                    )
                else:
                    totals_sql = """
                        SELECT COALESCE(SUM(searches), 0) AS searches,
                          COALESCE(SUM(CASE WHEN outcome = 'found' THEN searches ELSE 0 END), 0) AS found,
                          COALESCE(SUM(result_count_sum), 0) AS result_sum,
                          COALESCE(SUM(latency_ms_sum), 0) AS latency_sum
                        FROM search_daily
                        WHERE day >= ? AND category IN ('address', 'parcel', 'poi')
                    """
                    category_sql = """
                        SELECT category, SUM(searches) AS searches,
                          SUM(CASE WHEN outcome = 'found' THEN searches ELSE 0 END) AS found,
                          SUM(result_count_sum) AS result_sum, SUM(latency_ms_sum) AS latency_sum
                        FROM search_daily
                        WHERE day >= ? AND category IN ('address', 'parcel', 'poi')
                        GROUP BY category
                    """
                    scope_sql = """
                        SELECT scope, SUM(searches) AS searches,
                          SUM(CASE WHEN outcome = 'found' THEN searches ELSE 0 END) AS found,
                          SUM(result_count_sum) AS result_sum, SUM(latency_ms_sum) AS latency_sum
                        FROM search_daily
                        WHERE day >= ? AND category IN ('address', 'parcel', 'poi')
                        GROUP BY scope
                    """
                    daily_sql = """
                        SELECT day || 'T00:00:00Z' AS timestamp, SUM(searches) AS searches,
                          SUM(CASE WHEN category = 'address' THEN searches ELSE 0 END) AS address_searches,
                          SUM(CASE WHEN category = 'parcel' THEN searches ELSE 0 END) AS parcel_searches,
                          SUM(CASE WHEN category = 'poi' THEN searches ELSE 0 END) AS poi_searches,
                          SUM(CASE WHEN outcome = 'found' THEN searches ELSE 0 END) AS found
                        FROM search_daily
                        WHERE day >= ? AND category IN ('address', 'parcel', 'poi')
                        GROUP BY day ORDER BY day
                    """
                    stats_parameter = start_day
                    timeline_parameters = (start_day,)

                totals = connection.execute(totals_sql, (stats_parameter,)).fetchone()
                total_searches = int(totals["searches"] or 0)
                found = int(totals["found"] or 0)
                base["stats"].update(
                    {
                        "total_searches": total_searches,
                        "found": found,
                        "not_found": total_searches - found,
                        "success_rate": round(found / total_searches, 4) if total_searches else 0.0,
                        "avg_latency_ms": round(int(totals["latency_sum"] or 0) / total_searches, 1) if total_searches else 0.0,
                        "avg_results": round(int(totals["result_sum"] or 0) / total_searches, 2) if total_searches else 0.0,
                    }
                )

                grouped_categories = {
                    row["category"]: row
                    for row in connection.execute(category_sql, (stats_parameter,))
                }
                base["stats"]["by_category"] = [
                    self._stat_row("category", category, None, grouped_categories.get(category))
                    for category in ANALYTICS_CATEGORIES
                ]

                grouped_scopes = {
                    row["scope"]: row
                    for row in connection.execute(scope_sql, (stats_parameter,))
                }
                base["stats"]["by_scope"] = [
                    self._stat_row("scope", scope, SCOPE_CATEGORY[scope], grouped_scopes.get(scope))
                    for scope in ANALYTICS_SCOPES
                ]

                base["stats"]["daily"] = [
                    {
                        "timestamp": row["timestamp"],
                        "searches": int(row["searches"] or 0),
                        "address_searches": int(row["address_searches"] or 0),
                        "parcel_searches": int(row["parcel_searches"] or 0),
                        "poi_searches": int(row["poi_searches"] or 0),
                        "found": int(row["found"] or 0),
                        "not_found": int(row["searches"] or 0) - int(row["found"] or 0),
                    }
                    for row in connection.execute(daily_sql, timeline_parameters)
                ]

                recent_total = int(
                    connection.execute(
                        """
                        SELECT COUNT(*)
                        FROM search_events
                        WHERE occurred_at >= ? AND category IN ('address', 'parcel', 'poi')
                        """,
                        (raw_cutoff,),
                    ).fetchone()[0]
                )
                total_pages = max(1, math.ceil(recent_total / safe_per_page))
                safe_page = min(safe_page, total_pages)
                base["pagination"] = {
                    "page": safe_page,
                    "per_page": safe_per_page,
                    "total_items": recent_total,
                    "total_pages": total_pages,
                }

                base["recent"] = [
                    {
                        "at": self._iso_timestamp(row["occurred_at"]),
                        "category": row["category"],
                        "scope": row["scope"],
                        "query": row["query_text"],
                        "query_key": row["query_key"],
                        "state": row["state"],
                        "outcome": row["outcome"],
                        "result_count": int(row["result_count"] or 0),
                        "counts": self._json_dict(row["counts_json"]),
                        "labels": self._json_list(row["labels_json"]),
                        "types": self._json_list(row["types_json"]),
                        "access_mode": row["access_mode"],
                        "latency_ms": int(row["latency_ms"] or 0),
                    }
                    for row in connection.execute(
                        """
                        SELECT occurred_at, category, scope, query_text, query_key, state,
                          outcome, result_count, counts_json, labels_json, types_json,
                          access_mode, latency_ms
                        FROM search_events
                        WHERE occurred_at >= ? AND category IN ('address', 'parcel', 'poi')
                        ORDER BY occurred_at DESC, id DESC LIMIT ? OFFSET ?
                        """,
                        (
                            raw_cutoff,
                            safe_per_page,
                            (safe_page - 1) * safe_per_page,
                        ),
                    )
                ]

                base["top_misses"] = [
                    {
                        "category": row["category"],
                        "scope": row["scope"],
                        "query": row["query_text"],
                        "query_key": row["query_key"],
                        "state": row["state"],
                        "searches": int(row["searches"] or 0),
                        "last_seen": self._iso_timestamp(row["last_seen"]),
                    }
                    for row in connection.execute(
                        """
                        SELECT category, scope, query_text, query_key, state,
                          COUNT(*) AS searches, MAX(occurred_at) AS last_seen
                        FROM search_events
                        WHERE occurred_at >= ? AND outcome = 'not_found'
                          AND category IN ('address', 'parcel', 'poi')
                        GROUP BY category, scope, query_key, state
                        ORDER BY searches DESC, last_seen DESC, query_key ASC
                        LIMIT 50
                        """,
                        (now_timestamp - min(days, self.raw_days) * 86_400,),
                    )
                ]

                raw_retention = connection.execute(
                    """
                    SELECT COUNT(*) AS rows, MIN(occurred_at) AS oldest, MAX(occurred_at) AS newest
                    FROM search_events WHERE category IN ('address', 'parcel', 'poi')
                    """
                ).fetchone()
                aggregate_retention = connection.execute(
                    """
                    SELECT COUNT(*) AS rows, MIN(day) AS oldest, MAX(day) AS newest
                    FROM search_daily WHERE category IN ('address', 'parcel', 'poi')
                    """
                ).fetchone()
                base["retention"].update(
                    {
                        "raw_rows": int(raw_retention["rows"] or 0),
                        "raw_oldest": self._iso_timestamp(raw_retention["oldest"]),
                        "raw_newest": self._iso_timestamp(raw_retention["newest"]),
                        "aggregate_rows": int(aggregate_retention["rows"] or 0),
                        "aggregate_oldest": aggregate_retention["oldest"],
                        "aggregate_newest": aggregate_retention["newest"],
                    }
                )
            return base
        except (OSError, sqlite3.Error, TypeError, ValueError):
            return self._empty_dashboard(
                days,
                available=False,
                page=safe_page,
                per_page=safe_per_page,
            )
