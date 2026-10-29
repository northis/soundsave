"""Check and fill missing tags, covers and lyrics from Yandex Music."""

from __future__ import annotations

import logging
import re
import shutil
import time
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from mutagen import File as MutagenFile

from .archive import (
    TAGGABLE_EXTENSIONS,
    ArchiveError,
    TagData,
    has_artists,
    has_text,
    parse_filename,
    read_tag_data,
    scan_archive,
    update_tag_data,
)
from .catalogs import CONFIDENT_STATUSES, MetadataCatalog
from .config import Config
from .domain import LocalTrack, MetadataCandidate, MetadataMatch, TrackQuery
from .matching import normalize
from .report import write_csv, write_json
from .yandex_catalog import parse_track_url

logger = logging.getLogger(__name__)


@dataclass
class TagsOptions:
    """Options for :func:`run_tags`."""

    apply: bool = False
    search: bool = True
    lyrics: bool = True
    skip_complete: bool = False
    min_score: float = 0.85
    duration_tolerance: float = 5.0
    limit: int | None = None
    file_id: int | None = None
    delay: float = 1.0
    report_csv: Path | None = None
    report_json: Path | None = None


@dataclass
class MetadataReportRow:
    """One row of a tags report."""

    path: str
    file_id: int | None
    artist: str
    title: str
    album: str | None
    duration_s: float | None
    status: str
    source: str | None = None
    source_id: str | None = None
    source_url: str | None = None
    confidence: float | None = None
    missing: str | None = None
    conflicts: str | None = None
    cover: str = 'unknown'
    lyrics: str = 'unknown'
    applied: bool = False
    error: str | None = None


def _tolerance(track: LocalTrack, options: TagsOptions) -> float:
    if track.duration_s is None:
        return options.duration_tolerance
    return max(options.duration_tolerance, track.duration_s * 0.02)


def _date_year(value: str | None) -> str | None:
    if not value:
        return None
    match = re.match(r'(\d{4})', value)
    return match.group(1) if match is not None else None


def _format_date(candidate: MetadataCandidate) -> str | None:
    if candidate.release_date:
        value = candidate.release_date.replace('Z', '+00:00')
        try:
            return datetime.fromisoformat(value).date().isoformat()
        except ValueError:
            logger.debug('Invalid release date for %s: %s', candidate.provider_id, candidate.release_date)
    if candidate.year:
        return str(candidate.year)
    return None


def _queries(track: LocalTrack) -> list[TrackQuery]:
    queries: list[TrackQuery] = []
    seen: set[tuple[str, str]] = set()
    if track.artist and track.title:
        queries.append(
            TrackQuery(
                artist=track.artist,
                title=track.title,
                album=track.album,
                duration_s=track.duration_s,
            ),
        )
        seen.add((normalize(track.artist), normalize(track.title)))
    parts = parse_filename(track.path.name)
    if parts is not None:
        key = (normalize(parts.artist), normalize(parts.title))
        if key not in seen:
            queries.append(
                TrackQuery(
                    artist=parts.artist,
                    title=parts.title,
                    album=track.album,
                    duration_s=track.duration_s,
                ),
            )
    return queries


def _missing_fields(
    tags: TagData,
    candidate: MetadataCandidate,
    *,
    include_lyrics: bool,
) -> tuple[TagData, list[str]]:
    data = TagData()
    names: list[str] = []
    if not has_text(tags.title) and candidate.title:
        data.title = candidate.title
        names.append('title')
    if not has_artists(tags.artists) and candidate.artists:
        data.artists = list(candidate.artists)
        names.append('artists')
    if not has_text(tags.album) and candidate.album:
        data.album = candidate.album
        names.append('album')
    if not has_text(tags.album_artist) and candidate.album_artists:
        data.album_artist = ', '.join(candidate.album_artists)
        names.append('album_artist')
    if not has_text(tags.date):
        date = _format_date(candidate)
        if date:
            data.date = date
            names.append('date')
    if not has_text(tags.genre) and candidate.genre:
        data.genre = candidate.genre
        names.append('genre')
    if not has_text(tags.url) and candidate.url:
        data.url = candidate.url
        names.append('url')
    if include_lyrics and not has_text(tags.lyrics) and candidate.lyrics_available:
        names.append('lyrics')
    return data, names


