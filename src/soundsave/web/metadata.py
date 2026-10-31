"""Metadata extraction for the flat web playlist folders."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from mutagen import File as MutagenFile
from mutagen.aac import AAC
from mutagen.aiff import AIFF
from mutagen.flac import FLAC
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis
from mutagen.wave import WAVE

from ..archive import read_tag_data

logger = logging.getLogger(__name__)

LIBRARY_EXTENSIONS = frozenset(
    {'.mp3', '.flac', '.m4a', '.mp4', '.aac', '.ogg', '.opus', '.wav', '.aiff', '.aif'},
)
MIME_TYPES = {
    '.mp3': 'audio/mpeg',
    '.flac': 'audio/flac',
    '.m4a': 'audio/mp4',
    '.mp4': 'audio/mp4',
    '.aac': 'audio/aac',
    '.ogg': 'audio/ogg',
    '.opus': 'audio/ogg',
    '.wav': 'audio/wav',
    '.aiff': 'audio/aiff',
    '.aif': 'audio/aiff',
}
_NUMBER_RE = re.compile(r'^(\d+)\s*[.\-_)]\s*')
_YEAR_RE = re.compile(r'(?<!\d)(\d{4})(?!\d)')


@dataclass(frozen=True)
class LibraryMetadata:
    """Everything the web library needs from one audio file."""

    title: str
    artists: list[str]
    album: str | None
    year: int | None
    duration_s: float | None
    format: str
    bitrate_kbps: int | None
    sample_rate_hz: int | None
    has_cover: bool


def is_supported_audio(path: Path) -> bool:
    return path.suffix.lower() in LIBRARY_EXTENSIONS


def mime_for_path(path: Path) -> str:
    return MIME_TYPES.get(path.suffix.lower(), 'application/octet-stream')


def track_number(filename: str) -> int | None:
    match = _NUMBER_RE.match(filename)
    if match is None:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def parse_library_filename(filename: str) -> tuple[int | None, str, str]:
    """Return ``(number, artist, title)`` from a filename.

    Tags win over this fallback. The parser intentionally keeps the first
    `` - `` as the artist/title boundary, matching the archive convention.
    """
    stem = Path(filename).stem
    number = track_number(stem)
    rest = _NUMBER_RE.sub('', stem, count=1).strip()
    if ' - ' in rest:
        artist, title = rest.split(' - ', 1)
        return number, artist.strip(), title.strip()
    return number, '', rest


def natural_key(value: str) -> tuple[object, ...]:
    """Sort filenames naturally: ``02`` before ``10``, case-insensitive."""
    parts = re.split(r'(\d+)', value.casefold())
    return tuple(int(part) if part.isdigit() else part for part in parts)


def etag_for_stat(size: int, mtime_ns: int) -> str:
    return f'{size:x}-{mtime_ns:x}'


def year_from_date(value: str | None) -> int | None:
    """Return the four-digit year from a tag date, or ``None``."""
    if not value:
        return None
    match = _YEAR_RE.search(value)
    if match is None:
        return None
    year = int(match.group(1))
    return year if 1000 <= year <= 9999 else None


def _format_for(audio: object, path: Path) -> str:
    if isinstance(audio, MP3):
        return 'MP3'
    if isinstance(audio, FLAC):
        return 'FLAC'
    if isinstance(audio, MP4):
        codec = str(getattr(getattr(audio, 'info', None), 'codec', '') or '').lower()
        return 'ALAC' if 'alac' in codec else 'AAC'
    if isinstance(audio, AAC):
        return 'AAC'
    if isinstance(audio, OggVorbis):
        return 'OGG'
    if isinstance(audio, OggOpus):
        return 'OPUS'
    if isinstance(audio, WAVE):
        return 'WAV'
    if isinstance(audio, AIFF):
        return 'AIFF'
    return path.suffix.lstrip('.').upper() or 'UNKNOWN'


def read_library_metadata(path: Path) -> LibraryMetadata:
    """Read tags and audio properties, falling back to the filename."""
    _, filename_artist, filename_title = parse_library_filename(path.name)
    try:
        tags = read_tag_data(path, include_cover=False)
    except Exception as exc:
        logger.warning('Could not read tags from %s: %s', path.name, exc)
        tags = None

    title = (tags.title.strip() if tags and tags.title else '') or filename_title or path.stem
    artists = list(tags.artists) if tags and tags.artists else []
    if not artists and filename_artist:
        artists = [filename_artist]
    album = tags.album if tags else None
    year = year_from_date(tags.date if tags else None)
    if year is None:
        # read_tag_data() covers MP3/FLAC/MP4; other library formats (OGG,
        # Opus, WAV, AIFF, AAC) can still expose a date through mutagen's
        # easy interface.
        try:
            easy = MutagenFile(path, easy=True)
            if easy is not None and easy.tags:
                raw_date = easy.tags.get('date') or easy.tags.get('year')
                if raw_date:
                    year = year_from_date(str(raw_date[0]))
        except Exception as exc:
            logger.debug('Could not read date from %s: %s', path.name, exc)
    has_cover = bool(tags and tags.has_cover)

    duration_s: float | None = None
    bitrate_kbps: int | None = None
    sample_rate_hz: int | None = None
    format_name = path.suffix.lstrip('.').upper() or 'UNKNOWN'
    try:
        audio = MutagenFile(path)
    except Exception as exc:
        logger.warning('Could not read audio properties from %s: %s', path.name, exc)
        audio = None
    if audio is not None:
        info = audio.info
        length = getattr(info, 'length', None)
        duration_s = float(length) if length is not None else None
        bitrate = getattr(info, 'bitrate', None)
        bitrate_kbps = round(bitrate / 1000) if bitrate else None
        sample_rate = getattr(info, 'sample_rate', None)
        sample_rate_hz = int(sample_rate) if sample_rate else None
        format_name = _format_for(audio, path)

    return LibraryMetadata(
        title=title,
        artists=artists,
        album=album,
        year=year,
        duration_s=duration_s,
        format=format_name,
        bitrate_kbps=bitrate_kbps,
        sample_rate_hz=sample_rate_hz,
        has_cover=has_cover,
    )
