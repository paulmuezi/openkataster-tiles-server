from __future__ import annotations

import sqlite3
import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from openkataster_tiles import main


LIVE_REFERENCES_AVAILABLE = (
    main.OPENPLZ_DB.exists()
    and (main.DATA_DIR / "sachsen.search.sqlite").exists()
    and (main.DATA_DIR / "baden-wurttemberg.search.sqlite").exists()
    and (main.DATA_DIR / "nordrhein-westfalen.search.sqlite").exists()
)

ADDRESS_FALLBACK_REFERENCES_AVAILABLE = (
    main.OPENPLZ_DB.exists()
    and (main.DATA_DIR / "baden-wurttemberg.search.sqlite").exists()
    and (main.DATA_DIR / "berlin.search.sqlite").exists()
    and (main.DATA_DIR / "bremen.search.sqlite").exists()
    and (main.DATA_DIR / "rheinland-pfalz.search.sqlite").exists()
)

GEMARKUNG_REFERENCES_AVAILABLE = all(
    (main.DATA_DIR / f"{state}.search.sqlite").exists()
    for state in (
        "baden-wurttemberg",
        "bremen",
        "rheinland-pfalz",
        "schleswig-holstein",
    )
)

CENTRAL_ADDRESS_REFERENCES_AVAILABLE = (
    main.OPENPLZ_DB.exists()
    and all(
        (main.DATA_DIR / f"{state}.search.sqlite").exists()
        for state in (
            "baden-wurttemberg",
            "berlin",
            "brandenburg",
            "bremen",
            "mecklenburg-vorpommern",
            "niedersachsen",
            "nordrhein-westfalen",
            "rheinland-pfalz",
            "saarland",
            "schleswig-holstein",
            "thueringen",
        )
    )
)

MIXED_PARCEL_REFERENCES_AVAILABLE = all(
    (main.DATA_DIR / f"{state}.search.sqlite").exists()
    for state in ("baden-wurttemberg", "niedersachsen", "sachsen")
)


