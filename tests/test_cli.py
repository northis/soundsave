from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from soundsave.catalogs import CompositeCatalog
from soundsave.cli import build_parser, main
from soundsave.config import Config


def make_config(
    tmp_path: Path,
    *,
    yandex_token: str | None = None,
    discogs_token: str | None = None,
    discogs_delay: float = 2.0,
) -> Config:
    return Config(
        yandex_token=yandex_token,
        zvuk_token=None,
        playlist_url=None,
        target_dir=tmp_path,
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

    snapshot = build_parser().parse_args(['snapshot', '--label', 'pre-update', '--keep', '4'])
    assert snapshot.command == 'snapshot'
    assert snapshot.label == 'pre-update'
    assert snapshot.keep == 4


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

        monkeypatch.setattr('soundsave.cli.Config.load', lambda *_: config)
        monkeypatch.setattr('soundsave.cli.YandexCatalog.load', lambda *_: object())
        monkeypatch.setattr('soundsave.cli.run_tags', fake_run_tags)

        result = main(['tags', '--apply', '--no-search', '--no-lyrics'])

        assert result == 0
        options = captured['options']
        assert options.apply is True
        assert options.search is False
        assert options.lyrics is False

    def test_missing_token_returns_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config = make_config(tmp_path, yandex_token=None)
        monkeypatch.setattr('soundsave.cli.Config.load', lambda *_: config)

        assert main(['tags']) == 1

    def test_source_yandex_skips_discogs(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config = make_config(tmp_path, yandex_token='token', discogs_token='discogs')
        FakeDiscogsCatalog.instances = []
        monkeypatch.setattr('soundsave.cli.Config.load', lambda *_: config)
        monkeypatch.setattr('soundsave.cli.YandexCatalog.load', lambda *_: object())
        monkeypatch.setattr('soundsave.cli.DiscogsCatalog', FakeDiscogsCatalog)
        monkeypatch.setattr('soundsave.cli.run_tags', lambda *args: [])

        assert main(['tags', '--source', 'yandex']) == 0
        assert FakeDiscogsCatalog.instances == []

    def test_source_discogs_requires_token(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config = make_config(tmp_path, yandex_token='token', discogs_token=None)
        monkeypatch.setattr('soundsave.cli.Config.load', lambda *_: config)

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

        monkeypatch.setattr('soundsave.cli.Config.load', lambda *_: config)
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

        monkeypatch.setattr('soundsave.cli.Config.load', lambda *_: config)
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
        monkeypatch.setattr('soundsave.cli.Config.load', lambda *_: config)

        assert main(['tags', '--source', 'discogs', '--no-search']) == 1

    def test_discogs_delay_override(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config = make_config(tmp_path, yandex_token='token', discogs_token='discogs')
        FakeDiscogsCatalog.instances = []
        monkeypatch.setattr('soundsave.cli.Config.load', lambda *_: config)
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
        monkeypatch.setattr('soundsave.cli.Config.load', lambda *_: config)
        monkeypatch.setattr('soundsave.cli.YandexCatalog.load', lambda *_: object())
        monkeypatch.setattr('soundsave.cli.DiscogsCatalog', FakeDiscogsCatalog)
        monkeypatch.setattr('soundsave.cli.run_tags', lambda *args: [])

        assert main(['tags']) == 0
        assert FakeDiscogsCatalog.instances == []
