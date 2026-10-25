from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from mutagen.id3 import APIC, ID3, TALB, TCON, TDRC, TIT2, TPE1, TPE2, TRCK, USLT

from soundsave.archive import TagData, read_tag_data
from soundsave.config import Config
from soundsave.domain import AudioQuality, LocalTrack, MetadataCandidate, MetadataMatch
from soundsave.tags import TagsOptions, _apply_file, _queries, run_tags
from soundsave.yandex_catalog import YandexCatalogError


def make_mp3(path: Path, *, bitrate_index: int = 9, frame_length: int = 417, frames: int = 20) -> None:
    header = bytes((0xFF, 0xFB, bitrate_index << 4, 0x00))
    path.write_bytes((header + b'\x00' * (frame_length - 4)) * frames)


def make_flac(path: Path) -> None:
    packed = (44100 << 44) | (15 << 36)
    streaminfo = (
        (4096).to_bytes(2, 'big') + (4096).to_bytes(2, 'big') + b'\x00' * 6 + packed.to_bytes(8, 'big') + b'\x00' * 16
    )
    path.write_bytes(b'fLaC' + bytes((0x80,)) + (34).to_bytes(3, 'big') + streaminfo)


def write_tags(
    path: Path,
    *,
    title: str = 'Saturnine',
    artist: str = 'The Gathering',
    album: str | None = None,
    album_artist: str | None = None,
    date: str | None = None,
    genre: str | None = None,
    track_number: str | None = None,
    lyrics: str | None = None,
    cover: bytes | None = None,
) -> None:
    tags = ID3()
    tags.add(TIT2(encoding=3, text=title))
    tags.add(TPE1(encoding=3, text=[artist]))
    if album:
        tags.add(TALB(encoding=3, text=album))
    if album_artist:
        tags.add(TPE2(encoding=3, text=album_artist))
    if date:
        tags.add(TDRC(encoding=3, text=date))
    if genre:
        tags.add(TCON(encoding=3, text=genre))
    if track_number:
        tags.add(TRCK(encoding=3, text=track_number))
    if lyrics:
        tags.add(USLT(encoding=3, text=lyrics))
    if cover:
        tags.add(APIC(encoding=3, mime='image/jpeg', type=3, desc='', data=cover))
    tags.save(path)


def make_candidate(**overrides: Any) -> MetadataCandidate:
    values: dict[str, Any] = {
        'provider': 'yandex',
        'provider_id': '123',
        'title': 'Saturnine',
        'artists': ('The Gathering',),
        'album': 'Mandylion',
        'duration_s': 300.0,
        'album_artists': ('The Gathering',),
        'year': 1995,
        'release_date': '1995-08-22T00:00:00+04:00',
        'genre': 'gothic metal',
        'url': 'https://music.yandex.ru/album/10/track/123',
        'lyrics_available': False,
    }
    values.update(overrides)
    return MetadataCandidate(**values)


class FakeCatalog:
    def __init__(
        self,
        candidate: MetadataCandidate | None,
        *,
        status: str = 'matched_playlist',
        cover: tuple[bytes, str] | None = None,
        lyrics: str | None = None,
        cover_error: bool = False,
        lyrics_error: bool = False,
    ) -> None:
        self.candidate = candidate
        self.status = status
        self.cover = cover
        self.lyrics = lyrics
        self.cover_error = cover_error
        self.lyrics_error = lyrics_error
        self.find_calls = 0
        self.cover_calls = 0
        self.lyrics_calls = 0
        self.last_search = True
        self.last_url: str | None = None

    def find(
        self,
        queries: list[object],
        *,
        url: str | None,
        search: bool,
        min_score: float,
        tolerance: float,
    ) -> MetadataMatch:
        self.find_calls += 1
        self.last_search = search
        self.last_url = url
        if self.status not in {'matched_playlist', 'matched_discogs'}:
            candidate = self.candidate if self.status == 'ambiguous' else None
            return MetadataMatch(status=self.status, candidate=candidate, score=0.5 if candidate else 0.0)
        return MetadataMatch(status=self.status, candidate=self.candidate, score=0.95)

    def has_cover(self, candidate: MetadataCandidate) -> bool:
        return self.cover is not None

    def fetch_cover(self, candidate: MetadataCandidate) -> tuple[bytes, str] | None:
        self.cover_calls += 1
        if self.cover_error:
            raise YandexCatalogError('cover failed')
        return self.cover

    def fetch_lyrics(self, candidate: MetadataCandidate) -> str | None:
        self.lyrics_calls += 1
        if self.lyrics_error:
            raise YandexCatalogError('lyrics failed')
        return self.lyrics


