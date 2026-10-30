from __future__ import annotations

import struct
from pathlib import Path
from urllib.parse import quote_from_bytes

import pytest
from mutagen import File as MutagenFile
from mutagen.flac import FLAC
from mutagen.id3 import ID3, TDRC, TIT2, TPE1

from soundsave.export import (
    FPL_MAGIC,
    PlaylistEntry,
    PlaylistError,
    ResolvedTrack,
    _resolve_path,
    _transcode,
    export_playlist,
    is_lossless,
    parse_cue,
    parse_fpl,
    parse_m3u8,
    parse_playlist,
    resolve_tracks,
    safe_component,
    track_filename,
)

from .web_helpers import make_mp3


def make_fpl(path: Path, entries: list[dict[str, object]], *, encoding: str = 'utf-8') -> None:
    """Write a minimal v1 foobar2000 playlist for the parser tests."""
    strings = bytearray()
    offsets: dict[str, int] = {}

    def add(value: str) -> int:
        if value not in offsets:
            offsets[value] = len(strings)
            strings.extend(value.encode(encoding) + b'\x00')
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

    report = export_playlist(playlist, output, retag=False, ffmpeg='ffmpeg')

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

    report = export_playlist(playlist, output, ffmpeg='ffmpeg')

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


def test_export_requires_ffmpeg_for_lossless_tracks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'two.flac').write_bytes(b'not really flac')
    playlist = source / 'list.m3u8'
    playlist.write_text('#EXTM3U\ntwo.flac\n', encoding='utf-8')
    monkeypatch.setattr('soundsave.export.shutil.which', lambda _: None)

    with pytest.raises(PlaylistError, match='ffmpeg is required'):
        export_playlist(playlist, tmp_path / 'out')


def test_export_rejects_a_missing_playlist(tmp_path: Path) -> None:
    with pytest.raises(PlaylistError, match='Playlist not found'):
        export_playlist(tmp_path / 'missing.fpl', tmp_path / 'out')


def test_parse_playlist_decodes_cp1251_and_utf16(tmp_path: Path) -> None:
    playlist = tmp_path / 'list.m3u8'
    content = '#EXTM3U\n#EXTINF:100,Исполнитель - Название\nтрек.mp3\n'

    playlist.write_bytes(content.encode('cp1251'))
    entries = parse_playlist(playlist)
    assert entries[0].artist == 'Исполнитель'
    assert entries[0].title == 'Название'
    assert entries[0].path == tmp_path / 'трек.mp3'

    playlist.write_bytes(content.encode('utf-16'))
    entries = parse_playlist(playlist)
    assert entries[0].artist == 'Исполнитель'
    assert entries[0].title == 'Название'


def test_resolve_path_decodes_percent_encoded_cp1251(tmp_path: Path) -> None:
    target = tmp_path / 'трек.mp3'
    raw = str(target)
    encoded = quote_from_bytes(raw.removeprefix('/').encode('cp1251'))

    assert _resolve_path(f'file:///{encoded}', tmp_path) == target


def test_parse_fpl_decodes_cp1251(tmp_path: Path) -> None:
    audio = tmp_path / 'альбом.ape'
    audio.write_bytes(b'ape')
    playlist = tmp_path / 'list.fpl'
    make_fpl(
        playlist,
        [
            {
                'path': str(audio),
                'metadata': {
                    'title': 'Название',
                    'artist': 'Исполнитель',
                    'album': 'Альбом',
                    'date': '1995',
                },
            },
        ],
        encoding='cp1251',
    )

    entries = parse_fpl(playlist.read_bytes(), tmp_path)

    assert entries[0].path == audio
    assert entries[0].title == 'Название'
    assert entries[0].artist == 'Исполнитель'
    assert entries[0].album == 'Альбом'
    assert entries[0].date == '1995'


def test_parse_cue_decodes_cp1251_and_reads_rem_date(tmp_path: Path) -> None:
    cue = tmp_path / 'album.cue'
    cue.write_bytes(
        'REM DATE 1995\n'
        'PERFORMER "Исполнитель"\n'
        'TITLE "Альбом"\n'
        'FILE "альбом.ape" WAVE\n'
        '  TRACK 01 AUDIO\n'
        '    TITLE "Песня"\n'
        '    INDEX 01 00:00:00\n'.encode('cp1251'),
    )

    sheet = parse_cue(cue)

    assert sheet.title == 'Альбом'
    assert sheet.performer == 'Исполнитель'
    assert sheet.date == '1995'
    assert sheet.tracks[0].title == 'Песня'
    assert sheet.tracks[0].file == tmp_path / 'альбом.ape'


