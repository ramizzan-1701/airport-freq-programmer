"""Loads NormalizedData into an in-memory SQLite database, indexed on
every column a §3 filter touches, so filter interactions are fast WHERE
queries (§2) rather than repeated Python-side scans of ~20k airports and
~40k frequencies.
"""

from __future__ import annotations

import sqlite3

from ..schema import NormalizedData
from .geo import haversine_nm

_SCHEMA = """
CREATE TABLE airports (
    id TEXT PRIMARY KEY,
    name TEXT,
    city TEXT,
    state TEXT,
    country TEXT,
    lat REAL,
    lon REAL,
    public_use INTEGER
);
CREATE INDEX idx_airports_state ON airports(state);
CREATE INDEX idx_airports_city ON airports(city);
CREATE INDEX idx_airports_public_use ON airports(public_use);

CREATE TABLE frequencies (
    airport_id TEXT,
    freq_mhz REAL,
    freq_category TEXT,
    platform_type TEXT,
    facility_status TEXT,
    weather_subtype TEXT,
    primary_approach_radio_call TEXT,
    tower_hours TEXT,
    raw_freq_use TEXT,
    source_remark TEXT,
    lat REAL,
    lon REAL,
    state TEXT,
    city TEXT,
    name TEXT
);
CREATE INDEX idx_freq_airport ON frequencies(airport_id);
CREATE INDEX idx_freq_category ON frequencies(freq_category);
CREATE INDEX idx_freq_platform_type ON frequencies(platform_type);
CREATE INDEX idx_freq_facility_status ON frequencies(facility_status);
CREATE INDEX idx_freq_weather_subtype ON frequencies(weather_subtype);
CREATE INDEX idx_freq_approach_call ON frequencies(primary_approach_radio_call);
CREATE INDEX idx_freq_state ON frequencies(state);
CREATE INDEX idx_freq_city ON frequencies(city);

CREATE TABLE ils (
    airport_id TEXT,
    runway_end_id TEXT,
    freq_mhz REAL,
    system_type TEXT,
    component_status TEXT
);
CREATE INDEX idx_ils_airport ON ils(airport_id);
CREATE INDEX idx_ils_status ON ils(component_status);
CREATE INDEX idx_ils_system_type ON ils(system_type);
"""


def build_database(data: NormalizedData) -> sqlite3.Connection:
    # check_same_thread=False: the web UI (milestone 4) serves sync request
    # handlers from a thread pool, so this connection is read from whatever
    # thread handles a given request -- never the thread that created it.
    # Safe here because all writes happen once, right below, before the
    # connection is ever handed to a caller; everything after is SELECT-only.
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.executescript(_SCHEMA)

    conn.executemany(
        "INSERT INTO airports VALUES (?,?,?,?,?,?,?,?)",
        [
            (a.id, a.name, a.city, a.state, a.country, a.lat, a.lon, int(a.public_use))
            for a in data.airports
        ],
    )
    conn.executemany(
        "INSERT INTO frequencies VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (
                f.airport_id, f.freq_mhz, f.freq_category, f.platform_type,
                f.facility_status, f.weather_subtype, f.primary_approach_radio_call,
                f.tower_hours, f.raw_freq_use, f.source_remark, f.lat, f.lon,
                f.state, f.city, f.name,
            )
            for f in data.frequencies
        ],
    )
    conn.executemany(
        "INSERT INTO ils VALUES (?,?,?,?,?)",
        [
            (i.airport_id, i.runway_end_id, i.freq_mhz, i.system_type, i.component_status)
            for i in data.ils
        ],
    )
    conn.commit()

    conn.create_function("haversine_nm", 4, haversine_nm, deterministic=True)
    return conn
