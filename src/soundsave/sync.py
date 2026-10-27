"""Download new tracks from a Yandex Music playlist."""

from __future__ import annotations

import contextlib
import datetime as dt
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from mutagen.id3 import APIC, TALB, TDRC, TIT2, TPE1, TPE2, TRCK, USLT, WOAF
from mutagen.id3._specs import ID3TimeStamp
from mutagen.mp3 import MP3
from yandex_music import Album, Client, DownloadInfo, Playlist, Track

from .archive import get_image_type, sanitize_component
from .config import Config, ConfigError
from .domain import MatchStatus, TrackQuery
from .downloader import download_best_stream
from .matching import rank_candidates
from .providers.base import MusicProvider, ProviderError
from .providers.zvuk import ZvukProvider
from .quality import TARGET_MP3_BITRATE, TargetFormat
from .yandex_catalog import full_title, parse_playlist_url

logger = logging.getLogger(__name__)

# The archive scanner deliberately only handles MP3/FLAC, but sync must not
# reuse an id that already belongs to a FLAC/AAC file in the main playlist.
SYNC_FILENAME_RE = re.compile(
    r'^(\d+)\.\s*(.+?)\s-\s*(.*?)\.(mp3|flac|m4a|aac|ogg|opus|wav|aiff|aif)$',
    re.IGNORECASE,
)
SYNC_AUDIO_EXTENSIONS = frozenset({'.mp3', '.flac', '.m4a', '.aac', '.ogg', '.opus', '.wav', '.aiff', '.aif'})


@dataclass
class SyncProgress:
    """Live progress reported by :func:`run_sync`."""

    phase: str
    total: int = 0
    completed: int = 0
    current_artist: str = ''
    current_title: str = ''
    added: int = 0
    failed: int = 0
    limit_reached: bool = False


@dataclass
class SyncResult:
    """Outcome of one sync run."""

    total_new: int = 0
    added: int = 0
    failed: int = 0
    limit_reached: bool = False
    dry_run: bool = False


ProgressCallback = Callable[[SyncProgress], None]


def get_highest_local_id(target_dir: Path) -> int:
    """Return the largest numeric id already present in *target_dir*."""
    if not target_dir.exists():
        return 0
    highest = 0
    for file in target_dir.iterdir():
        if not file.is_file() or file.suffix.lower() not in SYNC_AUDIO_EXTENSIONS:
            continue
        match = SYNC_FILENAME_RE.match(file.name)
        if match is not None:
            with contextlib.suppress(ValueError):
                highest = max(highest, int(match.group(1)))
    return highest


def build_track_identifier(track: Track) -> str:
    """Create a case-insensitive ``artist-title`` key for comparison."""
    artist = track.artists[0].name if track.artists else ''
    return sanitize_component(f'{artist}-{track.title}'.lower())


def choose_best_download_info(infos: list[DownloadInfo]) -> DownloadInfo:
    """Pick the highest-bitrate MP3 from *infos*."""
    mp3_infos = [info for info in infos if info.codec == 'mp3'] or infos
    return max(mp3_infos, key=lambda info: info.bitrate_in_kbps)


def is_320_kbps(info: DownloadInfo) -> bool:
    """Return whether *info* is a genuine 320 kbps MP3 stream."""
    return info.codec == 'mp3' and (info.bitrate_in_kbps or 0) >= TARGET_MP3_BITRATE


def download_from_yandex(track: Track, destination: Path, info: DownloadInfo) -> Path | None:
    """Download a Yandex Music stream into *destination* and return its path."""
    temporary = destination / f'.soundsave-{track.id}.mp3'
    try:
        destination.mkdir(parents=True, exist_ok=True)
        info.download(str(temporary))
    except Exception:
        logger.exception('Failed to download track %s from Yandex Music', track.id)
        temporary.unlink(missing_ok=True)
        return None
    return temporary


