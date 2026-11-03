"""Cover cache keys and file names shared by the scanner and the web layer."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

# Bump whenever the thumbnail rules or the cache layout change. The version is
# part of the public cover URL, so old thumbnails are neither reused nor served
# from the browser cache.
COVER_CACHE_VERSION = 3
COVER_CACHE_EXTENSIONS = ('.webp', '.png', '.jpg', '.gif')

# Versions 1 and 2 named every cached file after one track and its etag, so an
# album with the same embedded cover kept one copy per track. Version 3 uses a
# playlist-scoped content hash and keeps only one file per unique cover.
LEGACY_COVER_CACHE_VERSIONS = (1, 2)
_LEGACY_KEY = re.compile(r'^[0-9a-f]{24}$')


def cover_cache_key(audio_path: Path, etag: str, *, version: int = LEGACY_COVER_CACHE_VERSIONS[-1]) -> str:
    """Return the legacy per-track cache key used by versions 1 and 2.

    The current cache uses :func:`cover_content_key`; this formula is kept so
    ``scan --covers`` and the startup cleanup can remove files left by older
    releases.
    """
    prefix = '' if version <= 1 else f'{version}:'
    return hashlib.sha256(f'{prefix}{audio_path.resolve()}:{etag}'.encode()).hexdigest()[:24]


def playlist_cover_scope(folder_name: str) -> str:
    """Return a short stable scope for one playlist folder.

    Cover deduplication is intentionally limited to a playlist: two playlists
    may keep separate copies, so cleaning up one playlist never touches files
    used by another.
    """
    return hashlib.sha256(folder_name.encode('utf-8')).hexdigest()[:12]


def cover_content_key(scope: str, cover_hash: str) -> str:
    """Return the cache key shared by identical covers inside one playlist."""
    return f'p{scope}-{cover_hash[:24]}'


def is_legacy_cover_key(stem: str) -> bool:
    """Return whether *stem* is a cache file name from versions 1 or 2."""
    return _LEGACY_KEY.fullmatch(stem) is not None
