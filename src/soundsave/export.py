"""Export foobar2000 playlists into a folder for containers and portable players.

Lossy sources are copied unchanged; lossless sources are transcoded to AAC in
an MP4 container (``.m4a``) with ``ffmpeg``. ``.ape``/``.flac`` albums described
by a CUE sheet are split into separate tracks using the sheet's ``INDEX`` times.

Output files are named ``<playlist number>. <artist> - <title>.<ext>`` with
leading zeros wide enough to keep the folder sortable.
"""

from __future__ import annotations

import logging
import re
import shutil
import struct
import subprocess
import unicodedata
from collections import Counter
from collections.abc import Iterable, Sequence
from contextlib import suppress
from dataclasses import dataclass, field, replace
from pathlib import Path
from urllib.parse import unquote, urlparse

from mutagen import File as MutagenFile

logger = logging.getLogger('soundsave')

FPL_MAGIC = bytes.fromhex('e1a09c91f83c7742852c3bcc1401d3f2')
FPL_V2_MAGIC = bytes.fromhex('003759db444d564e8034c62aba89a6b9')
_FPL_CHUNK = 68

PLAYLIST_SUFFIXES = frozenset({'.fpl', '.m3u', '.m3u8'})
LOSSLESS_SUFFIXES = frozenset({'.flac', '.ape', '.wav', '.aiff', '.aif', '.alac', '.wv', '.tta', '.dsf', '.dff'})
MP4_SUFFIXES = frozenset({'.m4a', '.mp4'})

# Characters that Windows rejects in file names. The exported folder is meant to
# be readable on every platform, so the same rules are applied everywhere.
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = frozenset(
    {'CON', 'PRN', 'AUX', 'NUL'} | {f'COM{i}' for i in range(1, 10)} | {f'LPT{i}' for i in range(1, 10)},
)
_SUBSONG = re.compile(r'^(?P<path>.*)\|subsong=(?P<index>\d+)$', re.IGNORECASE)

_CUE_FILE = re.compile(r'^FILE\s+(?:"(?P<quoted>[^"]+)"|(?P<plain>\S+))(?:\s+\S+)?\s*$', re.IGNORECASE)
_CUE_TRACK = re.compile(r'^TRACK\s+(\d+)\s+AUDIO\s*$', re.IGNORECASE)
_CUE_TITLE = re.compile(r'^TITLE\s+"(.*)"\s*$', re.IGNORECASE)
_CUE_PERFORMER = re.compile(r'^PERFORMER\s+"(.*)"\s*$', re.IGNORECASE)
_CUE_INDEX = re.compile(r'^INDEX\s+(\d+)\s+(\d+):(\d+):(\d+)\s*$', re.IGNORECASE)


class PlaylistError(RuntimeError):
    """Raised when a playlist or a CUE sheet cannot be read."""


@dataclass(slots=True)
class PlaylistEntry:
    """One item of a playlist before CUE expansion."""

    path: Path
    subsong: int | None = None
    title: str = ''
    artist: str = ''
    album: str = ''
    album_artist: str = ''
    duration: float | None = None


@dataclass(slots=True)
class CueTrack:
    """One track inside a CUE sheet."""

    number: int
    title: str
    performer: str
    start: float
    end: float | None
    file: Path


@dataclass(slots=True)
class CueSheet:
    """A parsed CUE sheet."""

    path: Path
    title: str = ''
    performer: str = ''
    tracks: list[CueTrack] = field(default_factory=list)


@dataclass(slots=True)
class ResolvedTrack:
    """A playlist item after CUE expansion, ready to be exported."""

    index: int
    source: Path
    start: float | None
    end: float | None
    title: str
    artist: str
    album: str
    album_artist: str
    duration: float | None
    lossless: bool
    original_track: int | None = None