def build_zvuk_query(track: Track) -> TrackQuery:
    """Build a Zvuk search query from Yandex Music track metadata."""
    album = track.albums[0] if track.albums else None
    album_title = full_title(album) if album is not None else ''
    duration_ms = getattr(track, 'duration_ms', None)
    return TrackQuery(
        artist=track.artists[0].name if track.artists else '',
        title=full_title(track),
        album=album_title or None,
        duration_s=duration_ms / 1000 if duration_ms else None,
    )


def download_from_zvuk(
    track: Track,
    destination: Path,
    provider: MusicProvider,
    *,
    min_score: float,
    duration_tolerance: float,
) -> Path | None:
    """Find *track* in Zvuk and download its best 320 kbps stream."""
    query = build_zvuk_query(track)
    if not query.artist or not query.title:
        logger.warning('Track %s has no artist or title; cannot search Zvuk', track.id)
        return None

    tolerance = max(duration_tolerance, (query.duration_s or 0.0) * 0.02)
    try:
        candidates = provider.search(query, limit=10)
    except Exception as exc:
        logger.warning('Zvuk search failed for track %s: %s', track.id, exc)
        return None

    match = rank_candidates(query, candidates, min_score=min_score, tolerance=tolerance)
    if match.status is not MatchStatus.MATCHED or match.candidate is None:
        logger.info(
            'No confident Zvuk match for %s - %s: %s',
            query.artist,
            query.title,
            match.reason or match.status.value,
        )
        return None

    candidate = match.candidate
    logger.info(
        'Zvuk match for %s - %s: %s (score %.3f)',
        query.artist,
        query.title,
        candidate.artist,
        candidate.score,
    )
    try:
        path, _quality, _extension = download_best_stream(
            provider,
            candidate,
            target_format=TargetFormat.MP3,
            temporary_dir=destination,
            stem=f'.soundsave-{track.id}-zvuk',
            duration_tolerance=tolerance,
        )
    except Exception as exc:
        logger.warning('Zvuk download failed for track %s: %s', track.id, exc)
        for suffix in ('mp3', 'flac'):
            (destination / f'.soundsave-{track.id}-zvuk.{suffix}').unlink(missing_ok=True)
        return None
    return path


def build_zvuk_provider(config: Config) -> MusicProvider | None:
    """Return a Zvuk provider for the sync fallback, or ``None`` if unavailable."""
    if not config.zvuk_token:
        logger.warning('ZVUK_TOKEN is not set; sync will use Yandex Music streams only')
        return None
    try:
        return ZvukProvider(config.zvuk_token)
    except ProviderError as exc:
        logger.warning('Zvuk is unavailable (%s); sync will use Yandex Music streams only', exc)
        return None


def download_track(
    track: Track,
    destination: Path,
    *,
    provider: MusicProvider | None = None,
    min_score: float = 0.85,
    duration_tolerance: float = 5.0,
) -> Path | None:
    """Download *track* into *destination* and return the temporary path.

    A genuine 320 kbps MP3 from Yandex Music is preferred. When Yandex only
    offers a lower bitrate, Zvuk is searched; the best Yandex stream is used as
    a fallback if Zvuk has no confident match or cannot provide 320 kbps.
    """
    info: DownloadInfo | None = None
    try:
        infos = track.get_download_info()
        if infos:
            info = choose_best_download_info(infos)
    except Exception:
        logger.exception('Failed to get Yandex Music download info for track %s', track.id)

    if info is not None and is_320_kbps(info):
        return download_from_yandex(track, destination, info)

    if provider is not None:
        logger.info(
            'Yandex Music has no 320 kbps stream for track %s (%s); trying Zvuk',
            track.id,
            f'{info.codec} {info.bitrate_in_kbps} kbps' if info is not None else 'no stream',
        )
        temporary = download_from_zvuk(
            track,
            destination,
            provider,
            min_score=min_score,
            duration_tolerance=duration_tolerance,
        )
        if temporary is not None:
            return temporary

    if info is not None:
        logger.warning(
            'Falling back to Yandex Music %s %s kbps for track %s',
            info.codec,
            info.bitrate_in_kbps,
            track.id,
        )
        return download_from_yandex(track, destination, info)

    logger.error('No downloadable stream found for track %s', track.id)
    return None


