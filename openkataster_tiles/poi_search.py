"""Read-only runtime access to the versioned OpenStreetMap POI index.

The index is built offline and activated with an atomic symlink switch.  This
module deliberately owns its SQLite connection so a broken or missing optional
POI index can never take the existing ALKIS search down with it.
"""

from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import threading
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


DEFAULT_POI_DB_PATH = Path(
    "/srv/openkataster-tiles/poi/active/osm-poi.sqlite"
)
POI_DB_ENV = "OPENKATASTER_POI_DB"
LEGACY_POI_DB_ENV = "OPENKATASTER_OSM_POI_DB"
POI_SEARCH_ENABLED_ENV = "OPENKATASTER_POI_SEARCH_ENABLED"

MAX_CANDIDATES = 80
MAX_RESULTS = 80
MAX_QUERY_LENGTH = 256
MAX_QUERY_TOKENS = 10
NEAR_RADIUS_KM = 50.0
LOCAL_CANDIDATE_SHARE = 0.75

_OSM_TYPE_NAMES = {
    "n": "node",
    "w": "way",
    "r": "relation",
}

_STATE_LABELS = {
    "baden-wurttemberg": "Baden-Württemberg",
    "bayern": "Bayern",
    "berlin": "Berlin",
    "brandenburg": "Brandenburg",
    "bremen": "Bremen",
    "hamburg": "Hamburg",
    "hessen": "Hessen",
    "mecklenburg-vorpommern": "Mecklenburg-Vorpommern",
    "niedersachsen": "Niedersachsen",
    "nordrhein-westfalen": "Nordrhein-Westfalen",
    "rheinland-pfalz": "Rheinland-Pfalz",
    "saarland": "Saarland",
    "sachsen": "Sachsen",
    "sachsen-anhalt": "Sachsen-Anhalt",
    "schleswig-holstein": "Schleswig-Holstein",
    "thueringen": "Thüringen",
}

_CATEGORY_LABELS = {
    "amenity": "Einrichtung",
    "community": "Gemeinschaft",
    "culture": "Kultur",
    "education": "Bildung",
    "emergency": "Notfall",
    "finance": "Finanzen",
    "food": "Gastronomie",
    "healthcare": "Gesundheit",
    "landmark": "Sehenswürdigkeit",
    "leisure": "Freizeit",
    "natural": "Natur",
    "religion": "Religion",
    "retail": "Einkaufen",
    "services": "Dienstleistungen",
    "tourism": "Tourismus",
    "transport": "Verkehr",
}

_REQUIRED_POI_COLUMNS = {
    "id",
    "name",
    "name_norm",
    "search_norm",
    "aliases",
    "brand",
    "operator",
    "category",
    "class_key",
    "subtype",
    "category_terms",
    "address",
    "address_norm",
    "street",
    "housenumber",
    "postcode",
    "city",
    "state",
    "lon",
    "lat",
    "quality",
}
_REQUIRED_SOURCE_COLUMNS = {"poi_id", "osm_type", "osm_id"}
_REQUIRED_RTREE_COLUMNS = {
    "id",
    "min_lon",
    "max_lon",
    "min_lat",
    "max_lat",
}


@dataclass(frozen=True)
class _IndexSnapshot:
    path: Path
    target: Path | None
    value: tuple[object, ...]


@dataclass(frozen=True)
class _IndexSchema:
    poi_columns: frozenset[str]
    has_rtree: bool
    has_meta: bool


@dataclass
class _ConnectionState:
    signature: tuple[object, ...]
    connection: sqlite3.Connection
    schema: _IndexSchema


_LOCK = threading.RLock()
_CONNECTION_STATE: _ConnectionState | None = None
_FAILED_SIGNATURE: tuple[object, ...] | None = None


def _feature_enabled() -> bool:
    value = os.environ.get(POI_SEARCH_ENABLED_ENV, "1").strip().casefold()
    return value not in {"0", "false", "no", "off", "disabled"}


def _database_path() -> Path:
    raw_path = (
        os.environ.get(POI_DB_ENV)
        or os.environ.get(LEGACY_POI_DB_ENV)
        or str(DEFAULT_POI_DB_PATH)
    )
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path


