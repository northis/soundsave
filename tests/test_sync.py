from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from soundsave import sync
from soundsave.config import Config


def make_track(track_id: int = 1, title: str = 'Title', artist: str = 'Artist') -> Any:
    return SimpleNamespace(
        id=track_id,
        title=title,
        version=None,
        artists=[SimpleNamespace(name=artist)],
        albums=[],
        lyrics_info=None,
        cover_uri=None,
    )


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


def make_config(tmp_path: Path) -> Config:
    return Config(
        yandex_token='token',
        zvuk_token=None,
        playlist_url='https://music.yandex.ru/users/alice/playlists/42',
        target_dir=tmp_path,
        max_downloads=20,
        delay=0.0,
    )


def setup_playlist(monkeypatch: pytest.MonkeyPatch, tracks: list[Any]) -> None:
    FakeClient.playlist = FakePlaylist([FakeTrackShort(track) for track in tracks])
    monkeypatch.setattr(sync, 'Client', FakeClient)
    monkeypatch.setattr(sync.time, 'sleep', lambda _: None)


class TestParsePlaylistUrl:
    def test_plain_url(self) -> None:
        assert sync.parse_playlist_url('https://music.yandex.ru/users/alice/playlists/42') == ('alice', 42)

    def test_url_with_query(self) -> None:
        assert sync.parse_playlist_url('https://music.yandex.ru/users/alice/playlists/42?foo=bar') == ('alice', 42)

    def test_invalid_url(self) -> None:
        with pytest.raises(ValueError, match='Unsupported playlist url'):
            sync.parse_playlist_url('https://example.com/playlist/42')


class TestLocalCollection:
    def test_highest_id(self, tmp_path: Path) -> None:
        (tmp_path / '0001. A - B.mp3').write_bytes(b'')
        (tmp_path / '0042. C - D.mp3').write_bytes(b'')
        (tmp_path / 'temp_download.mp3').write_bytes(b'')
        (tmp_path / 'notes.txt').write_text('ignored')

        assert sync.get_highest_local_id(tmp_path) == 42

    def test_missing_directory(self, tmp_path: Path) -> None:
        assert sync.get_highest_local_id(tmp_path / 'missing') == 0


class TestTrackIdentifier:
    def test_uses_first_artist_and_lowercase(self) -> None:
        track = make_track(title='Song', artist='Some Artist')
        assert sync.build_track_identifier(track) == 'some artist-song'

    def test_sanitizes_illegal_characters(self) -> None:
        track = make_track(title='AC/DC: Live', artist='A|B')
        assert sync.build_track_identifier(track) == 'a-b-ac-dc- live'


class TestRunSync:
    def test_dry_run_does_not_download(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        (tmp_path / '0001. Old - Existing.mp3').write_bytes(b'')
        setup_playlist(
            monkeypatch,
            [make_track(2, 'New 2'), make_track(1, 'New 1'), make_track(3, 'Existing', 'Old')],
        )

        def fail_download(*args: object, **kwargs: object) -> None:
            raise AssertionError('download_track must not be called in dry-run mode')

        monkeypatch.setattr(sync, 'download_track', fail_download)

        sync.run_sync(make_config(tmp_path), dry_run=True)

        assert [path.name for path in tmp_path.glob('*.mp3')] == ['0001. Old - Existing.mp3']

    def test_downloads_new_tracks_in_playlist_order(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        (tmp_path / '0001. Old - Existing.mp3').write_bytes(b'')
        setup_playlist(
            monkeypatch,
            [make_track(2, 'New 2'), make_track(1, 'New 1'), make_track(3, 'Existing', 'Old')],
        )

        def fake_download(track: Any, destination: Path) -> Path:
            path = destination / f'.soundsave-{track.id}.mp3'
            path.write_bytes(b'fake mp3')
            return path

        monkeypatch.setattr(sync, 'download_track', fake_download)
        monkeypatch.setattr(sync, 'set_tags', lambda *args, **kwargs: None)

        sync.run_sync(make_config(tmp_path))

        names = sorted(path.name for path in tmp_path.glob('*.mp3'))
        assert names == [
            '0001. Old - Existing.mp3',
            '0002. Artist - New 1.mp3',
            '0003. Artist - New 2.mp3',
        ]

    def test_no_new_tracks_is_noop(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        (tmp_path / '0001. Artist - Same.mp3').write_bytes(b'')
        setup_playlist(monkeypatch, [make_track(1, 'Same')])

        def fail_download(*args: object, **kwargs: object) -> None:
            raise AssertionError('download_track must not be called')

        monkeypatch.setattr(sync, 'download_track', fail_download)

        sync.run_sync(make_config(tmp_path))

        assert [path.name for path in tmp_path.glob('*.mp3')] == ['0001. Artist - Same.mp3']