def _conflicting_fields(tags: TagData, candidate: MetadataCandidate) -> list[str]:
    conflicts: list[str] = []
    if has_text(tags.title) and candidate.title and normalize(tags.title) != normalize(candidate.title):
        conflicts.append('title')
    if has_artists(tags.artists) and candidate.artists:
        local_artists = {normalize(artist) for artist in tags.artists}
        remote_artists = {normalize(artist) for artist in candidate.artists}
        if local_artists != remote_artists:
            conflicts.append('artists')
    if has_text(tags.album) and candidate.album and normalize(tags.album) != normalize(candidate.album):
        conflicts.append('album')
    if has_text(tags.album_artist) and candidate.album_artists:
        remote_album_artist = ', '.join(candidate.album_artists)
        if normalize(tags.album_artist) != normalize(remote_album_artist):
            conflicts.append('album_artist')
    if has_text(tags.date):
        remote_date = _format_date(candidate)
        local_year = _date_year(tags.date)
        remote_year = _date_year(remote_date)
        if local_year and remote_year and local_year != remote_year:
            conflicts.append('date')
    if has_text(tags.genre) and candidate.genre and normalize(tags.genre) != normalize(candidate.genre):
        conflicts.append('genre')
    if has_text(tags.url) and candidate.url and parse_track_url(tags.url) != parse_track_url(candidate.url):
        conflicts.append('url')
    return conflicts


def _cover_status(tags: TagData, candidate: MetadataCandidate, catalog: MetadataCatalog) -> str:
    if tags.cover is not None and tags.cover_type == 3:
        return 'present'
    if not catalog.has_cover(candidate):
        return 'unavailable'
    return 'missing'


def _lyrics_status(tags: TagData, candidate: MetadataCandidate, *, lyrics_enabled: bool) -> str:
    if has_text(tags.lyrics):
        return 'present'
    if not lyrics_enabled:
        return 'skipped'
    if candidate.lyrics_available:
        return 'missing'
    return 'unavailable'


def _has_local_gaps(tags: TagData) -> bool:
    """Return whether Discogs can fill at least one missing field.

    ``WOAF`` is Yandex-only and Discogs has no lyrics, so neither is a gap for
    a Discogs-only run.
    """
    if not has_text(tags.title):
        return True
    if not has_artists(tags.artists):
        return True
    if not has_text(tags.album):
        return True
    if not has_text(tags.album_artist):
        return True
    if not has_text(tags.date):
        return True
    if not has_text(tags.genre):
        return True
    return tags.cover is None or tags.cover_type != 3


def _complete_row(track: LocalTrack, tags: TagData, options: TagsOptions) -> MetadataReportRow:
    return MetadataReportRow(
        path=str(track.path),
        file_id=track.file_id,
        artist=track.artist,
        title=track.title,
        album=track.album,
        duration_s=track.duration_s,
        status='ok',
        cover='present' if tags.cover is not None and tags.cover_type == 3 else 'missing',
        lyrics='present' if has_text(tags.lyrics) else ('skipped' if not options.lyrics else 'missing'),
    )


def _row(track: LocalTrack, match: MetadataMatch) -> MetadataReportRow:
    candidate = match.candidate
    confidence = match.score
    if not confidence and candidate is not None:
        confidence = candidate.score
    return MetadataReportRow(
        path=str(track.path),
        file_id=track.file_id,
        artist=track.artist,
        title=track.title,
        album=track.album,
        duration_s=track.duration_s,
        status=match.status,
        source=candidate.provider if candidate is not None else None,
        source_id=candidate.provider_id if candidate is not None else None,
        source_url=candidate.url if candidate is not None else None,
        confidence=round(confidence, 4) if confidence else None,
        error=match.reason if match.status == 'error' and match.reason else None,
    )


def _error_row(track: LocalTrack, error: Exception) -> MetadataReportRow:
    return MetadataReportRow(
        path=str(track.path),
        file_id=track.file_id,
        artist=track.artist,
        title=track.title,
        album=track.album,
        duration_s=track.duration_s,
        status='error',
        error=str(error),
    )


def _append_error(current: str | None, message: str) -> str:
    return f'{current}; {message}' if current else message


def _has_changes(data: TagData) -> bool:
    return any(
        (
            data.title,
            data.artists,
            data.album,
            data.album_artist,
            data.date,
            data.genre,
            data.lyrics,
            data.cover,
            data.url,
        ),
    )


def _verify_tagged_audio(temporary: Path, original: Path) -> None:
    if MutagenFile(temporary) is None:
        raise ArchiveError(f'Unreadable audio after tagging: {original.name}')


