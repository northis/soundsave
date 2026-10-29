from __future__ import annotations

from pathlib import Path

import pytest

from soundsave.config import Config, ConfigError


def test_music_dir_and_main_playlist_derive_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    empty = tmp_path / 'empty.env'
    empty.write_text('')
    monkeypatch.delenv('TARGET_DIR', raising=False)
    monkeypatch.setenv('MUSIC_DIR', str(tmp_path / 'music'))
    monkeypatch.setenv('MAIN_PLAYLIST', 'Main')
    monkeypatch.setenv('DATA_DIR', str(tmp_path / 'data'))

    config = Config.load(empty)

    assert config.music_dir == tmp_path / 'music'
    assert config.music_dir_configured is True
    assert config.target_dir == tmp_path / 'music' / 'Main'
    assert config.data_dir == tmp_path / 'data'


def test_legacy_target_dir_is_a_flat_library(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    empty = tmp_path / 'empty.env'
    empty.write_text('')
    monkeypatch.delenv('MUSIC_DIR', raising=False)
    monkeypatch.delenv('MAIN_PLAYLIST', raising=False)
    monkeypatch.setenv('TARGET_DIR', str(tmp_path / 'library'))

    config = Config.load(empty)

    assert config.music_dir == tmp_path / 'library'
    assert config.music_dir_configured is False
    assert config.target_dir == tmp_path / 'library'


def test_target_must_be_inside_music_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    empty = tmp_path / 'empty.env'
    empty.write_text('')
    monkeypatch.setenv('MUSIC_DIR', str(tmp_path / 'music'))
    monkeypatch.setenv('TARGET_DIR', str(tmp_path / 'outside'))

    with pytest.raises(ConfigError, match='inside MUSIC_DIR'):
        Config.load(empty)


def test_main_playlist_must_be_a_folder_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    empty = tmp_path / 'empty.env'
    empty.write_text('')
    monkeypatch.setenv('MUSIC_DIR', str(tmp_path / 'music'))
    monkeypatch.setenv('MAIN_PLAYLIST', 'Main/Nested')

    with pytest.raises(ConfigError, match='single folder'):
        Config.load(empty)
