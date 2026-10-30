from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from soundsave.catalogs import CompositeCatalog
from soundsave.cli import build_parser, main
from soundsave.config import Config
from soundsave.web.db import LibraryStore
from soundsave.web.settings import WebSettings

from .web_helpers import make_library, make_mp3


def make_config(
    tmp_path: Path,
    *,
    yandex_token: str | None = None,
    discogs_token: str | None = None,
    discogs_delay: float = 2.0,
    music_dir: Path | None = None,
    target_dir: Path | None = None,
    music_dir_configured: bool = False,
) -> Config:
    return Config(
        yandex_token=yandex_token,
        zvuk_token=None,
        playlist_url=None,
        target_dir=target_dir or tmp_path,
        music_dir=music_dir,
        music_dir_configured=music_dir_configured,
        delay=0.0,
        discogs_token=discogs_token,
        discogs_delay=discogs_delay,
    )


def test_web_parser_has_serve_and_scan() -> None:
    serve = build_parser().parse_args(['serve'])
    assert serve.command == 'serve'
    assert serve.host is None
    assert serve.port is None
    assert serve.reload is False

    scan = build_parser().parse_args(['scan'])
    assert scan.command == 'scan'
    assert scan.playlist is None
    assert scan.target_dir is None
    assert scan.force is False
    assert scan.covers is False

    snapshot = build_parser().parse_args(['snapshot', '--label', 'pre-update', '--keep', '4'])
    assert snapshot.command == 'snapshot'
    assert snapshot.label == 'pre-update'
    assert snapshot.keep == 4


def test_scan_parser_rescans_one_playlist() -> None:
    args = build_parser().parse_args(['scan', '--playlist', 'Chill', '--force', '--covers'])
    assert args.playlist == 'Chill'
    assert args.target_dir is None
    assert args.force is True
    assert args.covers is True

    args = build_parser().parse_args(['scan', '--folder', 'music'])
    assert args.target_dir == Path('music')
    assert args.playlist is None
    assert args.force is False
    assert args.covers is False

    with pytest.raises(SystemExit):
        build_parser().parse_args(['scan', '--playlist', 'Chill', '--dir', 'Chill'])


def test_scan_command_rescans_one_playlist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make_library(tmp_path)
    music = tmp_path / 'music'
    config = replace(
        make_config(tmp_path, music_dir=music, target_dir=music / 'Main', music_dir_configured=True),
        data_dir=tmp_path / 'data',
    )
    monkeypatch.setattr('soundsave.cli.Config.load', lambda *_: config)

    assert main(['scan']) == 0
    settings = WebSettings.from_config(config)
    store = LibraryStore(settings.db_path)
    chill = next(playlist for playlist in store.list_playlists() if playlist.name == 'Chill')

    make_mp3(music / 'Chill' / '0002. Someone - New.mp3', 'New', 'Someone')
    assert main(['scan', '--playlist', 'Chill']) == 0
    assert [track.filename for track in store.tracks_for_playlist(chill.id)] == [
        '01 - Someone - Something.mp3',
        '0002. Someone - New.mp3',
    ]

    make_mp3(music / 'Chill' / '0003. Someone - Third.mp3', 'Third', 'Someone')
    assert main(['scan', '--dir', str(music / 'Chill')]) == 0
    assert len(store.tracks_for_playlist(chill.id)) == 3

    assert main(['scan', '--playlist', 'Missing']) == 1


def test_export_parser() -> None:
    args = build_parser().parse_args(
        ['export', 'list.fpl', '-o', 'out', '--bitrate', '192', '--map', 'D:/Music=/music', '--dry-run'],
    )

    assert args.command == 'export'
    assert args.playlist == Path('list.fpl')
    assert args.output == Path('out')
    assert args.bitrate == 192
    assert args.mappings == ['D:/Music=/music']
    assert args.dry_run is True
    assert args.no_m3u8 is False
    assert args.no_retag is False


def test_snapshot_copies_the_web_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = make_config(tmp_path)
    monkeypatch.setattr('soundsave.cli.Config.load', lambda *_: config)
    database = tmp_path / '.soundsave' / 'library.sqlite3'
    database.parent.mkdir(parents=True)
    with sqlite3.connect(database) as connection:
        connection.execute('CREATE TABLE marker (value TEXT)')

    assert main(['snapshot', '--label', 'test']) == 0

    snapshots = list((tmp_path / '.soundsave' / 'backups').glob('test-*.sqlite3'))
    assert len(snapshots) == 1