def make_config(tmp_path: Path) -> Config:
    return Config(
        yandex_token='token',
        zvuk_token=None,
        playlist_url=None,
        target_dir=tmp_path,
        delay=0.0,
    )


def make_options(**overrides: Any) -> TagsOptions:
    values: dict[str, Any] = {'apply': False, 'delay': 0.0}
    values.update(overrides)
    return TagsOptions(**values)


class TestQueries:
    def test_includes_filename_variant(self, tmp_path: Path) -> None:
        track = LocalTrack(
            path=tmp_path / '0001. File Artist - File Title.mp3',
            file_id=1,
            artist='Tag Artist',
            title='Tag Title',
            album=None,
            duration_s=1.0,
            quality=AudioQuality('mp3', 320),
        )

        queries = _queries(track)

        assert [(query.artist, query.title) for query in queries] == [
            ('Tag Artist', 'Tag Title'),
            ('File Artist', 'File Title'),
        ]

    def test_deduplicates_matching_variants(self, tmp_path: Path) -> None:
        track = LocalTrack(
            path=tmp_path / '0001. Same Artist - Same Title.mp3',
            file_id=1,
            artist='Same Artist',
            title='Same Title',
            album=None,
            duration_s=1.0,
            quality=AudioQuality('mp3', 320),
        )

        assert len(_queries(track)) == 1


