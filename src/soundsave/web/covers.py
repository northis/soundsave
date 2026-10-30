"""Embedded cover extraction and thumbnail caching."""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

from ..archive import get_image_type, read_tag_data
from .cover_cache import cover_cache_key

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


class CoverCache:
    """Extract front covers lazily and cache them up to 1000 px.

    Covers larger than 1000 px on either side are scaled down to 1000 px and
    stored as WebP. Smaller covers are served as-is, without re-encoding.
    """

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)

    def get(self, audio_path: Path, etag: str) -> CoverResult | None:
        key = cover_cache_key(audio_path, etag)
        cached = self._find(key)
        if cached is not None:
            return cached

        try:
            tags = read_tag_data(audio_path, include_cover=True)
        except Exception as exc:
            logger.warning('Could not read cover from %s: %s', audio_path.name, exc)
            return None
        if not tags.cover:
            return None

        try:
            Image.MAX_IMAGE_PIXELS = _MAX_PIXELS
            image = Image.open(io.BytesIO(tags.cover))
            if image.width <= _MAX_SIZE and image.height <= _MAX_SIZE:
                media_type = _FORMAT_MEDIA_TYPES.get(image.format or '', tags.cover_mime)
                original = self._store_original(key, tags.cover, media_type)
                if original is not None:
                    return original
            image = ImageOps.exif_transpose(image)
            if image.mode not in {'RGB', 'RGBA'}:
                image = image.convert('RGBA' if 'A' in image.getbands() else 'RGB')
            image.thumbnail(_THUMB_SIZE, Image.Resampling.LANCZOS)
            target = self.directory / f'{key}.webp'
            temporary = self.directory / f'.{key}.tmp'
            image.save(temporary, format='WEBP', quality=82, method=4)
            temporary.replace(target)
            return CoverResult(target, 'image/webp')
        except Exception as exc:
            logger.warning('Could not build cover thumbnail for %s: %s', audio_path.name, exc)
            return self._store_original(key, tags.cover, tags.cover_mime)

    def _find(self, key: str) -> CoverResult | None:
        for extension, media_type in _MEDIA_TYPES.items():
            path = self.directory / f'{key}{extension}'
            if path.is_file():
                return CoverResult(path, media_type)
        return None

    def _store_original(self, key: str, data: bytes, mime: str | None) -> CoverResult | None:
        media_type = mime or get_image_type(data)
        extension = _MEDIA_EXTENSIONS.get(media_type)
        if extension is None:
            return None
        target = self.directory / f'{key}{extension}'
        if not target.is_file():
            temporary = self.directory / f'.{key}.raw.tmp'
            temporary.write_bytes(data)
            temporary.replace(target)
        return CoverResult(target, media_type)