class TestTagsParser:
    def test_defaults(self) -> None:
        args = build_parser().parse_args(['tags'])

        assert args.command == 'tags'
        assert args.apply is False
        assert args.no_search is False
        assert args.no_lyrics is False
        assert args.no_skip_complete is False
        assert args.source == 'both'
        assert args.discogs_delay is None
        assert args.limit is None
        assert args.file_id is None
        assert args.playlist is None

    def test_folder_alias(self) -> None:
        args = build_parser().parse_args(['tags', '--folder', 'music'])

        assert args.target_dir == Path('music')

    def test_flags(self) -> None:
        args = build_parser().parse_args(
            [
                'tags',
                '--apply',
                '--no-search',
                '--no-lyrics',
                '--no-skip-complete',
                '--source',
                'discogs',
                '--discogs-delay',
                '5',
                '--id',
                '42',
                '--limit',
                '10',
                '--playlist',
                'Chill',
            ],
        )

        assert args.apply is True
        assert args.no_search is True
        assert args.no_lyrics is True
        assert args.no_skip_complete is True
        assert args.source == 'discogs'
        assert args.discogs_delay == 5.0
        assert args.file_id == 42
        assert args.limit == 10
        assert args.playlist == 'Chill'


class FakeDiscogsCatalog:
    instances: list[FakeDiscogsCatalog] = []

    def __init__(self, token: str, *, delay: float = 2.0, **kwargs: object) -> None:
        self.token = token
        self.delay = delay
        self.verified = False
        FakeDiscogsCatalog.instances.append(self)

    def verify(self) -> str:
        self.verified = True
        return 'tester'


