from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import yandex_music_organizer_api as organizer
from yandex_music import DownloadInfo


def make_track(
    track_id: int = 1,
    title: str = 'Title',
    artist: str = 'Artist',
) -> Any:
    return SimpleNamespace(
        id=track_id,
        title=title,
        version=None,
        artists=[SimpleNamespace(name=artist)],
        albums=[],
        lyrics_info=None,
        cover_uri=None,
    )


def make_download_info(codec: str, bitrate: int) -> DownloadInfo:
    return DownloadInfo(
        codec=codec,
        bitrate_in_kbps=bitrate,
        gain=False,
        preview=False,
        download_info_url='https://example.invalid/info.xml',
        direct=False,
    )


class TestParsePlaylistUrl:
    def test_plain_url(self) -> None:
        assert organizer.parse_playlist_url('https://music.yandex.ru/users/alice/playlists/42') == ('alice', 42)

    def test_url_with_trailing_path(self) -> None:
        assert organizer.parse_playlist_url('https://music.yandex.ru/users/alice/playlists/42?foo=bar') == ('alice', 42)

    def test_invalid_url(self) -> None:
        with pytest.raises(ValueError, match='Unsupported playlist url'):
            _ = organizer.parse_playlist_url('https://example.com/playlist/42')


class TestSanitizeComponent:
    def test_replaces_illegal_characters(self) -> None:
        assert organizer.sanitize_component('a/b:c*?"<>|') == 'a-b-c-'

    def test_keeps_unicode_and_spaces(self) -> None:
        assert organizer.sanitize_component('  Кино - Звезда  ') == 'Кино - Звезда'


class TestFilenameParsing:
    @pytest.mark.parametrize(
        ('filename', 'expected'),
        [
            ('0001. The Gathering - Saturnine.mp3', ('0001', 'The Gathering', 'Saturnine')),
            ('0042. Кино - Звезда по имени Солнце.mp3', ('0042', 'Кино', 'Звезда по имени Солнце')),
            ('0007. Artist - Part 1 - Part 2.mp3', ('0007', 'Artist', 'Part 1 - Part 2')),
        ],
    )
    def test_matches_organizer_filenames(self, filename: str, expected: tuple[str, str, str]) -> None:
        match = organizer.FILENAME_RE.match(filename)
        assert match is not None
        assert match.groups() == expected

    def test_ignores_temporary_file(self) -> None:
        assert organizer.FILENAME_RE.match('temp_download.mp3') is None


class TestLocalCollection:
    def test_highest_id(self, tmp_path: Path) -> None:
        _ = (tmp_path / '0001. A - B.mp3').write_bytes(b'')
        _ = (tmp_path / '0042. C - D.mp3').write_bytes(b'')
        _ = (tmp_path / 'temp_download.mp3').write_bytes(b'')
        _ = (tmp_path / 'notes.txt').write_text('ignored')

        assert organizer.get_highest_local_id(tmp_path) == 42

    def test_missing_directory(self, tmp_path: Path) -> None:
        assert organizer.get_highest_local_id(tmp_path / 'missing') == 0


class TestTrackIdentifier:
    def test_uses_first_artist_and_lowercase(self) -> None:
        track = make_track(title='Song', artist='Some Artist')
        assert organizer.build_track_identifier(track) == 'some artist-song'

    def test_sanitizes_illegal_characters(self) -> None:
        track = make_track(title='AC/DC: Live', artist='A|B')
        assert organizer.build_track_identifier(track) == 'a-b-ac-dc- live'

    def test_empty_artists(self) -> None:
        track = make_track()
        track.artists = []
        assert organizer.build_track_identifier(track) == '-title'


class TestDownloadInfoSelection:
    def test_prefers_mp3_over_other_codecs(self) -> None:
        infos = [
            make_download_info('aac', 320),
            make_download_info('mp3', 192),
            make_download_info('mp3', 320),
        ]
        assert organizer.choose_best_download_info(infos).bitrate_in_kbps == 320

    def test_falls_back_to_any_codec_without_mp3(self) -> None:
        infos = [make_download_info('aac', 192), make_download_info('aac', 256)]
        assert organizer.choose_best_download_info(infos).bitrate_in_kbps == 256


