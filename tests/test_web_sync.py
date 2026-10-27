from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from soundsave import sync
from soundsave.config import Config
from soundsave.web.db import LibraryStore
from soundsave.web.library import LibraryScanner
from soundsave.web.settings import WebSettings
from soundsave.web.sync_jobs import SyncError, SyncService

from .web_helpers import make_config, make_library


class FakeModel(SimpleNamespace):
    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)


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


def make_track(track_id: int, title: str, artist: str = 'Artist') -> Any:
    return FakeModel(
        id=track_id,
        title=title,
        version=None,
        artists=[FakeModel(name=artist)],
        albums=[],
        lyrics_info=None,
        cover_uri=None,
        duration_ms=180_000,
        get_download_info=list,
    )


def make_sync_config(root: Path, *, max_downloads: int = 20) -> Config:
    return Config(
        yandex_token='token',
        zvuk_token=None,
        playlist_url='https://music.yandex.ru/users/alice/playlists/1',
        target_dir=root,
        max_downloads=max_downloads,
        delay=0.0,
    )


def test_run_sync_without_zvuk_uses_yandex_stream(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeClient.playlist = FakePlaylist(
        [FakeTrackShort(make_track(1, 'One')), FakeTrackShort(make_track(2, 'Two'))],
    )
    monkeypatch.setattr(sync, 'Client', FakeClient)
    monkeypatch.setattr(sync.time, 'sleep', lambda _: None)

    def fail_provider(config: Config) -> None:
        raise AssertionError('Zvuk must not be called from the web sync')

    def fake_download(track: Any, destination: Path, **kwargs: object) -> Path:
        path = destination / f'.soundsave-{track.id}.mp3'
        path.write_bytes(b'fake')
        return path

    monkeypatch.setattr(sync, 'build_zvuk_provider', fail_provider)
    monkeypatch.setattr(sync, 'download_track', fake_download)
    monkeypatch.setattr(sync, 'set_tags', lambda *args, **kwargs: None)

    phases: list[str] = []
    result = sync.run_sync(
        make_sync_config(root),
        allow_zvuk=False,
        progress=lambda progress: phases.append(progress.phase),
    )

    assert result.total_new == 2
    assert result.added == 2
    assert result.failed == 0
    assert result.limit_reached is False
    assert phases[-1] == 'done'
    assert sorted(path.name for path in root.glob('*.mp3')) == [
        '0001. Artist - Two.mp3',
        '0002. Artist - One.mp3',
    ]


def test_run_sync_stops_at_max_downloads(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeClient.playlist = FakePlaylist(
        [FakeTrackShort(make_track(index, f'Track {index}')) for index in range(1, 5)],
    )
    monkeypatch.setattr(sync, 'Client', FakeClient)
    monkeypatch.setattr(sync.time, 'sleep', lambda _: None)
    monkeypatch.setattr(sync, 'build_zvuk_provider', lambda config: None)

    def fake_download(track: Any, destination: Path, **kwargs: object) -> Path:
        path = destination / f'.soundsave-{track.id}.mp3'
        path.write_bytes(b'fake')
        return path

    monkeypatch.setattr(sync, 'download_track', fake_download)
    monkeypatch.setattr(sync, 'set_tags', lambda *args, **kwargs: None)

    result = sync.run_sync(make_sync_config(root, max_downloads=2), allow_zvuk=False)

    assert result.total_new == 2
    assert result.added == 2
    assert result.limit_reached is True


def test_sync_service_runs_background_job(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    make_library(root)
    config = make_config(root, yandex_token='token', playlist_url='https://music.yandex.ru/users/a/playlists/1')
    settings = WebSettings.from_config(config)
    store = LibraryStore(settings.db_path)
    store.init()
    scanner = LibraryScanner(settings, store)
    scanner.reconcile()
    main = store.get_syncable_playlist()
    assert main is not None
    monkeypatch.setattr(scanner, 'reconcile', lambda: None)
    seen: dict[str, object] = {}

    def fake_run(config: Config, *, allow_zvuk: bool, progress: object) -> sync.SyncResult:
        seen['allow_zvuk'] = allow_zvuk
        progress(sync.SyncProgress(phase='done', total=1, completed=1, added=1))
        return sync.SyncResult(total_new=1, added=1)

    monkeypatch.setattr('soundsave.web.sync_jobs.run_sync', fake_run)
    service = SyncService(config, store, scanner)

    service.start(main.id)
    for _ in range(100):
        status = service.status(main.id)
        if status['state'] != 'running':
            break
        time.sleep(0.01)

    assert seen['allow_zvuk'] is False
    assert status['state'] == 'done'
    assert status['added'] == 1


def test_sync_service_rejects_readonly_and_unconfigured(root: Path) -> None:
    make_library(root)
    config = make_config(root)
    settings = WebSettings.from_config(config)
    store = LibraryStore(settings.db_path)
    store.init()
    scanner = LibraryScanner(settings, store)
    scanner.reconcile()
    service = SyncService(config, store, scanner)
    main = store.get_syncable_playlist()
    assert main is not None
    chill = next(playlist for playlist in store.list_playlists() if not playlist.syncable)

    with pytest.raises(SyncError) as readonly:
        service.start(chill.id)
    assert readonly.value.status_code == 403

    with pytest.raises(SyncError) as unconfigured:
        service.start(main.id)
    assert unconfigured.value.status_code == 503
