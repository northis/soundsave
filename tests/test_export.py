from __future__ import annotations

import struct
from pathlib import Path

import pytest

from soundsave.export import (
    FPL_MAGIC,
    PlaylistEntry,
    PlaylistError,
    ResolvedTrack,
    export_playlist,
    parse_cue,
    parse_fpl,
    parse_m3u8,
    resolve_tracks,
    safe_component,
    track_filename,
)

from .web_helpers import make_mp3


def make_fpl(path: Path, entries: list[dict[str, object]]) -> None:
    """Write a minimal v1 foobar2000 playlist for the parser tests."""
    strings = bytearray()
    offsets: dict[str, int] = {}

    def add(value: str) -> int:
        if value not in offsets:
            offsets[value] = len(strings)
            strings.extend(value.encode('utf-8') + b'\x00')
        return offsets[value]

    chunks = bytearray()
    for entry in entries:
        metadata: dict[str, str] = entry.get('metadata', {})
        file_offset = add(str(entry['path']))
        keys: list[int] = []
        for index, name in enumerate(metadata):
            keys.extend([index, add(name)])
        keys.append(0)
        keys.extend(add(value) for value in metadata.values())
        chunk = struct.pack('<7I', 0, file_offset, int(entry.get('subsong', 0)), 0, 0, 0, 0)
        chunk += struct.pack('<d', float(entry.get('duration', 0.0)))
        chunk += struct.pack('<4f', 0.0, 0.0, 0.0, 0.0)
        chunk += struct.pack('<4I', 3 + len(keys), len(metadata), 0, 0)
        chunk += struct.pack(f'<{len(keys)}I', *keys)
        chunks.extend(chunk)

    path.write_bytes(
        FPL_MAGIC + struct.pack('<I', len(strings)) + bytes(strings) + struct.pack('<I', len(entries)) + bytes(chunks),
    )


def test_parse_m3u8_reads_paths_labels_and_subsongs(tmp_path: Path) -> None:
    playlist = tmp_path / 'list.m3u8'
    playlist.write_text(
        '#EXTM3U\n#EXTINF:123,Artist - Title\nalbum.ape|subsong=2\nrelative.flac\n',
        encoding='utf-8',
    )

    entries = parse_m3u8(playlist.read_text(encoding='utf-8'), tmp_path)

    assert [entry.path for entry in entries] == [tmp_path / 'album.ape', tmp_path / 'relative.flac']
    assert entries[0].subsong == 2
    assert entries[0].artist == 'Artist'
    assert entries[0].title == 'Title'
    assert entries[0].duration == 123
    assert entries[1].subsong is None


def test_parse_fpl_reads_path_subsong_and_metadata(tmp_path: Path) -> None:
    audio = tmp_path / 'album.ape'
    audio.write_bytes(b'ape')
    playlist = tmp_path / 'list.fpl'
    make_fpl(
        playlist,
        [
            {
                'path': str(audio),
                'subsong': 3,
                'duration': 241.5,
                'metadata': {'title': 'The Song', 'artist': 'The Band', 'album': 'The Album'},
            },
        ],
    )

    entries = parse_fpl(playlist.read_bytes(), tmp_path)

    assert len(entries) == 1
    entry = entries[0]
    assert entry.path == audio
    assert entry.subsong == 3
    assert entry.duration == pytest.approx(241.5)
    assert entry.title == 'The Song'
    assert entry.artist == 'The Band'
    assert entry.album == 'The Album'


def test_parse_cue_computes_track_ranges(tmp_path: Path) -> None:
    cue = tmp_path / 'album.cue'
    cue.write_text(
        'PERFORMER "Album Artist"\n'
        'TITLE "The Album"\n'
        'FILE "album.ape" WAVE\n'
        '  TRACK 01 AUDIO\n'
        '    TITLE "First"\n'
        '    PERFORMER "Singer"\n'
        '    INDEX 01 00:00:00\n'
        '  TRACK 02 AUDIO\n'
        '    TITLE "Second"\n'
        '    PERFORMER "Singer"\n'
        '    INDEX 01 03:30:00\n',
        encoding='utf-8',
    )

    sheet = parse_cue(cue)

    assert sheet.title == 'The Album'
    assert sheet.performer == 'Album Artist'
    assert [track.title for track in sheet.tracks] == ['First', 'Second']
    assert sheet.tracks[0].start == 0
    assert sheet.tracks[0].end == 210
    assert sheet.tracks[1].end is None
    assert sheet.tracks[0].file == tmp_path / 'album.ape'


