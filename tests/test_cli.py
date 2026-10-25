from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from soundsave.cli import build_parser, main
from soundsave.config import Config


def make_config(tmp_path: Path, *, yandex_token: str | None = None) -> Config:
    return Config(
        yandex_token=yandex_token,
        zvuk_token=None,
        playlist_url=None,
        target_dir=tmp_path,
        delay=0.0,
    )


class TestTagsParser:
    def test_defaults(self) -> None:
        args = build_parser().parse_args(['tags'])

        assert args.command == 'tags'
        assert args.apply is False
        assert args.no_search is False
        assert args.no_lyrics is False
        assert args.limit is None
        assert args.file_id is None

    def test_flags(self) -> None:
        args = build_parser().parse_args(
            ['tags', '--apply', '--no-search', '--no-lyrics', '--id', '42', '--limit', '10'],
        )

        assert args.apply is True
        assert args.no_search is True
        assert args.no_lyrics is True
        assert args.file_id == 42
        assert args.limit == 10


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
