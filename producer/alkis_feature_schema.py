"""SQLite schema shared by the ALKIS feature producer and merge tool."""

from __future__ import annotations

import sqlite3


def create_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE features (
            id INTEGER PRIMARY KEY,
            state_key TEXT,
            kind TEXT NOT NULL,
            source_db TEXT NOT NULL,
            gml_id TEXT NOT NULL,
            properties_json TEXT NOT NULL,
            geometry_wkb BLOB NOT NULL,
            center_lon REAL,
            center_lat REAL,
            min_lon REAL NOT NULL,
            max_lon REAL NOT NULL,
            min_lat REAL NOT NULL,
            max_lat REAL NOT NULL,
            UNIQUE(kind, gml_id)
        );
        CREATE VIRTUAL TABLE feature_index USING rtree(
            id,
            min_lon,
            max_lon,
            min_lat,
            max_lat
        );
        CREATE TABLE address_points (
            id INTEGER PRIMARY KEY,
            source_db TEXT NOT NULL,
            properties_json TEXT NOT NULL,
            geometry_wkb BLOB NOT NULL,
            lon REAL NOT NULL,
            lat REAL NOT NULL
        );
        CREATE VIRTUAL TABLE address_index USING rtree(
            id,
            min_lon,
            max_lon,
            min_lat,
            max_lat
        );
        CREATE TABLE feature_addresses (
            id INTEGER PRIMARY KEY,
            source_db TEXT NOT NULL,
            kind TEXT NOT NULL,
            gml_id TEXT NOT NULL,
            properties_json TEXT NOT NULL
        );
        CREATE UNIQUE INDEX idx_feature_addresses_unique
            ON feature_addresses(source_db, kind, gml_id, properties_json);
        """
    )
