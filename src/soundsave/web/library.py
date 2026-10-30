"""Flat playlist discovery and the startup database reconciliation."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .db import LibraryStore, PlaylistRow, TrackRow
from .metadata import (
    etag_for_stat,
    is_supported_audio,
    natural_key,
    read_library_metadata,
    track_number,
)
from .settings import WebSettings

logger = logging.getLogger(__name__)

_SYSTEM_FOLDERS = frozenset({'@eaDir', '#recycle', 'System Volume Information', '$RECYCLE.BIN'})


class LibraryError(RuntimeError):
    """Raised when the music root cannot be scanned."""


@dataclass
class ScanReport:
    """Summary of one reconciliation pass."""

    playlists: int = 0
    tracks: int = 0
    added: int = 0
    updated: int = 0
    removed: int = 0
    unchanged: int = 0
    warnings: list[str] = field(default_factory=list)
    elapsed_s: float = 0.0


class LibraryScanner:
    """Reconcile the SQLite index with the flat playlist folders on disk."""

    def __init__(self, settings: WebSettings, store: LibraryStore) -> None:
        self.settings = settings
        self.store = store
        self._lock = threading.RLock()

    def reconcile(self, *, force: bool = False) -> ScanReport:
        """Synchronise playlists and tracks; disk is the source of truth."""
        with self._lock:
            started = time.monotonic()
            self.settings.ensure()
            report = ScanReport()
            active: set[str] = set()
            for folder_name, path, name in self._discover_playlists():
                active.add(folder_name)
                syncable = path == self.settings.target_dir
                playlist_id = self.store.upsert_playlist(folder_name, name, syncable=syncable)
                report.playlists += 1
                try:
                    self._scan_playlist(playlist_id, path, force=force, report=report)
                except Exception as exc:
                    logger.exception('Failed to scan playlist %s', path)
                    report.warnings.append(f'{name}: {exc}')
                    self.store.set_playlist_scanned(playlist_id, track_count=0, error=str(exc))
            self.store.mark_missing(active)
            report.elapsed_s = time.monotonic() - started
            logger.info(
                'Library reconciled: %d playlists, %d tracks (+%d ~%d -%d =%d) in %.2fs',
                report.playlists,
                report.tracks,
                report.added,
                report.updated,
                report.removed,
                report.unchanged,
                report.elapsed_s,
            )
            return report

    def reconcile_playlist(self, folder_name: str, *, force: bool = False) -> ScanReport:
        """Synchronise one playlist folder, leaving every other playlist untouched.

        Unlike :meth:`reconcile`, this neither discovers other folders nor
        marks them missing. It is meant for an explicit rescan of one folder
        after its files were retagged or renamed. With ``force`` every track is
        re-read even when its size and mtime did not change.
        """
        with self._lock:
            started = time.monotonic()
            self.settings.ensure()
            path, name = self._playlist_target(folder_name)
            report = ScanReport(playlists=1)
            syncable = path == self.settings.target_dir
            playlist_id = self.store.upsert_playlist(folder_name, name, syncable=syncable)
            try:
                self._scan_playlist(playlist_id, path, force=force, report=report)
            except Exception as exc:
                logger.exception('Failed to scan playlist %s', path)
                self.store.set_playlist_scanned(playlist_id, track_count=0, error=str(exc))
                raise
            report.elapsed_s = time.monotonic() - started
            logger.info(
                'Playlist %r reconciled: %d tracks (+%d ~%d -%d =%d) in %.2fs',
                name,
                report.tracks,
                report.added,
                report.updated,
                report.removed,
                report.unchanged,
                report.elapsed_s,
            )
            return report

    def _playlist_target(self, folder_name: str) -> tuple[Path, str]:
        """Resolve a playlist folder name to its path and display name."""
        if folder_name == '.':
            if self.settings.is_multi_playlist:
                raise LibraryError('MUSIC_DIR is the library root; specify a playlist folder instead')
            path = self.settings.music_dir
            name = path.name or 'Library'
        else:
            if not self.settings.is_multi_playlist:
                raise LibraryError('The library is a single flat playlist; scan MUSIC_DIR instead')
            try:
                path = self.settings.playlist_path(folder_name)
            except ValueError as exc:
                raise LibraryError(str(exc)) from exc
            if path.name.startswith('.') or path.name in _SYSTEM_FOLDERS:
                raise LibraryError(f'Not a playlist folder: {path}')
            if path in (self.settings.data_dir, self.settings.covers_dir):
                raise LibraryError(f'Not a playlist folder: {path}')
            name = path.name
        if not path.is_dir():
            raise LibraryError(f'Playlist folder does not exist: {path}')
        return path, name

    def _discover_playlists(self) -> list[tuple[str, Path, str]]:
        if not self.settings.is_multi_playlist:
            root = self.settings.music_dir
            return [('.', root, root.name or 'Library')]

        playlists: list[tuple[str, Path, str]] = []
        for entry in self.settings.music_dir.iterdir():
            if not entry.is_dir() or entry.name.startswith('.'):
                continue
            if entry.name in _SYSTEM_FOLDERS:
                continue
            try:
                resolved = entry.resolve()
            except OSError:
                continue
            if resolved in (self.settings.data_dir, self.settings.covers_dir):
                continue
            playlists.append((entry.name, resolved, entry.name))
        playlists.sort(key=lambda item: natural_key(item[2]))
        return playlists

    def _scan_playlist(
        self,
        playlist_id: int,
        path: Path,
        *,
        force: bool,
        report: ScanReport,
    ) -> None:
        existing = {track.filename: track for track in self.store.tracks_for_playlist(playlist_id)}
        files: list[tuple[Path, int, int]] = []
        for entry in path.iterdir():
            if entry.name.startswith('.'):
                continue
            if entry.is_dir():
                report.warnings.append(f'{path.name}/{entry.name}: nested directories are ignored')
                continue
            if not entry.is_file() or not is_supported_audio(entry):
                continue
            try:
                resolved = entry.resolve()
                resolved.relative_to(path.resolve())
                stat = entry.stat()
            except (OSError, ValueError) as exc:
                report.warnings.append(f'{path.name}/{entry.name}: {exc}')
                continue
            files.append((entry, stat.st_size, stat.st_mtime_ns))

        files.sort(key=lambda item: natural_key(item[0].name))
        present: set[str] = set()
        ordered: list[tuple[str, int, int]] = []
        for position, (file, size, mtime_ns) in enumerate(files, start=1):
            filename = file.name
            present.add(filename)
            number = track_number(filename) or position
            ordered.append((filename, number, position))
            old = existing.get(filename)
            if old is not None and not force and old.size_bytes == size and old.mtime_ns == mtime_ns:
                report.unchanged += 1
                continue
            metadata = read_library_metadata(file)
            self.store.upsert_track(
                playlist_id,
                {
                    'filename': filename,
                    'number': number,
                    'position': position,
                    'title': metadata.title,
                    'artists': metadata.artists,
                    'album': metadata.album,
                    'duration_s': metadata.duration_s,
                    'format': metadata.format,
                    'bitrate_kbps': metadata.bitrate_kbps,
                    'sample_rate_hz': metadata.sample_rate_hz,
                    'size_bytes': size,
                    'mtime_ns': mtime_ns,
                    'etag': etag_for_stat(size, mtime_ns),
                    'has_cover': metadata.has_cover,
                },
            )
            if old is None:
                report.added += 1
            else:
                report.updated += 1

        missing = set(existing) - present
        if missing:
            report.removed += self.store.delete_tracks(playlist_id, missing)
        if ordered:
            self.store.update_positions(playlist_id, ordered)
        self.store.set_playlist_scanned(playlist_id, track_count=len(files))
        report.tracks += len(files)

    def track_path(self, playlist: PlaylistRow, track: TrackRow) -> Path:
        """Resolve a track to disk, refusing anything outside its playlist."""
        folder = self.settings.playlist_path(playlist.folder_name)
        path = (folder / track.filename).resolve()
        try:
            path.relative_to(folder.resolve())
        except ValueError as exc:
            raise LibraryError(f'Track escapes its playlist: {track.filename}') from exc
        if not path.is_file():
            raise LibraryError(f'Track is missing on disk: {track.filename}')
        return path