class SearchRuntimeFixTests(unittest.TestCase):
    def unified_address_suggestions_from_fixture(
        self,
        query: str,
        rows_by_state: dict[
            str,
            list[tuple[str, str, str, str, str, int, float, float]],
        ],
        *,
        limit: int = 8,
    ) -> dict:
        """Run unified suggestions against production-shaped search shards."""
        with TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            entries = []
            for state, fixture_rows in rows_by_state.items():
                path = directory / f"{state}.search.sqlite"
                connection = sqlite3.connect(path)
                connection.executescript(
                    """
                    CREATE TABLE address_lookup (
                        id INTEGER PRIMARY KEY,
                        feature_kind TEXT NOT NULL,
                        source_db TEXT NOT NULL,
                        gml_id TEXT NOT NULL,
                        street_norm TEXT NOT NULL,
                        street_label TEXT NOT NULL,
                        house_number_norm TEXT NOT NULL,
                        house_number_label TEXT NOT NULL,
                        city_norm TEXT NOT NULL,
                        city_label TEXT NOT NULL,
                        post_code TEXT NOT NULL,
                        label TEXT NOT NULL,
                        lon REAL,
                        lat REAL,
                        min_lon REAL NOT NULL,
                        max_lon REAL NOT NULL,
                        min_lat REAL NOT NULL,
                        max_lat REAL NOT NULL
                    );
                    CREATE INDEX idx_address_no_city
                        ON address_lookup(street_norm, house_number_norm);

                    CREATE TABLE street_lookup (
                        id INTEGER PRIMARY KEY,
                        street_norm TEXT NOT NULL,
                        street_label TEXT NOT NULL,
                        city_norm TEXT NOT NULL,
                        city_label TEXT NOT NULL,
                        post_code TEXT NOT NULL,
                        label TEXT NOT NULL,
                        address_count INTEGER NOT NULL,
                        feature_count INTEGER NOT NULL,
                        lon REAL,
                        lat REAL,
                        min_lon REAL NOT NULL,
                        max_lon REAL NOT NULL,
                        min_lat REAL NOT NULL,
                        max_lat REAL NOT NULL
                    );
                    CREATE INDEX idx_street_no_city ON street_lookup(street_norm);
                    """
                )
                for (
                    street_norm,
                    street_label,
                    city_norm,
                    city_label,
                    post_code,
                    address_count,
                    lon,
                    lat,
                ) in fixture_rows:
                    locality = " ".join(
                        part for part in (post_code, city_label) if part
                    )
                    label = (
                        f"{street_label}, {locality}" if locality else street_label
                    )
                    connection.execute(
                        """
                        INSERT INTO street_lookup (
                            street_norm, street_label, city_norm, city_label,
                            post_code, label, address_count, feature_count,
                            lon, lat, min_lon, max_lon, min_lat, max_lat
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            street_norm,
                            street_label,
                            city_norm,
                            city_label,
                            post_code,
                            label,
                            address_count,
                            address_count,
                            lon,
                            lat,
                            lon - 0.01,
                            lon + 0.01,
                            lat - 0.01,
                            lat + 0.01,
                        ),
                    )
                connection.commit()
                connection.close()
                entries.append(main.FeatureDbEntry(name=state, path=path))
            entries.sort(key=lambda entry: entry.name)
            states = {entry.name for entry in entries}
            signature = tuple(
                (entry.name, str(entry.path), *main.sqlite_file_signature(entry.path))
                for entry in entries
            )
            cached_function = getattr(
                main,
                "search_unified_address_suggestions_cached",
                None,
            )
            if cached_function is not None and hasattr(cached_function, "cache_clear"):
                cached_function.cache_clear()
            try:
                with (
                    patch.object(
                        main,
                        "search_suggestion_states_for_dataset",
                        return_value=states,
                    ),
                    patch.object(
                        main,
                        "active_bucket_state_keys",
                        return_value=tuple(sorted(states)),
                    ),
                    patch.object(
                        main,
                        "search_db_entries_for_states",
                        return_value=tuple(entries),
                    ),
                    patch.object(
                        main,
                        "search_db_signature_for_states",
                        return_value=signature,
                    ),
                    patch.object(
                        main,
                        "openplz_signature",
                        return_value=(0, 0),
                    ),
                ):
                    return main.search_unified_address_suggestions_for_dataset(
                        "deutschland",
                        query,
                        limit,
                    )
            finally:
                if cached_function is not None and hasattr(cached_function, "cache_clear"):
                    cached_function.cache_clear()
                for entry in entries:
                    cached = main._SEARCH_DB_CONNECTIONS.pop(str(entry.path), None)
                    if cached:
                        cached[1].close()

    def gemarkung_suggestions_from_fixture(
        self,
        query: str,
        limit: int,
        rows_by_state: dict[str, list[tuple[str, str, str, int]]],
    ) -> tuple[dict, ...]:
        with TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            entries = []
            for state, fixture_rows in rows_by_state.items():
                path = directory / f"{state}.search.sqlite"
                connection = sqlite3.connect(path)
                connection.execute(
                    """
                    CREATE TABLE parcel_lookup (
                        gemarkung_norm TEXT NOT NULL,
                        gemarkung_label TEXT NOT NULL,
                        gemarkungsnummer TEXT NOT NULL
                    )
                    """
                )
                for gemarkung_norm, label, number, parcel_count in fixture_rows:
                    connection.executemany(
                        "INSERT INTO parcel_lookup VALUES (?, ?, ?)",
                        [(gemarkung_norm, label, number)] * parcel_count,
                    )
                connection.commit()
                connection.close()
                entries.append(main.FeatureDbEntry(name=state, path=path))
            entries.sort(key=lambda entry: entry.name)
            signature = tuple(
                (entry.name, str(entry.path), *main.sqlite_file_signature(entry.path))
                for entry in entries
            )
            main.search_gemarkung_suggestions_cached.cache_clear()
            try:
                with patch.object(main, "search_db_entries_for_states", return_value=tuple(entries)):
                    return main.search_gemarkung_suggestions_cached(
                        query,
                        limit,
                        tuple(entry.name for entry in entries),
                        signature,
                    )
            finally:
                main.search_gemarkung_suggestions_cached.cache_clear()
                for entry in entries:
                    cached = main._SEARCH_DB_CONNECTIONS.pop(str(entry.path), None)
                    if cached:
                        cached[1].close()

    def parcel_suggestions_from_fixture(
        self,
        query: str,
        rows_by_state: dict[str, list[dict]],
        *,
        limit: int = 12,
        municipality: str = "",
        located_municipality: str | None = None,
    ) -> dict:
        """Run free-text parcel parsing against production-shaped parcel_lookup shards."""
        with TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            entries = []
            for state, fixture_rows in rows_by_state.items():
                path = directory / f"{state}.search.sqlite"
                connection = sqlite3.connect(path)
                connection.executescript(
                    """
                    CREATE TABLE parcel_lookup (
                        id INTEGER PRIMARY KEY,
                        source_db TEXT NOT NULL,
                        gml_id TEXT NOT NULL,
                        gemarkung_norm TEXT NOT NULL,
                        gemarkung_label TEXT NOT NULL,
                        gemarkungsnummer TEXT NOT NULL,
                        flur_norm TEXT NOT NULL,
                        flur_label TEXT NOT NULL,
                        flurstueck_norm TEXT NOT NULL,
                        flurstueck_label TEXT NOT NULL,
                        zaehler TEXT NOT NULL,
                        nenner TEXT NOT NULL,
                        amtliche_flaeche_m2 REAL,
                        lon REAL,
                        lat REAL,
                        min_lon REAL NOT NULL,
                        max_lon REAL NOT NULL,
                        min_lat REAL NOT NULL,
                        max_lat REAL NOT NULL,
                        UNIQUE(source_db, gml_id)
                    );
                    CREATE INDEX idx_parcel_exact
                        ON parcel_lookup(gemarkung_norm, flur_norm, flurstueck_norm);
                    """
                )
                for index, row in enumerate(fixture_rows):
                    gemarkung = row["gemarkung"]
                    flur = str(row.get("flur") or "")
                    flurstueck = str(row["flurstueck"])
                    zaehler, _, nenner = flurstueck.partition("/")
                    lon = float(row.get("lon", 9.0))
                    lat = float(row.get("lat", 52.0))
                    connection.execute(
                        """
                        INSERT INTO parcel_lookup (
                            source_db, gml_id, gemarkung_norm, gemarkung_label,
                            gemarkungsnummer, flur_norm, flur_label,
                            flurstueck_norm, flurstueck_label, zaehler, nenner,
                            amtliche_flaeche_m2, lon, lat,
                            min_lon, max_lon, min_lat, max_lat
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            f"{state}.features.sqlite",
                            row.get("gml_id", f"parcel-{index}"),
                            main.normalize_geocoder_text(
                                re.sub(r"\s*\([^)]*\)\s*$", "", gemarkung)
                            ),
                            gemarkung,
                            str(row.get("gemarkungsnummer") or ""),
                            main.fast_compact_norm(flur),
                            flur,
                            main.fast_compact_norm(flurstueck),
                            flurstueck,
                            zaehler,
                            nenner,
                            row.get("area"),
                            lon,
                            lat,
                            lon - 0.001,
                            lon + 0.001,
                            lat - 0.001,
                            lat + 0.001,
                        ),
                    )
                connection.commit()
                connection.close()
                entries.append(main.FeatureDbEntry(name=state, path=path))
            entries.sort(key=lambda entry: entry.name)
            states = {entry.name for entry in entries}
            signature = tuple(
                (entry.name, str(entry.path), *main.sqlite_file_signature(entry.path))
                for entry in entries
            )
            main.search_gemarkung_suggestions_cached.cache_clear()
            main.search_sqlite_parcel_lookup.cache_clear()
            try:
                with (
                    patch.object(main, "search_suggestion_states_for_dataset", return_value=states),
                    patch.object(main, "search_db_entries_for_states", return_value=tuple(entries)),
                    patch.object(main, "search_db_signature_for_states", return_value=signature),
                    patch.object(
                        main,
                        "requested_municipality",
                        side_effect=(
                            (
                                lambda value, _states: {
                                    "name": municipality,
                                    "folded": municipality.casefold(),
                                    "source_name": str(value or "").strip(),
                                }
                                if value and municipality
                                else None
                            )
                        ),
                    ),
                    patch.object(
                        main,
                        "municipality_at",
                        return_value=(
                            {
                                "name": located_municipality if located_municipality is not None else municipality,
                                "folded": (
                                    located_municipality if located_municipality is not None else municipality
                                ).casefold(),
                            }
                            if (located_municipality if located_municipality is not None else municipality)
                            else None
                        ),
                    ),
                ):
                    return main.search_free_text_parcel_suggestions_for_dataset(
                        "deutschland", query, limit
                    )
            finally:
                main.search_gemarkung_suggestions_cached.cache_clear()
                main.search_sqlite_parcel_lookup.cache_clear()
                for entry in entries:
                    cached = main._SEARCH_DB_CONNECTIONS.pop(str(entry.path), None)
                    if cached:
                        cached[1].close()

    def contextual_parcel_suggestions_from_fixture(
        self,
        query: str,
        rows_by_state: dict[str, list[dict]],
        *,
        limit: int = 12,
        municipality: dict | None = None,
        openplz_rows: list[dict] | None = None,
        query_log: list[str] | None = None,
        near_lon: float | None = None,
        near_lat: float | None = None,
    ) -> dict:
        """Run the exact address↔parcel relation path on v1-shaped keys."""
        with TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            entries = []
            for state, fixture_rows in rows_by_state.items():
                path = directory / f"{state}.search.sqlite"
                connection = sqlite3.connect(path)
                connection.executescript(
                    """
                    CREATE TABLE address_lookup (
                        id INTEGER PRIMARY KEY,
                        feature_kind TEXT NOT NULL,
                        source_db TEXT NOT NULL,
                        gml_id TEXT NOT NULL,
                        street_norm TEXT NOT NULL,
                        street_label TEXT NOT NULL,
                        house_number_norm TEXT NOT NULL,
                        house_number_label TEXT NOT NULL,
                        city_norm TEXT NOT NULL,
                        city_label TEXT NOT NULL,
                        post_code TEXT NOT NULL,
                        label TEXT NOT NULL,
                        lon REAL,
                        lat REAL,
                        min_lon REAL NOT NULL,
                        max_lon REAL NOT NULL,
                        min_lat REAL NOT NULL,
                        max_lat REAL NOT NULL
                    );
                    CREATE INDEX idx_address_no_city
                        ON address_lookup(street_norm, house_number_norm);
                    CREATE INDEX idx_address_exact
                        ON address_lookup(city_norm, street_norm, house_number_norm);
                    CREATE INDEX idx_address_street
                        ON address_lookup(city_norm, street_norm);
                    CREATE TABLE street_lookup (
                        id INTEGER PRIMARY KEY,
                        street_norm TEXT NOT NULL,
                        street_label TEXT NOT NULL,
                        city_norm TEXT NOT NULL,
                        city_label TEXT NOT NULL,
                        post_code TEXT NOT NULL,
                        label TEXT NOT NULL,
                        address_count INTEGER NOT NULL,
                        feature_count INTEGER NOT NULL,
                        lon REAL,
                        lat REAL,
                        min_lon REAL NOT NULL,
                        max_lon REAL NOT NULL,
                        min_lat REAL NOT NULL,
                        max_lat REAL NOT NULL
                    );
                    CREATE INDEX idx_street_exact
                        ON street_lookup(city_norm, street_norm);
                    CREATE TABLE parcel_lookup (
                        id INTEGER PRIMARY KEY,
                        source_db TEXT NOT NULL,
                        gml_id TEXT NOT NULL,
                        gemarkung_norm TEXT NOT NULL,
                        gemarkung_label TEXT NOT NULL,
                        gemarkungsnummer TEXT NOT NULL,
                        flur_norm TEXT NOT NULL,
                        flur_label TEXT NOT NULL,
                        flurstueck_norm TEXT NOT NULL,
                        flurstueck_label TEXT NOT NULL,
                        zaehler TEXT NOT NULL,
                        nenner TEXT NOT NULL,
                        amtliche_flaeche_m2 REAL,
                        lon REAL,
                        lat REAL,
                        min_lon REAL NOT NULL,
                        max_lon REAL NOT NULL,
                        min_lat REAL NOT NULL,
                        max_lat REAL NOT NULL,
                        UNIQUE(source_db, gml_id)
                    );
                    CREATE INDEX idx_parcel_exact
                        ON parcel_lookup(
                            gemarkung_norm, flur_norm, flurstueck_norm
                        );
                    """
                )
                for index, row in enumerate(fixture_rows):
                    source_db = str(row.get("source_db") or f"{state}.features.sqlite")
                    gml_id = str(row.get("gml_id") or f"parcel-{index}")
                    gemarkung = str(row["gemarkung"])
                    flur = str(row.get("flur") or "")
                    flurstueck = str(row["flurstueck"])
                    zaehler, _, nenner = flurstueck.partition("/")
                    street = str(row["street"])
                    house_number = str(row.get("house_number") or "")
                    city = str(row.get("city") or "")
                    postcode = str(row.get("postcode") or "")
                    lon = float(row.get("lon", 9.0 + index * 0.01))
                    lat = float(row.get("lat", 52.0 + index * 0.01))
                    parcel_bbox = tuple(row.get(
                        "bbox",
                        (lon - 0.001, lat - 0.001, lon + 0.001, lat + 0.001),
                    ))
                    connection.execute(
                        """
                        INSERT INTO parcel_lookup (
                            source_db, gml_id, gemarkung_norm, gemarkung_label,
                            gemarkungsnummer, flur_norm, flur_label,
                            flurstueck_norm, flurstueck_label, zaehler, nenner,
                            amtliche_flaeche_m2, lon, lat,
                            min_lon, max_lon, min_lat, max_lat
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            source_db, gml_id,
                            main.normalize_geocoder_text(gemarkung), gemarkung,
                            str(row.get("gemarkungsnummer") or ""),
                            main.fast_compact_norm(flur), flur,
                            # Production Niedersachsen is still v1: the slash
                            # is collapsed in the SQL key and must be verified
                            # through zaehler/nenner plus the original label.
                            main.fast_compact_norm(flurstueck), flurstueck,
                            zaehler, nenner, None, lon, lat,
                            parcel_bbox[0], parcel_bbox[2],
                            parcel_bbox[1], parcel_bbox[3],
                        ),
                    )
                    if row.get("linked_address", True):
                        connection.execute(
                            """
                            INSERT INTO address_lookup (
                                feature_kind, source_db, gml_id,
                                street_norm, street_label,
                                house_number_norm, house_number_label,
                                city_norm, city_label, post_code, label,
                                lon, lat, min_lon, max_lon, min_lat, max_lat
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                "parcel", source_db, gml_id,
                                main.normalize_geocoder_text(street), street,
                                main.normalize_geocoder_house(house_number),
                                house_number,
                                main.normalize_geocoder_text(city), city,
                                postcode, f"{street}, {postcode} {city}".strip(),
                                lon, lat, parcel_bbox[0], parcel_bbox[2],
                                parcel_bbox[1], parcel_bbox[3],
                            ),
                        )
                    street_bbox = row.get("street_bbox")
                    street_city_norm = str(
                        row.get("street_city_norm")
                        or main.normalize_geocoder_text(city)
                    )
                    if street_bbox:
                        connection.execute(
                            """
                            INSERT INTO street_lookup (
                                street_norm, street_label, city_norm, city_label,
                                post_code, label, address_count, feature_count,
                                lon, lat, min_lon, max_lon, min_lat, max_lat
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                main.normalize_geocoder_text(street), street,
                                street_city_norm, city, postcode,
                                f"{street}, {postcode} {city}".strip(),
                                len(row.get("building_points") or []), 1,
                                lon, lat, street_bbox[0], street_bbox[2],
                                street_bbox[1], street_bbox[3],
                            ),
                        )
                    for building_index, point in enumerate(
                        row.get("building_points") or []
                    ):
                        building_lon, building_lat = point
                        building_gml = f"{gml_id}-building-{building_index}"
                        connection.execute(
                            """
                            INSERT INTO address_lookup (
                                feature_kind, source_db, gml_id,
                                street_norm, street_label,
                                house_number_norm, house_number_label,
                                city_norm, city_label, post_code, label,
                                lon, lat, min_lon, max_lon, min_lat, max_lat
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                "building", source_db, building_gml,
                                main.normalize_geocoder_text(street), street,
                                str(building_index + 1), str(building_index + 1),
                                street_city_norm, city, postcode,
                                f"{street} {building_index + 1}",
                                building_lon, building_lat,
                                building_lon, building_lon,
                                building_lat, building_lat,
                            ),
                        )
                connection.commit()
                connection.close()
                entries.append(main.FeatureDbEntry(name=state, path=path))
            openplz_path = directory / "openplz.sqlite"
            openplz_connection = sqlite3.connect(openplz_path)
            openplz_connection.executescript(
                """
                CREATE TABLE streets (
                    id INTEGER PRIMARY KEY,
                    street TEXT NOT NULL,
                    street_norm TEXT NOT NULL,
                    postal_code TEXT NOT NULL,
                    locality TEXT NOT NULL,
                    locality_norm TEXT NOT NULL,
                    regional_key TEXT NOT NULL,
                    state_key TEXT NOT NULL,
                    borough TEXT NOT NULL,
                    borough_norm TEXT NOT NULL,
                    suburb TEXT NOT NULL,
                    suburb_norm TEXT NOT NULL
                );
                CREATE INDEX idx_streets_norm_state
                    ON streets(street_norm, state_key);
                """
            )
            for row in openplz_rows or []:
                openplz_connection.execute(
                    """
                    INSERT INTO streets (
                        street, street_norm, postal_code,
                        locality, locality_norm, regional_key, state_key,
                        borough, borough_norm, suburb, suburb_norm
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(row["street"]),
                        str(row.get("street_norm") or main.fast_compact_norm(row["street"])),
                        str(row["postcode"]),
                        str(row["locality"]),
                        str(row.get("locality_norm") or main.normalize_geocoder_text(row["locality"])),
                        "", str(row["state"]),
                        str(row.get("borough") or ""),
                        str(row.get("borough_norm") or main.normalize_geocoder_text(row.get("borough") or "")),
                        str(row.get("suburb") or ""),
                        str(row.get("suburb_norm") or main.normalize_geocoder_text(row.get("suburb") or "")),
                    ),
                )
            openplz_connection.commit()
            openplz_connection.close()
            entries.sort(key=lambda entry: entry.name)
            states = {entry.name for entry in entries}
            signature = tuple(
                (entry.name, str(entry.path), *main.sqlite_file_signature(entry.path))
                for entry in entries
            )
            main.search_contextual_parcel_suggestions_cached.cache_clear()
            main.openplz_storage_state_keys_cached.cache_clear()
            original_fetchall = main.search_db_fetchall

            def fixture_fetchall(path, query, parameters=()):
                if query_log is not None:
                    query_log.append(str(query))
                return original_fetchall(path, query, parameters)

            try:
                with (
                    patch.object(main, "OPENPLZ_DB", openplz_path),
                    patch.object(
                        main,
                        "search_db_fetchall",
                        side_effect=fixture_fetchall,
                    ),
                    patch.object(main, "search_suggestion_states_for_dataset", return_value=states),
                    patch.object(main, "search_db_entries_for_states", return_value=tuple(entries)),
                    patch.object(main, "search_db_signature_for_states", return_value=signature),
                    patch.object(main, "states_for_place_context", return_value=tuple()),
                    patch.object(main, "municipality_at", return_value=municipality),
                ):
                    return main.search_free_text_parcel_suggestions_for_dataset(
                        "deutschland",
                        query,
                        limit,
                        near_lon=near_lon,
                        near_lat=near_lat,
                    )
            finally:
                main.search_contextual_parcel_suggestions_cached.cache_clear()
                main.openplz_storage_state_keys_cached.cache_clear()
                for entry in entries:
                    cached = main._SEARCH_DB_CONNECTIONS.pop(str(entry.path), None)
                    if cached:
                        cached[1].close()

    def test_search_db_errors_are_structured_503s(self) -> None:
        with patch.object(
            main,
            "search_db_connection",
            side_effect=sqlite3.OperationalError("forced test failure"),
        ):
            with self.assertRaises(main.HTTPException) as raised:
                main.search_db_fetchall(Path("/tmp/test.search.sqlite"), "SELECT 1")
        error = raised.exception
        self.assertEqual(503, error.status_code)
        self.assertEqual("search_database_unavailable", error.detail["code"])
        self.assertTrue(error.detail["request_id"].startswith("search-"))
        self.assertEqual(error.detail["request_id"], error.headers["X-Request-ID"])

    def test_search_db_errors_do_not_fill_response_cache(self) -> None:
        saved_cache = dict(main._SEARCH_RESPONSE_CACHE)
        main._SEARCH_RESPONSE_CACHE.clear()
        error = main.HTTPException(
            status_code=503,
            detail={"code": "search_database_unavailable", "request_id": "search-test"},
        )
        try:
            with patch.object(
                main,
                "search_direct_geocoder_for_dataset",
                side_effect=error,
            ):
                with self.assertRaises(main.HTTPException):
                    main.cached_search_features_for_dataset(
                        "deutschland",
                        "Gelnhaarer Strasse 6 Kefenrod",
                        12,
                        "address",
                        state="hessen",
                    )
            self.assertEqual({}, main._SEARCH_RESPONSE_CACHE)
        finally:
            main._SEARCH_RESPONSE_CACHE.clear()
            main._SEARCH_RESPONSE_CACHE.update(saved_cache)

    def test_parcel_api_requires_only_gemarkung_and_flurstueck(self) -> None:
        parameters = main.app.openapi()["paths"]["/api/v1/search/parcel"]["get"]["parameters"]
        query_requirements = {
            parameter["name"]: parameter["required"]
            for parameter in parameters
            if parameter["in"] == "query"
        }
        self.assertTrue(query_requirements["gemarkung"])
        self.assertTrue(query_requirements["flurstueck"])
        self.assertFalse(query_requirements["flur"])

    def test_parcel_number_normalization_preserves_slash(self) -> None:
        self.assertEqual("1/11", main.fast_parcel_number_norm(" 1 / 11 "))
        self.assertNotEqual(
            main.fast_parcel_number_norm("1/11"),
            main.fast_parcel_number_norm("11/1"),
        )

    def test_parcel_label_omits_empty_flur(self) -> None:
        row = {
            "lon": 9.0,
            "lat": 48.0,
            "flur_label": "",
            "flurstueck_label": "1066",
            "gemarkung_label": "Hofen (0976)",
            "source_db": "alkis.sqlite",
            "gml_id": "parcel-1",
            "gemarkungsnummer": "0976",
            "zaehler": "1066",
            "nenner": "",
            "amtliche_flaeche_m2": None,
            "min_lon": 8.9,
            "min_lat": 47.9,
            "max_lon": 9.1,
            "max_lat": 48.1,
        }
        self.assertEqual(
            "Flurstück 1066, Hofen (0976)",
            main.search_parcel_result_from_row(row, "baden-wurttemberg")["label"],
        )

    def test_free_text_parcel_accepts_bw_code_without_flur(self) -> None:
        result = self.parcel_suggestions_from_fixture(
            "Hofen (0976) 1066",
            {
                "baden-wurttemberg": [
                    {
                        "gemarkung": "Hofen (0976)",
                        "gemarkungsnummer": "0976",
                        "flurstueck": "1066",
                    },
                    {
                        "gemarkung": "Hofen (2384)",
                        "gemarkungsnummer": "2384",
                        "flurstueck": "1066",
                        "gml_id": "wrong-homonym",
                    },
                ],
            },
        )
        self.assertEqual(1, result["count"])
        item = result["results"][0]
        self.assertEqual("parcel", item["search_scope"])
        self.assertEqual("parcel", item["kind"])
        self.assertEqual("feature", item["result_type"])
        self.assertEqual(
            {
                "gemarkung": "Hofen (0976)",
                "flur": "",
                "flurstueck": "1066",
                "state": "baden-wurttemberg",
            },
            item["parcel_search"],
        )

    def test_free_text_parcel_accepts_unparenthesized_gemarkung_code(self) -> None:
        rows = {
            "baden-wurttemberg": [
                {
                    "gemarkung": f"Hofen ({code})",
                    "gemarkungsnummer": code,
                    "flur": "0976" if code == "1327" else "",
                    "flurstueck": "1066",
                    "gml_id": f"hofen-{code}",
                }
                for code in ("0976", "1327", "8792", "1467")
            ],
        }
        selected = self.parcel_suggestions_from_fixture(
            "Flurstück 1066 Hofen 0976", rows
        )
        self.assertEqual(1, selected["count"])
        self.assertEqual("Hofen (0976)", selected["results"][0]["parcel_search"]["gemarkung"])
        self.assertEqual("", selected["results"][0]["parcel_search"]["flur"])

        ambiguous = self.parcel_suggestions_from_fixture(
            "Flurstück 1066 in Hofen", rows
        )
        self.assertEqual(
            {"0976", "1327", "8792", "1467"},
            {item["feature"]["gemarkungsnummer"] for item in ambiguous["results"]},
        )

    def test_free_text_parcel_accepts_natural_municipality_context(self) -> None:
        result = self.parcel_suggestions_from_fixture(
            "Flurstück 100/1 in Bemerode Hannover",
            {
                "niedersachsen": [
                    {
                        "gemarkung": "Bemerode (4887)",
                        "gemarkungsnummer": "4887",
                        "flur": "1",
                        "flurstueck": "100/1",
                    },
                    {
                        "gemarkung": "Hannover (0001)",
                        "gemarkungsnummer": "0001",
                        "flur": "1",
                        "flurstueck": "100/1",
                        "gml_id": "municipality-must-not-be-gemarkung",
                    },
                ],
            },
            municipality="Hannover",
        )
        self.assertEqual(1, result["count"])
        item = result["results"][0]
        self.assertEqual("Flurstück 100/1", item["primary_label"])
        self.assertIn("Gemarkung Bemerode (4887)", item["secondary_label"])
        self.assertIn("Flur 1", item["secondary_label"])
        self.assertIn("Hannover", item["secondary_label"])

    def test_free_text_parcel_accepts_compact_gemarkung_municipality_order(self) -> None:
        rows = {
            "niedersachsen": [
                {
                    "gemarkung": "Bemerode (4887)",
                    "gemarkungsnummer": "4887",
                    "flur": "1",
                    "flurstueck": "100/1",
                },
                {
                    "gemarkung": "Hannover (0001)",
                    "gemarkungsnummer": "0001",
                    "flur": "1",
                    "flurstueck": "100/1",
                    "gml_id": "municipality-must-not-be-gemarkung",
                },
            ],
        }
        for query in (
            "Bemerode Hannover 100/1",
            "Hannover Bemerode 100/1",
        ):
            with self.subTest(query=query):
                result = self.parcel_suggestions_from_fixture(
                    query,
                    rows,
                    municipality="Hannover",
                )
                self.assertEqual(1, result["count"])
                self.assertEqual(
                    "4887",
                    result["results"][0]["feature"]["gemarkungsnummer"],
                )

    def test_compact_parcel_requires_matching_municipality_at_result(self) -> None:
        result = self.parcel_suggestions_from_fixture(
            "Bemerode Hannover 100/1",
            {
                "niedersachsen": [
                    {
                        "gemarkung": "Bemerode (4887)",
                        "gemarkungsnummer": "4887",
                        "flur": "1",
                        "flurstueck": "100/1",
                    },
                ],
            },
            municipality="Hannover",
            located_municipality="Laatzen",
        )
        self.assertEqual([], result["results"])

    def test_free_text_parcel_path_never_reads_feature_database(self) -> None:
        with patch.object(
            main,
            "feature_db_entries_for_dataset",
            side_effect=AssertionError("features.sqlite must not be read"),
        ):
            result = self.parcel_suggestions_from_fixture(
                "Bemerode 100/1",
                {
                    "niedersachsen": [
                        {
                            "gemarkung": "Bemerode (4887)",
                            "gemarkungsnummer": "4887",
                            "flur": "1",
                            "flurstueck": "100/1",
                        },
                    ],
                },
            )
        self.assertEqual(1, result["count"])

    def test_free_text_parcel_accepts_compact_optional_flur(self) -> None:
        rows = {
            "niedersachsen": [
                {
                    "gemarkung": "Bemerode (4887)",
                    "gemarkungsnummer": "4887",
                    "flur": "1",
                    "flurstueck": "100/1",
                },
            ],
        }
        compact = self.parcel_suggestions_from_fixture("Bemerode 1 100/1", rows)
        without_flur = self.parcel_suggestions_from_fixture("Bemerode 100/1", rows)
        self.assertEqual("1", compact["results"][0]["parcel_search"]["flur"])
        self.assertEqual("1", without_flur["results"][0]["parcel_search"]["flur"])

    def test_free_text_parcel_accepts_explicit_fields_in_both_orders(self) -> None:
        rows = {
            "niedersachsen": [
                {
                    "gemarkung": "Bemerode (4887)",
                    "gemarkungsnummer": "4887",
                    "flur": "1",
                    "flurstueck": "100/1",
                },
            ],
        }
        for query in (
            "Gemarkung Bemerode Flur 1 Flurstück 100/1",
            "Flurstück 100/1 Flur 1 Gemarkung Bemerode",
        ):
            with self.subTest(query=query):
                result = self.parcel_suggestions_from_fixture(query, rows)
                self.assertEqual(1, result["count"])
                self.assertEqual("100/1", result["results"][0]["parcel_search"]["flurstueck"])

    def test_contextual_parcel_relation_accepts_street_city_and_postcode_orders(self) -> None:
        rows = {
            "niedersachsen": [
                {
                    "gemarkung": "Hannover",
                    "gemarkungsnummer": "4880",
                    "flur": "31",
                    "flurstueck": "62/5",
                    "street": "Meterstraße",
                    "city": "Hannover",
                    "postcode": "30169",
                    "gml_id": "hannover-62-5",
                },
                {
                    "gemarkung": "Freden (Leine)",
                    "gemarkungsnummer": "6006",
                    "flur": "23",
                    "flurstueck": "42/6",
                    "street": "Maschstraße",
                    "city": "Freden (Leine)",
                    "postcode": "31084",
                    "gml_id": "freden-42-6",
                },
                {
                    "gemarkung": "Hildesheim",
                    "gemarkungsnummer": "5083",
                    "flur": "50",
                    "flurstueck": "37/8",
                    "street": "Feldstraße",
                    "city": "Hildesheim",
                    "postcode": "31141",
                    "gml_id": "hildesheim-37-8",
                },
                {
                    "gemarkung": "Buchholz i. d. N.",
                    "gemarkungsnummer": "1352",
                    "flur": "15",
                    "flurstueck": "37/8",
                    "street": "Feldstraße",
                    "city": "Buchholz in der Nordheide",
                    "postcode": "21244",
                    "gml_id": "buchholz-37-8",
                },
                {
                    "gemarkung": "Sarstedt",
                    "gemarkungsnummer": "5108",
                    "flur": "13",
                    "flurstueck": "47/1",
                    "street": "Holztorstraße",
                    "city": "Sarstedt",
                    "postcode": "31157",
                    "gml_id": "sarstedt-47-1",
                },
            ],
        }
        cases = (
            ("62/5 hannover Meterstraße", {"hannover-62-5"}),
            ("Maschstraße, freden 42/6", {"freden-42-6"}),
            ("Flur 23, Flurstück 42/6, Freden", {"freden-42-6"}),
            (
                "Flurstück 37/8, feldstraße",
                {"hildesheim-37-8", "buchholz-37-8"},
            ),
            ("Flurstück 37/8, 31141 Hildesheim", {"hildesheim-37-8"}),
            ("47/1 Holztorstraße", {"sarstedt-47-1"}),
            ("Holztorstraße 47/1", {"sarstedt-47-1"}),
        )
        for query, expected_ids in cases:
            with self.subTest(query=query):
                payload = self.contextual_parcel_suggestions_from_fixture(query, rows)
                self.assertEqual(
                    expected_ids,
                    {item["feature"]["gml_id"] for item in payload["results"]},
                )

    def test_contextual_parcel_relation_preserves_official_parenthetical_name(self) -> None:
        payload = self.contextual_parcel_suggestions_from_fixture(
            "Flur 23, Flurstück 42/6, Freden",
            {
                "niedersachsen": [{
                    "gemarkung": "Freden (Leine)",
                    "gemarkungsnummer": "6006",
                    "flur": "23",
                    "flurstueck": "42/6",
                    "street": "Maschstraße",
                    "city": "Freden (Leine)",
                    "postcode": "31084",
                }],
            },
        )
        self.assertEqual(
            "Freden (Leine) (6006)",
            payload["results"][0]["parcel_search"]["gemarkung"],
        )

    def test_contextual_parcel_relation_uses_exact_linked_house_number(self) -> None:
        rows = {
            "niedersachsen": [
                {
                    "gemarkung": "Sarstedt",
                    "gemarkungsnummer": "5108",
                    "flur": "15",
                    "flurstueck": "77/9",
                    "street": "Querstraße",
                    "house_number": "1A",
                    "city": "Sarstedt",
                    "postcode": "31157",
                    "source_db": "alkis_niedersachsen_14",
                    "gml_id": "DENIAL5600004OQi",
                },
                {
                    "gemarkung": "Testgemarkung",
                    "gemarkungsnummer": "9999",
                    "flur": "15",
                    "flurstueck": "77/9",
                    "street": "Querstraße",
                    "house_number": "1B",
                    "city": "Teststadt",
                    "postcode": "99999",
                    "source_db": "alkis_niedersachsen_99",
                    "gml_id": "wrong-house-number",
                },
            ],
        }
        for query in (
            "Querstraße 1A Flur 15 Flurstück 77/9",
            "querstraße 1a 77/9",
        ):
            with self.subTest(query=query):
                payload = self.contextual_parcel_suggestions_from_fixture(
                    query,
                    rows,
                )
                self.assertEqual(1, payload["count"])
                self.assertEqual(
                    "DENIAL5600004OQi",
                    payload["results"][0]["feature"]["gml_id"],
                )
                self.assertEqual(
                    "1A",
                    payload["results"][0]["linked_address"]["house_number"],
                )

    def test_contextual_parcel_relation_accepts_one_plain_number_as_parcel(self) -> None:
        rows = {
            "baden-wurttemberg": [{
                "gemarkung": "Heilbronn",
                "gemarkungsnummer": "0910",
                "flur": "0",
                "flurstueck": "784",
                "street": "Kurze Straße",
                "house_number": "4",
                "city": "Heilbronn",
                "postcode": "74072",
                "source_db": "alkis_baden_wuerttemberg",
                "gml_id": "DEBWL51000005FLl",
            }],
            "nordrhein-westfalen": [{
                "gemarkung": "Wassenberg",
                "gemarkungsnummer": "4501",
                "flur": "3",
                "flurstueck": "784",
                "street": "Kurze Straße",
                "house_number": "4",
                "city": "Wassenberg",
                "postcode": "41849",
                "source_db": "alkis_nordrhein_westfalen",
                "gml_id": "DENW43AL0000vsKk",
            }],
        }
        expected_ids = {
            "DEBWL51000005FLl",
            "DENW43AL0000vsKk",
        }
        for query in (
            "Kurze Straße 784",
            "Flurstück 784 Kurze Straße",
        ):
            with self.subTest(query=query):
                payload = self.contextual_parcel_suggestions_from_fixture(
                    query,
                    rows,
                )
                self.assertEqual(
                    expected_ids,
                    {
                        item["feature"]["gml_id"]
                        for item in payload["results"]
                    },
                )

    def test_contextual_lookup_finishes_global_street_phase_before_city_fallback(self) -> None:
        street_rows = {
            "baden-wurttemberg": [{
                "gemarkung": "Test A",
                "flur": "0",
                "flurstueck": "7",
                "street": "Teststraße",
                "city": "Teststadt A",
                "gml_id": "street-a",
            }],
            "rheinland-pfalz": [{
                "gemarkung": "Test B",
                "flur": "0",
                "flurstueck": "7",
                "street": "Teststraße",
                "city": "Teststadt B",
                "gml_id": "street-b",
            }],
        }
        street_log: list[str] = []
        payload = self.contextual_parcel_suggestions_from_fixture(
            "Flurstück 7 Teststraße",
            street_rows,
            query_log=street_log,
        )
        self.assertEqual(2, payload["count"])
        self.assertEqual(
            2,
            sum("INDEXED BY idx_address_no_city" in query for query in street_log),
        )
        self.assertFalse(any(
            "INDEXED BY idx_address_exact" in query
            for query in street_log
        ))

        city_rows = {
            state: [{
                "gemarkung": f"Freden {index}",
                "flur": "23",
                "flurstueck": "42/6",
                "street": "Maschstraße",
                "city": "Freden",
                "gml_id": f"city-{index}",
            }]
            for index, state in enumerate((
                "baden-wurttemberg",
                "rheinland-pfalz",
            ))
        }
        city_log: list[str] = []
        payload = self.contextual_parcel_suggestions_from_fixture(
            "Flur 23 Flurstück 42/6 Freden",
            city_rows,
            query_log=city_log,
        )
        self.assertEqual(2, payload["count"])
        self.assertEqual(
            2,
            sum("INDEXED BY idx_address_no_city" in query for query in city_log),
        )
        self.assertEqual(
            2,
            sum("INDEXED BY idx_address_exact" in query for query in city_log),
        )

    def test_contextual_lookup_pool_keeps_later_states_fairly(self) -> None:
        rows = {}
        for state, city in (
            ("baden-wurttemberg", "Teststadt A"),
            ("rheinland-pfalz", "Teststadt B"),
        ):
            rows[state] = [
                {
                    "gemarkung": f"Gemarkung {index:02d}",
                    "flur": "0",
                    "flurstueck": "7",
                    "street": "Teststraße",
                    "city": city,
                    "source_db": f"{state}-{index:02d}",
                    "gml_id": f"{state}-parcel-{index:02d}",
                }
                for index in range(40)
            ]
        payload = self.contextual_parcel_suggestions_from_fixture(
            "Flurstück 7 Teststraße",
            rows,
            limit=2,
        )
        self.assertEqual(
            {"baden-wurttemberg", "rheinland-pfalz"},
            {item["state"] for item in payload["results"]},
        )

    def test_contextual_parcel_relation_replaces_numeric_city_for_display(self) -> None:
        payload = self.contextual_parcel_suggestions_from_fixture(
            "Kurze Straße 784",
            {
                "baden-wurttemberg": [{
                    # The cadastral district must not accidentally make this
                    # pass: the display value has to come from municipality_at.
                    "gemarkung": "Böckingen",
                    "gemarkungsnummer": "0910",
                    "flur": "0",
                    "flurstueck": "784",
                    "street": "Kurze Straße",
                    "house_number": "4",
                    "city": "74072",
                    "postcode": "74072",
                    "source_db": "alkis_baden_wuerttemberg",
                    "gml_id": "DEBWL51000005FLl",
                }],
            },
            municipality={"name": "Heilbronn"},
        )
        self.assertEqual(1, payload["count"])
        self.assertEqual(
            "Heilbronn",
            payload["results"][0]["linked_address"]["city"],
        )
        self.assertIn(
            "Heilbronn",
            payload["results"][0]["secondary_label"],
        )

    def test_explicit_addressless_parcel_uses_nearby_street_evidence(self) -> None:
        rows = {
            "baden-wurttemberg": [
                {
                    "gemarkung": "Heilbronn",
                    "gemarkungsnummer": "0910",
                    "flur": "0",
                    "flurstueck": "4752",
                    "street": "Bergstraße",
                    "city": "74072",
                    "postcode": "74072",
                    "gml_id": "heilbronn-4752",
                    "linked_address": False,
                    "lon": 9.0,
                    "lat": 49.0,
                    "bbox": (9.0, 49.0, 9.0001, 49.0001),
                    "street_city_norm": "74072",
                    "street_bbox": (8.9999, 48.9999, 9.0002, 49.0002),
                    "building_points": [
                        (9.00011, 49.00005),
                        (9.00012, 49.00005),
                    ],
                },
                {
                    # v1 compact keys collide, but original semantics must not.
                    "gemarkung": "Heilbronn",
                    "gemarkungsnummer": "0910",
                    "flur": "0",
                    "flurstueck": "475/2",
                    "street": "Bergstraße",
                    "city": "74072",
                    "postcode": "74072",
                    "gml_id": "heilbronn-475-slash-2",
                    "linked_address": False,
                    "lon": 9.0,
                    "lat": 49.0,
                    "bbox": (9.0, 49.0, 9.0001, 49.0001),
                },
            ],
            "rheinland-pfalz": [
                {
                    # The authoritative same-GML relation stays first.
                    "gemarkung": "Rinnthal",
                    "gemarkungsnummer": "5534",
                    "flur": "0",
                    "flurstueck": "4752",
                    "street": "Bergstraße",
                    "city": "Rinnthal",
                    "postcode": "76857",
                    "gml_id": "rinnthal-4752",
                    "linked_address": True,
                    "lon": 7.92,
                    "lat": 49.22,
                    "bbox": (7.9199, 49.2199, 7.9201, 49.2201),
                },
                {
                    # An exact name alone is insufficient without both
                    # spatial street and nearby-building evidence.
                    "gemarkung": "Jettenbach",
                    "gemarkungsnummer": "5204",
                    "flur": "0",
                    "flurstueck": "4752",
                    "street": "Bergstraße",
                    "city": "Jettenbach",
                    "postcode": "66887",
                    "gml_id": "jettenbach-4752",
                    "linked_address": False,
                    "lon": 7.55,
                    "lat": 49.53,
                    "bbox": (7.55, 49.53, 7.5501, 49.5301),
                    "street_bbox": (7.551, 49.531, 7.552, 49.532),
                    "building_points": [
                        (7.553, 49.533),
                        (7.554, 49.534),
                    ],
                },
                {
                    # Street bbox intersects, but one nearby building is not
                    # enough evidence for an addressless parcel.
                    "gemarkung": "Birkweiler",
                    "gemarkungsnummer": "5503",
                    "flur": "0",
                    "flurstueck": "4752",
                    "street": "Bergstraße",
                    "city": "Birkweiler",
                    "postcode": "76831",
                    "gml_id": "birkweiler-4752",
                    "linked_address": False,
                    "lon": 8.0,
                    "lat": 49.2,
                    "bbox": (8.0, 49.2, 8.0001, 49.2001),
                    "street_bbox": (8.0, 49.2, 8.0001, 49.2001),
                    "building_points": [(8.00011, 49.20005)],
                },
                {
                    # Two buildings may be near, but the exact street bbox
                    # itself must also intersect the parcel.
                    "gemarkung": "Annweiler",
                    "gemarkungsnummer": "5501",
                    "flur": "0",
                    "flurstueck": "4752",
                    "street": "Bergstraße",
                    "city": "Annweiler",
                    "postcode": "76855",
                    "gml_id": "annweiler-4752",
                    "linked_address": False,
                    "lon": 8.1,
                    "lat": 49.2,
                    "bbox": (8.1, 49.2, 8.1001, 49.2001),
                    "street_bbox": (8.101, 49.201, 8.102, 49.202),
                    "building_points": [
                        (8.10011, 49.20005),
                        (8.10012, 49.20005),
                    ],
                },
            ],
        }
        openplz_rows = [
            {
                "street": "Bergstr.",
                "street_norm": "bergstr",
                "postcode": "74072",
                "locality": "Heilbronn",
                "state": "baden-wurttemberg",
            },
            {
                "street": "Bergstr.",
                "street_norm": "bergstr",
                "postcode": "76857",
                "locality": "Rinnthal",
                "state": "rheinland-pfalz",
            },
            {
                "street": "Bergstr.",
                "street_norm": "bergstr",
                "postcode": "66887",
                "locality": "Jettenbach",
                "state": "rheinland-pfalz",
            },
            {
                "street": "Bergstr.",
                "street_norm": "bergstr",
                "postcode": "76831",
                "locality": "Birkweiler",
                "state": "rheinland-pfalz",
            },
            {
                "street": "Bergstr.",
                "street_norm": "bergstr",
                "postcode": "76855",
                "locality": "Annweiler",
                "state": "rheinland-pfalz",
            },
        ]
        query_log: list[str] = []
        for query in (
            "Flur 0 Flurstück 4752 Bergstraße",
            "Bergstraße Flurstück 4752 Flur 0",
        ):
            with self.subTest(query=query):
                payload = self.contextual_parcel_suggestions_from_fixture(
                    query,
                    rows,
                    openplz_rows=openplz_rows,
                    query_log=query_log,
                )
                self.assertEqual(
                    ["rinnthal-4752", "heilbronn-4752"],
                    [
                        item["feature"]["gml_id"]
                        for item in payload["results"]
                    ],
                )
                derived = payload["results"][1]
                self.assertNotIn("linked_address", derived)
                self.assertEqual(
                    {
                        "street": "Bergstraße",
                        "relation": "nearby",
                        "post_code": "74072",
                        "max_distance_m": 10,
                    },
                    derived["street_context"],
                )
        joined_queries = "\n".join(query_log)
        self.assertIn(
            "parcel_lookup INDEXED BY idx_parcel_exact",
            joined_queries,
        )
        self.assertIn(
            "street_lookup INDEXED BY idx_street_exact",
            joined_queries,
        )
        self.assertIn(
            "address_lookup INDEXED BY idx_address_street",
            joined_queries,
        )
        nearby_payload = self.contextual_parcel_suggestions_from_fixture(
            "Flur 0 Flurstück 4752 Bergstraße",
            rows,
            openplz_rows=openplz_rows,
            near_lon=9.2162091425,
            near_lat=49.1366157046,
        )
        self.assertEqual(
            "heilbronn-4752",
            nearby_payload["results"][0]["feature"]["gml_id"],
        )

    def test_explicit_addressless_parcel_filters_requested_place_context(self) -> None:
        rows = {
            "baden-wurttemberg": [{
                "gemarkung": "Heilbronn",
                "gemarkungsnummer": "0910",
                "flur": "0",
                "flurstueck": "4752",
                "street": "Bergstraße",
                "city": "74072",
                "postcode": "74072",
                "gml_id": "heilbronn-4752",
                "linked_address": False,
                "lon": 9.0,
                "lat": 49.0,
                "bbox": (9.0, 49.0, 9.0001, 49.0001),
                "street_city_norm": "74072",
                "street_bbox": (9.0, 49.0, 9.0001, 49.0001),
                "building_points": [
                    (9.00011, 49.00005),
                    (9.00012, 49.00005),
                ],
            }],
            "rheinland-pfalz": [{
                "gemarkung": "Rinnthal",
                "gemarkungsnummer": "5534",
                "flur": "0",
                "flurstueck": "4752",
                "street": "Bergstraße",
                "city": "Rinnthal",
                "postcode": "76857",
                "gml_id": "rinnthal-4752",
                "linked_address": True,
                "lon": 7.92,
                "lat": 49.22,
                "bbox": (7.9199, 49.2199, 7.9201, 49.2201),
            }],
        }
        openplz_rows = [
            {
                "street": "Bergstr.",
                "street_norm": "bergstr",
                "postcode": "74072",
                "locality": "Heilbronn",
                "state": "baden-wurttemberg",
            },
            {
                "street": "Bergstr.",
                "street_norm": "bergstr",
                "postcode": "76857",
                "locality": "Rinnthal",
                "state": "rheinland-pfalz",
            },
        ]
        expected = {
            "Heilbronn": {"heilbronn-4752"},
            "Rinnthal": {"rinnthal-4752"},
        }
        for place, expected_ids in expected.items():
            with self.subTest(place=place):
                payload = self.contextual_parcel_suggestions_from_fixture(
                    f"Flur 0 Flurstück 4752 Bergstraße {place}",
                    rows,
                    openplz_rows=openplz_rows,
                )
                self.assertEqual(
                    expected_ids,
                    {
                        item["feature"]["gml_id"]
                        for item in payload["results"]
                    },
                )

    def test_explicit_street_constraint_cannot_be_bypassed_by_gemarkung_lookup(self) -> None:
        rows = {
            "rheinland-pfalz": [{
                "gemarkung": "Jettenbach",
                "gemarkungsnummer": "5204",
                "flur": "0",
                "flurstueck": "4752",
                "street": "Klausstraße",
                "house_number": "8",
                "city": "Jettenbach",
                "postcode": "66887",
                "gml_id": "DERPLP1700002GQ1",
                "linked_address": True,
                "lon": 7.55,
                "lat": 49.53,
                "bbox": (7.55, 49.53, 7.5501, 49.5301),
            }],
        }
        for street in ("Bergstraße", "Bergstr."):
            with self.subTest(wrong_street=street):
                wrong_street = self.contextual_parcel_suggestions_from_fixture(
                    f"Flur 0 Flurstück 4752 {street} Jettenbach",
                    rows,
                )
                self.assertEqual([], wrong_street["results"])

        for street in ("Klausstraße", "Klausstr."):
            with self.subTest(correct_street=street):
                correct_street = self.contextual_parcel_suggestions_from_fixture(
                    f"Flur 0 Flurstück 4752 {street} Jettenbach",
                    rows,
                )
                self.assertEqual(1, correct_street["count"])
                self.assertEqual(
                    "DERPLP1700002GQ1",
                    correct_street["results"][0]["feature"]["gml_id"],
                )

    def test_addressless_fallback_requires_explicit_fields_and_no_house(self) -> None:
        rows = {
            "baden-wurttemberg": [{
                "gemarkung": "Heilbronn",
                "gemarkungsnummer": "0910",
                "flur": "0",
                "flurstueck": "4752",
                "street": "Bergstraße",
                "city": "74072",
                "postcode": "74072",
                "gml_id": "heilbronn-4752",
                "linked_address": False,
                "lon": 9.0,
                "lat": 49.0,
                "bbox": (9.0, 49.0, 9.0001, 49.0001),
                "street_city_norm": "74072",
                "street_bbox": (9.0, 49.0, 9.0001, 49.0001),
                "building_points": [
                    (9.00011, 49.00005),
                    (9.00012, 49.00005),
                ],
            }],
        }
        openplz_rows = [{
            "street": "Bergstr.",
            "street_norm": "bergstr",
            "postcode": "74072",
            "locality": "Heilbronn",
            "state": "baden-wurttemberg",
        }]
        for query in (
            "Bergstraße 4752",
            "Flurstück 4752 Bergstraße",
            "Flur 0 4752 Bergstraße",
            "Bergstraße 1A Flur 0 Flurstück 4752",
        ):
            with self.subTest(query=query):
                payload = self.contextual_parcel_suggestions_from_fixture(
                    query,
                    rows,
                    openplz_rows=openplz_rows,
                )
                self.assertEqual([], payload["results"])

    def test_addressless_openplz_context_uses_stored_place_norms(self) -> None:
        with TemporaryDirectory() as raw_directory:
            path = Path(raw_directory) / "openplz.sqlite"
            connection = sqlite3.connect(path)
            connection.executescript(
                """
                CREATE TABLE streets (
                    id INTEGER PRIMARY KEY,
                    street TEXT NOT NULL,
                    street_norm TEXT NOT NULL,
                    postal_code TEXT NOT NULL,
                    locality TEXT NOT NULL,
                    locality_norm TEXT NOT NULL,
                    regional_key TEXT NOT NULL,
                    state_key TEXT NOT NULL,
                    borough TEXT NOT NULL,
                    borough_norm TEXT NOT NULL,
                    suburb TEXT NOT NULL,
                    suburb_norm TEXT NOT NULL
                );
                CREATE INDEX idx_streets_norm_state
                    ON streets(street_norm, state_key);
                """
            )
            connection.execute(
                """
                INSERT INTO streets (
                    street, street_norm, postal_code,
                    locality, locality_norm, regional_key, state_key,
                    borough, borough_norm, suburb, suburb_norm
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "Bergstr.", "bergstr", "74072",
                    "Locality label must not be normalized", "heilbronn",
                    "", "baden-wurttemberg",
                    "Borough label must not be normalized", "innenstadt",
                    "Suburb label must not be normalized", "altstadt",
                ),
            )
            connection.commit()
            connection.close()
            guarded_labels = {
                "Locality label must not be normalized",
                "Borough label must not be normalized",
                "Suburb label must not be normalized",
            }
            original_normalize = main.normalize_geocoder_text_variants

            def guarded_normalize(value):
                if str(value or "") in guarded_labels:
                    raise AssertionError("stored OpenPLZ norm was ignored")
                return original_normalize(value)

            with (
                patch.object(main, "OPENPLZ_DB", path),
                patch.object(
                    main,
                    "normalize_geocoder_text_variants",
                    side_effect=guarded_normalize,
                ),
            ):
                contexts = main._addressless_parcel_openplz_contexts(
                    {"context": "Bergstraße"},
                    {"baden-wurttemberg"},
                    main.sqlite_file_signature(path),
                )
        self.assertEqual(1, len(contexts))
        self.assertEqual(
            ("heilbronn", "innenstadt", "altstadt"),
            contexts[0]["gemarkung_norms"],
        )

    def test_contextual_plain_parcel_requires_context_and_rejects_postcodes(self) -> None:
        self.assertIsNone(main._contextual_parcel_query_parts("784"))
        self.assertIsNone(
            main._contextual_parcel_query_parts("Kurze Straße 74072")
        )
        explicit = main._contextual_parcel_query_parts(
            "Flurstück 74072 Kurze Straße"
        )
        self.assertIsNotNone(explicit)
        self.assertEqual("74072", explicit["flurstueck"])
        self.assertEqual("", explicit["postcode"])
        explicit_with_postcode = main._contextual_parcel_query_parts(
            "Flurstück 123456789 Kurze Straße 74072"
        )
        self.assertIsNotNone(explicit_with_postcode)
        self.assertEqual("123456789", explicit_with_postcode["flurstueck"])
        self.assertEqual("74072", explicit_with_postcode["postcode"])

    def test_exact_gemarkung_identity_does_not_treat_official_parenthetical_as_code(self) -> None:
        suggestion = {
            "gemarkung": "Freden (Leine)",
            "label": "Freden (Leine)",
            "gemarkungsnummer": "6006",
            "state": "niedersachsen",
            "state_label": "Niedersachsen",
        }
        with (
            patch.object(
                main,
                "search_gemarkung_suggestions_cached",
                return_value=(suggestion,),
            ),
            patch.object(main, "search_db_signature_for_states", return_value=tuple()),
        ):
            identities = main._exact_gemarkung_identities(
                "Freden (Leine)", {"niedersachsen"}
            )
        self.assertEqual(1, len(identities))
        self.assertEqual("Freden (Leine)", identities[0]["gemarkung"])
        self.assertEqual("6006", identities[0]["gemarkungsnummer"])

    def test_contextual_parcel_relation_is_parameterized_and_never_reads_features(self) -> None:
        query = "47/1 Holztorstraße' OR 1=1 --"
        with patch.object(
            main,
            "feature_db_entries_for_dataset",
            side_effect=AssertionError("features.sqlite must not be read"),
        ):
            payload = self.contextual_parcel_suggestions_from_fixture(
                query,
                {
                    "niedersachsen": [{
                        "gemarkung": "Sarstedt",
                        "gemarkungsnummer": "5108",
                        "flur": "13",
                        "flurstueck": "47/1",
                        "street": "Holztorstraße",
                        "city": "Sarstedt",
                        "postcode": "31157",
                    }],
                },
            )
        self.assertEqual([], payload["results"])

    def test_official_city_display_name_keeps_casing_and_qualifiers(self) -> None:
        with TemporaryDirectory() as raw_directory:
            path = Path(raw_directory) / "places.sqlite"
            connection = sqlite3.connect(path)
            connection.executescript(
                """
                CREATE TABLE places (
                    state_key TEXT NOT NULL,
                    name TEXT NOT NULL,
                    municipality TEXT,
                    class TEXT,
                    population INTEGER
                );
                CREATE INDEX idx_places_state_name ON places(state_key, name);
                INSERT INTO places VALUES (
                    'niedersachsen', 'Freden (Leine)', 'Freden (Leine)',
                    'Gemeinde', 5000
                );
                INSERT INTO places VALUES (
                    'baden_wuerttemberg', 'Stuttgart', 'Stuttgart',
                    'Gemeinde', 600000
                );
                """
            )
            connection.commit()
            connection.close()
            main._official_city_display_name_cached.cache_clear()
            with patch.object(main, "GN250_PLACES_DB", path):
                self.assertEqual(
                    "Freden (Leine)",
                    main._official_city_display_name_cached(
                        "freden", "niedersachsen", main.sqlite_file_signature(path)
                    ),
                )
                self.assertEqual(
                    "Stuttgart",
                    main._official_city_display_name_cached(
                        "stuttgart", "baden-wurttemberg", main.sqlite_file_signature(path)
                    ),
                )
            main._official_city_display_name_cached.cache_clear()

    def test_scoped_unified_street_uses_canonical_source_locality(self) -> None:
        parsed = {
            "query": "Maschstraße freden",
            "postcode": "",
            "place": "freden",
            "place_context": {"name": "Freden (Leine)"},
            "place_contexts": [],
            "street": "Maschstraße",
            "house_number": "",
            "has_house_number": False,
        }
        source = {
            "label": "Maschstraße",
            "value": "Maschstraße",
            "subtitle": "freden",
            "state": "niedersachsen",
            "state_label": "Niedersachsen",
            "result_type": "street",
            "kind": "street",
        }
        with (
            patch.object(main, "search_suggestion_states_for_dataset", return_value={"niedersachsen"}),
            patch.object(main, "parse_unified_address_query", return_value=parsed),
            patch.object(
                main,
                "search_street_suggestions_for_dataset",
                return_value={"results": [source]},
            ),
            patch.object(
                main,
                "city_display_name_for_state",
                return_value="Freden (Leine)",
            ),
        ):
            payload = main.search_unified_address_suggestions_for_dataset(
                "deutschland", "Maschstraße freden", 8
            )
        self.assertEqual("Freden (Leine)", payload["results"][0]["municipality"])
        self.assertIn("Freden (Leine)", payload["results"][0]["secondary_label"])

    def test_free_text_parcel_rejects_numeric_only_and_address_false_positives(self) -> None:
        rows = {
            "niedersachsen": [
                {
                    "gemarkung": "Hannover (0001)",
                    "gemarkungsnummer": "0001",
                    "flurstueck": "12",
                },
                {
                    "gemarkung": "Bemerode (4887)",
                    "gemarkungsnummer": "4887",
                    "flur": "1",
                    "flurstueck": "100/1",
                },
            ],
        }
        for query in (
            "100/1",
            "Hauptstraße 12 Hannover",
            "Am Kanal 1/2 Hannover",
            "Bemerode 100/1 Hannover",
            "Bemeroder Straße Hannover 100/1",
            "Hannover 30539",
            "Hannover' OR 1=1 -- 12",
        ):
            with self.subTest(query=query):
                self.assertEqual(
                    [],
                    self.parcel_suggestions_from_fixture(
                        query,
                        rows,
                        municipality="Hannover",
                    )["results"],
                )

    def test_free_text_parcel_keeps_exact_homonyms_fairly(self) -> None:
        result = self.parcel_suggestions_from_fixture(
            "Gemarkung Hausen Flurstück 7",
            {
                "baden-wurttemberg": [
                    {
                        "gemarkung": "Hausen (1000)",
                        "gemarkungsnummer": "1000",
                        "flurstueck": "7",
                    },
                ],
                "rheinland-pfalz": [
                    {
                        "gemarkung": "Hausen (1238)",
                        "gemarkungsnummer": "1238",
                        "flurstueck": "7",
                    },
                ],
            },
            limit=2,
        )
        self.assertEqual(
            {("baden-wurttemberg", "1000"), ("rheinland-pfalz", "1238")},
            {
                (item["state"], item["feature"]["gemarkungsnummer"])
                for item in result["results"]
            },
        )

    def test_mixed_suggestions_tag_both_scopes_and_rank_explicit_parcel_first(self) -> None:
        address = {
            "kind": "street",
            "result_type": "street",
            "label": "Bemeroder Straße",
        }
        parcel = {
            "kind": "parcel",
            "result_type": "feature",
            "search_scope": "parcel",
            "label": "Flurstück 100/1, Bemerode (4887)",
        }
        with (
            patch.object(
                main,
                "search_unified_address_suggestions_for_dataset",
                return_value={"results": [address]},
            ),
            patch.object(
                main,
                "search_free_text_parcel_suggestions_for_dataset",
                return_value={"explicit_signal": True, "results": [parcel]},
            ),
        ):
            result = main.search_unified_suggestions_for_dataset(
                "deutschland", "Flurstück 100/1 in Bemerode", 10
            )
        self.assertEqual(["parcel", "address"], [item["search_scope"] for item in result["results"]])

    def test_mixed_suggestions_rank_poi_before_generic_place_but_after_address(self) -> None:
        address = {
            "kind": "address",
            "result_type": "address",
            "label": "Hauptbahnhof 1, Hannover",
            "address": {"house_number": "1"},
        }
        place = {
            "kind": "place",
            "result_type": "place",
            "label": "Hannover",
            "value": "Hannover",
        }
        poi = {
            "kind": "poi",
            "result_type": "poi",
            "search_scope": "poi",
            "poi_id": "osm:n:42",
            "label": "Hannover Hauptbahnhof",
        }
        with (
            patch.object(
                main,
                "search_unified_address_suggestions_for_dataset",
                return_value={"results": [address, place], "_parsed_address": {}},
            ),
            patch.object(
                main,
                "search_free_text_parcel_suggestions_for_dataset",
                return_value={
                    "explicit_signal": False,
                    "strong_intent": False,
                    "results": [],
                },
            ),
            patch.object(
                main,
                "search_suggestion_states_for_dataset",
                return_value={"niedersachsen"},
            ),
            patch.object(main, "search_poi_suggestions", return_value=[poi]),
        ):
            result = main.search_unified_suggestions_for_dataset(
                "deutschland", "Hannover Hauptbahnhof", 10
            )
        self.assertEqual(
            ["address", "poi", "address"],
            [item["search_scope"] for item in result["results"]],
        )

    def test_mixed_suggestions_do_not_add_pois_to_plain_place_or_postcode(self) -> None:
        cases = (
            (
                "Hannover",
                {
                    "kind": "place",
                    "result_type": "place",
                    "label": "Hannover",
                    "value": "Hannover",
                },
                {"place": "Hannover", "street": ""},
            ),
            (
                "30539",
                {
                    "kind": "place",
                    "result_type": "place",
                    "label": "Hannover",
                    "value": "Hannover",
                },
                {},
            ),
            (
                "Han",
                {
                    "kind": "place",
                    "result_type": "place",
                    "label": "Hannover",
                    "value": "Hannover",
                },
                {"place": "", "street": "Han"},
            ),
            (
                "Hanno",
                {
                    "kind": "place",
                    "result_type": "place",
                    "label": "Hannover",
                    "value": "Hannover",
                },
                {"place": "", "street": "Hanno"},
            ),
            (
                "30539 Hannover",
                {
                    "kind": "place",
                    "result_type": "place",
                    "label": "Hannover",
                    "value": "Hannover",
                },
                {"postcode": "30539", "place": "Hannover", "street": ""},
            ),
        )
        for query, place, parsed in cases:
            with (
                self.subTest(query=query),
                patch.object(
                    main,
                    "search_unified_address_suggestions_for_dataset",
                    return_value={
                        "results": [place],
                        "_parsed_address": parsed,
                    },
                ),
                patch.object(
                    main,
                    "search_free_text_parcel_suggestions_for_dataset",
                    return_value={
                        "explicit_signal": False,
                        "strong_intent": False,
                        "results": [],
                    },
                ),
                patch.object(main, "search_poi_suggestions") as poi_search,
            ):
                result = main.search_unified_suggestions_for_dataset(
                    "deutschland", query, 10
                )
            poi_search.assert_not_called()
            self.assertEqual(["address"], [item["search_scope"] for item in result["results"]])

    def test_mixed_suggestions_rank_exact_plain_parcel_before_places(self) -> None:
        address = {
            "kind": "place",
            "result_type": "place",
            "label": "Hofen",
        }
        parcel = {
            "kind": "parcel",
            "result_type": "feature",
            "search_scope": "parcel",
            "label": "Flurstück 1066, Hofen (0976)",
        }
        with (
            patch.object(
                main,
                "search_unified_address_suggestions_for_dataset",
                return_value={"results": [address]},
            ),
            patch.object(
                main,
                "search_free_text_parcel_suggestions_for_dataset",
                return_value={
                    "explicit_signal": True,
                    "strong_intent": False,
                    "results": [parcel],
                },
            ),
        ):
            result = main.search_unified_suggestions_for_dataset(
                "deutschland", "Hofen 1066", 10
            )
        self.assertEqual(
            ["parcel", "address"],
            [item["search_scope"] for item in result["results"]],
        )

    def test_exact_slash_building_address_stays_before_linked_parcel(self) -> None:
        address = {
            "kind": "building",
            "result_type": "address",
            "address": {"house_number": "17/1"},
            "label": "Kröpeliner Str. 17/1, 18209 Bad Doberan",
        }
        parcel = {
            "kind": "parcel",
            "result_type": "feature",
            "search_scope": "parcel",
            "label": "Flurstück 17/1",
        }
        with (
            patch.object(
                main,
                "search_unified_address_suggestions_for_dataset",
                return_value={"results": [address]},
            ),
            patch.object(
                main,
                "search_free_text_parcel_suggestions_for_dataset",
                return_value={
                    "explicit_signal": True,
                    "strong_intent": False,
                    "results": [parcel],
                },
            ),
        ):
            result = main.search_unified_suggestions_for_dataset(
                "deutschland", "Kröpeliner Str. 17/1 Bad Doberan", 10
            )
        self.assertEqual(
            ["address", "parcel"],
            [item["search_scope"] for item in result["results"]],
        )

    def test_exact_plain_building_address_stays_first_and_reserves_parcel_slot(self) -> None:
        addresses = [
            {
                "kind": "building",
                "result_type": "address",
                "address": {"house_number": "12"},
                "label": f"Hauptstraße 12, Teststadt {index}",
            }
            for index in range(4)
        ]
        parcel = {
            "kind": "parcel",
            "result_type": "feature",
            "search_scope": "parcel",
            "label": "Flurstück 12",
        }
        with (
            patch.object(
                main,
                "search_unified_address_suggestions_for_dataset",
                return_value={"results": addresses},
            ),
            patch.object(
                main,
                "search_free_text_parcel_suggestions_for_dataset",
                return_value={
                    "explicit_signal": True,
                    "strong_intent": False,
                    "results": [parcel],
                },
            ),
            patch.object(
                main,
                "search_suggestion_states_for_dataset",
                return_value=set(),
            ),
        ):
            result = main.search_unified_suggestions_for_dataset(
                "deutschland",
                "Hauptstraße 12",
                3,
            )
        self.assertEqual(
            ["address", "address", "parcel"],
            [item["search_scope"] for item in result["results"]],
        )

    def test_street_only_result_does_not_outrank_linked_parcel(self) -> None:
        street = {
            "kind": "street",
            "result_type": "street",
            "label": "Holztorstraße, Sarstedt",
        }
        parcel = {
            "kind": "parcel",
            "result_type": "feature",
            "search_scope": "parcel",
            "label": "Flurstück 47/1, Sarstedt",
        }
        with (
            patch.object(
                main,
                "search_unified_address_suggestions_for_dataset",
                return_value={"results": [street]},
            ),
            patch.object(
                main,
                "search_free_text_parcel_suggestions_for_dataset",
                return_value={
                    "explicit_signal": True,
                    "strong_intent": False,
                    "results": [parcel],
                },
            ),
        ):
            result = main.search_unified_suggestions_for_dataset(
                "deutschland", "Holztorstraße 47/1", 10
            )
        self.assertEqual(
            ["parcel", "address"],
            [item["search_scope"] for item in result["results"]],
        )

    def test_complete_postcode_returns_all_openplz_localities(self) -> None:
        with TemporaryDirectory() as raw_directory:
            path = Path(raw_directory) / "openplz.sqlite"
            connection = sqlite3.connect(path)
            connection.executescript(
                """
                CREATE TABLE streets (
                    locality TEXT NOT NULL,
                    state_key TEXT NOT NULL,
                    postal_code TEXT NOT NULL
                );
                CREATE INDEX idx_test_postcode ON streets(postal_code);
                INSERT INTO streets VALUES ('Hannover', 'niedersachsen', '30539');
                INSERT INTO streets VALUES ('Alpha', 'niedersachsen', '12345');
                INSERT INTO streets VALUES ('Beta', 'niedersachsen', '12345');
                """
            )
            connection.commit()
            connection.close()
            main._openplz_postcode_places_cached.cache_clear()
            with (
                patch.object(main, "OPENPLZ_DB", path),
                patch.object(main, "openplz_signature", return_value=main.sqlite_file_signature(path)),
                patch.object(main, "_openplz_street_geometry", return_value=([9.0, 52.0], [8.9, 51.9, 9.1, 52.1])),
                patch.object(main, "city_display_name_for_state", side_effect=lambda city, _state: city),
            ):
                hannover = main.search_unified_address_suggestions_for_dataset(
                    "deutschland", "30539", 8
                )
                shared = main.search_unified_address_suggestions_for_dataset(
                    "deutschland", "12345", 8
                )
            main._openplz_postcode_places_cached.cache_clear()
        self.assertEqual(["Hannover"], [item["label"] for item in hannover["results"]])
        self.assertEqual(
            {"Alpha", "Beta"},
            {item["label"] for item in shared["results"]},
        )

    def test_explicit_missing_parcel_does_not_fall_back_to_a_place(self) -> None:
        with (
            patch.object(
                main,
                "search_unified_address_suggestions_for_dataset",
            ) as address_search,
            patch.object(
                main,
                "search_free_text_parcel_suggestions_for_dataset",
                return_value={
                    "explicit_signal": True,
                    "strong_intent": True,
                    "results": [],
                },
            ),
            patch.object(
                main,
                "parse_unified_address_query",
            ) as address_parser,
            patch.object(main, "search_poi_suggestions") as poi_search,
        ):
            result = main.search_unified_suggestions_for_dataset(
                "deutschland",
                "Flurstück 999999/999999 in Bemerode Hannover",
                10,
            )
        address_search.assert_not_called()
        address_parser.assert_not_called()
        poi_search.assert_not_called()
        self.assertEqual([], result["results"])

    def test_sources_only_expose_public_osm_metadata_when_index_is_active(self) -> None:
        with (
            patch.object(main, "_api_v1_state_rows", return_value=[]),
            patch.object(main, "poi_index_available", return_value=False),
        ):
            inactive = main.api_v1_sources()
        self.assertNotIn("poi", inactive)
        self.assertNotIn("attributions", inactive)

        with (
            patch.object(main, "_api_v1_state_rows", return_value=[]),
            patch.object(main, "poi_index_available", return_value=True),
            patch.object(
                main,
                "poi_index_metadata",
                return_value={
                    "created_at_utc": "2026-07-17T08:00:00Z",
                    "active_states": ["niedersachsen"],
                    "source_path": "/private/germany.osm.pbf",
                    "database_path": "/private/osm-poi.sqlite",
                },
            ),
        ):
            active = main.api_v1_sources()
        self.assertEqual("OpenStreetMap", active["poi"]["source"])
        self.assertEqual(
            "https://www.openstreetmap.org/copyright",
            active["attributions"][0]["href"],
        )
        serialized = repr(active)
        self.assertNotIn("/private/", serialized)

    def test_mixed_suggestion_and_parcel_analytics_contract_is_public(self) -> None:
        schema = main.app.openapi()
        parameters = schema["paths"]["/api/v1/suggest/search"]["get"]["parameters"]
        by_name = {parameter["name"]: parameter for parameter in parameters if parameter["in"] == "query"}
        self.assertTrue(by_name["q"]["required"])
        self.assertFalse(by_name["near_lon"]["required"])
        parcel_parameters = schema["paths"]["/api/v1/search/parcel"]["get"]["parameters"]
        parcel_names = {parameter["name"] for parameter in parcel_parameters if parameter["in"] == "query"}
        self.assertIn("analytics_query", parcel_names)
        poi_parameters = schema["paths"]["/api/v1/search/poi"]["get"]["parameters"]
        poi_names = {parameter["name"] for parameter in poi_parameters if parameter["in"] == "query"}
        self.assertIn("poi_id", poi_names)
        self.assertIn("analytics_query", poi_names)

    @unittest.skipUnless(
        MIXED_PARCEL_REFERENCES_AVAILABLE,
        "requires mounted free-text parcel reference databases",
    )
    def test_live_free_text_parcel_examples_resolve_exactly(self) -> None:
        cases = (
            (
                "Flurstück 100/1 in Bemerode Hannover",
                "niedersachsen",
                "4887",
                "1",
                "100/1",
            ),
            (
                "Bemerode Hannover 100/1",
                "niedersachsen",
                "4887",
                "1",
                "100/1",
            ),
            (
                "Hofen (0976) 1066",
                "baden-wurttemberg",
                "0976",
                "",
                "1066",
            ),
            (
                "Flurstück 4515/1 in Leipzig",
                "sachsen",
                "0415",
                "",
                "4515/1",
            ),
        )
        for query, state, code, expected_flur, parcel in cases:
            with self.subTest(query=query):
                result = main.search_free_text_parcel_suggestions_for_dataset(
                    "deutschland", query, 12, state=state
                )
                matches = [
                    item
                    for item in result["results"]
                    if item["feature"]["gemarkungsnummer"] == code
                    and item["parcel_search"]["flurstueck"] == parcel
                ]
                self.assertTrue(matches)
                self.assertEqual(expected_flur, matches[0]["parcel_search"]["flur"])

    def test_selected_free_text_parcel_records_original_typed_query(self) -> None:
        request = main.Request({
            "type": "http",
            "method": "GET",
            "path": "/api/v1/search/parcel",
            "headers": [],
        })
        payload = {"query": "Bemerode 1 100/1", "results": [{"kind": "parcel"}]}
        access = main.ApiAccessContext(mode="free", token="viewer")
        with (
            patch.object(main, "valid_analytics_marker", return_value=True),
            patch.object(main, "cached_search_features_for_dataset", return_value=payload),
            patch.object(main.SEARCH_ANALYTICS, "record_response") as record,
        ):
            returned = main.api_v1_search_parcel(
                request=request,
                gemarkung="Bemerode (4887)",
                flurstueck="100/1",
                access=access,
                flur="1",
                state="niedersachsen",
                analytics_query="Flurstück 100/1 in Bemerode Hannover",
                analytics_id="search-test",
                analytics_scope="parcel",
            )
        self.assertEqual(payload, returned)
        self.assertEqual(
            "Flurstück 100/1 in Bemerode Hannover",
            record.call_args.kwargs["query_text"],
        )

    def test_selected_poi_resolves_exact_id_and_records_original_typed_query(self) -> None:
        request = main.Request({
            "type": "http",
            "method": "GET",
            "path": "/api/v1/search/poi",
            "headers": [],
        })
        result = {
            "kind": "poi",
            "result_type": "poi",
            "search_scope": "poi",
            "poi_id": "osm:n:42",
            "label": "Hannover Hauptbahnhof",
            "state": "niedersachsen",
        }
        access = main.ApiAccessContext(mode="free", token="viewer")
        with (
            patch.object(main, "valid_analytics_marker", return_value=True),
            patch.object(main, "active_bucket_state_keys", return_value=("niedersachsen",)),
            patch.object(main, "search_poi_by_id", return_value=result) as resolve,
            patch.object(main.SEARCH_ANALYTICS, "record_response") as record,
        ):
            returned = main.api_v1_search_poi(
                request=request,
                access=access,
                poi_id="osm:n:42",
                analytics_query="Hannover Hauptbahnhof",
                analytics_id="search-test",
                analytics_scope="poi",
            )
        self.assertEqual([result], returned["results"])
        resolve.assert_called_once_with("osm:n:42", {"niedersachsen"})
        self.assertEqual(
            "Hannover Hauptbahnhof",
            record.call_args.kwargs["query_text"],
        )
        self.assertEqual(
            "niedersachsen",
            record.call_args.kwargs["state"],
        )

    def test_selected_poi_never_records_missing_or_technical_query_text(self) -> None:
        request = main.Request({
            "type": "http",
            "method": "GET",
            "path": "/api/v1/search/poi",
            "headers": [],
        })
        result = {
            "kind": "poi",
            "result_type": "poi",
            "search_scope": "poi",
            "poi_id": "osm:n:42",
            "label": "Hannover Hauptbahnhof",
            "state": "niedersachsen",
        }
        access = main.ApiAccessContext(mode="free", token="viewer")
        for analytics_query in ("", "   ", "osm:n:42", "n42", "w:77"):
            with self.subTest(analytics_query=analytics_query):
                with (
                    patch.object(main, "valid_analytics_marker", return_value=True),
                    patch.object(main, "active_bucket_state_keys", return_value=("niedersachsen",)),
                    patch.object(main, "search_poi_by_id", return_value=result),
                    patch.object(main.SEARCH_ANALYTICS, "record_response") as record,
                ):
                    returned = main.api_v1_search_poi(
                        request=request,
                        access=access,
                        poi_id="osm:n:42",
                        analytics_query=analytics_query,
                        analytics_id="search-test",
                        analytics_scope="poi",
                    )
                self.assertEqual(
                    analytics_query.strip() or "osm:n:42",
                    returned["query"],
                )
                record.assert_not_called()

    def test_city_norms_accept_prefixed_and_plain_city(self) -> None:
        self.assertIn("stadt dresden", main.city_norms_for_state_context("Dresden", "sachsen"))
        self.assertIn("dresden", main.city_norms_for_state_context("Stadt Dresden", "sachsen"))

    def test_city_norms_accept_bremen_municipality_alias(self) -> None:
        self.assertIn(
            "stadtgemeinde bremen",
            main.city_norms_for_state_context("Bremen", "bremen"),
        )

    def test_umlaut_transliteration_does_not_collapse_ordinary_ue(self) -> None:
        variants = main.normalize_geocoder_text_variants("Suederquerweg")
        self.assertIn("suderquerweg", variants)
        self.assertIn("suederquerweg", variants)

    def test_postcode_city_is_replaced_by_requested_city(self) -> None:
        self.assertEqual(
            "Stuttgart",
            main.search_result_city_label("70184", "70184", "baden-wurttemberg", "Stuttgart"),
        )

    def test_spaced_house_suffix_stays_with_house_number(self) -> None:
        candidates = main.geocoder_direct_candidates("Mühlenweg 8 a Loose")
        self.assertEqual(
            ("address", "Mühlenweg", "8 a", "Loose"),
            candidates[0],
        )

    def test_spaced_house_range_stays_with_house_number(self) -> None:
        candidates = main.geocoder_direct_candidates("Hauptstraße 18 - 20 Dresden")
        self.assertEqual(
            ("address", "Hauptstraße", "18-20", "Dresden"),
            candidates[0],
        )
        self.assertEqual(
            "",
            main.search_result_city_label("70184", "70184", "baden-wurttemberg"),
        )

    def test_house_number_semantics_preserve_meaningful_separators(self) -> None:
        self.assertEqual(
            main.normalize_house_number_semantic("17 B7"),
            main.normalize_house_number_semantic("17 b7"),
        )
        self.assertEqual(
            main.normalize_house_number_semantic("1 1⁄10"),
            main.normalize_house_number_semantic("1 1/10"),
        )
        for typed, stored in (
            ("101", "10/1"),
            ("1719", "17/19"),
            ("1ad", "1A-D"),
            ("33a1", "33 a - 1"),
        ):
            with self.subTest(typed=typed, stored=stored):
                self.assertNotEqual(
                    main.normalize_house_number_semantic(typed),
                    main.normalize_house_number_semantic(stored),
                )

    def test_fallback_place_context_rejects_neighbor_rows(self) -> None:
        rows = [
            {
                "lon": 7.5,
                "lat": 48.5,
                "post_code": "12345",
                "city_label": "12345",
                "city_norm": "12345",
            },
            {
                "lon": 7.5,
                "lat": 48.5,
                "post_code": "12345",
                "city_label": "Nachbarort",
                "city_norm": "nachbarort",
            },
            {
                "lon": 8.5,
                "lat": 48.5,
                "post_code": "12345",
                "city_label": "Zielort",
                "city_norm": "zielort",
            },
            {
                "lon": 7.6,
                "lat": 48.6,
                "post_code": "12345",
                "city_label": "Veralteter Ort",
                "city_norm": "veralteter ort",
            },
            {
                "lon": 7.5,
                "lat": 48.5,
                "post_code": "12345",
                "city_label": "",
                "city_norm": "nachbarort",
            },
        ]
        with (
            patch.object(
                main,
                "openplz_place_comparison_norms",
                return_value=("zielort",),
            ),
            patch.object(
                main,
                "city_norms_for_state_context",
                side_effect=lambda value, _state: (main.normalize_geocoder_text(value),),
            ),
            patch.object(
                main,
                "gn250_place_bboxes_for_state_context",
                side_effect=lambda value, _state, _signature: (
                    ((7.0, 48.0, 8.0, 49.0),)
                    if main.normalize_geocoder_text(value) == "nachbarort"
                    else (((9.0, 50.0, 10.0, 51.0),) if value else tuple())
                ),
            ),
            patch.object(main, "postcode_area_lookup", return_value="12345"),
        ):
            accepted = main.filter_address_rows_by_place_context(
                rows,
                "Zielort",
                "test-state",
                ((7.0, 48.0, 8.0, 49.0),),
                (1, 1),
            )
        self.assertEqual([rows[0], rows[3]], accepted)

    def test_structured_address_fields_do_not_round_trip_through_free_text(self) -> None:
        self.assertEqual(
            (("address", "Altenkesseler Straße", "17 B7", "Saarbrücken"),),
            main.structured_geocoder_candidates(
                "Altenkesseler Straße", "17 B7", "Saarbrücken"
            ),
        )

    def test_free_text_parser_keeps_multi_part_house_numbers_together(self) -> None:
        cases = (
            (
                "Altenkesseler Straße 17 B7 Saarbrücken",
                ("address", "Altenkesseler Straße", "17 B7", "Saarbrücken"),
            ),
            (
                "Östliche Ringstraße 1 1/10 Karben",
                ("address", "Östliche Ringstraße", "1 1/10", "Karben"),
            ),
            (
                "Chausseestraße 33 a - 1 Beetzsee",
                ("address", "Chausseestraße", "33 a-1", "Beetzsee"),
            ),
        )
        for query, expected in cases:
            with self.subTest(query=query):
                self.assertEqual(expected, main.geocoder_direct_candidates(query)[0])

    def test_unified_parser_accepts_street_and_house_without_a_place(self) -> None:
        parsed = main.parse_unified_address_query(
            "  Hauptstraße 12  ",
            {"nordrhein-westfalen", "baden-wurttemberg"},
        )
        self.assertEqual("Hauptstraße 12", parsed["query"])
        self.assertEqual("", parsed["postcode"])
        self.assertIsNone(parsed["place_context"])
        self.assertEqual("Hauptstraße", parsed["street"])
        self.assertEqual("12", parsed["house_number"])
        self.assertTrue(parsed["has_house_number"])

    def test_unified_parser_accepts_city_before_street_and_house(self) -> None:
        place_context = {
            "state": "nordrhein-westfalen",
            "name": "Köln",
            "folded": "koeln",
            "bbox": [6.75, 50.83, 7.16, 51.09],
            "municipality": "Köln",
        }
        with patch.object(
            main,
            "_unified_exact_place_span",
            return_value=("Köln", (place_context,), "Aachener Straße 1"),
        ):
            parsed = main.parse_unified_address_query(
                "Köln Aachener Straße 1",
                {"nordrhein-westfalen"},
            )
        self.assertEqual(place_context, parsed["place_context"])
        self.assertEqual("Aachener Straße", parsed["street"])
        self.assertEqual("1", parsed["house_number"])
        self.assertTrue(parsed["has_house_number"])

    def test_unified_parser_extracts_a_leading_postcode_before_parsing(self) -> None:
        place_context = {
            "state": "nordrhein-westfalen",
            "name": "Köln",
            "folded": "koeln",
            "bbox": [6.75, 50.83, 7.16, 51.09],
            "municipality": "Köln",
        }
        with patch.object(
            main,
            "_unified_exact_place_span",
            return_value=("Köln", (place_context,), "Hohe Straße 1"),
        ):
            parsed = main.parse_unified_address_query(
                "50667 Köln Hohe Straße 1",
                {"nordrhein-westfalen"},
            )
        self.assertEqual("50667", parsed["postcode"])
        self.assertEqual(place_context, parsed["place_context"])
        self.assertEqual("Hohe Straße", parsed["street"])
        self.assertEqual("1", parsed["house_number"])
        self.assertTrue(parsed["has_house_number"])

    def test_unified_street_prefix_suggestions_collect_multiple_states(self) -> None:
        result = self.unified_address_suggestions_from_fixture(
            "Hauptst",
            {
                "baden-wurttemberg": [
                    (
                        "hauptstrasse",
                        "Hauptstraße",
                        "stuttgart",
                        "Stuttgart",
                        "70173",
                        50,
                        9.1829,
                        48.7758,
                    ),
                ],
                "nordrhein-westfalen": [
                    (
                        "hauptstrasse",
                        "Hauptstraße",
                        "koeln",
                        "Köln",
                        "50667",
                        20,
                        6.9603,
                        50.9375,
                    ),
                ],
            },
        )
        labels = {item["label"] for item in result["results"]}
        self.assertEqual(
            {
                "Hauptstraße, 70173 Stuttgart",
                "Hauptstraße, 50667 Köln",
            },
            labels,
        )
        self.assertTrue(
            all(item["result_type"] == "street" for item in result["results"])
        )

    def test_unified_ranking_prefers_explicit_postcode_and_place_over_distance(self) -> None:
        parsed = {
            "query": "50667 Köln Hauptstraße 12",
            "postcode": "50667",
            "place_context": {
                "state": "nordrhein-westfalen",
                "name": "Köln",
                "folded": "koeln",
            },
            "street": "Hauptstraße",
            "house_number": "12",
            "has_house_number": True,
        }
        nearby_wrong_context = {
            "kind": "building",
            "result_type": "address",
            "label": "Hauptstraße 12, 70173 Stuttgart",
            "state": "baden-wurttemberg",
            "center": [9.1829, 48.7758],
            "address": {
                "street": "Hauptstraße",
                "house_number": "12",
                "post_code": "70173",
                "city": "Stuttgart",
            },
        }
        far_exact_context = {
            "kind": "building",
            "result_type": "address",
            "label": "Hauptstraße 12, 50667 Köln",
            "state": "nordrhein-westfalen",
            "center": [6.9603, 50.9375],
            "address": {
                "street": "Hauptstraße",
                "house_number": "12",
                "post_code": "50667",
                "city": "Köln",
            },
        }
        ranked = main.rank_unified_search_results(
            [nearby_wrong_context, far_exact_context],
            parsed,
            near_lon=9.1829,
            near_lat=48.7758,
        )
        self.assertEqual(far_exact_context["label"], ranked[0]["label"])

    def test_unified_ranking_uses_map_proximity_without_explicit_context(self) -> None:
        parsed = {
            "query": "Hauptstraße 12",
            "postcode": "",
            "place_context": None,
            "street": "Hauptstraße",
            "house_number": "12",
            "has_house_number": True,
        }
        far_result = {
            "kind": "building",
            "result_type": "address",
            "label": "Hauptstraße 12, 70173 Stuttgart",
            "state": "baden-wurttemberg",
            "center": [9.1829, 48.7758],
            "address": {
                "street": "Hauptstraße",
                "house_number": "12",
                "post_code": "70173",
                "city": "Stuttgart",
            },
        }
        near_result = {
            "kind": "building",
            "result_type": "address",
            "label": "Hauptstraße 12, 50667 Köln",
            "state": "nordrhein-westfalen",
            "center": [6.9603, 50.9375],
            "address": {
                "street": "Hauptstraße",
                "house_number": "12",
                "post_code": "50667",
                "city": "Köln",
            },
        }
        ranked = main.rank_unified_search_results(
            [far_result, near_result],
            parsed,
            near_lon=6.9603,
            near_lat=50.9375,
        )
        self.assertEqual(near_result["label"], ranked[0]["label"])

    def test_unified_address_suggestion_api_exposes_one_line_query_and_bias(self) -> None:
        parameters = main.app.openapi()["paths"]["/api/v1/suggest/addresses"]["get"][
            "parameters"
        ]
        by_name = {
            parameter["name"]: parameter
            for parameter in parameters
            if parameter["in"] == "query"
        }
        self.assertTrue(by_name["q"]["required"])
        self.assertFalse(by_name["state"]["required"])
        self.assertFalse(by_name["near_lon"]["required"])
        self.assertFalse(by_name["near_lat"]["required"])
        self.assertFalse(by_name["limit"]["required"])

    def test_city_context_variants_handle_ot_and_kurort_generically(self) -> None:
        kindelbrueck = main.city_norms_for_state_context(
            "Kindelbrück OT Düppel", "thueringen"
        )
        self.assertIn("kindelbruck", kindelbrueck)
        self.assertIn("duppel", kindelbrueck)
        self.assertIn(
            "schmalkalden kurort",
            main.city_norms_for_state_context("Schmalkalden", "thueringen"),
        )
        self.assertIn(
            "stadtgemeinde bremerhaven",
            main.city_norms_for_state_context("Bremerhaven", "bremen"),
        )
        self.assertIn(
            "Oldenburg",
            main.gn250_place_name_aliases("Oldenburg (Oldb)", "niedersachsen"),
        )

    @unittest.skipUnless(LIVE_REFERENCES_AVAILABLE, "requires mounted OpenKataster reference databases")
    def test_sachsen_suggestions_use_plain_place_name(self) -> None:
        result = main.search_street_suggestions_for_dataset(
            "deutschland", "Dresden", "Pra", 8, state="sachsen"
        )
        self.assertIn("Prager Straße", [item["label"] for item in result["results"]])

    @unittest.skipUnless(LIVE_REFERENCES_AVAILABLE, "requires mounted OpenKataster reference databases")
    def test_bw_suggestions_use_openplz_and_selected_place(self) -> None:
        result = main.search_street_suggestions_for_dataset(
            "deutschland", "Stuttgart", "Aach", 8, state="baden-wurttemberg"
        )
        self.assertIn(
            ("Aachener Straße", "Stuttgart"),
            [(item["label"], item["subtitle"]) for item in result["results"]],
        )

    @unittest.skipUnless(LIVE_REFERENCES_AVAILABLE, "requires mounted OpenKataster reference databases")
    def test_bw_parcel_search_accepts_empty_flur_and_keeps_gemarkung_code(self) -> None:
        results = main.search_fast_cadastre_parcels_for_dataset(
            "Hofen (0976)", "", "1066", 12, {"baden-wurttemberg"}
        )
        self.assertTrue(results)
        self.assertTrue(all(item["feature"]["gemarkungsnummer"] == "0976" for item in results))
        self.assertEqual("Flurstück 1066, Hofen (0976)", results[0]["label"])

    @unittest.skipUnless(LIVE_REFERENCES_AVAILABLE, "requires mounted OpenKataster reference databases")
    def test_supplied_flur_is_strict(self) -> None:
        valid = main.search_fast_cadastre_parcels_for_dataset(
            "Bietigheim (1000)", "1", "771/1", 12, {"baden-wurttemberg"}
        )
        invalid = main.search_fast_cadastre_parcels_for_dataset(
            "Bietigheim (1000)", "999999", "771/1", 12, {"baden-wurttemberg"}
        )
        self.assertTrue(valid)
        self.assertEqual([], invalid)

    @unittest.skipUnless(LIVE_REFERENCES_AVAILABLE, "requires mounted OpenKataster reference databases")
    def test_legacy_compact_parcel_keys_do_not_mix_slash_positions(self) -> None:
        for number in ("1/11", "11/1", "111"):
            with self.subTest(number=number):
                results = main.search_fast_cadastre_parcels_for_dataset(
                    "Reicholzheim (0021)", "", number, 12, {"baden-wurttemberg"}
                )
                self.assertEqual([number], [item["feature"]["flurstueck"] for item in results])

    @unittest.skipUnless(LIVE_REFERENCES_AVAILABLE, "requires mounted OpenKataster reference databases")
    def test_optional_flur_can_return_multiple_disambiguated_results(self) -> None:
        results = main.search_fast_cadastre_parcels_for_dataset(
            "Elberfeld (3135)", "", "16", 12, {"nordrhein-westfalen"}
        )
        self.assertGreater(len(results), 1)
        self.assertTrue(all(item["feature"]["flur"] for item in results))
        self.assertGreater(len({item["feature"]["flur"] for item in results}), 1)

    @unittest.skipUnless(LIVE_REFERENCES_AVAILABLE, "requires mounted OpenKataster reference databases")
    def test_gemarkung_suggestions_keep_homonyms_with_distinct_codes(self) -> None:
        result = main.search_gemarkung_suggestions_for_dataset(
            "deutschland", "Hofen", 8, state="baden-wurttemberg"
        )
        codes = {item["gemarkungsnummer"] for item in result["results"]}
        self.assertIn("0976", codes)
        self.assertIn("2384", codes)

    def test_gemarkung_suggestions_use_producer_umlaut_normalization(self) -> None:
        rows = self.gemarkung_suggestions_from_fixture(
            "Überseehafen",
            8,
            {
                "baden-wurttemberg": [
                    ("uberseehafener feld", "Überseehafener Feld (1000)", "1000", 50),
                ],
                "bremen": [
                    ("uberseehafen", "Überseehafen (0009)", "0009", 5),
                ],
            },
        )
        self.assertEqual(
            [("bremen", "0009")],
            [(item["state"], item["gemarkungsnummer"]) for item in rows],
        )

    def test_exact_gemarkung_outranks_prefixes_from_earlier_states(self) -> None:
        rows = self.gemarkung_suggestions_from_fixture(
            "Hemme",
            8,
            {
                "baden-wurttemberg": [
                    (f"hemmendorf {index}", f"Hemmendorf {index} ({index:04d})", f"{index:04d}", 20)
                    for index in range(1, 9)
                ],
                "schleswig-holstein": [
                    ("hemme", "Hemme (3324)", "3324", 1),
                ],
            },
        )
        self.assertEqual(
            [("schleswig-holstein", "3324")],
            [(item["state"], item["gemarkungsnummer"]) for item in rows],
        )

    def test_full_gemarkung_label_filters_by_displayed_code(self) -> None:
        rows = self.gemarkung_suggestions_from_fixture(
            "Hausen (5933)",
            8,
            {
                "baden-wurttemberg": [
                    ("hausen", "Hausen (1000)", "1000", 100),
                    ("hausen", "Hausen (5933)", "5933", 1),
                ],
            },
        )
        self.assertEqual(
            ["5933"],
            [item["gemarkungsnummer"] for item in rows],
        )

    def test_primary_gemarkung_prefix_outranks_digraph_fallback(self) -> None:
        rows = self.gemarkung_suggestions_from_fixture(
            "Neuenk",
            8,
            {
                "saarland": [
                    ("neunkirchen", "Neunkirchen (0001)", "0001", 100),
                    ("neuenkirchen", "Neuenkirchen (0002)", "0002", 1),
                ],
            },
        )
        self.assertEqual(
            ["0002", "0001"],
            [item["gemarkungsnummer"] for item in rows],
        )

    def test_viewer_limit_keeps_all_current_hausen_homonyms_selectable(self) -> None:
        baden_codes = [f"{index:04d}" for index in range(1, 17)] + ["5933"]
        rheinland_codes = ["1238"] + [f"9{index:03d}" for index in range(1, 12)]
        rows = self.gemarkung_suggestions_from_fixture(
            "Hausen",
            50,
            {
                "baden-wurttemberg": [
                    ("hausen", f"Hausen ({code})", code, len(baden_codes) - index)
                    for index, code in enumerate(baden_codes)
                ],
                "rheinland-pfalz": [
                    ("hausen", f"Hausen ({code})", code, len(rheinland_codes) - index)
                    for index, code in enumerate(rheinland_codes)
                ],
            },
        )
        identities = {(item["state"], item["gemarkungsnummer"]) for item in rows}
        self.assertEqual(29, len(rows))
        self.assertIn(("baden-wurttemberg", "5933"), identities)
        self.assertIn(("rheinland-pfalz", "1238"), identities)

    def test_gemarkung_suggestion_api_accepts_viewer_limit(self) -> None:
        parameters = main.app.openapi()["paths"]["/api/v1/suggest/gemarkungen"]["get"]["parameters"]
        limit_parameter = next(
            parameter for parameter in parameters if parameter["name"] == "limit"
        )
        self.assertEqual(50, limit_parameter["schema"]["maximum"])

    @unittest.skipUnless(
        GEMARKUNG_REFERENCES_AVAILABLE,
        "requires mounted OpenKataster Gemarkung databases",
    )
    def test_live_gemarkung_edge_cases_are_selectable(self) -> None:
        cases = (
            ("Überseehafen", "bremen", "0009"),
            ("Hemme", "schleswig-holstein", "3324"),
            ("Hausen", "baden-wurttemberg", "5933"),
            ("Hausen", "rheinland-pfalz", "1238"),
        )
        for query, state, number in cases:
            with self.subTest(query=query, state=state, number=number):
                result = main.search_gemarkung_suggestions_for_dataset(
                    "deutschland", query, 50
                )
                identities = {
                    (item["state"], item["gemarkungsnummer"])
                    for item in result["results"]
                }
                self.assertIn((state, number), identities)

    @unittest.skipUnless(
        CENTRAL_ADDRESS_REFERENCES_AVAILABLE,
        "requires mounted central address reference databases",
    )
    def test_legacy_house_keys_never_return_separator_collisions(self) -> None:
        cases = (
            (
                "Am Hang 101 69181",
                "Am Hang 10/1 69181",
                "baden-wurttemberg",
                "Am Hang 10/1",
            ),
            (
                "Schlossweiherstraße 1719 Aachen",
                "Schlossweiherstraße 17/19 Aachen",
                "nordrhein-westfalen",
                "Schlossweiherstraße 17/19",
            ),
        )
        for false_query, true_query, state, expected in cases:
            with self.subTest(state=state):
                self.assertEqual(
                    [],
                    main.search_direct_geocoder_for_dataset(
                        false_query, 12, {state}
                    ),
                )
                positive = main.search_direct_geocoder_for_dataset(
                    true_query, 12, {state}
                )
                self.assertTrue(positive)
                self.assertTrue(all(expected in item["label"] for item in positive))

    @unittest.skipUnless(
        CENTRAL_ADDRESS_REFERENCES_AVAILABLE,
        "requires mounted central address reference databases",
    )
    def test_street_postcode_fallback_never_relabels_neighbor_addresses(self) -> None:
        cases = (
            ("brandenburg", "Lindenstraße", "11", "Alt Tucheband"),
            (
                "schleswig-holstein",
                "Massower Straße",
                "19",
                "Klein Pampau",
            ),
        )
        for state, street, house, city in cases:
            with self.subTest(state=state, city=city):
                results = main.search_direct_geocoder_for_dataset(
                    " ",
                    12,
                    {state},
                    candidate_override=main.structured_geocoder_candidates(
                        street,
                        house,
                        city,
                    ),
                )
                self.assertEqual([], results)

    @unittest.skipUnless(
        CENTRAL_ADDRESS_REFERENCES_AVAILABLE,
        "requires mounted central address reference databases",
    )
    def test_same_city_addresses_remain_visible_without_a_postcode(self) -> None:
        results = main.search_direct_geocoder_for_dataset(
            " ",
            12,
            {"saarland"},
            candidate_override=main.structured_geocoder_candidates(
                "Pfählerstraße",
                "14",
                "Saarbrücken",
            ),
        )
        labels = {item["label"] for item in results}
        self.assertIn("Pfählerstraße 14, 66125 Saarbrücken", labels)
        self.assertIn("Pfählerstraße 14, 66128 Saarbrücken", labels)

    @unittest.skipUnless(
        CENTRAL_ADDRESS_REFERENCES_AVAILABLE,
        "requires mounted central address reference databases",
    )
    def test_context_recovery_keeps_official_titles_and_stale_city_rows(self) -> None:
        cases = (
            (
                "niedersachsen",
                "Theodor-Francksen-Straße",
                "90",
                "Oldenburg",
                "Theodor-Francksen-Straße 90, 26123 Oldenburg",
            ),
            (
                "bremen",
                "Anton-Schumacher-Straße",
                "20",
                "Bremerhaven",
                "Anton-Schumacher-Straße 20, 27568 Bremerhaven",
            ),
            (
                "rheinland-pfalz",
                "Karlstraße",
                "31",
                "Wörth am Rhein",
                "Karlstraße 31, 76744 Wörth am Rhein",
            ),
            (
                "brandenburg",
                "Klein Jamno Nr.",
                "25",
                "Forst (Lausitz)",
                "Klein Jamno Nr. 25, 03149 Forst (Lausitz)",
            ),
            (
                "mecklenburg-vorpommern",
                "Neue Straße",
                "6",
                "Wustrow",
                "Neue Str. 6, 18347 Wustrow",
            ),
        )
        for state, street, house, city, expected in cases:
            with self.subTest(state=state, city=city):
                results = main.search_direct_geocoder_for_dataset(
                    " ",
                    12,
                    {state},
                    candidate_override=main.structured_geocoder_candidates(
                        street,
                        house,
                        city,
                    ),
                )
                self.assertIn(expected, [item["label"] for item in results])

    @unittest.skipUnless(
        CENTRAL_ADDRESS_REFERENCES_AVAILABLE,
        "requires mounted central address reference databases",
    )
    def test_structured_address_edge_cases_resolve_centrally(self) -> None:
        cases = (
            (
                "Altenkesseler Straße", "17 B7", "Saarbrücken", "saarland",
                "Altenkesseler Straße 17 b7, 66115 Saarbrücken",
            ),
            (
                "Bergstraße", "28a", "Schmalkalden", "thueringen",
                "Bergstraße 28a, 98574 Schmalkalden",
            ),
            (
                "Mittelgasse", "1", "Schönbrunn", "thueringen",
                "Mittelgasse 1, 98667 Schönbrunn",
            ),
            (
                "Röblingstraße", "7", "Mühlhausen", "thueringen",
                "Röblingstraße 7, 99974 Mühlhausen",
            ),
            (
                "Dorfstraße", "21", "Kindelbrück OT Düppel", "thueringen",
                "Dorfstraße 21, 99638 Kindelbrück OT Düppel",
            ),
            (
                "Guldengasse", "35", "Wyhl am Kaiserstuhl", "baden-wurttemberg",
                "Guldengasse 35, 79369 Wyhl am Kaiserstuhl",
            ),
            (
                "Hauptstraße", "44", "Endingen am Kaiserstuhl", "baden-wurttemberg",
                "Hauptstraße 44, 79346 Endingen am Kaiserstuhl",
            ),
            (
                "Feriendorf Freizeitcenter", "33", "Rheinmünster", "baden-wurttemberg",
                "Feriendorf Freizeitcenter 33, 77836 Rheinmünster",
            ),
        )
        for street, house, city, state, expected in cases:
            with self.subTest(street=street, city=city):
                results = main.search_direct_geocoder_for_dataset(
                    " ",
                    12,
                    {state},
                    candidate_override=main.structured_geocoder_candidates(
                        street, house, city
                    ),
                )
                self.assertIn(expected, [item["label"] for item in results])

    @unittest.skipUnless(
        CENTRAL_ADDRESS_REFERENCES_AVAILABLE,
        "requires mounted central address reference databases",
    )
    def test_place_and_street_suggestions_keep_context_aliases_selectable(self) -> None:
        places = main.search_place_suggestions_for_dataset(
            "deutschland", "Mühlhausen", 8
        )["results"]
        self.assertIn(
            ("Mühlhausen/Thüringen", "thueringen"),
            [(item["label"], item["state"]) for item in places],
        )
        cases = (
            ("Mühlhausen", "Röbl", "thueringen", "Röblingstraße"),
            ("Schönbrunn", "Mitt", "thueringen", "Mittelgasse"),
            ("Wyhl am Kaiserstuhl", "Guld", "baden-wurttemberg", "Guldengasse"),
            ("Endingen am Kaiserstuhl", "Haupt", "baden-wurttemberg", "Hauptstraße"),
            ("Rheinmünster", "Feri", "baden-wurttemberg", "Feriendorf Freizeitcenter"),
        )
        for place, query, state, expected in cases:
            with self.subTest(place=place, query=query):
                result = main.search_street_suggestions_for_dataset(
                    "deutschland", place, query, 8, state=state
                )
                self.assertIn(expected, [item["label"] for item in result["results"]])

    @unittest.skipUnless(
        CENTRAL_ADDRESS_REFERENCES_AVAILABLE,
        "requires mounted central address reference databases",
    )
    def test_openplz_state_slug_aliases_are_resolved_from_the_database(self) -> None:
        self.assertIn("thuringen", main.openplz_storage_state_keys("thueringen"))

    @unittest.skipUnless(LIVE_REFERENCES_AVAILABLE, "requires mounted OpenKataster reference databases")
    def test_direct_search_is_place_scoped_and_labels_are_not_duplicated(self) -> None:
        dresden = main.search_direct_geocoder_for_dataset(
            "Hauptstraße 1 Dresden", 20, {"sachsen"}
        )
        self.assertTrue(dresden)
        self.assertTrue(all("Dresden" in item["label"] for item in dresden))

        stuttgart = main.search_direct_geocoder_for_dataset(
            "Alexanderstraße 1 Stuttgart", 12, {"baden-wurttemberg"}
        )
        self.assertEqual(
            ["Alexanderstraße 1, 70184 Stuttgart"],
            [item["label"] for item in stuttgart],
        )

    @unittest.skipUnless(
        ADDRESS_FALLBACK_REFERENCES_AVAILABLE,
        "requires mounted OpenKataster address and OpenPLZ databases",
    )
    def test_unique_postcode_proof_is_state_and_locality_scoped(self) -> None:
        self.assertEqual(
            ("74219",),
            main.openplz_unique_postcodes_for_place(
                ("74219",), "Möckmühl", "baden-wurttemberg"
            ),
        )
        self.assertEqual(
            ("56075",),
            main.openplz_unique_postcodes_for_place(
                ("56075",), "Koblenz", "rheinland-pfalz"
            ),
        )
        self.assertEqual(
            (),
            main.openplz_unique_postcodes_for_place(
                ("15537",), "Treptow-Köpenick", "berlin"
            ),
        )

    @unittest.skipUnless(
        ADDRESS_FALLBACK_REFERENCES_AVAILABLE,
        "requires mounted OpenKataster address and OpenPLZ databases",
    )
    def test_exact_address_recovery_remains_building_only(self) -> None:
        cases = (
            ("Raiffeisenweg 4 Möckmühl", {"baden-wurttemberg"}),
            ("Vosshaller Weg 18 Bremen", {"bremen"}),
            ("Zum Domherrenwald 1 A Koblenz", {"rheinland-pfalz"}),
        )
        for query, states in cases:
            with self.subTest(query=query):
                results = main.search_direct_geocoder_for_dataset(query, 12, states)
                self.assertTrue(results)
                self.assertTrue(all(item["kind"] == "building" for item in results))

    @unittest.skipUnless(
        ADDRESS_FALLBACK_REFERENCES_AVAILABLE,
        "requires mounted OpenKataster address and OpenPLZ databases",
    )
    def test_street_suggestion_recovery_is_locality_scoped(self) -> None:
        cases = (
            ("Möckmühl", "Raiff", "baden-wurttemberg", "Raiffeisenweg"),
            ("Bremen", "Vossh", "bremen", "Vosshaller Weg"),
            ("Koblenz", "Zum Dom", "rheinland-pfalz", "Zum Domherrenwald"),
        )
        for place, query, state, expected in cases:
            with self.subTest(place=place, query=query):
                result = main.search_street_suggestions_for_dataset(
                    "deutschland", place, query, 8, state=state
                )
                self.assertIn(expected, [item["label"] for item in result["results"]])

    @unittest.skipUnless(
        ADDRESS_FALLBACK_REFERENCES_AVAILABLE,
        "requires mounted OpenKataster address and OpenPLZ databases",
    )
    def test_inconsistent_berlin_postcode_is_not_recovered(self) -> None:
        self.assertEqual(
            [],
            main.search_direct_geocoder_for_dataset(
                "Am Zwiebusch 57 Treptow-Köpenick", 12, {"berlin"}
            ),
        )


if __name__ == "__main__":
    unittest.main()
