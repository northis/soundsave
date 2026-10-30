"""Cover cache keys and file names shared by the scanner and the web layer."""

from __future__ import annotations

import hashlib
from pathlib import Path

# Bump whenever the thumbnail rules change. The version is part of both the
# cache key and the public cover URL, so old thumbnails are neither reused nor
# served from the browser cache.
COVER_CACHE_VERSION = 2
COVER_CACHE_VERSIONS = tuple(range(1, COVER_CACHE_VERSION + 1))
COVER_CACHE_EXTENSIONS = ('.webp', '.png', '.jpg', '.gif')


def cover_cache_key(audio_path: Path, etag: str, *, version: int = COVER_CACHE_VERSION) -> str:
    """Return the cache key for *audio_path* at a given cache version.

    Version 1 used the key without a version prefix; keeping that formula lets
    the scanner delete thumbnails left behind by older releases.
    """
    prefix = '' if version <= 1 else f'{version}:'
    return hashlib.sha256(f'{prefix}{audio_path.resolve()}:{etag}'.encode()).hexdigest()[:24]
