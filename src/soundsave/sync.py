"""Download new tracks from a Yandex Music playlist."""

from __future__ import annotations

import contextlib
import datetime as dt
import logging
import re
import time
from pathlib import Path

from mutagen.id3 import APIC, TALB, TDRC, TIT2, TPE1, TPE2, TRCK, USLT, WOAF
from mutagen.id3._specs import ID3TimeStamp
from mutagen.mp3 import MP3
from yandex_music import Album, Client, DownloadInfo, Playlist, Track, YandexMusicModel

from .archive import FILENAME_RE, sanitize_component
from .config import Config, ConfigError

logger = logging.getLogger(__name__)

MAGIC_BYTES = (
    ('image/jpeg', bytes((0xFF, 0xD8, 0xFF))),
    ('image/png', bytes((0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A))),
)


def get_image_type(data: bytes) -> str:
    """Return the MIME type of an image based on its magic bytes."""
    for mime_type, magic_bytes in MAGIC_BYTES:
        if data.startswith(magic_bytes):
            return mime_type
    return 'image/jpeg'


def parse_playlist_url(url: str) -> tuple[str, int]:
    """Return ``(owner, kind)`` from a Yandex Music playlist URL."""
    match = re.match(r'https?://music\.yandex\.ru/users/([^/]+)/playlists/(\d+)', url)
    if match is None:
        raise ValueError(f'Unsupported playlist url: {url}')
    owner, kind = match.groups()
    return owner, int(kind)


def get_highest_local_id(target_dir: Path) -> int:
    """Return the largest numeric id already present in *target_dir*."""
    if not target_dir.exists():
        return 0
    highest = 0
    for file in target_dir.glob('*.mp3'):
        match = FILENAME_RE.match(file.name)
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


def download_track(track: Track, destination: Path) -> Path | None:
    """Download *track* into *destination* and return the temporary path."""
    temporary = destination / f'.soundsave-{track.id}.mp3'
    try:
        infos = track.get_download_info()
        best = choose_best_download_info(infos)
        destination.mkdir(parents=True, exist_ok=True)
        best.download(str(temporary))
    except Exception:
        logger.exception('Failed to download track %s', track.id)
        temporary.unlink(missing_ok=True)
        return None
    return temporary


def full_title(obj: YandexMusicModel) -> str:
    """Return ``title`` with an optional ``version`` suffix."""
    result = obj['title']
    if result is None:
        return ''
    version = obj['version']
    if version:
        result += f' ({version})'
    return result


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


def run_sync(config: Config, *, dry_run: bool = False, limit: int | None = None) -> None:
    """Download new playlist tracks into the archive."""
    token, playlist_url = config.require_yandex()
    owner, kind = parse_playlist_url(playlist_url)

    client = Client(token=token).init()
    playlist: Playlist = client.users_playlists(kind, owner)  # type: ignore[arg-type]
    playlist_tracks = playlist.tracks or []
    logger.info("Fetched playlist '%s' (%d tracks)", playlist.title, len(playlist_tracks))

    target_dir = config.target_dir
    highest_id = get_highest_local_id(target_dir)
    logger.info('Highest local id: %d', highest_id)

    existing_identifiers = set()
    for file in target_dir.glob('*.mp3'):
        match = FILENAME_RE.match(file.name)
        if match is not None:
            existing_identifiers.add(f'{match.group(2)}-{match.group(3)}'.lower())

    max_downloads = limit if limit is not None else config.max_downloads
    new_tracks: list[Track] = []
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
            logger.info('Maximum number of tracks to download reached.')
            break

    if not new_tracks:
        logger.info('No new tracks to download.')
        return

    logger.info('Will download %d new tracks', len(new_tracks))
    new_tracks.reverse()

    if dry_run:
        for track in new_tracks:
            artist = sanitize_component(track.artists[0].name if track.artists else '')
            title = sanitize_component(track.title)
            logger.info('[dry-run] would download: %s - %s', artist, title)
        return

    next_id = highest_id + 1
    for track in new_tracks:
        temporary = download_track(track, target_dir)
        if temporary is None:
            continue
        try:
            set_tags(temporary, track, next_id)
            artist = sanitize_component(track.artists[0].name if track.artists else '')
            title = sanitize_component(track.title)
            final_path = target_dir / f'{next_id:04d}. {artist} - {title}.mp3'
            temporary.replace(final_path)
            logger.info('Saved %s', final_path.name)
        except Exception:
            logger.exception('Failed to tag or move track %s', track.id)
            temporary.unlink(missing_ok=True)
            continue
        next_id += 1
        time.sleep(1)

    logger.info('Done. Collection now contains %d items.', next_id - 1)


__all__ = [
    'ConfigError',
    'build_track_identifier',
    'choose_best_download_info',
    'download_track',
    'full_title',
    'get_highest_local_id',
    'get_image_type',
    'parse_playlist_url',
    'run_sync',
    'set_tags',
]