class TestRunTags:
    def test_report_only_writes_nothing(self, tmp_path: Path) -> None:
        path = tmp_path / '0001. The Gathering - Saturnine.mp3'
        make_mp3(path)
        write_tags(path)
        original = path.read_bytes()
        catalog = FakeCatalog(make_candidate(lyrics_available=True), cover=(b'\xff\xd8\xffcover', 'image/jpeg'))

        rows = run_tags(make_config(tmp_path), catalog, make_options())

        assert len(rows) == 1
        assert rows[0].status == 'matched_playlist'
        assert rows[0].applied is False
        assert rows[0].cover == 'missing'
        assert rows[0].lyrics == 'missing'
        assert rows[0].missing is not None
        assert 'album' in rows[0].missing
        assert path.read_bytes() == original
        assert catalog.cover_calls == 0
        assert catalog.lyrics_calls == 0

    def test_skip_complete_does_not_query(self, tmp_path: Path) -> None:
        path = tmp_path / '0001. The Gathering - Saturnine.mp3'
        make_mp3(path)
        write_tags(
            path,
            album='Mandylion',
            album_artist='The Gathering',
            date='1995-08-22',
            genre='gothic metal',
            lyrics='line',
            cover=b'cover',
        )
        catalog = FakeCatalog(make_candidate())

        rows = run_tags(make_config(tmp_path), catalog, make_options(skip_complete=True))

        assert rows[0].status == 'ok'
        assert rows[0].applied is False
        assert rows[0].cover == 'present'
        assert rows[0].lyrics == 'present'
        assert catalog.find_calls == 0

    def test_skip_complete_ignores_missing_lyrics(self, tmp_path: Path) -> None:
        path = tmp_path / '0001. The Gathering - Saturnine.mp3'
        make_mp3(path)
        write_tags(
            path,
            album='Mandylion',
            album_artist='The Gathering',
            date='1995-08-22',
            genre='gothic metal',
            cover=b'cover',
        )
        catalog = FakeCatalog(make_candidate(lyrics_available=True))

        rows = run_tags(make_config(tmp_path), catalog, make_options(skip_complete=True))

        assert rows[0].status == 'ok'
        assert rows[0].lyrics == 'missing'
        assert catalog.find_calls == 0

    def test_skip_complete_queries_when_gap(self, tmp_path: Path) -> None:
        path = tmp_path / '0001. The Gathering - Saturnine.mp3'
        make_mp3(path)
        write_tags(
            path,
            album='Mandylion',
            album_artist='The Gathering',
            date='1995-08-22',
            lyrics='line',
            cover=b'cover',
        )
        catalog = FakeCatalog(make_candidate())

        rows = run_tags(make_config(tmp_path), catalog, make_options(skip_complete=True))

        assert rows[0].status == 'matched_playlist'
        assert catalog.find_calls == 1

    def test_apply_fills_empty_tags(self, tmp_path: Path) -> None:
        path = tmp_path / '0001. The Gathering - Saturnine.mp3'
        make_mp3(path)
        catalog = FakeCatalog(make_candidate())

        rows = run_tags(make_config(tmp_path), catalog, make_options(apply=True, lyrics=False))

        assert rows[0].applied is True
        data = read_tag_data(path)
        assert data.title == 'Saturnine'
        assert data.artists == ['The Gathering']
        assert data.album == 'Mandylion'
        assert data.album_artist == 'The Gathering'
        assert data.date == '1995-08-22'
        assert data.genre == 'gothic metal'
        assert data.url == 'https://music.yandex.ru/album/10/track/123'

    def test_matched_discogs_fills_fields(self, tmp_path: Path) -> None:
        path = tmp_path / '0001. The Gathering - Saturnine.mp3'
        make_mp3(path)
        write_tags(path)
        catalog = FakeCatalog(
            make_candidate(
                provider='discogs',
                url=None,
                lyrics_available=True,
            ),
            status='matched_discogs',
            cover=(b'\xff\xd8\xffdiscogs-cover', 'image/jpeg'),
            lyrics='discogs lyrics',
        )

        rows = run_tags(make_config(tmp_path), catalog, make_options(apply=True))

        assert rows[0].status == 'matched_discogs'
        assert rows[0].source == 'discogs'
        assert rows[0].applied is True
        assert rows[0].cover == 'added'
        assert rows[0].lyrics == 'added'
        data = read_tag_data(path)
        assert data.album == 'Mandylion'
        assert data.genre == 'gothic metal'
        assert data.cover == b'\xff\xd8\xffdiscogs-cover'
        assert data.lyrics == 'discogs lyrics'
        assert data.url is None

    def test_apply_fills_only_missing(self, tmp_path: Path) -> None:
        path = tmp_path / '0001. The Gathering - Saturnine.mp3'
        make_mp3(path)
        write_tags(path, track_number='0007/1256')
        catalog = FakeCatalog(
            make_candidate(
                title='Another title',
                artists=('Another artist',),
                lyrics_available=True,
            ),
            cover=(b'\xff\xd8\xffcover', 'image/jpeg'),
            lyrics='line 1',
        )

        rows = run_tags(make_config(tmp_path), catalog, make_options(apply=True))

        assert len(rows) == 1
        assert rows[0].applied is True
        assert rows[0].cover == 'added'
        assert rows[0].lyrics == 'added'
        assert 'title' in (rows[0].conflicts or '')
        assert 'artists' in (rows[0].conflicts or '')
        data = read_tag_data(path)
        assert data.title == 'Saturnine'
        assert data.artists == ['The Gathering']
        assert data.album == 'Mandylion'
        assert data.album_artist == 'The Gathering'
        assert data.date == '1995-08-22'
        assert data.genre == 'gothic metal'
        assert data.url == 'https://music.yandex.ru/album/10/track/123'
        assert data.lyrics == 'line 1'
        assert data.cover == b'\xff\xd8\xffcover'
        assert data.track_number == '0007/1256'

    def test_preserves_existing_cover(self, tmp_path: Path) -> None:
        path = tmp_path / '0001. The Gathering - Saturnine.mp3'
        make_mp3(path)
        write_tags(path, cover=b'old cover')
        catalog = FakeCatalog(make_candidate(), cover=(b'new cover', 'image/jpeg'))

        rows = run_tags(make_config(tmp_path), catalog, make_options(apply=True))

        assert rows[0].cover == 'present'
        assert catalog.cover_calls == 0
        assert read_tag_data(path).cover == b'old cover'

    def test_preserves_existing_lyrics(self, tmp_path: Path) -> None:
        path = tmp_path / '0001. The Gathering - Saturnine.mp3'
        make_mp3(path)
        write_tags(path, lyrics='old lyrics')
        catalog = FakeCatalog(make_candidate(lyrics_available=True), lyrics='new lyrics')

        rows = run_tags(make_config(tmp_path), catalog, make_options(apply=True))

        assert rows[0].lyrics == 'present'
        assert catalog.lyrics_calls == 0
        assert read_tag_data(path).lyrics == 'old lyrics'

    def test_no_lyrics_skips_fetch(self, tmp_path: Path) -> None:
        path = tmp_path / '0001. The Gathering - Saturnine.mp3'
        make_mp3(path)
        write_tags(path)
        catalog = FakeCatalog(make_candidate(lyrics_available=True), lyrics='new lyrics')

        rows = run_tags(make_config(tmp_path), catalog, make_options(apply=True, lyrics=False))

        assert rows[0].lyrics == 'skipped'
        assert catalog.lyrics_calls == 0
        assert read_tag_data(path).lyrics is None

    def test_lyrics_error_does_not_block_other_fields(self, tmp_path: Path) -> None:
        path = tmp_path / '0001. The Gathering - Saturnine.mp3'
        make_mp3(path)
        write_tags(path)
        catalog = FakeCatalog(make_candidate(lyrics_available=True), lyrics_error=True)

        rows = run_tags(make_config(tmp_path), catalog, make_options(apply=True))

        assert rows[0].applied is True
        assert rows[0].lyrics == 'error'
        assert 'lyrics failed' in (rows[0].error or '')
        assert read_tag_data(path).album == 'Mandylion'

    def test_cover_error_does_not_block_other_fields(self, tmp_path: Path) -> None:
        path = tmp_path / '0001. The Gathering - Saturnine.mp3'
        make_mp3(path)
        write_tags(path)
        catalog = FakeCatalog(make_candidate(), cover=(b'cover', 'image/jpeg'), cover_error=True)

        rows = run_tags(make_config(tmp_path), catalog, make_options(apply=True))

        assert rows[0].applied is True
        assert rows[0].cover == 'error'
        assert 'cover failed' in (rows[0].error or '')
        assert read_tag_data(path).album == 'Mandylion'

    def test_no_match_does_not_write(self, tmp_path: Path) -> None:
        path = tmp_path / '0001. The Gathering - Saturnine.mp3'
        make_mp3(path)
        write_tags(path)
        original = path.read_bytes()
        catalog = FakeCatalog(None, status='no_match')

        rows = run_tags(make_config(tmp_path), catalog, make_options(apply=True))

        assert rows[0].status == 'no_match'
        assert rows[0].applied is False
        assert path.read_bytes() == original

    def test_ambiguous_does_not_write(self, tmp_path: Path) -> None:
        path = tmp_path / '0001. The Gathering - Saturnine.mp3'
        make_mp3(path)
        write_tags(path)
        original = path.read_bytes()
        catalog = FakeCatalog(make_candidate(), status='ambiguous')

        rows = run_tags(make_config(tmp_path), catalog, make_options(apply=True))

        assert rows[0].status == 'ambiguous'
        assert rows[0].applied is False
        assert path.read_bytes() == original

    def test_limit(self, tmp_path: Path) -> None:
        for file_id in (1, 2):
            path = tmp_path / f'{file_id:04d}. The Gathering - Saturnine.mp3'
            make_mp3(path)
            write_tags(path)
        catalog = FakeCatalog(make_candidate())

        rows = run_tags(make_config(tmp_path), catalog, make_options(limit=1))

        assert len(rows) == 1
        assert catalog.find_calls == 1

    def test_reports_are_written(self, tmp_path: Path) -> None:
        path = tmp_path / '0001. The Gathering - Saturnine.mp3'
        make_mp3(path)
        write_tags(path)
        catalog = FakeCatalog(make_candidate())
        csv_path = tmp_path / 'tags.csv'
        json_path = tmp_path / 'tags.json'

        run_tags(
            make_config(tmp_path),
            catalog,
            make_options(report_csv=csv_path, report_json=json_path),
        )

        assert csv_path.exists()
        assert json_path.exists()
        payload = json.loads(json_path.read_text(encoding='utf-8'))
        assert payload[0]['status'] == 'matched_playlist'
        assert payload[0]['missing'] is not None

    def test_flac_apply(self, tmp_path: Path) -> None:
        path = tmp_path / '0001. The Gathering - Saturnine.flac'
        make_flac(path)
        catalog = FakeCatalog(
            make_candidate(lyrics_available=True),
            cover=(b'\xff\xd8\xffcover', 'image/jpeg'),
            lyrics='line 1',
        )

        rows = run_tags(make_config(tmp_path), catalog, make_options(apply=True))

        assert rows[0].applied is True
        data = read_tag_data(path)
        assert data.title == 'Saturnine'
        assert data.artists == ['The Gathering']
        assert data.album == 'Mandylion'
        assert data.lyrics == 'line 1'
        assert data.cover == b'\xff\xd8\xffcover'

    def test_apply_file_keeps_original_on_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        path = tmp_path / '0001. The Gathering - Saturnine.mp3'
        make_mp3(path)
        write_tags(path)
        original = path.read_bytes()

        def fail(*args: object, **kwargs: object) -> bool:
            raise RuntimeError('write failed')

        monkeypatch.setattr('soundsave.tags.update_tag_data', fail)

        with pytest.raises(RuntimeError, match='write failed'):
            _apply_file(path, TagData(album='Mandylion'), workspace=tmp_path / '.soundsave')

        assert path.read_bytes() == original
        assert list((tmp_path / '.soundsave' / 'tmp').iterdir()) == []
