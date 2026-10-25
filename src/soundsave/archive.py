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
MAGIC_BYTES = (
    ('image/jpeg', bytes((0xFF, 0xD8, 0xFF))),
    ('image/png', bytes((0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A))),
)


class ArchiveError(RuntimeError):
    """Raised when a file in the archive cannot be read."""


def get_image_type(data: bytes) -> str:
    """Return the MIME type of an image based on its magic bytes."""
    for mime_type, magic_bytes in MAGIC_BYTES:
        if data.startswith(magic_bytes):
            return mime_type
    return 'image/jpeg'


def has_text(value: str | None) -> bool:
    """Return whether *value* contains non-whitespace text."""
    return bool(value and value.strip())


def has_artists(artists: list[str]) -> bool:
    """Return whether *artists* contains at least one non-empty name."""
    return any(artist.strip() for artist in artists)


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
    cover_type: int | None = None
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


def _first_text(tags: object, key: str) -> str | None:
    """Return the first non-empty value for *key*, or ``None``."""
    value = tags.get(key) if hasattr(tags, 'get') else None
    if value is None:
        return None
    values = value if isinstance(value, list | tuple) else [value]
    for item in values:
        text = str(item)
        if text.strip():
            return text
    return None


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
    for frame in lyrics_frames:
        text = str(frame.text)
        if text.strip():
            data.lyrics = text
            break
    picture_frames = tags.getall('APIC')
    if picture_frames:
        picture = next((frame for frame in picture_frames if frame.type == 3), picture_frames[0])
        data.cover = picture.data
        data.cover_mime = picture.mime
        data.cover_type = picture.type
    url_frames = tags.getall('WOAF')
    for frame in url_frames:
        url = str(getattr(frame, 'url', '') or '')
        if url.strip():
            data.url = url
            break
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
        data.lyrics = _first_text(tags, 'lyrics')
        data.url = _first_text(tags, 'url')
        artist = tags.get('artist')
        if artist:
            data.artists = [str(item) for item in artist]
    if audio.pictures:
        picture = next((item for item in audio.pictures if item.type == 3), audio.pictures[0])
        data.cover = picture.data
        data.cover_mime = picture.mime
        data.cover_type = picture.type
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
    if audio.tags is None:
        audio.add_tags()
    tags = audio.tags
    if tags is None:
        raise ArchiveError(f'Could not create FLAC tags: {path}')
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
    if data.url:
        tags['url'] = [data.url]
    audio.clear_pictures()
    if data.cover:
        picture = Picture()
        picture.type = 3
        picture.mime = data.cover_mime or 'image/jpeg'
        picture.desc = ''
        picture.data = data.cover
        audio.add_picture(picture)
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


def _replace_mp3_frame(tags: ID3, frame_id: str, frame: object) -> None:
    tags.delall(frame_id)
    tags.add(frame)  # type: ignore[arg-type]


def _update_mp3_tags(path: Path, data: TagData) -> bool:
    existing = _read_mp3_tags(path)
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()

    changed = False
    if data.title and not has_text(existing.title):
        _replace_mp3_frame(tags, 'TIT2', TIT2(encoding=3, text=data.title))
        changed = True
    if data.artists and not has_artists(existing.artists):
        _replace_mp3_frame(tags, 'TPE1', TPE1(encoding=3, text=data.artists))
        changed = True
    if data.album and not has_text(existing.album):
        _replace_mp3_frame(tags, 'TALB', TALB(encoding=3, text=data.album))
        changed = True
    if data.album_artist and not has_text(existing.album_artist):
        _replace_mp3_frame(tags, 'TPE2', TPE2(encoding=3, text=data.album_artist))
        changed = True
    if data.date and not has_text(existing.date):
        _replace_mp3_frame(tags, 'TDRC', TDRC(encoding=3, text=data.date))
        changed = True
    if data.genre and not has_text(existing.genre):
        _replace_mp3_frame(tags, 'TCON', TCON(encoding=3, text=data.genre))
        changed = True
    if data.lyrics and not has_text(existing.lyrics):
        _replace_mp3_frame(tags, 'USLT', USLT(encoding=3, text=data.lyrics))
        changed = True
    if data.url and not has_text(existing.url):
        _replace_mp3_frame(tags, 'WOAF', WOAF(url=data.url))
        changed = True
    if data.cover and (existing.cover is None or existing.cover_type != 3):
        for key in list(tags.keys()):
            if key.startswith('APIC:') and tags[key].type == 3:
                del tags[key]
        tags.add(
            APIC(
                encoding=3,
                mime=data.cover_mime or 'image/jpeg',
                type=3,
                data=data.cover,
            ),
        )
        changed = True

    if not changed:
        return False
    tags.save(path)
    return True


def _update_flac_tags(path: Path, data: TagData) -> bool:
    existing = _read_flac_tags(path)
    audio = FLAC(path)
    if audio.tags is None:
        audio.add_tags()
    tags = audio.tags
    if tags is None:
        raise ArchiveError(f'Could not create FLAC tags: {path}')

    changed = False
    if data.title and not has_text(existing.title):
        tags['title'] = [data.title]
        changed = True
    if data.artists and not has_artists(existing.artists):
        tags['artist'] = list(data.artists)
        changed = True
    if data.album and not has_text(existing.album):
        tags['album'] = [data.album]
        changed = True
    if data.album_artist and not has_text(existing.album_artist):
        tags['albumartist'] = [data.album_artist]
        changed = True
    if data.date and not has_text(existing.date):
        tags['date'] = [data.date]
        changed = True
    if data.genre and not has_text(existing.genre):
        tags['genre'] = [data.genre]
        changed = True
    if data.lyrics and not has_text(existing.lyrics):
        tags['lyrics'] = [data.lyrics]
        changed = True
    if data.url and not has_text(existing.url):
        tags['url'] = [data.url]
        changed = True
    if data.cover and (existing.cover is None or existing.cover_type != 3):
        for picture in list(audio.pictures or []):
            if picture.type == 3:
                audio.remove_picture(picture)
        picture = Picture()
        picture.type = 3
        picture.mime = data.cover_mime or 'image/jpeg'
        picture.desc = ''
        picture.data = data.cover
        audio.add_picture(picture)
        changed = True

    if not changed:
        return False
    audio.save()
    return True


def update_tag_data(path: Path, data: TagData, *, cover_mode: str = 'missing') -> bool:
    """Fill empty tags in *path* from *data*, preserving all other frames.

    Only fields that are empty in the file are written; non-empty fields are
    left untouched. The function returns whether the file was changed.
    """
    if cover_mode != 'missing':
        raise ValueError(f'Unsupported cover mode: {cover_mode}')
    suffix = path.suffix.lower()
    if suffix == '.mp3':
        return _update_mp3_tags(path, data)
    if suffix == '.flac':
        return _update_flac_tags(path, data)
    raise ArchiveError(f'Unsupported audio format: {path.suffix}')
