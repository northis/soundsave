"""Build metadata baked into the Docker image by the update script."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .. import __version__

DEFAULT_REPO_URL = 'https://github.com/northis/soundsave'
SHORT_COMMIT_LEN = 7
REPO_ROOT = Path(__file__).resolve().parents[3]


def _git(*args: str) -> str | None:
    try:
        result = subprocess.run(
            ['git', *args],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def build_info() -> dict[str, str | bool]:
    """Return version, commit and repository links for the header badge."""
    version = os.environ.get('SOUNDSAVE_VERSION') or __version__
    commit = os.environ.get('SOUNDSAVE_COMMIT') or _git('rev-parse', 'HEAD') or ''
    commit_date = os.environ.get('SOUNDSAVE_COMMIT_DATE') or (
        _git('show', '-s', '--format=%cI', commit) if commit else ''
    )
    repo_url = (os.environ.get('SOUNDSAVE_REPO_URL') or DEFAULT_REPO_URL).rstrip('/')
    short = commit[:SHORT_COMMIT_LEN] if commit else ''
    tag = f'v{version}'
    dirty = bool(commit and _git('status', '--porcelain'))
    return {
        'version': version,
        'commit': commit,
        'short': short,
        'commit_url': f'{repo_url}/commit/{commit}' if commit else '',
        'commit_date': commit_date or '',
        'dirty': dirty,
        'repo_url': repo_url,
        'tag': tag,
        'tag_url': f'{repo_url}/releases/tag/{tag}',
        'created': os.environ.get('SOUNDSAVE_CREATED', ''),
    }
