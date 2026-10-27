"""SQLite index of playlists and tracks shown by the web player."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS playlists (
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

CREATE UNIQUE INDEX IF NOT EXISTS playlists_one_syncable
    ON playlists(syncable) WHERE syncable = 1;

CREATE TABLE IF NOT EXISTS tracks (
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

CREATE INDEX IF NOT EXISTS tracks_playlist_position
    ON tracks(playlist_id, position);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def connect(path: Path) -> sqlite3.Connection:
    """Open a short-lived connection with the settings the web app needs."""
    connection = sqlite3.connect(path, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA foreign_keys = ON')
    connection.execute('PRAGMA busy_timeout = 5000')
    connection.execute('PRAGMA journal_mode = WAL')
    return connection


@dataclass(frozen=True)
class PlaylistRow:
    id: int
    folder_name: str
    name: str
    syncable: bool
    missing: bool
    track_count: int
    last_scanned_at: str | None
    last_scan_error: str | None
    last_synced_at: str | None
    last_sync_status: str | None
    last_sync_error: str | None


@dataclass(frozen=True)
class TrackRow:
    id: int
    playlist_id: int
    filename: str
    number: int
    position: int
    title: str
    artists: list[str]
    album: str | None
    duration_s: float | None
    format: str
    bitrate_kbps: int | None
    sample_rate_hz: int | None
    size_bytes: int
    mtime_ns: int
    etag: str
    has_cover: bool


def _playlist(row: sqlite3.Row) -> PlaylistRow:
    return PlaylistRow(
        id=int(row['id']),
        folder_name=str(row['folder_name']),
        name=str(row['name']),
        syncable=bool(row['syncable']),
        missing=bool(row['missing']),
        track_count=int(row['track_count']),
        last_scanned_at=row['last_scanned_at'],
        last_scan_error=row['last_scan_error'],
        last_synced_at=row['last_synced_at'],
        last_sync_status=row['last_sync_status'],
        last_sync_error=row['last_sync_error'],
    )


def _track(row: sqlite3.Row) -> TrackRow:
    try:
        artists = json.loads(row['artists'] or '[]')
    except (TypeError, ValueError):
        artists = []
    return TrackRow(
        id=int(row['id']),
        playlist_id=int(row['playlist_id']),
        filename=str(row['filename']),
        number=int(row['number']),
        position=int(row['position']),
        title=str(row['title']),
        artists=[str(item) for item in artists],
        album=row['album'],
        duration_s=row['duration_s'],
        format=str(row['format']),
        bitrate_kbps=row['bitrate_kbps'],
        sample_rate_hz=row['sample_rate_hz'],
        size_bytes=int(row['size_bytes']),
        mtime_ns=int(row['mtime_ns']),
        etag=str(row['etag']),
        has_cover=bool(row['has_cover']),
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


class LibraryStore:
    """Small data-access layer; every operation uses its own connection."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def init(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(connect(self.path)) as connection:
            version = int(connection.execute('PRAGMA user_version').fetchone()[0])
            if version > SCHEMA_VERSION:
                raise RuntimeError(f'Library database is newer than this app (schema {version})')
            connection.executescript(SCHEMA)
            connection.execute(f'PRAGMA user_version = {SCHEMA_VERSION}')
            connection.commit()

    def list_playlists(self, *, include_missing: bool = False) -> list[PlaylistRow]:
        query = 'SELECT * FROM playlists'
        if not include_missing:
            query += ' WHERE missing = 0'
        query += ' ORDER BY syncable DESC, name COLLATE NOCASE, id'
        with closing(connect(self.path)) as connection:
            return [_playlist(row) for row in connection.execute(query).fetchall()]

    def get_playlist(self, playlist_id: int) -> PlaylistRow | None:
        with closing(connect(self.path)) as connection:
            row = connection.execute('SELECT * FROM playlists WHERE id = ?', (playlist_id,)).fetchone()
        return _playlist(row) if row is not None else None

    def get_syncable_playlist(self) -> PlaylistRow | None:
        with closing(connect(self.path)) as connection:
            row = connection.execute('SELECT * FROM playlists WHERE syncable = 1').fetchone()
        return _playlist(row) if row is not None else None

    def upsert_playlist(self, folder_name: str, name: str, *, syncable: bool) -> int:
        with closing(connect(self.path)) as connection:
            if syncable:
                connection.execute('UPDATE playlists SET syncable = 0 WHERE folder_name != ?', (folder_name,))
            connection.execute(
                """
                INSERT INTO playlists (folder_name, name, syncable, missing)
                VALUES (?, ?, ?, 0)
                ON CONFLICT(folder_name) DO UPDATE SET
                    name = excluded.name,
                    syncable = excluded.syncable,
                    missing = 0
                """,
                (folder_name, name, int(syncable)),
            )
            row = connection.execute('SELECT id FROM playlists WHERE folder_name = ?', (folder_name,)).fetchone()
            connection.commit()
        assert row is not None
        return int(row['id'])

    def mark_missing(self, active_folders: set[str]) -> None:
        with closing(connect(self.path)) as connection:
            if active_folders:
                placeholders = ', '.join('?' for _ in active_folders)
                connection.execute(
                    f'UPDATE playlists SET missing = 1 WHERE folder_name NOT IN ({placeholders})',  # noqa: S608
                    tuple(active_folders),
                )
            else:
                connection.execute('UPDATE playlists SET missing = 1')
            connection.commit()

    def set_playlist_scanned(self, playlist_id: int, *, track_count: int, error: str | None = None) -> None:
        with closing(connect(self.path)) as connection:
            connection.execute(
                """
                UPDATE playlists
                SET track_count = ?, last_scanned_at = ?, last_scan_error = ?
                WHERE id = ?
                """,
                (track_count, _now(), error, playlist_id),
            )
            connection.commit()

    def record_sync(self, playlist_id: int, *, status: str, error: str | None = None) -> None:
        with closing(connect(self.path)) as connection:
            connection.execute(
                """
                UPDATE playlists
                SET last_synced_at = ?, last_sync_status = ?, last_sync_error = ?
                WHERE id = ?
                """,
                (_now(), status, error, playlist_id),
            )
            connection.commit()

    def tracks_for_playlist(self, playlist_id: int) -> list[TrackRow]:
        with closing(connect(self.path)) as connection:
            rows = connection.execute(
                'SELECT * FROM tracks WHERE playlist_id = ? ORDER BY position, id',
                (playlist_id,),
            ).fetchall()
        return [_track(row) for row in rows]

    def get_track(self, playlist_id: int, track_id: int) -> TrackRow | None:
        with closing(connect(self.path)) as connection:
            row = connection.execute(
                'SELECT * FROM tracks WHERE playlist_id = ? AND id = ?',
                (playlist_id, track_id),
            ).fetchone()
        return _track(row) if row is not None else None

    def upsert_track(self, playlist_id: int, values: dict[str, object]) -> None:
        columns = (
            'playlist_id',
            'filename',
            'number',
            'position',
            'title',
            'artists',
            'album',
            'duration_s',
            'format',
            'bitrate_kbps',
            'sample_rate_hz',
            'size_bytes',
            'mtime_ns',
            'etag',
            'has_cover',
        )
        data = {
            'playlist_id': playlist_id,
            'artists': json.dumps(values.get('artists', []), ensure_ascii=False),
            'has_cover': int(bool(values.get('has_cover'))),
            **{key: values.get(key) for key in columns if key not in {'playlist_id', 'artists', 'has_cover'}},
        }
        placeholders = ', '.join('?' for _ in columns)
        updates = ', '.join(
            f'{column} = excluded.{column}' for column in columns if column not in {'playlist_id', 'filename'}
        )
        with closing(connect(self.path)) as connection:
            connection.execute(
                f'INSERT INTO tracks ({", ".join(columns)}) VALUES ({placeholders}) '  # noqa: S608
                f'ON CONFLICT(playlist_id, filename) DO UPDATE SET {updates}',
                [data[column] for column in columns],
            )
            connection.commit()

    def delete_tracks(self, playlist_id: int, filenames: set[str]) -> int:
        if not filenames:
            return 0
        with closing(connect(self.path)) as connection:
            before = connection.total_changes
            connection.executemany(
                'DELETE FROM tracks WHERE playlist_id = ? AND filename = ?',
                [(playlist_id, filename) for filename in filenames],
            )
            deleted = connection.total_changes - before
            connection.commit()
        return deleted

    def update_positions(self, playlist_id: int, ordered: list[tuple[str, int, int]]) -> None:
        """Set ``number`` and ``position`` for every track in playlist order."""
        with closing(connect(self.path)) as connection:
            connection.executemany(
                'UPDATE tracks SET number = ?, position = ? WHERE playlist_id = ? AND filename = ?',
                [(number, position, playlist_id, filename) for filename, number, position in ordered],
            )
            connection.commit()

    def known_etags(self) -> set[str]:
        with closing(connect(self.path)) as connection:
            rows = connection.execute('SELECT DISTINCT etag FROM tracks').fetchall()
        return {str(row['etag']) for row in rows}
