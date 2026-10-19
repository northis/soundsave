from __future__ import annotations

from pathlib import Path

from mutagen.id3 import ID3, TALB, TIT2, TPE1

from soundsave.archive import (
    build_filename,
    copy_tags,
    parse_filename,
    read_local_track,
    read_tag_data,
    sanitize_component,
)


def make_mp3(path: Path, *, bitrate_index: int, frame_length: int, frames: int = 20) -> None:
    header = bytes((0xFF, 0xFB, bitrate_index << 4, 0x00))
    path.write_bytes((header + b'\x00' * (frame_length - 4)) * frames)


def write_tags(path: Path) -> None:
    tags = ID3()
    tags.add(TIT2(encoding=3, text='Saturnine'))
    tags.add(TPE1(encoding=3, text=['The Gathering']))
    tags.add(TALB(encoding=3, text='Mandylion'))
    tags.save(path)


class TestParseFilename:
    def test_plain_name(self) -> None:
        parts = parse_filename('0001. The Gathering - Saturnine.mp3')
        assert parts is not None
        assert parts.file_id == 1
        assert parts.artist == 'The Gathering'
        assert parts.title == 'Saturnine'
        assert parts.extension == 'mp3'

    def test_title_with_dash(self) -> None:
        parts = parse_filename('0007. Artist - Part 1 - Part 2.flac')
        assert parts is not None
        assert parts.title == 'Part 1 - Part 2'
        assert parts.extension == 'flac'

    def test_cyrillic(self) -> None:
        parts = parse_filename('0042. Кино - Звезда по имени Солнце.mp3')
        assert parts is not None
        assert parts.artist == 'Кино'
        assert parts.title == 'Звезда по имени Солнце'

    def test_ignores_temporary_file(self) -> None:
        assert parse_filename('temp_download.mp3') is None

    def test_ignores_other_extensions(self) -> None:
        assert parse_filename('0001. Artist - Title.m4a') is None


class TestBuildFilename:
    def test_zero_pads_id(self) -> None:
        assert build_filename(7, 'Artist', 'Title', 'mp3') == '0007. Artist - Title.mp3'

    def test_sanitizes_components(self) -> None:
        assert build_filename(1, 'A/B', 'C:D', 'flac') == '0001. A-B - C-D.flac'


class TestSanitizeComponent:
    def test_replaces_illegal_characters(self) -> None:
        assert sanitize_component('a/b:c*?"<>|') == 'a-b-c-'

    def test_keeps_unicode_and_spaces(self) -> None:
        assert sanitize_component('  Кино - Звезда  ') == 'Кино - Звезда'


def test_build_filename_round_trip() -> None:
    name = build_filename(42, 'The Gathering', 'Saturnine', 'mp3')
    parts = parse_filename(name)
    assert parts is not None
    assert parts.file_id == 42
    assert parts.artist == 'The Gathering'
    assert parts.title == 'Saturnine'


def test_parse_filename_returns_none_for_directory(tmp_path: Path) -> None:
    assert parse_filename(tmp_path.name) is None


class TestReadLocalTrack:
    def test_reads_tags_and_bitrate(self, tmp_path: Path) -> None:
        path = tmp_path / '0001. The Gathering - Saturnine.mp3'
        make_mp3(path, bitrate_index=9, frame_length=417)
        write_tags(path)

        track = read_local_track(path)
        assert track.file_id == 1
        assert track.artist == 'The Gathering'
        assert track.title == 'Saturnine'
        assert track.album == 'Mandylion'
        assert track.quality.codec == 'mp3'
        assert track.quality.bitrate_kbps == 128
        assert track.duration_s is not None
        assert track.duration_s > 0

    def test_falls_back_to_filename_without_tags(self, tmp_path: Path) -> None:
        path = tmp_path / '0002. Artist - Title.mp3'
        make_mp3(path, bitrate_index=9, frame_length=417)

        track = read_local_track(path)
        assert track.artist == 'Artist'
        assert track.title == 'Title'


class TestCopyTags:
    def test_mp3_to_mp3(self, tmp_path: Path) -> None:
        source = tmp_path / 'source.mp3'
        destination = tmp_path / 'destination.mp3'
        make_mp3(source, bitrate_index=9, frame_length=417)
        write_tags(source)
        make_mp3(destination, bitrate_index=14, frame_length=1044)

        copy_tags(source, destination)

        tags = read_tag_data(destination)
        assert tags.title == 'Saturnine'
        assert tags.artists == ['The Gathering']
        assert tags.album == 'Mandylion'
