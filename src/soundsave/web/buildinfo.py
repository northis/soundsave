"""Build metadata baked into the Docker image by the update script."""

from __future__ import annotations

import os

from .. import __version__


def build_info() -> dict[str, str]:
    """Return version and optional commit fields for the UI and update script."""
    return {
        'version': os.environ.get('SOUNDSAVE_VERSION') or __version__,
        'commit': os.environ.get('SOUNDSAVE_COMMIT', ''),
        'commit_date': os.environ.get('SOUNDSAVE_COMMIT_DATE', ''),
        'created': os.environ.get('SOUNDSAVE_CREATED', ''),
    }