def set_tags(path: Path, track: Track, track_id: int) -> None:
    """Populate ID3 tags for a freshly downloaded Yandex Music track."""
    album = track.albums[0] if track.albums else Album()
    track_artists = [artist.name for artist in (track.artists or []) if artist.name]
    album_artists = [artist.name for artist in (album.artists or []) if artist.name]
    tag = MP3(path)
    album_title = full_title(album)
    track_title = full_title(track)

    iso8601_release_date = None
    release_year: str | None = None
    if album.release_date is not None:
        iso8601_release_date = dt.datetime.fromisoformat(album.release_date).astimezone(dt.timezone.utc)
        release_year = str(iso8601_release_date.year)
        iso8601_release_date = iso8601_release_date.strftime('%Y-%m-%d %H:%M:%S')
    if album.year:
        release_year = str(album.year)
    track_url = f'https://music.yandex.ru/album/{album.id}/track/{track.id}'

    tag['TIT2'] = TIT2(encoding=3, text=track_title)
    tag['TALB'] = TALB(encoding=3, text=album_title)
    tag['TPE1'] = TPE1(encoding=3, text=track_artists)
    tag['TPE2'] = TPE2(encoding=3, text=album_artists)

    if tdrc_text := iso8601_release_date or release_year:
        tag['TDRC'] = TDRC(encoding=3, text=[ID3TimeStamp(tdrc_text)])
    if track_id:
        tag['TRCK'] = TRCK(encoding=3, text=str(track_id))

    if track.lyrics_info is not None and track.lyrics_info.has_available_text_lyrics:
        try:
            if track_lyrics := track.get_lyrics(format_='TEXT'):
                tag['USLT'] = USLT(encoding=3, text=track_lyrics.fetch_lyrics())
        except Exception as exc:
            logger.warning('Failed to fetch lyrics for track %s: %s', track.id, exc)

    if track.cover_uri is not None:
        try:
            cover_bytes = track.download_cover_bytes(size='orig')
            tag['APIC'] = APIC(encoding=3, mime=get_image_type(cover_bytes), type=3, data=cover_bytes)
        except Exception as exc:
            logger.warning('Failed to fetch cover for track %s: %s', track.id, exc)

    tag['WOAF'] = WOAF(url=track_url)
    tag.save()


def _report(callback: ProgressCallback | None, progress: SyncProgress) -> None:
    """Deliver progress without letting a UI callback break the download."""
    if callback is None:
        return
    try:
        callback(progress)
    except Exception:
        logger.exception('Sync progress callback failed')


def _track_labels(track: Track) -> tuple[str, str]:
    artist = sanitize_component(track.artists[0].name if track.artists else '')
    title = sanitize_component(track.title)
    return artist, title