@dataclass(slots=True)
class ExportReport:
    """Summary of one export run."""

    total: int = 0
    copied: int = 0
    transcoded: int = 0
    skipped: int = 0
    failed: int = 0
    bytes_written: int = 0
    files: list[Path] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Return whether every planned track was produced."""
        return self.failed == 0


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ('utf-8-sig', 'cp1251', 'latin-1'):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', errors='replace')


def _cstr(buffer: bytes, offset: int) -> str:
    if offset < 0 or offset >= len(buffer):
        return ''
    end = buffer.find(b'\x00', offset)
    return buffer[offset : end if end != -1 else len(buffer)].decode('utf-8', errors='replace')


def _resolve_path(raw: str, base: Path) -> Path:
    """Turn a playlist path into a local one, keeping it unresolved when missing."""
    value = raw.strip().strip('"')
    if value.lower().startswith('file://'):
        parsed = urlparse(value)
        value = unquote(parsed.path)
        if parsed.netloc:
            value = f'//{parsed.netloc}{value}'
        if re.match(r'^/[A-Za-z]:', value):
            value = value[1:]
    # Keep drive-letter and UNC paths intact even on POSIX: a --map rule may
    # rewrite them to a path that exists on this machine.
    if re.match(r'^[A-Za-z]:[\\/]', value) or value.startswith('\\\\'):
        return Path(value)
    candidates = [Path(value)]
    if '\\' in value:
        candidates.append(Path(value.replace('\\', '/')))
    for candidate in candidates:
        if candidate.is_absolute():
            if candidate.exists():
                return candidate
        elif (base / candidate).exists():
            return base / candidate
    last = candidates[-1]
    return last if last.is_absolute() else base / last


def _split_subsong(raw: str) -> tuple[str, int | None]:
    match = _SUBSONG.match(raw)
    if match is None:
        return raw, None
    return match.group('path'), int(match.group('index'))


def _number(value: str) -> int | None:
    match = re.search(r'\d+', value or '')
    return int(match.group(0)) if match else None


def _metadata_value(value: str) -> str:
    value = value.strip()
    if not value or len(value) > 300 or value.lower().startswith('file://'):
        return ''
    return value


# --------------------------------------------------------------------------
# FPL (foobar2000 binary playlist)
# --------------------------------------------------------------------------


def _fpl_metadata(
    strings: bytes,
    keys: Sequence[int],
    key_count: int,
    primary: int,
    secondary: int,
    secondary_offset: int,
) -> dict[str, str]:
    metadata: dict[str, str] = {}

    def store(name: str, value: str) -> None:
        name = name.strip().lower()
        value = _metadata_value(value)
        if name and value and name not in metadata:
            metadata[name] = value

    for index in range(min(primary, key_count // 2)):
        key_value = keys[index * 2]
        name = _cstr(strings, keys[index * 2 + 1])
        offset = 1 + key_value + primary * 2
        if offset >= key_count:
            offset = primary * 2 + index
        store(name, _cstr(strings, keys[offset]) if 0 <= offset < key_count else '')

    for index in range(secondary):
        base = secondary_offset + index * 2
        if base + 1 >= key_count:
            break
        store(_cstr(strings, keys[base]), _cstr(strings, keys[base + 1]))
    return metadata


def parse_fpl(data: bytes, base_dir: Path) -> list[PlaylistEntry]:
    """Parse a foobar2000 ``.fpl`` playlist (v1 and the newer v2 header)."""
    if data[:16] == FPL_MAGIC:
        header = 20
    elif data[:16] == FPL_V2_MAGIC:
        header = 60
    else:
        raise PlaylistError('Not a foobar2000 FPL playlist')

    if len(data) < header:
        raise PlaylistError('Truncated FPL header')
    string_size = struct.unpack_from('<I', data, header - 4)[0]
    strings_end = header + string_size
    if strings_end + 4 > len(data):
        raise PlaylistError('Truncated FPL string table')

    strings = data[header:strings_end]
    position = strings_end
    count = struct.unpack_from('<I', data, position)[0]
    position += 4

    entries: list[PlaylistEntry] = []
    for _ in range(count):
        if position + _FPL_CHUNK > len(data):
            break
        _, file_offset, subsong, _, _, _, _ = struct.unpack_from('<7I', data, position)
        duration = struct.unpack_from('<d', data, position + 28)[0]
        keys_dex, primary, secondary, secondary_offset = struct.unpack_from('<4I', data, position + 52)
        position += _FPL_CHUNK

        key_count = max(0, keys_dex - 3)
        if position + key_count * 4 > len(data):
            break
        keys = struct.unpack_from(f'<{key_count}I', data, position)
        position += key_count * 4

        metadata = _fpl_metadata(strings, keys, key_count, primary, secondary, secondary_offset)
        entries.append(
            PlaylistEntry(
                path=_resolve_path(_cstr(strings, file_offset), base_dir),
                subsong=int(subsong),
                title=metadata.get('title', ''),
                artist=metadata.get('artist', ''),
                album=metadata.get('album', ''),
                album_artist=metadata.get('album artist', ''),
                duration=duration if 0 < duration < 1e7 else None,
            ),
        )
    return entries


# --------------------------------------------------------------------------
# M3U / M3U8
# --------------------------------------------------------------------------


def _split_label(label: str) -> tuple[str, str]:
    value = label.strip()
    if not value:
        return '', ''
    if ' - ' in value:
        artist, _, title = value.partition(' - ')
        return artist.strip(), title.strip()
    return '', value


def parse_m3u8(text: str, base_dir: Path) -> list[PlaylistEntry]:
    """Parse an extended M3U/M3U8 playlist."""
    entries: list[PlaylistEntry] = []
    duration: float | None = None
    label = ''
    for raw_line in text.splitlines():
        line = raw_line.strip().lstrip('\ufeff')
        if not line:
            continue
        if line.upper().startswith('#EXTINF:'):
            head, _, label = line[8:].partition(',')
            try:
                duration = float(head.strip())
            except ValueError:
                duration = None
            if duration is not None and duration < 0:
                duration = None
            continue
        if line.startswith('#'):
            continue
        raw_path, subsong = _split_subsong(line)
        artist, title = _split_label(label)
        entries.append(
            PlaylistEntry(
                path=_resolve_path(raw_path, base_dir),
                subsong=subsong,
                title=title,
                artist=artist,
                duration=duration,
            ),
        )
        duration = None
        label = ''
    return entries


def parse_playlist(path: Path) -> list[PlaylistEntry]:
    """Read a ``.fpl``, ``.m3u`` or ``.m3u8`` playlist."""
    playlist = Path(path).expanduser()
    if not playlist.is_file():
        raise PlaylistError(f'Playlist not found: {playlist}')
    suffix = playlist.suffix.lower()
    if suffix not in PLAYLIST_SUFFIXES:
        raise PlaylistError(f'Unsupported playlist format: {suffix or playlist.name}')
    data = playlist.read_bytes()
    if data[:16] in (FPL_MAGIC, FPL_V2_MAGIC):
        return parse_fpl(data, playlist.parent)
    return parse_m3u8(_read_text(playlist), playlist.parent)


# --------------------------------------------------------------------------
# CUE sheets
# --------------------------------------------------------------------------


def _cue_time(minutes: str, seconds: str, frames: str) -> float:
    return int(minutes) * 60 + int(seconds) + int(frames) / 75


def _same_file(left: Path, right: Path) -> bool:
    try:
        if left.resolve() == right.resolve():
            return True
    except OSError:
        pass
    return left.name.casefold() == right.name.casefold()


def parse_cue(path: Path) -> CueSheet:
    """Parse a CUE sheet and compute each track's start and end time."""
    sheet = CueSheet(path=Path(path))
    tracks: list[CueTrack] = []
    current_file: Path | None = None
    current: dict[str, object] | None = None

    def flush() -> None:
        nonlocal current
        if current is None:
            return
        source = current_file or path.with_suffix('.flac')
        tracks.append(
            CueTrack(
                number=int(current['number']),
                title=str(current.get('title') or ''),
                performer=str(current.get('performer') or ''),
                start=float(current.get('start') or 0.0),
                end=None,
                file=source,
            ),
        )
        current = None

    for raw_line in _read_text(path).splitlines():
        line = raw_line.strip()
        if not line or line.upper().startswith('REM '):
            continue
        match = _CUE_FILE.match(line)
        if match:
            flush()
            current_file = _resolve_path(match.group('quoted') or match.group('plain'), path.parent)
            continue
        match = _CUE_TRACK.match(line)
        if match:
            flush()
            current = {'number': int(match.group(1)), 'title': '', 'performer': '', 'start': None}
            continue
        match = _CUE_TITLE.match(line)
        if match:
            if current is not None:
                current['title'] = match.group(1).strip()
            else:
                sheet.title = match.group(1).strip()
            continue
        match = _CUE_PERFORMER.match(line)
        if match:
            if current is not None:
                current['performer'] = match.group(1).strip()
            else:
                sheet.performer = match.group(1).strip()
            continue
        match = _CUE_INDEX.match(line)
        if match and current is not None:
            number = int(match.group(1))
            if number == 1 or (number == 0 and current.get('start') is None):
                current['start'] = _cue_time(match.group(2), match.group(3), match.group(4))
    flush()

    for index, track in enumerate(tracks):
        following = tracks[index + 1] if index + 1 < len(tracks) else None
        if following is not None and _same_file(following.file, track.file):
            track.end = following.start
    sheet.tracks = tracks
    return sheet


