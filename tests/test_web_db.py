from __future__ import annotations

import sqlite3
from pathlib import Path

from soundsave.web.db import SCHEMA_VERSION, LibraryStore

OLD_SCHEMA = """
CREATE TABLE playlists (
    id INTEGER PRIMARY KEY,
    folder_name TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    syncable INTEGER NOT NULL DEFAULT 0,
    missing INTEGER NOT NULL DEFAULT 0,
    track_count INTEGER NOT NULL DEFAULT 0,
    last_scanned_at TEXT,
    last_scan_error TEXT,
    last_synced_at TEXT,
    last_sync_status TEXT,
    last_sync_error TEXT
);

CREATE TABLE tracks (
    id INTEGER PRIMARY KEY,
    playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    number INTEGER NOT NULL,
    position INTEGER NOT NULL,
    title TEXT NOT NULL,
    artists TEXT NOT NULL,
    album TEXT,
    duration_s REAL,
    format TEXT NOT NULL,
    bitrate_kbps INTEGER,
    sample_rate_hz INTEGER,
    size_bytes INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    etag TEXT NOT NULL,
    has_cover INTEGER NOT NULL DEFAULT 0,
    UNIQUE(playlist_id, filename)
);

INSERT INTO playlists (folder_name, name, syncable) VALUES ('Main', 'Main', 1);
INSERT INTO tracks (
    playlist_id, filename, number, position, title, artists, format, size_bytes, mtime_ns, etag
) VALUES (1, '0001. A - One.mp3', 1, 1, 'One', '["A"]', 'MP3', 1, 123, 'old-etag');

PRAGMA user_version = 1;
"""


def test_schema_v2_adds_year_and_forces_one_rescan(tmp_path: Path) -> None:
    database = tmp_path / 'library.sqlite3'
    with sqlite3.connect(database) as connection:
        connection.executescript(OLD_SCHEMA)

    LibraryStore(database).init()

    with sqlite3.connect(database) as connection:
        columns = {str(row[1]) for row in connection.execute('PRAGMA table_info(tracks)')}
        assert 'year' in columns
        assert connection.execute('SELECT year FROM tracks').fetchone()[0] is None
        assert connection.execute('SELECT mtime_ns FROM tracks').fetchone()[0] == -1
        assert connection.execute('PRAGMA user_version').fetchone()[0] == SCHEMA_VERSION


def test_schema_v3_adds_cover_hash_without_a_rescan(tmp_path: Path) -> None:
    database = tmp_path / 'library.sqlite3'
    with sqlite3.connect(database) as connection:
        connection.executescript(OLD_SCHEMA)
        connection.execute('ALTER TABLE tracks ADD COLUMN year INTEGER')
        connection.execute('PRAGMA user_version = 2')

    LibraryStore(database).init()

    with sqlite3.connect(database) as connection:
        columns = {str(row[1]) for row in connection.execute('PRAGMA table_info(tracks)')}
        assert 'cover_hash' in columns
        assert connection.execute('SELECT cover_hash FROM tracks').fetchone()[0] is None
        assert connection.execute('SELECT mtime_ns FROM tracks').fetchone()[0] == 123
        assert connection.execute('PRAGMA user_version').fetchone()[0] == SCHEMA_VERSION


def test_migration_does_not_rescan_again(tmp_path: Path) -> None:
    database = tmp_path / 'library.sqlite3'
    with sqlite3.connect(database) as connection:
        connection.executescript(OLD_SCHEMA)
    LibraryStore(database).init()
    with sqlite3.connect(database) as connection:
        connection.execute('UPDATE tracks SET mtime_ns = 456')

    LibraryStore(database).init()

    with sqlite3.connect(database) as connection:
        assert connection.execute('SELECT mtime_ns FROM tracks').fetchone()[0] == 456