class TestFullTitle:
    def test_appends_version(self) -> None:
        payload: Any = {'title': 'Song', 'version': 'Remix'}
        assert organizer.full_title(payload) == 'Song (Remix)'

    def test_without_version(self) -> None:
        payload: Any = {'title': 'Song', 'version': None}
        assert organizer.full_title(payload) == 'Song'

    def test_missing_title(self) -> None:
        payload: Any = {'title': None, 'version': None}
        assert organizer.full_title(payload) == ''


class FakeTrackShort:
    def __init__(self, track: Any) -> None:
        self._track = track

    def fetch_track(self) -> Any:
        return self._track


class FakePlaylist:
    def __init__(self, tracks: list[FakeTrackShort]) -> None:
        self.title = 'Test playlist'
        self.tracks = tracks


class FakeClient:
    playlist: FakePlaylist

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    def init(self) -> FakeClient:
        return self

    def users_playlists(self, kind: int, owner: str) -> FakePlaylist:
        return self.playlist


@pytest.fixture
def organizer_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(
        organizer,
        'load_config',
        lambda: {
            'API_KEY': 'test-token',
            'PLAYLIST_URL': 'https://music.yandex.ru/users/alice/playlists/42',
            'TARGET_DIR': str(tmp_path),
            'MAX_DOWNLOADS': '20',
        },
    )
    monkeypatch.setattr(organizer, 'Client', FakeClient)

    def fake_sleep(_: float) -> None:
        pass

    monkeypatch.setattr(organizer.time, 'sleep', fake_sleep)
    return tmp_path


def setup_playlist(monkeypatch: pytest.MonkeyPatch, tracks: list[Any]) -> None:
    FakeClient.playlist = FakePlaylist([FakeTrackShort(track) for track in tracks])
    monkeypatch.setattr(organizer, 'Client', FakeClient)


class TestMain:
    def test_dry_run_does_not_download(
        self,
        organizer_env: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        caplog.set_level(logging.INFO, logger=organizer.logger.name)
        existing = make_track(track_id=3, title='Existing', artist='Old')
        _ = (organizer_env / '0001. Old - Existing.mp3').write_bytes(b'')
        setup_playlist(monkeypatch, [make_track(2, 'New 2'), make_track(1, 'New 1'), existing])

        def fail_download(*args: object, **kwargs: object) -> None:
            raise AssertionError('download_track must not be called in dry-run mode')

        monkeypatch.setattr(organizer, 'download_track', fail_download)

        organizer.main(['--dry-run'])

        assert [path.name for path in organizer_env.glob('*.mp3')] == ['0001. Old - Existing.mp3']
        assert 'would download: Artist - New 1' in caplog.text
        assert 'would download: Artist - New 2' in caplog.text

    def test_downloads_new_tracks_in_playlist_order(
        self,
        organizer_env: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _ = (organizer_env / '0001. Old - Existing.mp3').write_bytes(b'')
        setup_playlist(
            monkeypatch,
            [make_track(2, 'New 2'), make_track(1, 'New 1'), make_track(3, 'Existing', 'Old')],
        )

        def fake_download(track: Any, dest: Path) -> Path:
            path = dest / 'temp_download.mp3'
            _ = path.write_bytes(b'fake mp3')
            return path

        def fake_set_tags(*args: object, **kwargs: object) -> None:
            pass

        monkeypatch.setattr(organizer, 'download_track', fake_download)
        monkeypatch.setattr(organizer, 'set_tags', fake_set_tags)

        organizer.main([])

        names = sorted(path.name for path in organizer_env.glob('*.mp3'))
        assert names == [
            '0001. Old - Existing.mp3',
            '0002. Artist - New 1.mp3',
            '0003. Artist - New 2.mp3',
        ]

    def test_no_new_tracks_is_noop(
        self,
        organizer_env: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _ = (organizer_env / '0001. Artist - Same.mp3').write_bytes(b'')
        setup_playlist(monkeypatch, [make_track(1, 'Same')])

        def fail_download(*args: object, **kwargs: object) -> None:
            raise AssertionError('download_track must not be called')

        monkeypatch.setattr(organizer, 'download_track', fail_download)

        organizer.main([])

        assert [path.name for path in organizer_env.glob('*.mp3')] == ['0001. Artist - Same.mp3']