def _cue_for(path: Path, cache: dict[Path, CueSheet | None]) -> CueSheet | None:
    candidates = [path.with_suffix('.cue')]
    if path.parent.is_dir():
        candidates.extend(sorted(cue for cue in path.parent.glob('*.cue') if cue not in candidates))
    for cue_path in candidates:
        if not cue_path.is_file():
            continue
        if cue_path not in cache:
            try:
                cache[cue_path] = parse_cue(cue_path)
            except (OSError, PlaylistError):
                cache[cue_path] = None
        sheet = cache[cue_path]
        if sheet is not None and any(_same_file(track.file, path) for track in sheet.tracks):
            return sheet
    return None


def _normalise(value: str) -> str:
    return ' '.join(value.casefold().split())


def _track_length(track: CueTrack) -> float | None:
    if track.end is None:
        return None
    return max(0.0, track.end - track.start)


def _match_cue_track(entry: PlaylistEntry, sheet: CueSheet, occurrences: int) -> CueTrack | None:
    tracks = [track for track in sheet.tracks if _same_file(track.file, entry.path)]
    if not tracks:
        return None
    if entry.subsong is not None and 0 <= entry.subsong < len(tracks):
        candidate = tracks[entry.subsong]
        if occurrences > 1 or len(tracks) == 1:
            return candidate
        if entry.title and _normalise(candidate.title) == _normalise(entry.title):
            return candidate
        length = _track_length(candidate)
        if entry.duration and length and abs(length - entry.duration) <= 2.0:
            return candidate
    if entry.title:
        for track in tracks:
            if _normalise(track.title) == _normalise(entry.title):
                return track
    if entry.duration:
        for track in tracks:
            length = _track_length(track)
            if length and abs(length - entry.duration) <= 2.0:
                return track
    if entry.subsong is not None and 0 <= entry.subsong < len(tracks):
        return tracks[entry.subsong]
    return None


