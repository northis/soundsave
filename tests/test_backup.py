from __future__ import annotations

import json
import os
import zipfile
from datetime import datetime
from pathlib import Path

import pytest

from soundsave.backup import DELETE_LIST, STATE_FILE, run_backup
from soundsave.cli import build_parser, main
from soundsave.config import Config


def make_config(tmp_path: Path) -> Config:
    music = tmp_path / 'music'
    music.mkdir(exist_ok=True)
    return Config(
        yandex_token=None,
        zvuk_token=None,
        playlist_url=None,
        target_dir=music,
        music_dir=music,
        backup_dir=tmp_path / 'backup',
    )


def make_track(path: Path, mtime: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'track')
    moment = datetime.fromisoformat(mtime).astimezone()
    os.utime(path, (moment.timestamp(), moment.timestamp()))


def rewrite_cutoff(config: Config, cutoff: str) -> None:
    state_path = config.backup_dir / STATE_FILE
    payload = json.loads(state_path.read_text(encoding='utf-8'))
    payload['last_backup'] = cutoff
    state_path.write_text(json.dumps(payload), encoding='utf-8')


def test_backup_parser() -> None:
    args = build_parser().parse_args(['backup', '--since', '2026-01-01'])

    assert args.command == 'backup'
    assert args.since == '2026-01-01'
    assert build_parser().parse_args(['backup']).since is None


def test_first_backup_requires_since(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    make_track(config.music_dir / 'Main' / 'a.mp3', '2026-01-01')

    with pytest.raises(ValueError, match='--since'):
        run_backup(config)
    assert not config.backup_dir.exists()


def test_first_backup_takes_only_files_newer_than_since(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    make_track(config.music_dir / 'Main' / 'old.mp3', '2025-01-01')
    make_track(config.music_dir / 'Main' / 'new.mp3', '2026-02-01')

    archive = run_backup(config, since='2026-01-01')

    assert archive is not None
    with zipfile.ZipFile(archive) as bundle:
        assert bundle.namelist() == ['Main/new.mp3']
        assert bundle.getinfo('Main/new.mp3').compress_type == zipfile.ZIP_STORED
        assert DELETE_LIST not in bundle.namelist()
    state = json.loads((config.backup_dir / STATE_FILE).read_text(encoding='utf-8'))
    assert state['files'] == ['Main/new.mp3', 'Main/old.mp3']


def test_later_backups_take_changed_and_new_files(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    make_track(config.music_dir / 'Main' / 'keep.mp3', '2024-01-01')
    run_backup(config, since='2023-01-01')
    rewrite_cutoff(config, '2025-01-01T00:00:00Z')
    make_track(config.music_dir / 'Main' / 'keep.mp3', '2026-01-01')
    make_track(config.music_dir / 'Main' / 'added.mp3', '2024-06-01')

    archive = run_backup(config)

    assert archive is not None
    with zipfile.ZipFile(archive) as bundle:
        assert sorted(bundle.namelist()) == ['Main/added.mp3', 'Main/keep.mp3']


def test_deleted_files_are_listed_in_the_archive(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    make_track(config.music_dir / 'Main' / 'keep.mp3', '2024-01-01')
    gone = config.music_dir / 'Main' / 'gone.mp3'
    make_track(gone, '2024-01-01')
    run_backup(config, since='2023-01-01')
    gone.unlink()

    archive = run_backup(config)

    assert archive is not None
    with zipfile.ZipFile(archive) as bundle:
        assert bundle.namelist() == [DELETE_LIST]
        assert bundle.read(DELETE_LIST).decode() == 'Main/gone.mp3\n'


def test_archive_has_no_delete_list_when_nothing_was_deleted(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    make_track(config.music_dir / 'Main' / 'keep.mp3', '2024-01-01')
    run_backup(config, since='2023-01-01')
    rewrite_cutoff(config, '2025-01-01T00:00:00Z')
    make_track(config.music_dir / 'Main' / 'added.mp3', '2026-01-01')

    archive = run_backup(config)

    assert archive is not None
    with zipfile.ZipFile(archive) as bundle:
        assert DELETE_LIST not in bundle.namelist()


def test_nothing_changed_writes_no_archive(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    make_track(config.music_dir / 'Main' / 'keep.mp3', '2024-01-01')
    first = run_backup(config, since='2023-01-01')

    assert run_backup(config) is None
    assert list(config.backup_dir.glob('*.zip')) == [first]


def test_previous_since_is_rejected(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    make_track(config.music_dir / 'Main' / 'keep.mp3', '2024-01-01')
    run_backup(config, since='2023-01-01')

    with pytest.raises(ValueError, match='previous backup'):
        run_backup(config, since='2020-01-01')


def test_hidden_paths_are_ignored(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    make_track(config.music_dir / 'Main' / 'visible.mp3', '2026-01-01')
    make_track(config.music_dir / 'Main' / '.hidden.mp3', '2026-01-01')
    make_track(config.music_dir / '.soundsave' / 'tmp.mp3', '2026-01-01')

    archive = run_backup(config, since='2025-01-01')

    assert archive is not None
    with zipfile.ZipFile(archive) as bundle:
        assert bundle.namelist() == ['Main/visible.mp3']


def test_cli_backup_needs_since_on_first_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = make_config(tmp_path)
    monkeypatch.setattr('soundsave.cli.Config.load', lambda *_, **__: config)

    assert main(['backup']) == 1
    assert main(['backup', '--since', '2025-01-01']) == 0
    assert (config.backup_dir / STATE_FILE).exists()
