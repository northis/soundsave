"""Incremental track backups: uncompressed ZIP archives of changed files."""

from __future__ import annotations

import hashlib
import json
import logging
import time
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
CHUNK_SIZE = 1 << 20


@dataclass(frozen=True)
class FileRecord:
    """What the previous backup knew about one file."""

    size: int
    mtime_ns: int
    sha256: str | None  # None for state v1 and for files older than --since


@dataclass(frozen=True)
class BackupState:
    """The date and the files known at the previous backup."""

    last_backup: datetime
    files: dict[str, FileRecord | None]


@dataclass(frozen=True)
class LibraryFile:
    """A current audio file with the metadata used for change detection."""

    path: Path
    size: int
    mtime_ns: int


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
        raw_files = payload['files']
        files: dict[str, FileRecord | None]
        if isinstance(raw_files, list):
            # State written by 3.1: only paths, no sizes or hashes yet.
            files = dict.fromkeys(raw_files)
        else:
            files = {
                name: FileRecord(
                    size=int(record['size']),
                    mtime_ns=int(record['mtime_ns']),
                    sha256=record.get('sha256'),
                )
                for name, record in raw_files.items()
            }
        return BackupState(last_backup=_parse_moment(payload['last_backup']), files=files)
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise ValueError(f'Corrupt backup state {path}: {exc}') from exc


def _write_state(path: Path, moment: datetime, files: dict[str, FileRecord]) -> None:
    temporary = path.with_name(f'.{path.name}.part')
    payload = {
        'version': 2,
        'last_backup': moment.isoformat().replace('+00:00', 'Z'),
        'files': {
            name: {
                'size': record.size,
                'mtime_ns': record.mtime_ns,
                'sha256': record.sha256,
            }
            for name, record in sorted(files.items())
        },
    }
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    temporary.replace(path)


def _library_files(root: Path, backup_dir: Path) -> dict[str, LibraryFile]:
    """Map library-relative POSIX paths to audio files, skipping hidden paths."""
    skip = backup_dir.resolve()
    files: dict[str, LibraryFile] = {}
    for path in root.rglob('*'):
        if not path.is_file() or path.suffix.lower() not in LIBRARY_EXTENSIONS:
            continue
        relative = path.relative_to(root)
        if any(part.startswith('.') for part in relative.parts):
            continue
        if path.resolve().is_relative_to(skip):
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        files[relative.as_posix()] = LibraryFile(path, stat.st_size, stat.st_mtime_ns)
    return files


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(CHUNK_SIZE), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _write_archive(
    backup_dir: Path,
    started: datetime,
    changed: dict[str, LibraryFile],
    deleted: list[str],
) -> tuple[Path, dict[str, str]]:
    archive = backup_dir / f'{started:%Y%m%dT%H%M%SZ}.zip'
    suffix = 1
    while archive.exists():
        archive = backup_dir / f'{started:%Y%m%dT%H%M%SZ}-{suffix}.zip'
        suffix += 1
    temporary = archive.with_name(f'.{archive.name}.part')
    hashes: dict[str, str] = {}
    with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_STORED) as bundle:
        for name, item in sorted(changed.items()):
            info = zipfile.ZipInfo(name, time.localtime(item.mtime_ns // 1_000_000_000)[:6])
            info.compress_type = zipfile.ZIP_STORED
            digest = hashlib.sha256()
            with item.path.open('rb') as source, bundle.open(info, 'w') as target:
                for chunk in iter(lambda: source.read(CHUNK_SIZE), b''):
                    digest.update(chunk)
                    target.write(chunk)
            hashes[name] = digest.hexdigest()
        if deleted:
            bundle.writestr(DELETE_LIST, '\n'.join(deleted) + '\n')
    temporary.replace(archive)
    return archive, hashes


def run_backup(config: Config, *, since: str | None = None) -> Path | None:
    """Write tracks added or changed since the previous backup to a ZIP archive.

    The first run needs *since*: only files modified after it are considered
    new. Later runs compare size and mtime with ``state.json`` and hash the file
    only when those differ, so a retag or touch is not packed twice. The archive
    gets a ``deleted.txt`` entry when files known to the previous backup are
    gone.
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
    else:
        if since:
            raise ValueError('A previous backup exists; --since is only used for the first backup')
        cutoff = state.last_backup

    started = datetime.now(timezone.utc)
    backup_dir.mkdir(parents=True, exist_ok=True)
    current = _library_files(root, backup_dir)
    first = state is None
    known = state.files if state is not None else {}
    cutoff_ns = int(cutoff.timestamp() * 1_000_000_000)

    changed: dict[str, LibraryFile] = {}
    records: dict[str, FileRecord] = {}
    for name, item in current.items():
        if first:
            records[name] = FileRecord(item.size, item.mtime_ns, None)
            if item.mtime_ns > cutoff_ns:
                changed[name] = item
            continue
        if name not in known:
            changed[name] = item
            continue
        record = known[name]
        if record is None:
            # State written by 3.1: keep the path and use the date once more.
            records[name] = FileRecord(item.size, item.mtime_ns, None)
            if item.mtime_ns > cutoff_ns:
                changed[name] = item
            continue
        if item.size == record.size and item.mtime_ns == record.mtime_ns:
            # shortcut: a content change that preserves both size and mtime is
            # missed; hash every file if that ever matters.
            records[name] = record
            continue
        if record.sha256 is not None and _sha256(item.path) == record.sha256:
            # Only the timestamp changed: keep the old entry, refresh its stat.
            records[name] = FileRecord(item.size, item.mtime_ns, record.sha256)
            continue
        changed[name] = item

    deleted = [] if first else sorted(known.keys() - current.keys())

    archive: Path | None = None
    if changed or deleted:
        archive, hashes = _write_archive(backup_dir, started, changed, deleted)
        for name, digest in hashes.items():
            item = current[name]
            records[name] = FileRecord(item.size, item.mtime_ns, digest)
        logger.info(
            'Backup written to %s: %d changed, %d deleted%s',
            archive,
            len(changed),
            len(deleted),
            f' (list in {DELETE_LIST})' if deleted else '',
        )
    else:
        logger.info('Nothing changed since %s; no archive written', cutoff.isoformat())
    _write_state(state_path, started, records)
    return archive