# --------------------------------------------------------------------------
# Source inspection
# --------------------------------------------------------------------------


def _read_tags(path: Path) -> tuple[dict[str, str], float | None]:
    if not path.is_file():
        return {}, None
    try:
        audio = MutagenFile(path, easy=True)
    except Exception:  # a broken file must not abort the export
        return {}, None
    if audio is None:
        return {}, None
    tags: dict[str, str] = {}
    raw_tags = getattr(audio, 'tags', None)
    if raw_tags:
        for key in ('title', 'artist', 'album', 'albumartist', 'tracknumber'):
            try:
                value = raw_tags.get(key)
            except (KeyError, ValueError):
                continue
            if value:
                tags[key] = str(value[0])
    length = getattr(getattr(audio, 'info', None), 'length', None)
    return tags, float(length) if length else None


def is_lossless(path: Path) -> bool:
    """Return whether *path* holds a lossless stream."""
    suffix = path.suffix.lower()
    if suffix in LOSSLESS_SUFFIXES:
        return True
    if suffix not in MP4_SUFFIXES:
        return False
    try:
        audio = MutagenFile(path)
    except Exception:  # treat unreadable files as lossy copies
        return False
    codec = str(getattr(getattr(audio, 'info', None), 'codec', '') or '').lower()
    return 'alac' in codec


# --------------------------------------------------------------------------
# Track resolution
# --------------------------------------------------------------------------


def _resolved_plain(index: int, entry: PlaylistEntry) -> ResolvedTrack:
    tags, tag_duration = _read_tags(entry.path)
    artist = tags.get('artist') or entry.artist or tags.get('albumartist') or entry.album_artist
    return ResolvedTrack(
        index=index,
        source=entry.path,
        start=None,
        end=None,
        title=tags.get('title') or entry.title or entry.path.stem,
        artist=artist,
        album=tags.get('album') or entry.album,
        album_artist=tags.get('albumartist') or entry.album_artist or artist,
        duration=entry.duration or tag_duration,
        lossless=is_lossless(entry.path),
        original_track=_number(tags.get('tracknumber', '')),
    )


def _resolved_from_cue(index: int, entry: PlaylistEntry, sheet: CueSheet, track: CueTrack) -> ResolvedTrack:
    artist = track.performer or sheet.performer or entry.artist or entry.album_artist
    length = _track_length(track)
    return ResolvedTrack(
        index=index,
        source=track.file,
        start=track.start,
        end=track.end,
        title=track.title or entry.title or track.file.stem,
        artist=artist,
        album=sheet.title or entry.album,
        album_artist=sheet.performer or entry.album_artist or artist,
        duration=length or entry.duration,
        lossless=is_lossless(track.file),
        original_track=track.number,
    )