def run_sync(
    config: Config,
    *,
    dry_run: bool = False,
    limit: int | None = None,
    allow_zvuk: bool = True,
    progress: ProgressCallback | None = None,
) -> SyncResult:
    """Download new playlist tracks into the archive.

    The web player calls this with ``allow_zvuk=False``: it only stores the
    stream Yandex Music offers (often 192 kbps MP3) and never asks Zvuk for a
    320 kbps replacement. The CLI keeps the old fallback behaviour.
    """
    _report(progress, SyncProgress(phase='fetching'))
    token, playlist_url = config.require_yandex()
    owner, kind = parse_playlist_url(playlist_url)

    client = Client(token=token).init()
    playlist: Playlist = client.users_playlists(kind, owner)  # type: ignore[arg-type]
    playlist_tracks = playlist.tracks or []
    logger.info("Fetched playlist '%s' (%d tracks)", playlist.title, len(playlist_tracks))
    _report(progress, SyncProgress(phase='scanning'))

    target_dir = config.target_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    highest_id = get_highest_local_id(target_dir)
    logger.info('Highest local id: %d', highest_id)

    existing_identifiers = set()
    for file in target_dir.iterdir():
        if not file.is_file() or file.suffix.lower() not in SYNC_AUDIO_EXTENSIONS:
            continue
        match = SYNC_FILENAME_RE.match(file.name)
        if match is not None:
            existing_identifiers.add(f'{match.group(2)}-{match.group(3)}'.lower())

    max_downloads = limit if limit is not None else config.max_downloads
    new_tracks: list[Track] = []
    limit_reached = False
    for item in playlist_tracks:
        full = item.fetch_track()
        if full is None:
            continue
        identifier = build_track_identifier(full)
        if identifier in existing_identifiers:
            logger.info("Encountered already present track '%s' — stopping scan", identifier)
            break
        new_tracks.append(full)
        if len(new_tracks) >= max_downloads:
            limit_reached = True
            logger.info('Maximum number of tracks to download reached.')
            break

    total_new = len(new_tracks)
    if not new_tracks:
        logger.info('No new tracks to download.')
        result = SyncResult(dry_run=dry_run)
        _report(progress, SyncProgress(phase='done'))
        return result

    logger.info('Will download %d new tracks', total_new)
    new_tracks.reverse()

    if dry_run:
        for track in new_tracks:
            artist, title = _track_labels(track)
            logger.info('[dry-run] would download: %s - %s', artist, title)
        result = SyncResult(total_new=total_new, dry_run=True, limit_reached=limit_reached)
        _report(progress, SyncProgress(phase='done', total=total_new, limit_reached=limit_reached))
        return result

    next_id = highest_id + 1
    provider = build_zvuk_provider(config) if allow_zvuk else None
    if not allow_zvuk:
        logger.info('Zvuk fallback is disabled for this run; keeping Yandex quality')
    added = 0
    failed = 0
    completed = 0
    for track in new_tracks:
        artist, title = _track_labels(track)
        _report(
            progress,
            SyncProgress(
                phase='downloading',
                total=total_new,
                completed=completed,
                current_artist=artist,
                current_title=title,
                added=added,
                failed=failed,
                limit_reached=limit_reached,
            ),
        )
        temporary = download_track(
            track,
            target_dir,
            provider=provider,
            min_score=config.min_score,
            duration_tolerance=config.duration_tolerance,
        )
        if temporary is None:
            failed += 1
            completed += 1
            continue
        try:
            set_tags(temporary, track, next_id)
            final_path = target_dir / f'{next_id:04d}. {artist} - {title}.mp3'
            temporary.replace(final_path)
            logger.info('Saved %s', final_path.name)
        except Exception:
            logger.exception('Failed to tag or move track %s', track.id)
            temporary.unlink(missing_ok=True)
            failed += 1
            completed += 1
            continue
        added += 1
        completed += 1
        next_id += 1
        time.sleep(1)

    logger.info('Done. Collection now contains %d items.', next_id - 1)
    result = SyncResult(
        total_new=total_new,
        added=added,
        failed=failed,
        limit_reached=limit_reached,
    )
    _report(
        progress,
        SyncProgress(
            phase='done',
            total=total_new,
            completed=completed,
            added=added,
            failed=failed,
            limit_reached=limit_reached,
        ),
    )
    return result


__all__ = [
    'ConfigError',
    'SyncProgress',
    'SyncResult',
    'build_track_identifier',
    'build_zvuk_provider',
    'build_zvuk_query',
    'choose_best_download_info',
    'download_from_yandex',
    'download_from_zvuk',
    'download_track',
    'full_title',
    'get_highest_local_id',
    'get_image_type',
    'is_320_kbps',
    'parse_playlist_url',
    'run_sync',
    'set_tags',
]
