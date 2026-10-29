from __future__ import annotations

from pathlib import Path

import pytest
from mutagen.id3 import APIC, ID3, PRIV, TALB, TIT2, TPE1, TXXX, USLT
from mutagen.mp4 import MP4

from soundsave.archive import (
    ArchiveError,
    TagData,
    build_filename,
    copy_tags,
    get_image_type,
    parse_filename,
    read_local_track,
    read_tag_data,
    sanitize_component,
    update_tag_data,
)

DATA = Path(__file__).with_name('fixtures')


def make_m4a(path: Path) -> None:
    path.write_bytes((DATA / 'silence.m4a').read_bytes())


def make_mp3(path: Path, *, bitrate_index: int, frame_length: int, frames: int = 20) -> None:
    header = bytes((0xFF, 0xFB, bitrate_index << 4, 0x00))
    path.write_bytes((header + b'\x00' * (frame_length - 4)) * frames)


def make_flac(path: Path) -> None:
    packed = (44100 << 44) | (15 << 36)
    streaminfo = (
        (4096).to_bytes(2, 'big') + (4096).to_bytes(2, 'big') + b'\x00' * 6 + packed.to_bytes(8, 'big') + b'\x00' * 16
    )
    path.write_bytes(b'fLaC' + bytes((0x80,)) + (34).to_bytes(3, 'big') + streaminfo)


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
        assert parse_filename('0001. Artist - Title.wav') is None

    def test_m4a_extension(self) -> None:
        parts = parse_filename('0001. Artist - Title.m4a')
        assert parts is not None
        assert parts.file_id == 1
        assert parts.extension == 'm4a'


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

    def test_reads_m4a_fallback(self, tmp_path: Path) -> None:
        path = tmp_path / '0003. Artist - Title.m4a'
        make_m4a(path)

        track = read_local_track(path)

        assert track.file_id == 3
        assert track.artist == 'Artist'
        assert track.title == 'Title'
        assert track.quality.codec == 'aac'


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


class TestGetImageType:
    def test_jpeg(self) -> None:
        assert get_image_type(b'\xff\xd8\xffdata') == 'image/jpeg'

    def test_png(self) -> None:
        assert get_image_type(b'\x89PNG\r\n\x1a\ndata') == 'image/png'

    def test_default(self) -> None:
        assert get_image_type(b'unknown') == 'image/jpeg'