def resolve_tracks(entries: Sequence[PlaylistEntry]) -> list[ResolvedTrack]:
    """Expand CUE sheets and fill in metadata for every playlist item."""
    occurrences = Counter(entry.path for entry in entries)
    cue_cache: dict[Path, CueSheet | None] = {}
    tracks: list[ResolvedTrack] = []
    for entry in entries:
        if entry.path.suffix.lower() == '.cue' and entry.path.is_file():
            sheet = parse_cue(entry.path)
            for cue_track in sheet.tracks:
                tracks.append(_resolved_from_cue(len(tracks) + 1, entry, sheet, cue_track))
            continue
        sheet = None
        cue_track = None
        if entry.path.suffix.lower() != '.cue':
            sheet = _cue_for(entry.path, cue_cache)
            if sheet is not None:
                cue_track = _match_cue_track(entry, sheet, occurrences[entry.path])
        if sheet is not None and cue_track is not None:
            tracks.append(_resolved_from_cue(len(tracks) + 1, entry, sheet, cue_track))
        else:
            tracks.append(_resolved_plain(len(tracks) + 1, entry))
    return tracks


# --------------------------------------------------------------------------
# Export
# --------------------------------------------------------------------------


def safe_component(value: str, *, limit: int = 120) -> str:
    """Return *value* as a file-name component that every platform accepts."""
    value = unicodedata.normalize('NFC', value)
    value = _ILLEGAL.sub('_', value)
    value = ' '.join(value.split()).strip(' .')
    if len(value) > limit:
        value = value[:limit].rstrip(' .')
    if not value:
        return 'Untitled'
    if value.split('.')[0].upper() in _RESERVED:
        value = f'_{value}'
    return value


def track_filename(track: ResolvedTrack, width: int) -> str:
    """Build ``<number>. <artist> - <title>.<ext>`` for *track*."""
    suffix = '.m4a' if track.lossless else (track.source.suffix.lower() or '.mp3')
    number = f'{track.index:0{width}d}'
    artist = safe_component(track.artist or 'Unknown Artist', limit=80)
    title = safe_component(track.title or track.source.stem, limit=80)
    return f'{number}. {artist} - {title}{suffix}'


def _apply_mapping(path: Path, mappings: Iterable[tuple[str, str]]) -> Path:
    text = str(path)
    for old, new in mappings:
        if text.startswith(old):
            return Path(new + text[len(old) :])
        alt_text = text.replace('\\', '/')
        alt_old = old.replace('\\', '/')
        if alt_text.startswith(alt_old):
            return Path(new + alt_text[len(alt_old) :])
    return path


def _write_track_number(path: Path, number: int) -> None:
    try:
        audio = MutagenFile(path, easy=True)
        if audio is None:
            return
        if audio.tags is None:
            audio.add_tags()
        audio['tracknumber'] = str(number)
        audio.save()
    except Exception as exc:  # tagging is best effort
        logger.warning('Cannot set the track number in %s: %s', path.name, exc)


def _transcode(
    source: Path,
    destination: Path,
    *,
    ffmpeg: str,
    start: float | None,
    end: float | None,
    title: str,
    artist: str,
    album: str,
    album_artist: str,
    number: int,
    bitrate: int,
) -> None:
    command = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y']
    if start:
        command += ['-ss', f'{start:.3f}']
    command += ['-i', str(source)]
    if end is not None and end > (start or 0):
        command += ['-t', f'{end - (start or 0):.3f}']
    command += [
        '-map',
        '0:a:0',
        '-map',
        '0:v?',
        '-c:a',
        'aac',
        '-b:a',
        f'{bitrate}k',
        '-c:v',
        'copy',
        '-disposition:v',
        'attached_pic',
        '-movflags',
        '+faststart',
        '-metadata',
        f'title={title}',
        '-metadata',
        f'artist={artist}',
    ]
    if album:
        command += ['-metadata', f'album={album}']
    if album_artist:
        command += ['-metadata', f'album_artist={album_artist}']
    command += ['-metadata', f'track={number}', str(destination)]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise PlaylistError(f'ffmpeg was not found: {ffmpeg}') from exc
    except subprocess.CalledProcessError as exc:
        message = (exc.stderr or '').strip() or str(exc)
        raise PlaylistError(f'ffmpeg failed for {source.name}: {message}') from exc
    if not destination.is_file():
        raise PlaylistError(f'ffmpeg produced no output for {source.name}')


