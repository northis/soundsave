"""Embedded cover extraction and thumbnail caching."""

from __future__ import annotations

import hashlib
import io
import logging
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageOps

from ..archive import get_image_type, read_tag_data
from .cover_cache import (
    COVER_CACHE_EXTENSIONS,
    cover_content_key,
    is_legacy_cover_key,
    playlist_cover_scope,
)

logger = logging.getLogger(__name__)

_MAX_SIZE = 1000
_THUMB_SIZE = (_MAX_SIZE, _MAX_SIZE)
_MAX_PIXELS = 50_000_000

_MEDIA_TYPES = {
    '.webp': 'image/webp',
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.gif': 'image/gif',
}
_MEDIA_EXTENSIONS = {
    'image/webp': '.webp',
    'image/png': '.png',
    'image/jpeg': '.jpg',
    'image/jpg': '.jpg',
    'image/gif': '.gif',
}
_FORMAT_MEDIA_TYPES = {
    'JPEG': 'image/jpeg',
    'PNG': 'image/png',
    'WEBP': 'image/webp',
    'GIF': 'image/gif',
}


@dataclass(frozen=True)
class CoverResult:
    path: Path
    media_type: str
    cover_hash: str


class CoverCache:
    """Extract front covers lazily and cache them up to 1000 px.

    Covers larger than 1000 px on either side are scaled down to 1000 px and
    stored as WebP. Smaller covers are served as-is, without re-encoding.
    Identical covers inside one playlist share a single file, addressed by the
    SHA-256 hash of the embedded image bytes.
    """

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)

    def get(
        self,
        audio_path: Path,
        *,
        playlist_folder: str,
        cover_hash: str | None = None,
    ) -> CoverResult | None:
        """Return the cached cover for *audio_path*, building it on demand.

        ``cover_hash`` is the hash stored by the library scanner or a previous
        request. When it is known and the shared file still exists, the audio
        tags are not read at all.
        """
        scope = playlist_cover_scope(playlist_folder)
        if cover_hash:
            cached = self._find(cover_content_key(scope, cover_hash))
            if cached is not None:
                return CoverResult(cached[0], cached[1], cover_hash)

        try:
            tags = read_tag_data(audio_path, include_cover=True)
        except Exception as exc:
            logger.warning('Could not read cover from %s: %s', audio_path.name, exc)
            return None
        if not tags.cover:
            return None

        cover_hash = hashlib.sha256(tags.cover).hexdigest()
        key = cover_content_key(scope, cover_hash)
        cached = self._find(key)
        if cached is not None:
            return CoverResult(cached[0], cached[1], cover_hash)

        try:
            Image.MAX_IMAGE_PIXELS = _MAX_PIXELS
            image = Image.open(io.BytesIO(tags.cover))
            if image.width <= _MAX_SIZE and image.height <= _MAX_SIZE:
                media_type = _FORMAT_MEDIA_TYPES.get(image.format or '', tags.cover_mime)
                original = self._store_original(key, tags.cover, media_type)
                if original is not None:
                    return CoverResult(original[0], original[1], cover_hash)
            image = ImageOps.exif_transpose(image)
            if image.mode not in {'RGB', 'RGBA'}:
                image = image.convert('RGBA' if 'A' in image.getbands() else 'RGB')
            image.thumbnail(_THUMB_SIZE, Image.Resampling.LANCZOS)
            target = self.directory / f'{key}.webp'
            temporary = self._temporary_path(key)
            image.save(temporary, format='WEBP', quality=82, method=4)
            temporary.replace(target)
            return CoverResult(target, 'image/webp', cover_hash)
        except Exception as exc:
            logger.warning('Could not build cover thumbnail for %s: %s', audio_path.name, exc)
            original = self._store_original(key, tags.cover, tags.cover_mime)
            if original is None:
                return None
            return CoverResult(original[0], original[1], cover_hash)

    def cleanup_legacy(self) -> int:
        """Remove cache files left by the per-track naming used before v3.

        Files are named after one track and its etag in versions 1 and 2, so
        they cannot be shared. The current layout starts with ``p<scope>-``.
        """
        removed = 0
        for path in self.directory.iterdir():
            if not path.is_file():
                continue
            legacy = path.suffix.lower() in COVER_CACHE_EXTENSIONS and is_legacy_cover_key(path.stem)
            temporary = path.name.startswith('.') and path.name.endswith('.tmp')
            if not (legacy or temporary):
                continue
            try:
                path.unlink()
            except FileNotFoundError:
                continue
            except OSError as exc:
                logger.warning('Could not remove stale cover file %s: %s', path, exc)
            else:
                removed += 1
        return removed

    def _temporary_path(self, key: str) -> Path:
        # Concurrent requests for tracks with the same cover address the same
        # key, so the temporary name must stay unique.
        return self.directory / f'.{key}.{uuid4().hex}.tmp'

    def _find(self, key: str) -> tuple[Path, str] | None:
        for extension, media_type in _MEDIA_TYPES.items():
            path = self.directory / f'{key}{extension}'
            if path.is_file():
                return path, media_type
        return None

    def _store_original(self, key: str, data: bytes, mime: str | None) -> tuple[Path, str] | None:
        media_type = mime or get_image_type(data)
        extension = _MEDIA_EXTENSIONS.get(media_type)
        if extension is None:
            return None
        target = self.directory / f'{key}{extension}'
        if not target.is_file():
            temporary = self._temporary_path(key)
            temporary.write_bytes(data)
            temporary.replace(target)
        return target, media_type
