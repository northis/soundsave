"""Reading, scanning and retagging the local audio archive."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from mutagen import File as MutagenFile
from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, COMM, ID3, TALB, TCON, TDRC, TIT2, TPE1, TPE2, TRCK, USLT, WOAF, ID3NoHeaderError
from mutagen.mp3 import MP3

from .domain import AudioQuality, LocalTrack

logger = logging.getLogger(__name__)

FILENAME_RE = re.compile(r'^(\d+)\.\s*(.+?)\s-\s*(.*?)\.(mp3|flac)$', re.IGNORECASE)
AUDIO_EXTENSIONS = frozenset({'.mp3', '.flac'})
_LOSSLESS_EXTENSIONS = frozenset({'.flac', '.wav', '.aiff', '.alac'})


class ArchiveError(RuntimeError):
    """Raised when a file in the archive cannot be read."""


@dataclass(frozen=True)
class FilenameParts:
    """The numeric id and metadata encoded in an archive filename."""

    file_id: int
    artist: str
    title: str
    extension: str


@dataclass
class TagData:
    """Format-independent subset of audio tags."""

    title: str = ''
    artists: list[str] = field(default_factory=list)
    album: str | None = None
    album_artist: str | None = None
    date: str | None = None
    track_number: str | None = None
    genre: str | None = None
    comment: str | None = None
    lyrics: str | None = None
    cover: bytes | None = None
    cover_mime: str | None = None
    url: str | None = None


def sanitize_component(text: str) -> str:
    """Replace characters that are illegal in a Windows filename."""
    return re.sub(r'[\\/:*?"<>|]+', '-', text.strip())


def parse_filename(name: str) -> FilenameParts | None:
    """Parse ``NNNN. Artist - Title.ext`` and return its parts."""
    match = FILENAME_RE.match(name)
    if match is None:
        return None
    return FilenameParts(
        file_id=int(match.group(1)),
        artist=match.group(2).strip(),
        title=match.group(3).strip(),
        extension=match.group(4).lower(),
    )


def build_filename(file_id: int, artist: str, title: str, extension: str) -> str:
    """Build the canonical archive filename."""
    artist = sanitize_component(artist)
    title = sanitize_component(title)
    extension = extension.lstrip('.').lower()
    return f'{file_id:04d}. {artist} - {title}.{extension}'


def _first_tag(tags: object, key: str) -> str | None:
    value = tags.get(key) if hasattr(tags, 'get') else None
    if value is None:
        return None
    if isinstance(value, list | tuple):
        return str(value[0]) if value else None
    return str(value)


def _read_mp3_tags(path: Path) -> TagData:
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        return TagData()
    data = TagData()

    title_frames = tags.getall('TIT2')
    if title_frames and title_frames[0].text:
        data.title = str(title_frames[0].text[0])
    artist_frames = tags.getall('TPE1')
    if artist_frames:
        data.artists = [str(item) for item in artist_frames[0].text]
    album_frames = tags.getall('TALB')
    if album_frames and album_frames[0].text:
        data.album = str(album_frames[0].text[0])
    album_artist_frames = tags.getall('TPE2')
    if album_artist_frames and album_artist_frames[0].text:
        data.album_artist = str(album_artist_frames[0].text[0])
    date_frames = tags.getall('TDRC')
    if date_frames and date_frames[0].text:
        data.date = str(date_frames[0].text[0])
    track_frames = tags.getall('TRCK')
    if track_frames and track_frames[0].text:
        data.track_number = str(track_frames[0].text[0])
    genre_frames = tags.getall('TCON')
    if genre_frames and genre_frames[0].text:
        data.genre = str(genre_frames[0].text[0])
    comment_frames = tags.getall('COMM')
    if comment_frames and comment_frames[0].text:
        data.comment = str(comment_frames[0].text[0])
    lyrics_frames = tags.getall('USLT')
    if lyrics_frames:
        data.lyrics = str(lyrics_frames[0].text)
    picture_frames = tags.getall('APIC')
    if picture_frames:
        data.cover = picture_frames[0].data
        data.cover_mime = picture_frames[0].mime
    url_frames = tags.getall('WOAF')
    if url_frames:
        data.url = str(getattr(url_frames[0], 'url', '') or '')
    return data


def _read_flac_tags(path: Path) -> TagData:
    audio = FLAC(path)
    tags = audio.tags
    data = TagData()
    if tags is not None:
        data.title = _first_tag(tags, 'title') or ''
        data.album = _first_tag(tags, 'album')
        data.album_artist = _first_tag(tags, 'albumartist')
        data.date = _first_tag(tags, 'date')
        data.track_number = _first_tag(tags, 'tracknumber')
        data.genre = _first_tag(tags, 'genre')
        data.comment = _first_tag(tags, 'comment')
        data.lyrics = _first_tag(tags, 'lyrics')
        artist = tags.get('artist')
        if artist:
            data.artists = [str(item) for item in artist]
    if audio.pictures:
        picture = audio.pictures[0]
        data.cover = picture.data
        data.cover_mime = picture.mime
    return data


def read_tag_data(path: Path) -> TagData:
    """Read a format-independent subset of tags from *path*."""
    suffix = path.suffix.lower()
    if suffix == '.mp3':
        return _read_mp3_tags(path)
    if suffix == '.flac':
        return _read_flac_tags(path)
    return TagData()


def _read_audio_info(path: Path) -> tuple[AudioQuality, float | None]:
    audio = MutagenFile(path)
    if audio is None:
        raise ArchiveError(f'Unrecognised audio file: {path}')

    info = audio.info
    duration = float(info.length) if getattr(info, 'length', None) is not None else None
    suffix = path.suffix.lower()

    if isinstance(audio, FLAC):
        bitrate = getattr(info, 'bitrate', None)
        return AudioQuality('flac', round(bitrate / 1000) if bitrate else None, lossless=True), duration
    if isinstance(audio, MP3):
        bitrate = getattr(info, 'bitrate', None)
        return AudioQuality('mp3', round(bitrate / 1000) if bitrate else None), duration

    bitrate = getattr(info, 'bitrate', None)
    codec = suffix.lstrip('.') or 'unknown'
    return (
        AudioQuality(
            codec,
            round(bitrate / 1000) if bitrate else None,
            lossless=suffix in _LOSSLESS_EXTENSIONS,
        ),
        duration,
    )


def read_local_track(path: Path) -> LocalTrack:
    """Read metadata and audio quality for one archive file."""
    parts = parse_filename(path.name)
    tags = read_tag_data(path)
    quality, duration = _read_audio_info(path)

    artist = ', '.join(tags.artists) if tags.artists else (parts.artist if parts else '')
    title = tags.title or (parts.title if parts else path.stem)
    return LocalTrack(
        path=path,
        file_id=parts.file_id if parts else None,
        artist=artist,
        title=title,
        album=tags.album,
        duration_s=duration,
        quality=quality,
    )


def scan_archive(
    root: Path,
    *,
    limit: int | None = None,
    file_id: int | None = None,
) -> list[LocalTrack]:
    """Scan the top level of *root* for ``.mp3`` and ``.flac`` files."""
    if not root.exists():
        raise ArchiveError(f'Archive directory does not exist: {root}')
    if not root.is_dir():
        raise ArchiveError(f'Archive path is not a directory: {root}')

    tracks: list[LocalTrack] = []
    for path in sorted(root.iterdir(), key=lambda item: item.name.lower()):
        if not path.is_file() or path.suffix.lower() not in AUDIO_EXTENSIONS:
            continue
        if path.name.startswith('.'):
            continue
        parts = parse_filename(path.name)
        if file_id is not None and (parts is None or parts.file_id != file_id):
            continue
        try:
            tracks.append(read_local_track(path))
        except Exception as exc:
            logger.warning('Skipping unreadable file %s: %s', path.name, exc)
            continue
        if limit is not None and len(tracks) >= limit:
            break
    return tracks


def _write_mp3_tags(path: Path, data: TagData) -> None:
    tags = ID3()
    if data.title:
        tags.add(TIT2(encoding=3, text=data.title))
    if data.artists:
        tags.add(TPE1(encoding=3, text=data.artists))
    if data.album:
        tags.add(TALB(encoding=3, text=data.album))
    if data.album_artist:
        tags.add(TPE2(encoding=3, text=data.album_artist))
    if data.date:
        tags.add(TDRC(encoding=3, text=data.date))
    if data.track_number:
        tags.add(TRCK(encoding=3, text=data.track_number))
    if data.genre:
        tags.add(TCON(encoding=3, text=data.genre))
    if data.comment:
        tags.add(COMM(encoding=3, lang='eng', desc='', text=data.comment))
    if data.lyrics:
        tags.add(USLT(encoding=3, lang='eng', desc='', text=data.lyrics))
    if data.cover:
        tags.add(APIC(encoding=3, mime=data.cover_mime or 'image/jpeg', type=3, data=data.cover))
    if data.url:
        tags.add(WOAF(url=data.url))
    tags.save(path)


def _write_flac_tags(path: Path, data: TagData) -> None:
    audio = FLAC(path)
    tags = audio.tags if audio.tags is not None else {}
    tags.clear()
    if data.title:
        tags['title'] = [data.title]
    if data.artists:
        tags['artist'] = list(data.artists)
    if data.album:
        tags['album'] = [data.album]
    if data.album_artist:
        tags['albumartist'] = [data.album_artist]
    if data.date:
        tags['date'] = [data.date]
    if data.track_number:
        tags['tracknumber'] = [data.track_number]
    if data.genre:
        tags['genre'] = [data.genre]
    if data.comment:
        tags['comment'] = [data.comment]
    if data.lyrics:
        tags['lyrics'] = [data.lyrics]
    audio.tags = tags
    audio.clear_pictures()
    if data.cover:
        audio.add_picture(
            Picture(
                type=3,
                mime=data.cover_mime or 'image/jpeg',
                desc='',
                data=data.cover,
            ),
        )
    audio.save()


def copy_tags(source: Path, destination: Path) -> None:
    """Copy tags from *source* to *destination*, converting formats if needed."""
    source_suffix = source.suffix.lower()
    destination_suffix = destination.suffix.lower()

    if source_suffix == '.mp3' and destination_suffix == '.mp3':
        try:
            source_tags = ID3(source)
        except ID3NoHeaderError:
            return
        audio = MP3(destination)
        if audio.tags is None:
            audio.add_tags()
        assert audio.tags is not None
        audio.tags.clear()
        audio.tags.update(source_tags)
        audio.save()
        return

    if source_suffix == '.flac' and destination_suffix == '.flac':
        source_audio = FLAC(source)
        destination_audio = FLAC(destination)
        destination_audio.clear_pictures()
        for picture in source_audio.pictures:
            destination_audio.add_picture(picture)
        if source_audio.tags is not None:
            destination_audio.tags = source_audio.tags
        destination_audio.save()
        return

    data = read_tag_data(source)
    if destination_suffix == '.mp3':
        _write_mp3_tags(destination, data)
    elif destination_suffix == '.flac':
        _write_flac_tags(destination, data)