def _snapshot() -> _IndexSnapshot:
    path = _database_path()
    prefix: tuple[object, ...] = (str(path), int(_feature_enabled()))
    if not _feature_enabled():
        return _IndexSnapshot(path=path, target=None, value=(*prefix, 0))
    try:
        link_stat = path.lstat()
        target = path.resolve(strict=True)
        target_stat = target.stat()
    except OSError:
        return _IndexSnapshot(path=path, target=None, value=(*prefix, 0))
    return _IndexSnapshot(
        path=path,
        target=target,
        value=(
            *prefix,
            1,
            link_stat.st_dev,
            link_stat.st_ino,
            link_stat.st_mtime_ns,
            link_stat.st_ctime_ns,
            target_stat.st_dev,
            target_stat.st_ino,
            target_stat.st_size,
            target_stat.st_mtime_ns,
            target_stat.st_ctime_ns,
        ),
    )


def poi_index_signature() -> tuple[object, ...]:
    """Return a cache key that changes after an atomic active-index switch."""

    return _snapshot().value


def _close_connection_locked() -> None:
    global _CONNECTION_STATE
    if _CONNECTION_STATE is None:
        return
    try:
        _CONNECTION_STATE.connection.close()
    except Exception:
        pass
    _CONNECTION_STATE = None


def _table_columns(
    connection: sqlite3.Connection, table: str
) -> frozenset[str]:
    rows = connection.execute(
        f"PRAGMA table_info({table})"  # table names are module constants
    ).fetchall()
    return frozenset(str(row[1]) for row in rows)


def _load_schema(connection: sqlite3.Connection) -> _IndexSchema:
    tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
        ).fetchall()
    }
    if not {"poi", "poi_source", "poi_fts"}.issubset(tables):
        raise sqlite3.DatabaseError("POI index is missing required tables")

    poi_columns = _table_columns(connection, "poi")
    source_columns = _table_columns(connection, "poi_source")
    if not _REQUIRED_POI_COLUMNS.issubset(poi_columns):
        raise sqlite3.DatabaseError("POI index has an incompatible poi table")
    if not _REQUIRED_SOURCE_COLUMNS.issubset(source_columns):
        raise sqlite3.DatabaseError(
            "POI index has an incompatible poi_source table"
        )

    # This is both a schema probe and a check that SQLite has FTS5 support.
    connection.execute(
        "SELECT rowid FROM poi_fts WHERE poi_fts MATCH ? LIMIT 0",
        ('"__openkataster_schema_probe__"',),
    ).fetchall()

    rtree_columns = (
        _table_columns(connection, "poi_rtree")
        if "poi_rtree" in tables
        else frozenset()
    )
    return _IndexSchema(
        poi_columns=poi_columns,
        has_rtree=_REQUIRED_RTREE_COLUMNS.issubset(rtree_columns),
        has_meta="meta" in tables
        and {"key", "value"}.issubset(_table_columns(connection, "meta")),
    )