class TestTagsDispatch:
    def test_main_passes_options(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config = make_config(tmp_path, yandex_token='token')
        captured: dict[str, Any] = {}

        def fake_run_tags(config: Config, catalog: object, options: object) -> list[object]:
            captured['options'] = options
            return []

        monkeypatch.setattr('soundsave.cli.Config.load', lambda *_, **__: config)
        monkeypatch.setattr('soundsave.cli.YandexCatalog.load', lambda *_: object())
        monkeypatch.setattr('soundsave.cli.run_tags', fake_run_tags)

        result = main(['tags', '--apply', '--no-search', '--no-lyrics'])

        assert result == 0
        options = captured['options']
        assert options.apply is True
        assert options.search is False
        assert options.lyrics is False

    def test_folder_skips_target_validation(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config = make_config(tmp_path, yandex_token='token')
        captured: dict[str, Any] = {}

        def fake_load(*args: object, **kwargs: object) -> Config:
            captured.update(kwargs)
            return config

        monkeypatch.setattr('soundsave.cli.Config.load', fake_load)
        monkeypatch.setattr('soundsave.cli.YandexCatalog.load', lambda *_: object())
        monkeypatch.setattr('soundsave.cli.run_tags', lambda *args: [])

        assert main(['tags', '--folder', str(tmp_path)]) == 0
        assert captured['validate_target'] is False

    def test_playlist_skips_target_validation(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        music = tmp_path / 'music'
        chill = music / 'Chill'
        chill.mkdir(parents=True)
        config = make_config(tmp_path, yandex_token='token', music_dir=music, target_dir=tmp_path / 'outside')
        captured: dict[str, Any] = {}

        def fake_load(*args: object, **kwargs: object) -> Config:
            captured.update(kwargs)
            return config

        monkeypatch.setattr('soundsave.cli.Config.load', fake_load)
        monkeypatch.setattr('soundsave.cli.YandexCatalog.load', lambda *_: object())
        monkeypatch.setattr('soundsave.cli.run_tags', lambda *args: [])

        assert main(['tags', '--playlist', 'Chill']) == 0
        assert captured['validate_target'] is False

    def test_default_load_validates_target(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config = make_config(tmp_path, yandex_token='token')
        captured: dict[str, Any] = {}

        def fake_load(*args: object, **kwargs: object) -> Config:
            captured.update(kwargs)
            return config

        monkeypatch.setattr('soundsave.cli.Config.load', fake_load)
        monkeypatch.setattr('soundsave.cli.YandexCatalog.load', lambda *_: object())
        monkeypatch.setattr('soundsave.cli.run_tags', lambda *args: [])

        assert main(['tags']) == 0
        assert 'validate_target' not in captured

    def test_playlist_uses_music_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        music = tmp_path / 'music'
        library = music / 'Library'
        chill = music / 'Chill'
        library.mkdir(parents=True)
        chill.mkdir()
        config = make_config(tmp_path, yandex_token='token', music_dir=music, target_dir=library)
        captured: dict[str, Path] = {}

        def fake_run_tags(config: Config, catalog: object, options: object) -> list[object]:
            captured['target_dir'] = config.target_dir
            return []

        monkeypatch.setattr('soundsave.cli.Config.load', lambda *_, **__: config)
        monkeypatch.setattr('soundsave.cli.YandexCatalog.load', lambda *_: object())
        monkeypatch.setattr('soundsave.cli.run_tags', fake_run_tags)

        assert main(['tags', '--playlist', 'Chill']) == 0
        assert captured['target_dir'] == chill

    def test_playlist_uses_music_dir_in_flat_layout(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        music = tmp_path / 'music'
        chill = music / 'Chill'
        chill.mkdir(parents=True)
        config = make_config(
            tmp_path,
            yandex_token='token',
            music_dir=music,
            target_dir=music,
            music_dir_configured=True,
        )
        captured: dict[str, Path] = {}

        def fake_run_tags(config: Config, catalog: object, options: object) -> list[object]:
            captured['target_dir'] = config.target_dir
            return []

        monkeypatch.setattr('soundsave.cli.Config.load', lambda *_, **__: config)
        monkeypatch.setattr('soundsave.cli.YandexCatalog.load', lambda *_: object())
        monkeypatch.setattr('soundsave.cli.run_tags', fake_run_tags)

        assert main(['tags', '--playlist', 'Chill']) == 0
        assert captured['target_dir'] == chill

    def test_playlist_uses_target_parent_without_music_dir(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        library = tmp_path / 'Library'
        chill = tmp_path / 'Chill'
        library.mkdir()
        chill.mkdir()
        config = make_config(tmp_path, yandex_token='token', target_dir=library)
        captured: dict[str, Path] = {}

        def fake_run_tags(config: Config, catalog: object, options: object) -> list[object]:
            captured['target_dir'] = config.target_dir
            return []

        monkeypatch.setattr('soundsave.cli.Config.load', lambda *_, **__: config)
        monkeypatch.setattr('soundsave.cli.YandexCatalog.load', lambda *_: object())
        monkeypatch.setattr('soundsave.cli.run_tags', fake_run_tags)

        assert main(['tags', '--playlist', 'Chill']) == 0
        assert captured['target_dir'] == chill

    def test_playlist_rejects_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config = make_config(tmp_path, yandex_token='token')
        monkeypatch.setattr('soundsave.cli.Config.load', lambda *_, **__: config)

        assert main(['tags', '--playlist', 'Chill', '--dir', str(tmp_path)]) == 1

    def test_playlist_rejects_folder_alias(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config = make_config(tmp_path, yandex_token='token')
        monkeypatch.setattr('soundsave.cli.Config.load', lambda *_, **__: config)

        assert main(['tags', '--playlist', 'Chill', '--folder', str(tmp_path)]) == 1

    def test_playlist_rejects_nested_name(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config = make_config(tmp_path, yandex_token='token')
        monkeypatch.setattr('soundsave.cli.Config.load', lambda *_, **__: config)

        assert main(['tags', '--playlist', '../Chill']) == 1

    def test_playlist_rejects_missing_folder(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config = make_config(tmp_path, yandex_token='token', music_dir=tmp_path, target_dir=tmp_path / 'Library')
        monkeypatch.setattr('soundsave.cli.Config.load', lambda *_, **__: config)

        assert main(['tags', '--playlist', 'Chill']) == 1

    def test_missing_token_returns_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config = make_config(tmp_path, yandex_token=None)
        monkeypatch.setattr('soundsave.cli.Config.load', lambda *_, **__: config)

        assert main(['tags']) == 1

    def test_source_yandex_skips_discogs(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config = make_config(tmp_path, yandex_token='token', discogs_token='discogs')
        FakeDiscogsCatalog.instances = []
        monkeypatch.setattr('soundsave.cli.Config.load', lambda *_, **__: config)
        monkeypatch.setattr('soundsave.cli.YandexCatalog.load', lambda *_: object())
        monkeypatch.setattr('soundsave.cli.DiscogsCatalog', FakeDiscogsCatalog)
        monkeypatch.setattr('soundsave.cli.run_tags', lambda *args: [])

        assert main(['tags', '--source', 'yandex']) == 0
        assert FakeDiscogsCatalog.instances == []

    def test_source_discogs_requires_token(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config = make_config(tmp_path, yandex_token='token', discogs_token=None)
        monkeypatch.setattr('soundsave.cli.Config.load', lambda *_, **__: config)

        assert main(['tags', '--source', 'discogs']) == 1

    def test_source_discogs_skips_yandex(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config = make_config(tmp_path, yandex_token='token', discogs_token='discogs')
        FakeDiscogsCatalog.instances = []
        captured: dict[str, Any] = {}

        def fail_yandex(*args: object) -> None:
            raise AssertionError('Yandex must not be loaded')

        def fake_run_tags(config: Config, catalog: object, options: object) -> list[object]:
            captured['options'] = options
            return []

        monkeypatch.setattr('soundsave.cli.Config.load', lambda *_, **__: config)
        monkeypatch.setattr('soundsave.cli.YandexCatalog.load', fail_yandex)
        monkeypatch.setattr('soundsave.cli.DiscogsCatalog', FakeDiscogsCatalog)
        monkeypatch.setattr('soundsave.cli.run_tags', fake_run_tags)

        assert main(['tags', '--source', 'discogs']) == 0
        assert len(FakeDiscogsCatalog.instances) == 1
        assert FakeDiscogsCatalog.instances[0].verified is True
        assert captured['options'].skip_complete is True

    def test_source_both_uses_composite(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config = make_config(tmp_path, yandex_token='token', discogs_token='discogs')
        FakeDiscogsCatalog.instances = []
        captured: dict[str, Any] = {}

        def fake_run_tags(config: Config, catalog: object, options: object) -> list[object]:
            captured['catalog'] = catalog
            return []

        monkeypatch.setattr('soundsave.cli.Config.load', lambda *_, **__: config)
        monkeypatch.setattr('soundsave.cli.YandexCatalog.load', lambda *_: object())
        monkeypatch.setattr('soundsave.cli.DiscogsCatalog', FakeDiscogsCatalog)
        monkeypatch.setattr('soundsave.cli.run_tags', fake_run_tags)

        assert main(['tags', '--source', 'both']) == 0
        assert isinstance(captured['catalog'], CompositeCatalog)
        assert len(FakeDiscogsCatalog.instances) == 1
        assert FakeDiscogsCatalog.instances[0].verified is True

    def test_source_discogs_with_no_search_errors(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config = make_config(tmp_path, yandex_token='token', discogs_token='discogs')
        monkeypatch.setattr('soundsave.cli.Config.load', lambda *_, **__: config)

        assert main(['tags', '--source', 'discogs', '--no-search']) == 1

    def test_discogs_delay_override(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config = make_config(tmp_path, yandex_token='token', discogs_token='discogs')
        FakeDiscogsCatalog.instances = []
        monkeypatch.setattr('soundsave.cli.Config.load', lambda *_, **__: config)
        monkeypatch.setattr('soundsave.cli.DiscogsCatalog', FakeDiscogsCatalog)
        monkeypatch.setattr('soundsave.cli.run_tags', lambda *args: [])

        assert main(['tags', '--source', 'discogs', '--discogs-delay', '5']) == 0
        assert FakeDiscogsCatalog.instances[0].delay == 5.0

    def test_both_without_discogs_token_uses_yandex(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config = make_config(tmp_path, yandex_token='token', discogs_token=None)
        FakeDiscogsCatalog.instances = []
        monkeypatch.setattr('soundsave.cli.Config.load', lambda *_, **__: config)
        monkeypatch.setattr('soundsave.cli.YandexCatalog.load', lambda *_: object())
        monkeypatch.setattr('soundsave.cli.DiscogsCatalog', FakeDiscogsCatalog)
        monkeypatch.setattr('soundsave.cli.run_tags', lambda *args: [])

        assert main(['tags']) == 0
        assert FakeDiscogsCatalog.instances == []
