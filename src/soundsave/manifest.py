"""SQLite manifest of processed archive files."""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS tracks (
    path TEXT PRIMARY KEY,
    file_id INTEGER,
    artist TEXT,
    title TEXT,
    album TEXT,
    duration_s REAL,
    old_codec TEXT,
    old_bitrate INTEGER,
    status TEXT NOT NULL,
    provider TEXT,
    candidate_id TEXT,
    candidate_title TEXT,
    candidate_artist TEXT,
    candidate_duration_s REAL,
    score REAL,
    has_flac INTEGER,
    new_codec TEXT,
    new_bitrate INTEGER,
    backup_path TEXT,
    error TEXT,
    updated_at TEXT NOT NULL
);
"""


@dataclass
class ManifestRecord:
    """One row of the manifest."""

    path: str
    status: str
    file_id: int | None = None
    artist: str | None = None
    title: str | None = None
    album: str | None = None
    duration_s: float | None = None
    old_codec: str | None = None
    old_bitrate: int | None = None
    provider: str | None = None
    candidate_id: str | None = None
    candidate_title: str | None = None
    candidate_artist: str | None = None
    candidate_duration_s: float | None = None
    score: float | None = None
    has_flac: bool | None = None
    new_codec: str | None = None
    new_bitrate: int | None = None
    backup_path: str | None = None
    error: str | None = None
    updated_at: str = ''


class Manifest:
    """Thin wrapper around the manifest SQLite database."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path)
        self._connection.row_factory = sqlite3.Row
        self._connection.executescript(SCHEMA)
        self._connection.commit()

    def close(self) -> None:
        """Close the database connection."""
        self._connection.close()

    def __enter__(self) -> Manifest:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def get(self, path: str) -> ManifestRecord | None:
        """Return the record for *path* or ``None``."""
        row = self._connection.execute('SELECT * FROM tracks WHERE path = ?', (path,)).fetchone()
        if row is None:
            return None
        values = dict(row)
        values['has_flac'] = None if values['has_flac'] is None else bool(values['has_flac'])
        return ManifestRecord(**values)

    def save(self, record: ManifestRecord) -> None:
        """Insert or replace *record*."""
        if not record.updated_at:
            record.updated_at = datetime.now(timezone.utc).isoformat(timespec='seconds')
        data = asdict(record)
        if data['has_flac'] is not None:
            data['has_flac'] = int(data['has_flac'])
        columns = [field.name for field in fields(ManifestRecord)]
        placeholders = ', '.join('?' for _ in columns)
        updates = ', '.join(f'{column} = excluded.{column}' for column in columns if column != 'path')
        statement = (
            f'INSERT INTO tracks ({", ".join(columns)}) VALUES ({placeholders}) '  # noqa: S608
            f'ON CONFLICT(path) DO UPDATE SET {updates}'
        )
        self._connection.execute(statement, [data[column] for column in columns])
        self._connection.commit()

    def delete(self, path: str) -> None:
        """Remove the record for *path*."""
        self._connection.execute('DELETE FROM tracks WHERE path = ?', (path,))
        self._connection.commit()

    def all(self) -> list[ManifestRecord]:
        """Return every record."""
        rows = self._connection.execute('SELECT * FROM tracks ORDER BY path').fetchall()
        records: list[ManifestRecord] = []
        for row in rows:
            values = dict(row)
            values['has_flac'] = None if values['has_flac'] is None else bool(values['has_flac'])
            records.append(ManifestRecord(**values))
        return records