def _connection_locked() -> _ConnectionState | None:
    global _CONNECTION_STATE, _FAILED_SIGNATURE

    snapshot = _snapshot()
    if snapshot.target is None:
        _close_connection_locked()
        _FAILED_SIGNATURE = snapshot.value
        return None
    if (
        _CONNECTION_STATE is not None
        and _CONNECTION_STATE.signature == snapshot.value
    ):
        return _CONNECTION_STATE
    if _FAILED_SIGNATURE == snapshot.value:
        return None

    _close_connection_locked()
    connection: sqlite3.Connection | None = None
    try:
        uri = f"{snapshot.target.as_uri()}?mode=ro&immutable=1"
        connection = sqlite3.connect(
            uri,
            uri=True,
            timeout=5.0,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        connection.execute("PRAGMA mmap_size = 536870912")
        connection.execute("PRAGMA cache_size = -65536")
        connection.execute("PRAGMA temp_store = MEMORY")
        schema = _load_schema(connection)
    except (OSError, sqlite3.Error, ValueError):
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass
        _FAILED_SIGNATURE = snapshot.value
        return None

    _FAILED_SIGNATURE = None
    _CONNECTION_STATE = _ConnectionState(
        signature=snapshot.value,
        connection=connection,
        schema=schema,
    )
    return _CONNECTION_STATE


def _invalidate_connection_locked() -> None:
    global _FAILED_SIGNATURE
    snapshot_value = (
        _CONNECTION_STATE.signature
        if _CONNECTION_STATE is not None
        else _snapshot().value
    )
    _close_connection_locked()
    _FAILED_SIGNATURE = snapshot_value


def poi_index_available() -> bool:
    """Return whether a valid, enabled POI index can currently be queried."""

    if not _feature_enabled():
        return False
    with _LOCK:
        return _connection_locked() is not None


def poi_index_metadata() -> dict:
    """Return non-sensitive build metadata; missing/invalid indices yield `{}`."""

    if not _feature_enabled():
        return {}
    with _LOCK:
        state = _connection_locked()
        if state is None or not state.schema.has_meta:
            return {}
        try:
            rows = state.connection.execute(
                "SELECT key, value FROM meta ORDER BY key"
            ).fetchall()
        except sqlite3.Error:
            _invalidate_connection_locked()
            return {}

    metadata: dict[str, object] = {}
    for row in rows:
        key = str(row["key"])
        raw_value = row["value"]
        try:
            metadata[key] = json.loads(str(raw_value))
        except (TypeError, ValueError, json.JSONDecodeError):
            metadata[key] = raw_value
    return metadata


def _normalize_text(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    text = "".join(
        character
        for character in text
        if not unicodedata.combining(character)
    )
    return " ".join(
        re.sub(r"[^\w]+", " ", text, flags=re.UNICODE).split()
    )


def _fts_expression(query: object) -> tuple[str, str]:
    raw_query = str(query or "").strip()[:MAX_QUERY_LENGTH]
    tokens = re.findall(
        r"[^\W_]+",
        unicodedata.normalize("NFKC", raw_query).casefold(),
        flags=re.UNICODE,
    )
    normalized_tokens: list[str] = []
    for token in tokens:
        normalized = _normalize_text(token)[:64]
        if not normalized or normalized in normalized_tokens:
            continue
        normalized_tokens.append(normalized)
        if len(normalized_tokens) >= MAX_QUERY_TOKENS:
            break
    normalized_query = _normalize_text(raw_query)
    if len(normalized_query.replace(" ", "")) < 2 or not normalized_tokens:
        return "", normalized_query
    # Quoting every lexical token prevents FTS operators, column selectors,
    # parentheses and wildcard-only input from becoming executable syntax.
    expression = " AND ".join(
        f'"{token.replace(chr(34), chr(34) * 2)}"*'
        for token in normalized_tokens
    )
    return expression, normalized_query


def _normalize_state(value: object) -> str:
    state = (
        str(value or "")
        .strip()
        .casefold()
        .replace("_", "-")
        .replace(" ", "-")
    )
    return {
        "baden-wuerttemberg": "baden-wurttemberg",
        "thuringen": "thueringen",
        "thüringen": "thueringen",
    }.get(state, state)


def _allowed_state_key(
    allowed_states: Iterable[str] | None,
) -> tuple[str, ...] | None:
    if allowed_states is None:
        return None
    if isinstance(allowed_states, str):
        values: Iterable[str] = (allowed_states,)
    else:
        values = allowed_states
    return tuple(
        sorted(
            {
                normalized
                for value in values
                if (normalized := _normalize_state(value))
            }
        )
    )


def _valid_near_point(
    near_lon: float | None, near_lat: float | None
) -> tuple[float, float] | None:
    try:
        lon = float(near_lon)  # type: ignore[arg-type]
        lat = float(near_lat)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if (
        not math.isfinite(lon)
        or not math.isfinite(lat)
        or not -180.0 <= lon <= 180.0
        or not -90.0 <= lat <= 90.0
    ):
        return None
    return lon, lat


def _source_select_sql() -> str:
    order = (
        "ORDER BY CASE ps.osm_type "
        "WHEN 'n' THEN 0 WHEN 'w' THEN 1 ELSE 2 END, ps.osm_id"
    )
    return f"""
        (SELECT ps.osm_type FROM poi_source ps
          WHERE ps.poi_id = p.id {order} LIMIT 1) AS osm_type,
        (SELECT ps.osm_id FROM poi_source ps
          WHERE ps.poi_id = p.id {order} LIMIT 1) AS osm_id
    """


def _candidate_select_sql(
    schema: _IndexSchema,
    *,
    include_distance_hint: bool,
    include_fts_rank: bool = True,
    source: str = "fts",
) -> str:
    if source == "fts":
        from_sql = """
        FROM poi_fts
        JOIN poi p ON p.id = poi_fts.rowid
        """
    elif source == "poi":
        from_sql = "FROM poi p"
    else:
        raise ValueError(f"Unsupported POI candidate source: {source}")
    category_label = (
        "p.category_label"
        if "category_label" in schema.poi_columns
        else "''"
    )
    city_norm = (
        "p.city_norm" if "city_norm" in schema.poi_columns else "''"
    )
    distance_hint = (
        ", ABS(p.lat - ?) + ABS(p.lon - ?) * ? AS _distance_hint"
        if include_distance_hint
        else ""
    )
    fts_rank = (
        "bm25(poi_fts)"
        if include_fts_rank and source == "fts"
        else "0.0"
    )
    return f"""
        SELECT
            p.id, p.name, p.name_norm, p.aliases, p.brand, p.operator,
            p.category, {category_label} AS category_label,
            p.class_key, p.subtype, p.category_terms,
            p.address, p.address_norm, p.street, p.housenumber,
            p.postcode, p.city, {city_norm} AS city_norm,
            p.state, p.lon, p.lat, p.quality,
            {_source_select_sql()},
            {fts_rank} AS _bm25
            {distance_hint}
        {from_sql}
    """


def _state_clause(
    states: tuple[str, ...] | None,
) -> tuple[str, list[object]]:
    if states is None:
        return "", []
    placeholders = ",".join("?" for _ in states)
    return f" AND p.state IN ({placeholders})", list(states)


def _near_bbox(lon: float, lat: float) -> tuple[float, float, float, float]:
    lat_delta = NEAR_RADIUS_KM / 111.32
    lon_km = max(111.32 * math.cos(math.radians(lat)), 1.0)
    lon_delta = NEAR_RADIUS_KM / lon_km
    return (
        max(-180.0, lon - lon_delta),
        min(180.0, lon + lon_delta),
        max(-90.0, lat - lat_delta),
        min(90.0, lat + lat_delta),
    )


def _fetch_candidates_locked(
    state: _ConnectionState,
    fts_expression: str,
    states: tuple[str, ...] | None,
    near: tuple[float, float] | None,
) -> list[sqlite3.Row]:
    state_sql, state_parameters = _state_clause(states)
    rows: list[sqlite3.Row] = []
    selected_ids: list[int] = []

    if near is not None and state.schema.has_rtree:
        near_lon, near_lat = near
        min_lon, max_lon, min_lat, max_lat = _near_bbox(
            near_lon, near_lat
        )
        local_limit = max(
            1, min(MAX_CANDIDATES, int(MAX_CANDIDATES * LOCAL_CANDIDATE_SHARE))
        )
        lon_scale = max(math.cos(math.radians(near_lat)), 0.01)
        local_sql = (
            _candidate_select_sql(
                state.schema, include_distance_hint=True
            )
            + """
            JOIN poi_rtree r ON r.id = p.id
            WHERE poi_fts MATCH ?
            """
            + state_sql
            + """
              AND r.min_lon <= ? AND r.max_lon >= ?
              AND r.min_lat <= ? AND r.max_lat >= ?
            ORDER BY _distance_hint ASC, _bm25 ASC, p.quality DESC
            LIMIT ?
            """
        )
        rows = state.connection.execute(
            local_sql,
            [
                near_lat,
                near_lon,
                lon_scale,
                fts_expression,
                *state_parameters,
                max_lon,
                min_lon,
                max_lat,
                min_lat,
                local_limit,
            ],
        ).fetchall()
        selected_ids = [int(row["id"]) for row in rows]

    remaining = MAX_CANDIDATES - len(rows)
    if remaining <= 0:
        return rows[:MAX_CANDIDATES]

    exclusion_sql = ""
    exclusion_parameters: list[object] = []
    if selected_ids:
        exclusion_sql = (
            f" AND p.id NOT IN ({','.join('?' for _ in selected_ids)})"
        )
        exclusion_parameters = selected_ids
    global_sql = (
        _candidate_select_sql(state.schema, include_distance_hint=False)
        + """
        WHERE poi_fts MATCH ?
        """
        + state_sql
        + exclusion_sql
        + """
        ORDER BY _bm25 ASC, p.quality DESC, p.name_norm, p.id
        LIMIT ?
        """
    )
    rows.extend(
        state.connection.execute(
            global_sql,
            [
                fts_expression,
                *state_parameters,
                *exclusion_parameters,
                remaining,
            ],
        ).fetchall()
    )
    return rows[:MAX_CANDIDATES]


def _prefix_successor(prefix: str) -> str:
    """Return the exclusive upper bound for a normalized text prefix."""

    for index in range(len(prefix) - 1, -1, -1):
        codepoint = ord(prefix[index])
        if codepoint < 0x10FFFF:
            return prefix[:index] + chr(codepoint + 1)
    raise ValueError("Prefix has no finite lexical successor")


def _fetch_name_prefix_candidates_locked(
    state: _ConnectionState,
    name_prefix: str,
    states: tuple[str, ...] | None,
    near: tuple[float, float] | None,
) -> list[sqlite3.Row]:
    """Fetch bounded two-character name matches without a broad FTS sort.

    The offline index has ``poi_state_name_idx(state, name_norm)``. Using its
    lexical prefix range avoids asking FTS5 to rank very large two-character
    posting lists while retaining the richer FTS path for longer or multi-word
    queries.
    """

    state_sql, state_parameters = _state_clause(states)
    upper_bound = _prefix_successor(name_prefix)
    rows: list[sqlite3.Row] = []
    selected_ids: list[int] = []

    if near is not None and state.schema.has_rtree:
        near_lon, near_lat = near
        min_lon, max_lon, min_lat, max_lat = _near_bbox(
            near_lon, near_lat
        )
        local_limit = max(
            1, min(MAX_CANDIDATES, int(MAX_CANDIDATES * LOCAL_CANDIDATE_SHARE))
        )
        lon_scale = max(math.cos(math.radians(near_lat)), 0.01)
        local_sql = (
            _candidate_select_sql(
                state.schema,
                include_distance_hint=True,
                include_fts_rank=False,
                source="poi",
            )
            + """
            JOIN poi_rtree r ON r.id = p.id
            WHERE 1 = 1
            """
            + state_sql
            + """
              AND p.name_norm >= ? AND p.name_norm < ?
              AND r.min_lon <= ? AND r.max_lon >= ?
              AND r.min_lat <= ? AND r.max_lat >= ?
            ORDER BY
              CASE WHEN p.name_norm = ? THEN 0 ELSE 1 END,
              _distance_hint ASC, p.quality DESC, p.name_norm, p.id
            LIMIT ?
            """
        )
        rows = state.connection.execute(
            local_sql,
            [
                near_lat,
                near_lon,
                lon_scale,
                *state_parameters,
                name_prefix,
                upper_bound,
                max_lon,
                min_lon,
                max_lat,
                min_lat,
                name_prefix,
                local_limit,
            ],
        ).fetchall()
        selected_ids = [int(row["id"]) for row in rows]

    remaining = MAX_CANDIDATES - len(rows)
    if remaining <= 0:
        return rows[:MAX_CANDIDATES]

    exclusion_sql = ""
    exclusion_parameters: list[object] = []
    if selected_ids:
        exclusion_sql = (
            f" AND p.id NOT IN ({','.join('?' for _ in selected_ids)})"
        )
        exclusion_parameters = selected_ids
    global_sql = (
        _candidate_select_sql(
            state.schema,
            include_distance_hint=False,
            include_fts_rank=False,
            source="poi",
        )
        + """
        WHERE 1 = 1
        """
        + state_sql
        + """
          AND p.name_norm >= ? AND p.name_norm < ?
        """
        + exclusion_sql
        + """
        ORDER BY
          CASE WHEN p.name_norm = ? THEN 0 ELSE 1 END,
          p.quality DESC, p.name_norm, p.id
        LIMIT ?
        """
    )
    rows.extend(
        state.connection.execute(
            global_sql,
            [
                *state_parameters,
                name_prefix,
                upper_bound,
                *exclusion_parameters,
                name_prefix,
                remaining,
            ],
        ).fetchall()
    )
    return rows[:MAX_CANDIDATES]


def _haversine_m(
    lon_a: float, lat_a: float, lon_b: float, lat_b: float
) -> float:
    radius_m = 6_371_008.8
    lat_a_rad = math.radians(lat_a)
    lat_b_rad = math.radians(lat_b)
    delta_lat = lat_b_rad - lat_a_rad
    delta_lon = math.radians(lon_b - lon_a)
    value = (
        math.sin(delta_lat / 2.0) ** 2
        + math.cos(lat_a_rad)
        * math.cos(lat_b_rad)
        * math.sin(delta_lon / 2.0) ** 2
    )
    return radius_m * 2.0 * math.asin(min(1.0, math.sqrt(value)))


def _text_rank(row: sqlite3.Row, normalized_query: str) -> int:
    name = _normalize_text(row["name_norm"] or row["name"])
    location_tokens = set(
        _normalize_text(
            " ".join(
                (
                    str(row["city_norm"] or row["city"] or ""),
                    str(row["postcode"] or ""),
                )
            )
        ).split()
    )
    original_query_tokens = normalized_query.split()
    semantic_query_tokens = [
        token for token in original_query_tokens if token not in location_tokens
    ]
    original_name_tokens = name.split()
    original_exact = name == normalized_query or bool(
        original_query_tokens
        and len(original_name_tokens) == len(original_query_tokens)
        and sorted(original_name_tokens) == sorted(original_query_tokens)
    )
    if semantic_query_tokens and len(semantic_query_tokens) < len(
        original_query_tokens
    ):
        rank_query = " ".join(semantic_query_tokens)
        rank_name_tokens = [
            token for token in name.split() if token not in location_tokens
        ]
        rank_name = " ".join(rank_name_tokens)
    else:
        rank_query = normalized_query
        rank_name = name
    aliases = [
        _normalize_text(value)
        for value in re.split(r"[\x1f;|]+", str(row["aliases"] or ""))
        if value.strip()
    ]
    secondary = [
        value
        for value in (
            *aliases,
            _normalize_text(row["brand"]),
            _normalize_text(row["operator"]),
        )
        if value
    ]
    query_tokens = rank_query.split()
    name_tokens = rank_name.split()
    if original_exact:
        return 0
    if (
        rank_name == rank_query
        or rank_query in secondary
        or normalized_query in secondary
    ):
        return 1
    if rank_name.startswith(rank_query):
        return 2
    if (
        query_tokens
        and len(name_tokens) == len(query_tokens)
        and sorted(name_tokens) == sorted(query_tokens)
    ):
        return 2
    category_tokens = set(
        _normalize_text(
            " ".join(
                (
                    str(row["category_terms"] or ""),
                    str(row["subtype"] or ""),
                    str(row["category"] or ""),
                )
            )
        ).split()
    )
    if query_tokens and all(token in category_tokens for token in query_tokens):
        return 3
    if any(
        value.startswith(rank_query) or value.startswith(normalized_query)
        for value in secondary
    ):
        return 4
    if query_tokens and all(token in name_tokens for token in query_tokens):
        return 5
    return 6


def _category_label(row: sqlite3.Row) -> str:
    explicit = str(row["category_label"] or "").strip()
    if explicit:
        return explicit
    category = str(row["category"] or "").strip()
    return _CATEGORY_LABELS.get(
        category, category.replace("_", " ").strip().title() or "Ort"
    )


def _format_result(
    row: sqlite3.Row,
    near: tuple[float, float] | None,
) -> dict | None:
    osm_type = str(row["osm_type"] or "").strip().casefold()
    try:
        osm_id = int(row["osm_id"])
        lon = float(row["lon"])
        lat = float(row["lat"])
    except (TypeError, ValueError):
        return None
    if osm_type not in _OSM_TYPE_NAMES:
        return None

    poi_id = f"osm:{osm_type}:{osm_id}"
    osm_type_name = _OSM_TYPE_NAMES[osm_type]
    source_url = (
        f"https://www.openstreetmap.org/{osm_type_name}/{osm_id}"
    )
    state = _normalize_state(row["state"])
    category_label = _category_label(row)
    address = str(row["address"] or "").strip()
    city_label = " ".join(
        part
        for part in (
            str(row["postcode"] or "").strip(),
            str(row["city"] or "").strip(),
        )
        if part
    )
    location_label = address or city_label
    subtitle = location_label

    feature = {
        "id": poi_id,
        "name": str(row["name"] or "").strip(),
        "category": str(row["category"] or "").strip(),
        "category_label": category_label,
        "class_key": str(row["class_key"] or "").strip(),
        "subtype": str(row["subtype"] or "").strip(),
        "brand": str(row["brand"] or "").strip(),
        "operator": str(row["operator"] or "").strip(),
        "address": address,
        "street": str(row["street"] or "").strip(),
        "house_number": str(row["housenumber"] or "").strip(),
        "post_code": str(row["postcode"] or "").strip(),
        "city": str(row["city"] or "").strip(),
        "state": state,
        "source": "OpenStreetMap",
        "source_url": source_url,
        "osm_type": osm_type_name,
        "osm_id": osm_id,
    }
    result = {
        "kind": "poi",
        "result_type": "poi",
        "search_scope": "poi",
        "poi_id": poi_id,
        "label": str(row["name"] or "").strip() or category_label,
        "primary_label": str(row["name"] or "").strip() or category_label,
        "secondary_label": subtitle,
        "subtitle": subtitle,
        "category": str(row["category"] or "").strip(),
        "category_label": category_label,
        "state": state,
        "state_label": _STATE_LABELS.get(
            state, state.replace("-", " ").title()
        ),
        "center": [lon, lat],
        "bbox": [lon, lat, lon, lat],
        "zoom": 17.0,
        "source": "OpenStreetMap",
        "source_url": source_url,
        "attribution": "© OpenStreetMap-Mitwirkende",
        "osm_type": osm_type_name,
        "osm_id": osm_id,
        "feature": feature,
    }
    if near is not None:
        result["distance_m"] = round(
            _haversine_m(near[0], near[1], lon, lat)
        )
    return result


def _duplicates_existing_result(result: dict, selected: list[dict]) -> bool:
    """Collapse nearby duplicate OSM representations without dropping data.

    A station, for example, is often mapped as a node, several ways and a
    relation.  The index retains every stable OSM object, while autocomplete
    presents one useful row. Only differing OSM geometry types of the same
    semantic class are collapsed at close range. Distinct branches, stops and
    structured street addresses remain selectable.
    """

    name = _normalize_text(result.get("label"))
    category = _normalize_text(result.get("category"))
    state = _normalize_state(result.get("state"))
    center = result.get("center")
    feature = result.get("feature")
    if (
        not name
        or not isinstance(center, list)
        or len(center) < 2
        or not isinstance(feature, dict)
    ):
        return False
    street = _normalize_text(feature.get("street"))
    house_number = _normalize_text(feature.get("house_number"))
    structured_address = (street, house_number) if street and house_number else None
    class_key = _normalize_text(feature.get("class_key"))
    osm_type = _normalize_text(feature.get("osm_type"))
    for previous in selected:
        if (
            _normalize_text(previous.get("label")) != name
            or _normalize_text(previous.get("category")) != category
            or _normalize_state(previous.get("state")) != state
        ):
            continue
        previous_feature = previous.get("feature")
        previous_center = previous.get("center")
        if (
            not isinstance(previous_feature, dict)
            or not isinstance(previous_center, list)
            or len(previous_center) < 2
        ):
            continue
        previous_street = _normalize_text(previous_feature.get("street"))
        previous_house_number = _normalize_text(
            previous_feature.get("house_number")
        )
        previous_structured_address = (
            (previous_street, previous_house_number)
            if previous_street and previous_house_number
            else None
        )
        previous_class_key = _normalize_text(previous_feature.get("class_key"))
        previous_osm_type = _normalize_text(previous_feature.get("osm_type"))
        if (
            not class_key
            or class_key != previous_class_key
            or not osm_type
            or not previous_osm_type
            or osm_type == previous_osm_type
        ):
            continue
        if (
            structured_address
            and previous_structured_address
            and structured_address != previous_structured_address
        ):
            continue
        try:
            distance = _haversine_m(
                float(center[0]),
                float(center[1]),
                float(previous_center[0]),
                float(previous_center[1]),
            )
        except (TypeError, ValueError):
            continue
        if distance <= 100.0:
            return True
    return False


def search_poi_suggestions(
    query: str,
    allowed_states: Iterable[str] | None,
    limit: int,
    near_lon: float | None = None,
    near_lat: float | None = None,
) -> list[dict]:
    """Search POIs with bounded prefix queries.

    Single two-character name prefixes use the state/name B-tree index. Richer
    queries use FTS5. The function never falls back to ``LIKE '%…%'``. It
    returns an empty list if the optional index is disabled, absent, switched
    mid-request, or incompatible.
    """

    if not _feature_enabled():
        return []
    fts_expression, normalized_query = _fts_expression(query)
    if not fts_expression:
        return []
    states = _allowed_state_key(allowed_states)
    if states == ():
        return []
    try:
        result_limit = max(0, min(int(limit), MAX_RESULTS))
    except (TypeError, ValueError):
        return []
    if result_limit == 0:
        return []
    near = _valid_near_point(near_lon, near_lat)
    use_short_name_prefix = (
        states is not None
        and len(normalized_query) == 2
        and normalized_query.isalnum()
    )

    with _LOCK:
        state = _connection_locked()
        if state is None:
            return []
        try:
            if use_short_name_prefix:
                rows = _fetch_name_prefix_candidates_locked(
                    state, normalized_query, states, near
                )
            else:
                rows = _fetch_candidates_locked(
                    state, fts_expression, states, near
                )
        except sqlite3.Error:
            _invalidate_connection_locked()
            return []

    ranked: list[tuple[tuple[object, ...], dict]] = []
    for row in rows:
        result = _format_result(row, near)
        if result is None:
            continue
        text_rank = _text_rank(row, normalized_query)
        bm25_score = float(row["_bm25"] or 0.0)
        quality = int(row["quality"] or 0)
        if near is None:
            ranking = (
                text_rank,
                -quality,
                bm25_score,
                _normalize_text(row["name"]),
                int(row["id"]),
            )
        else:
            ranking = (
                text_rank,
                int(result.get("distance_m") or 0),
                -quality,
                bm25_score,
                _normalize_text(row["name"]),
                int(row["id"]),
            )
        ranked.append((ranking, result))
    ranked.sort(key=lambda item: item[0])
    selected: list[dict] = []
    for _ranking, result in ranked:
        if _duplicates_existing_result(result, selected):
            continue
        selected.append(result)
        if len(selected) >= result_limit:
            break
    return selected


_POI_ID_PATTERN = re.compile(
    r"^(?:osm:)?(?P<type>[nwr])(?::)?(?P<id>[1-9]\d*)$",
    flags=re.IGNORECASE,
)


def search_poi_by_id(
    poi_id: str, allowed_states: Iterable[str] | None
) -> dict | None:
    """Resolve a stable ``osm:n|w|r:<id>`` identifier exactly."""

    if not _feature_enabled():
        return None
    match = _POI_ID_PATTERN.fullmatch(str(poi_id or "").strip())
    if match is None:
        return None
    osm_type = match.group("type").casefold()
    osm_id = int(match.group("id"))
    states = _allowed_state_key(allowed_states)
    if states == ():
        return None
    state_sql, state_parameters = _state_clause(states)

    with _LOCK:
        state = _connection_locked()
        if state is None:
            return None
        query = (
            _candidate_select_sql(
                state.schema,
                include_distance_hint=False,
                include_fts_rank=False,
                source="poi",
            )
            + """
            JOIN poi_source requested_source
              ON requested_source.poi_id = p.id
            WHERE requested_source.osm_type = ?
              AND requested_source.osm_id = ?
            """
            + state_sql
            + """
            LIMIT 1
            """
        )
        try:
            row = state.connection.execute(
                query,
                [osm_type, osm_id, *state_parameters],
            ).fetchone()
        except sqlite3.Error:
            _invalidate_connection_locked()
            return None
    return _format_result(row, None) if row is not None else None


def _reset_for_tests() -> None:
    """Close cached state between isolated fixture databases."""

    global _FAILED_SIGNATURE
    with _LOCK:
        _close_connection_locked()
        _FAILED_SIGNATURE = None
