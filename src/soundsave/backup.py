"""Incremental track backups: uncompressed ZIP archives of changed files."""

from __future__ import annotations

import json
import logging
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .archive import ArchiveError
from .config import Config
from .web.metadata import LIBRARY_EXTENSIONS

logger = logging.getLogger('soundsave')

STATE_FILE = 'state.json'
DELETE_LIST = 'deleted.txt'


@dataclass(frozen=True)
class BackupState:
    """The date and the files known at the previous backup."""

    last_backup: datetime
    files: frozenset[str]


def _parse_moment(value: str) -> datetime:
    try:
        moment = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
    except ValueError as exc:
        raise ValueError(f'--since must be a date or an ISO datetime, got {value!r}') from exc
    if moment.tzinfo is None:
        moment = moment.astimezone()
    return moment.astimezone(timezone.utc)


def _load_state(path: Path) -> BackupState | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
        return BackupState(
            last_backup=_parse_moment(payload['last_backup']),
            files=frozenset(payload['files']),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f'Corrupt backup state {path}: {exc}') from exc


def _write_state(path: Path, moment: datetime, files: dict[str, Path]) -> None:
    temporary = path.with_name(f'.{path.name}.part')
    payload = {
        'last_backup': moment.isoformat().replace('+00:00', 'Z'),
        'files': sorted(files),
    }
    temporary.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def _audio_files(root: Path, backup_dir: Path) -> dict[str, Path]:
    """Map library-relative POSIX paths to audio files, skipping hidden paths."""
    skip = backup_dir.resolve()
    files: dict[str, Path] = {}
    for path in root.rglob('*'):
        if not path.is_file() or path.suffix.lower() not in LIBRARY_EXTENSIONS:
            continue
        relative = path.relative_to(root)
        if any(part.startswith('.') for part in relative.parts):
            continue
        if path.resolve().is_relative_to(skip):
            continue
        files[relative.as_posix()] = path
    return files


def _write_archive(
    backup_dir: Path,
    started: datetime,
    changed: dict[str, Path],
    deleted: list[str],
) -> Path:
    archive = backup_dir / f'{started:%Y%m%dT%H%M%SZ}.zip'
    suffix = 1
    while archive.exists():
        archive = backup_dir / f'{started:%Y%m%dT%H%M%SZ}-{suffix}.zip'
        suffix += 1
    temporary = archive.with_name(f'.{archive.name}.part')
    with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_STORED) as bundle:
        for name, path in sorted(changed.items()):
            bundle.write(path, name)
        if deleted:
            bundle.writestr(DELETE_LIST, '\n'.join(deleted) + '\n')
    temporary.replace(archive)
    return archive


def run_backup(config: Config, *, since: str | None = None) -> Path | None:
    """Write tracks added or changed since the previous backup to a ZIP archive.

    The first run needs *since*: only files modified after it are considered
    new. Later runs use the date stored in ``state.json``. The archive gets a
    ``deleted.txt`` entry when files known to the previous backup are gone.
    """
    root = (config.music_dir or config.target_dir).expanduser()
    if not root.is_dir():
        raise ArchiveError(f'Music directory does not exist: {root}')
    backup_dir = config.backup_dir.expanduser()
    state_path = backup_dir / STATE_FILE
    state = _load_state(state_path)
    if state is None:
        if not since:
            raise ValueError('The first backup needs --since YYYY-MM-DD')
        cutoff = _parse_moment(since)
        known: frozenset[str] = frozenset()
    else:
        if since:
            raise ValueError('A previous backup exists; --since is only used for the first backup')
        cutoff = state.last_backup
        known = state.files

    started = datetime.now(timezone.utc)
    backup_dir.mkdir(parents=True, exist_ok=True)
    current = _audio_files(root, backup_dir)
    first = state is None
    changed = {
        name: path
        for name, path in current.items()
        if path.stat().st_mtime > cutoff.timestamp() or (not first and name not in known)
    }
    deleted = [] if first else sorted(known - current.keys())

    archive: Path | None = None
    if changed or deleted:
        archive = _write_archive(backup_dir, started, changed, deleted)
        logger.info(
            'Backup written to %s: %d changed, %d deleted%s',
            archive,
            len(changed),
            len(deleted),
            f' (list in {DELETE_LIST})' if deleted else '',
        )
    else:
        logger.info('Nothing changed since %s; no archive written', cutoff.isoformat())
    _write_state(state_path, started, current)
    return archive