def _temporary_path(destination: Path) -> Path:
    """A sibling path with the same suffix so ffmpeg can pick the muxer."""
    return destination.with_name(f'.{destination.stem}.part{destination.suffix}')


def _write_m3u8(playlist: Path, output_dir: Path, tracks: Sequence[ResolvedTrack], names: Sequence[str]) -> Path:
    target = output_dir / f'{safe_component(playlist.stem)}.m3u8'
    lines = ['#EXTM3U']
    for track, name in zip(tracks, names, strict=True):
        duration = round(track.duration) if track.duration else -1
        label = ' - '.join(part for part in (track.artist, track.title) if part) or name
        label = label.replace('\r', ' ').replace('\n', ' ')
        lines.append(f'#EXTINF:{duration},{label}')
        lines.append(name)
    target.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return target


def export_playlist(
    playlist: Path,
    output_dir: Path,
    *,
    bitrate: int = 256,
    force: bool = False,
    dry_run: bool = False,
    ffmpeg: str | None = None,
    mappings: Sequence[tuple[str, str]] = (),
    write_m3u8: bool = True,
    retag: bool = True,
) -> ExportReport:
    """Export *playlist* into *output_dir* and return a summary."""
    if bitrate < 32:
        raise PlaylistError('The AAC bitrate must be at least 32 kbps')
    entries = parse_playlist(playlist)
    if mappings:
        entries = [replace(entry, path=_apply_mapping(entry.path, mappings)) for entry in entries]
    if not entries:
        raise PlaylistError(f'Playlist is empty: {playlist}')

    tracks = resolve_tracks(entries)
    width = max(2, len(str(len(tracks))))
    names = [track_filename(track, width) for track in tracks]
    destinations = [Path(output_dir) / name for name in names]

    missing = [track for track in tracks if not track.source.is_file()]
    if missing:
        raise PlaylistError(f'Source file not found: {missing[0].source}')

    report = ExportReport(total=len(tracks))
    if not dry_run:
        Path(output_dir).mkdir(parents=True, exist_ok=True)

    pending = [
        (track, destination)
        for track, destination in zip(tracks, destinations, strict=True)
        if force or not destination.exists()
    ]
    ffmpeg_path = ffmpeg or shutil.which('ffmpeg')
    if not dry_run and any(track.lossless for track, _ in pending) and not ffmpeg_path:
        raise PlaylistError('ffmpeg is required to transcode lossless tracks; install it or pass --ffmpeg')

    written_tracks: list[ResolvedTrack] = []
    written_names: list[str] = []
    for track, destination, name in zip(tracks, destinations, names, strict=True):
        label = f'[{track.index}/{len(tracks)}] {destination.name}'
        if destination.exists() and not force:
            report.skipped += 1
            written_tracks.append(track)
            written_names.append(name)
            logger.info('%s already exists, skipping', label)
            continue
        if dry_run:
            action = 'transcode' if track.lossless else 'copy'
            logger.info('%s <- %s (%s)', label, track.source, action)
            if track.lossless:
                report.transcoded += 1
            else:
                report.copied += 1
            report.files.append(destination)
            written_tracks.append(track)
            written_names.append(name)
            continue
        temporary = _temporary_path(destination)
        try:
            if track.lossless:
                _transcode(
                    track.source,
                    temporary,
                    ffmpeg=ffmpeg_path or 'ffmpeg',
                    start=track.start,
                    end=track.end,
                    title=track.title,
                    artist=track.artist,
                    album=track.album,
                    album_artist=track.album_artist,
                    number=track.index,
                    bitrate=bitrate,
                )
                report.transcoded += 1
            else:
                shutil.copy2(track.source, temporary)
                if retag:
                    _write_track_number(temporary, track.index)
                report.copied += 1
            temporary.replace(destination)
        except (OSError, PlaylistError) as exc:
            with suppress(OSError):
                temporary.unlink(missing_ok=True)
            report.failed += 1
            report.errors.append(str(exc))
            logger.error('%s failed: %s', label, exc)
            continue
        report.bytes_written += destination.stat().st_size
        report.files.append(destination)
        written_tracks.append(track)
        written_names.append(name)
        logger.info('%s <- %s', label, track.source.name)

    if write_m3u8 and not dry_run:
        _write_m3u8(playlist, Path(output_dir), written_tracks, written_names)
    return report