def test_resolve_tracks_reads_cue_date(tmp_path: Path) -> None:
    audio = tmp_path / 'album.ape'
    audio.write_bytes(b'ape')
    (tmp_path / 'album.cue').write_text(
        'REM DATE 1995\nFILE "album.ape" WAVE\n  TRACK 01 AUDIO\n    TITLE "First"\n    INDEX 01 00:00:00\n',
        encoding='utf-8',
    )

    tracks = resolve_tracks([PlaylistEntry(path=audio, subsong=0)])

    assert tracks[0].date == '1995'


def test_resolve_tracks_reads_date_from_cue_source_tags(tmp_path: Path) -> None:
    audio = tmp_path / 'album.flac'
    packed = (44100 << 44) | (15 << 36)
    streaminfo = (
        (4096).to_bytes(2, 'big') + (4096).to_bytes(2, 'big') + b'\x00' * 6 + packed.to_bytes(8, 'big') + b'\x00' * 16
    )
    audio.write_bytes(b'fLaC' + bytes((0x80,)) + (34).to_bytes(3, 'big') + streaminfo)
    flac = FLAC(audio)
    flac['date'] = '1998'
    flac.save()
    (tmp_path / 'album.cue').write_text(
        'FILE "album.flac" WAVE\n  TRACK 01 AUDIO\n    TITLE "First"\n    INDEX 01 00:00:00\n',
        encoding='utf-8',
    )

    tracks = resolve_tracks([PlaylistEntry(path=audio, subsong=0)])

    assert tracks[0].date == '1998'


def test_export_repairs_mojibake_in_names_tags_and_playlist(tmp_path: Path) -> None:
    source = tmp_path / 'source'
    source.mkdir()
    audio = source / 'one.mp3'
    make_mp3(audio, 'placeholder', 'placeholder')
    tags = ID3(audio)
    tags.setall('TIT2', [TIT2(encoding=0, text='Ìîñêâà')])
    tags.setall('TPE1', [TPE1(encoding=0, text='Èñïîëíèòåëü')])
    tags.setall('TDRC', [TDRC(encoding=3, text='1995')])
    tags.save(audio)
    playlist = source / 'list.m3u8'
    playlist.write_bytes('#EXTM3U\none.mp3\n'.encode('cp1251'))
    output = tmp_path / 'out'

    report = export_playlist(playlist, output)

    assert report.ok
    exported = output / '01. Исполнитель - Москва.mp3'
    assert exported.is_file()
    copied = MutagenFile(exported, easy=True)
    assert copied is not None
    assert str(copied['title'][0]) == 'Москва'
    assert str(copied['artist'][0]) == 'Исполнитель'
    assert str(copied['date'][0]) == '1995'
    playlist_text = (output / 'list.m3u8').read_text(encoding='utf-8')
    assert ',Исполнитель - Москва' in playlist_text
    assert '01. Исполнитель - Москва.mp3' in playlist_text


def test_transcode_passes_release_date(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / 'album.flac'
    source.write_bytes(b'flac')
    destination = tmp_path / 'out.m4a'
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> None:
        calls.append(command)
        destination.write_bytes(b'out')

    monkeypatch.setattr('soundsave.export.subprocess.run', fake_run)

    _transcode(
        source,
        destination,
        ffmpeg='ffmpeg',
        start=None,
        end=None,
        title='Title',
        artist='Artist',
        album='Album',
        album_artist='Artist',
        date='1995-08-22',
        number=1,
        bitrate=256,
    )

    assert 'date=1995-08-22' in calls[0]
    assert calls[0][calls[0].index('-map_metadata') + 1] == '0'


def test_is_lossless_checks_the_mp4_codec(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class AlacInfo:
        codec = ''
        codec_description = 'ALAC'

    class AlacAudio:
        info = AlacInfo()

    monkeypatch.setattr('soundsave.export.MutagenFile', lambda _: AlacAudio())

    assert is_lossless(tmp_path / 'track.m4a') is True
    assert is_lossless(tmp_path / 'track.mp4') is True


def test_is_lossless_treats_aac_m4a_as_lossy() -> None:
    assert is_lossless(Path(__file__).with_name('fixtures') / 'silence.m4a') is False