class TestUpdateTagData:
    def test_fills_only_empty_fields(self, tmp_path: Path) -> None:
        path = tmp_path / '0001. The Gathering - Saturnine.mp3'
        make_mp3(path, bitrate_index=9, frame_length=417)
        tags = ID3()
        tags.add(TIT2(encoding=3, text='Saturnine'))
        tags.add(TPE1(encoding=3, text=['The Gathering']))
        tags.save(path)

        changed = update_tag_data(
            path,
            TagData(title='Another title', artists=['Another artist'], album='Mandylion'),
        )

        assert changed
        data = read_tag_data(path)
        assert data.title == 'Saturnine'
        assert data.artists == ['The Gathering']
        assert data.album == 'Mandylion'

    def test_returns_false_when_nothing_to_fill(self, tmp_path: Path) -> None:
        path = tmp_path / '0001. The Gathering - Saturnine.mp3'
        make_mp3(path, bitrate_index=9, frame_length=417)
        tags = ID3()
        tags.add(TIT2(encoding=3, text='Saturnine'))
        tags.save(path)

        assert not update_tag_data(path, TagData(title='Saturnine'))

    def test_preserves_unknown_frames(self, tmp_path: Path) -> None:
        path = tmp_path / '0001. The Gathering - Saturnine.mp3'
        make_mp3(path, bitrate_index=9, frame_length=417)
        tags = ID3()
        tags.add(TXXX(encoding=3, desc='foo', text='bar'))
        tags.add(PRIV(owner='owner', data=b'data'))
        tags.save(path)

        assert update_tag_data(path, TagData(album='Mandylion'))

        updated = ID3(path)
        assert updated.getall('TXXX')[0].text == ['bar']
        assert updated.getall('PRIV')[0].data == b'data'

    def test_adds_front_cover_only_when_missing(self, tmp_path: Path) -> None:
        path = tmp_path / '0001. The Gathering - Saturnine.mp3'
        make_mp3(path, bitrate_index=9, frame_length=417)
        cover = b'\xff\xd8\xfffake-cover'

        assert update_tag_data(path, TagData(cover=cover, cover_mime='image/jpeg'))
        data = read_tag_data(path)
        assert data.cover == cover
        assert data.cover_type == 3

        assert not update_tag_data(path, TagData(cover=b'other', cover_mime='image/jpeg'))
        assert read_tag_data(path).cover == cover

    def test_preserves_back_cover(self, tmp_path: Path) -> None:
        path = tmp_path / '0001. The Gathering - Saturnine.mp3'
        make_mp3(path, bitrate_index=9, frame_length=417)
        tags = ID3()
        tags.add(APIC(encoding=3, mime='image/jpeg', type=4, desc='back', data=b'back'))
        tags.save(path)

        assert update_tag_data(path, TagData(cover=b'front', cover_mime='image/jpeg'))

        pictures = ID3(path).getall('APIC')
        assert len(pictures) == 2
        assert {picture.type for picture in pictures} == {3, 4}
        assert read_tag_data(path).cover_type == 3

    def test_detects_front_cover_after_back_cover(self, tmp_path: Path) -> None:
        path = tmp_path / '0001. The Gathering - Saturnine.mp3'
        make_mp3(path, bitrate_index=9, frame_length=417)
        tags = ID3()
        tags.add(APIC(encoding=3, mime='image/jpeg', type=4, desc='back', data=b'back'))
        tags.add(APIC(encoding=3, mime='image/jpeg', type=3, desc='front', data=b'front'))
        tags.save(path)

        assert not update_tag_data(path, TagData(cover=b'new', cover_mime='image/jpeg'))
        assert read_tag_data(path).cover == b'front'

    def test_adds_lyrics_only_when_missing(self, tmp_path: Path) -> None:
        path = tmp_path / '0001. The Gathering - Saturnine.mp3'
        make_mp3(path, bitrate_index=9, frame_length=417)

        assert update_tag_data(path, TagData(lyrics='first line'))
        assert read_tag_data(path).lyrics == 'first line'

        assert not update_tag_data(path, TagData(lyrics='other line'))
        assert read_tag_data(path).lyrics == 'first line'

    def test_keeps_non_empty_second_lyrics_frame(self, tmp_path: Path) -> None:
        path = tmp_path / '0001. The Gathering - Saturnine.mp3'
        make_mp3(path, bitrate_index=9, frame_length=417)
        tags = ID3()
        tags.add(USLT(encoding=3, lang='eng', desc='empty', text=''))
        tags.add(USLT(encoding=3, lang='eng', desc='full', text='existing'))
        tags.save(path)

        assert not update_tag_data(path, TagData(lyrics='new'))
        assert read_tag_data(path).lyrics == 'existing'
        assert {str(frame.text) for frame in ID3(path).getall('USLT')} == {'', 'existing'}

    def test_flac(self, tmp_path: Path) -> None:
        path = tmp_path / '0001. The Gathering - Saturnine.flac'
        make_flac(path)
        cover = b'\xff\xd8\xffcover'

        changed = update_tag_data(
            path,
            TagData(
                title='Saturnine',
                artists=['The Gathering'],
                album='Mandylion',
                lyrics='text',
                cover=cover,
                cover_mime='image/jpeg',
                url='https://music.yandex.ru/album/10/track/123',
            ),
        )

        assert changed
        data = read_tag_data(path)
        assert data.title == 'Saturnine'
        assert data.artists == ['The Gathering']
        assert data.album == 'Mandylion'
        assert data.lyrics == 'text'
        assert data.cover == cover
        assert data.cover_type == 3
        assert data.url == 'https://music.yandex.ru/album/10/track/123'
        assert not update_tag_data(path, TagData(title='Another title'))

    def test_m4a_fills_only_empty_fields(self, tmp_path: Path) -> None:
        path = tmp_path / '0001. The Gathering - Saturnine.m4a'
        make_m4a(path)
        cover = b'\xff\xd8\xffcover'
        url = 'https://music.yandex.ru/album/10/track/123'

        changed = update_tag_data(
            path,
            TagData(
                title='Saturnine',
                artists=['The Gathering'],
                album='Mandylion',
                album_artist='The Gathering',
                date='1995-08-22',
                genre='gothic metal',
                lyrics='text',
                cover=cover,
                cover_mime='image/jpeg',
                url=url,
            ),
        )

        assert changed
        data = read_tag_data(path)
        assert data.title == 'Saturnine'
        assert data.artists == ['The Gathering']
        assert data.album == 'Mandylion'
        assert data.album_artist == 'The Gathering'
        assert data.date == '1995-08-22'
        assert data.genre == 'gothic metal'
        assert data.lyrics == 'text'
        assert data.cover == cover
        assert data.cover_type == 3
        assert data.url == url
        assert MP4(path).tags['\xa9too'] == ['Lavf63.1.102']

        assert not update_tag_data(
            path,
            TagData(
                title='Another title',
                artists=['Another artist'],
                cover=b'other',
                cover_mime='image/jpeg',
                url='https://music.yandex.ru/album/10/track/999',
            ),
        )
        data = read_tag_data(path)
        assert data.title == 'Saturnine'
        assert data.artists == ['The Gathering']
        assert data.cover == cover
        assert data.url == url

    def test_m4a_preserves_existing_cover(self, tmp_path: Path) -> None:
        path = tmp_path / '0001. The Gathering - Saturnine.m4a'
        make_m4a(path)
        update_tag_data(path, TagData(cover=b'existing', cover_mime='image/jpeg'))

        assert not update_tag_data(path, TagData(cover=b'replacement', cover_mime='image/jpeg'))
        assert read_tag_data(path).cover == b'existing'

    def test_rejects_unsupported_format(self, tmp_path: Path) -> None:
        with pytest.raises(ArchiveError, match='Unsupported audio format'):
            update_tag_data(tmp_path / '0001. Artist - Title.wav', TagData())

    def test_rejects_unsupported_cover_mode(self, tmp_path: Path) -> None:
        path = tmp_path / '0001. The Gathering - Saturnine.mp3'
        make_mp3(path, bitrate_index=9, frame_length=417)

        with pytest.raises(ValueError, match='Unsupported cover mode'):
            update_tag_data(path, TagData(), cover_mode='replace')