def _apply_file(path: Path, data: TagData, *, workspace: Path) -> bool:
    temporary_dir = workspace / 'tmp'
    temporary_dir.mkdir(parents=True, exist_ok=True)
    temporary = temporary_dir / f'{uuid.uuid4().hex}-{path.name}'
    try:
        shutil.copy2(path, temporary)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    try:
        if not update_tag_data(temporary, data):
            temporary.unlink(missing_ok=True)
            return False
        _verify_tagged_audio(temporary, path)
        temporary.replace(path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return True


def _sleep(options: TagsOptions) -> None:
    if options.delay:
        time.sleep(options.delay)


def _process_track(
    track: LocalTrack,
    catalog: MetadataCatalog,
    options: TagsOptions,
    workspace: Path,
) -> tuple[MetadataReportRow, bool]:
    tags = read_tag_data(track.path)
    if options.skip_complete and not _has_local_gaps(tags):
        return _complete_row(track, tags, options), False
    match = catalog.find(
        _queries(track),
        url=tags.url,
        search=options.search,
        min_score=options.min_score,
        tolerance=_tolerance(track, options),
    )
    row = _row(track, match)

    if match.status not in CONFIDENT_STATUSES or match.candidate is None:
        if options.search and match.status not in {'matched_woaf', 'matched_playlist'}:
            _sleep(options)
        return row, False

    candidate = match.candidate
    to_write, missing_names = _missing_fields(tags, candidate, include_lyrics=options.lyrics)
    row.missing = ', '.join(missing_names) or None
    row.conflicts = ', '.join(_conflicting_fields(tags, candidate)) or None
    row.cover = _cover_status(tags, candidate, catalog)
    row.lyrics = _lyrics_status(tags, candidate, lyrics_enabled=options.lyrics)

    if options.search and match.status not in {'matched_woaf', 'matched_playlist'}:
        _sleep(options)
    if not options.apply:
        return row, False
    if not missing_names and row.cover != 'missing' and row.lyrics != 'missing':
        return row, False

    if row.cover == 'missing':
        try:
            cover = catalog.fetch_cover(candidate)
        except Exception as exc:
            row.cover = 'error'
            row.error = _append_error(row.error, str(exc))
        else:
            if cover is None:
                row.cover = 'unavailable'
            else:
                to_write.cover, to_write.cover_mime = cover
                row.cover = 'added'
        _sleep(options)

    if row.lyrics == 'missing':
        try:
            lyrics = catalog.fetch_lyrics(candidate)
        except Exception as exc:
            row.lyrics = 'error'
            row.error = _append_error(row.error, str(exc))
        else:
            if lyrics is None:
                row.lyrics = 'unavailable'
            else:
                to_write.lyrics = lyrics
                row.lyrics = 'added'
        _sleep(options)

    if not _has_changes(to_write):
        return row, False

    try:
        changed = _apply_file(track.path, to_write, workspace=workspace)
    except Exception as exc:
        row.status = 'error'
        row.error = _append_error(row.error, str(exc))
        return row, False

    if not changed:
        if row.cover == 'added':
            row.cover = 'missing'
        if row.lyrics == 'added':
            row.lyrics = 'missing'
        return row, False

    row.applied = True
    return row, True


def run_tags(config: Config, catalog: MetadataCatalog, options: TagsOptions) -> list[MetadataReportRow]:
    """Check the archive and fill missing values from *catalog*."""
    workspace = config.target_dir / '.soundsave'
    tracks = scan_archive(config.target_dir, file_id=options.file_id, extensions=TAGGABLE_EXTENSIONS)
    total = len(tracks)
    rows: list[MetadataReportRow] = []
    processed = 0
    applied = 0

    logger.info('Checking %d files in %s', total, config.target_dir)
    for index, track in enumerate(tracks, start=1):
        if options.limit is not None and processed >= options.limit:
            logger.info('Reached the limit of %d tracks', options.limit)
            break
        processed += 1
        try:
            row, changed = _process_track(track, catalog, options, workspace)
        except Exception as exc:
            logger.error('[%d/%d] %s — %s', index, total, track.path.name, exc)
            rows.append(_error_row(track, exc))
            continue
        rows.append(row)
        if changed:
            applied += 1
        logger.info(
            '[%d/%d] %s — %s%s',
            index,
            total,
            track.path.name,
            row.status,
            f' (missing: {row.missing})' if row.missing else '',
        )

    if options.report_csv is not None:
        write_csv(options.report_csv, rows, MetadataReportRow)
        logger.info('CSV report written to %s', options.report_csv)
    if options.report_json is not None:
        write_json(options.report_json, rows)
        logger.info('JSON report written to %s', options.report_json)

    statuses = Counter(row.status for row in rows)
    logger.info(
        'Done: %d files checked, %d changed, statuses: %s',
        processed,
        applied,
        ', '.join(f'{status}={count}' for status, count in sorted(statuses.items())),
    )
    return rows
