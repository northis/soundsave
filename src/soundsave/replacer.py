"""Backup and atomic replacement of archive files."""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path


class ReplaceError(RuntimeError):
    """Raised when a file cannot be backed up or replaced."""


def backup_file(source: Path, backup_root: Path) -> Path:
    """Copy *source* to ``backup_root/YYYYMMDD/`` and return the copy path."""
    date_dir = backup_root / datetime.now(timezone.utc).strftime('%Y%m%d')
    date_dir.mkdir(parents=True, exist_ok=True)
    destination = date_dir / source.name
    if destination.exists():
        stamp = datetime.now(timezone.utc).strftime('%H%M%S')
        destination = date_dir / f'{source.stem}.{stamp}{source.suffix}'
    try:
        shutil.copy2(source, destination)
    except OSError as exc:
        raise ReplaceError(f'Failed to back up {source}: {exc}') from exc
    return destination


def install_file(temporary: Path, original: Path, *, extension: str) -> Path:
    """Atomically install *temporary* in place of *original*.

    When *extension* differs from the original suffix, the original file is
    removed after the new file has been installed.
    """
    extension = extension.lstrip('.').lower()
    target = original.with_suffix(f'.{extension}')
    try:
        temporary.replace(target)
        if target != original:
            original.unlink(missing_ok=True)
    except OSError as exc:
        raise ReplaceError(f'Failed to replace {original}: {exc}') from exc
    return target