def test_resolve_tracks_expands_a_cue_for_subsongs(tmp_path: Path) -> None:
    audio = tmp_path / 'album.ape'
    audio.write_bytes(b'ape')
    cue = tmp_path / 'album.cue'
    cue.write_text(
        'FILE "album.ape" WAVE\n'
        '  TRACK 01 AUDIO\n'
        '    TITLE "First"\n'
        '    INDEX 01 00:00:00\n'
        '  TRACK 02 AUDIO\n'
        '    TITLE "Second"\n'
        '    INDEX 01 02:00:00\n',
        encoding='utf-8',
    )

    tracks = resolve_tracks(
        [
            PlaylistEntry(path=audio, subsong=0, title='First'),
            PlaylistEntry(path=audio, subsong=1, title='Second'),
        ],
    )

    assert [track.title for track in tracks] == ['First', 'Second']
    assert tracks[0].start == 0
    assert tracks[0].end == 120
    assert tracks[1].start == 120
    assert tracks[0].lossless is True


def test_safe_component_and_track_filename() -> None:
    assert safe_component('AC/DC: Live?') == 'AC_DC_ Live_'
    assert safe_component('CON') == '_CON'
    assert safe_component('   ') == 'Untitled'

    track = ResolvedTrack(
        index=7,
        source=Path('song.flac'),
        start=None,
        end=None,
        title='Title',
        artist='Artist',
        album='',
        album_artist='',
        duration=10,
        lossless=True,
    )
    assert track_filename(track, 2) == '07. Artist - Title.m4a'
    assert track_filename(track, 3) == '007. Artist - Title.m4a'


def test_export_copies_lossy_and_transcodes_lossless(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / 'source'
    source.mkdir()
    make_mp3(source / 'one.mp3', 'One', 'Alpha')
    (source / 'two.flac').write_bytes(b'not really flac')
    playlist = source / 'list.m3u8'
    playlist.write_text(
        '#EXTM3U\n#EXTINF:100,Alpha - One\none.mp3\n#EXTINF:200,Beta - Two\ntwo.flac\n',
        encoding='utf-8',
    )

    calls: list[dict[str, object]] = []

    def fake_transcode(source_path: Path, destination: Path, **kwargs: object) -> None:
        calls.append({'source': source_path, 'destination': destination, **kwargs})
        destination.write_bytes(b'transcoded')

    monkeypatch.setattr('soundsave.export._transcode', fake_transcode)
    output = tmp_path / 'out'

    report = export_playlist(playlist, output, retag=False)

    assert report.ok
    assert report.copied == 1
    assert report.transcoded == 1
    assert (output / '01. Alpha - One.mp3').read_bytes() == (source / 'one.mp3').read_bytes()
    assert (output / '02. Beta - Two.m4a').read_bytes() == b'transcoded'
    assert len(calls) == 1
    assert calls[0]['number'] == 2
    assert calls[0]['bitrate'] == 256
    assert (output / 'list.m3u8').is_file()


def test_export_transcodes_each_cue_track(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'album.ape').write_bytes(b'ape')
    (source / 'album.cue').write_text(
        'PERFORMER "Singer"\n'
        'FILE "album.ape" WAVE\n'
        '  TRACK 01 AUDIO\n'
        '    TITLE "First"\n'
        '    INDEX 01 00:00:00\n'
        '  TRACK 02 AUDIO\n'
        '    TITLE "Second"\n'
        '    INDEX 01 01:00:00\n',
        encoding='utf-8',
    )
    playlist = source / 'list.m3u8'
    playlist.write_text(
        '#EXTM3U\n#EXTINF:60,First\nalbum.ape|subsong=0\n#EXTINF:60,Second\nalbum.ape|subsong=1\n',
        encoding='utf-8',
    )

    calls: list[dict[str, object]] = []

    def fake_transcode(source_path: Path, destination: Path, **kwargs: object) -> None:
        calls.append({'source': source_path, 'destination': destination, **kwargs})
        destination.write_bytes(b'transcoded')

    monkeypatch.setattr('soundsave.export._transcode', fake_transcode)
    output = tmp_path / 'out'

    report = export_playlist(playlist, output)

    assert report.ok
    assert report.transcoded == 2
    assert [call['start'] for call in calls] == [0.0, 60.0]
    assert (output / '01. Singer - First.m4a').is_file()
    assert (output / '02. Singer - Second.m4a').is_file()


def test_export_dry_run_writes_nothing(tmp_path: Path) -> None:
    source = tmp_path / 'source'
    source.mkdir()
    make_mp3(source / 'one.mp3', 'One', 'Alpha')
    playlist = source / 'list.m3u8'
    playlist.write_text('#EXTM3U\none.mp3\n', encoding='utf-8')
    output = tmp_path / 'out'

    report = export_playlist(playlist, output, dry_run=True)

    assert report.copied == 1
    assert report.transcoded == 0
    assert not output.exists()


def test_export_rejects_a_missing_playlist(tmp_path: Path) -> None:
    with pytest.raises(PlaylistError, match='Playlist not found'):
        export_playlist(tmp_path / 'missing.fpl', tmp_path / 'out')
