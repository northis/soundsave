"""Embedded cover extraction and thumbnail caching."""

from __future__ import annotations

import hashlib
import io
import logging
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

from ..archive import get_image_type, read_tag_data

logger = logging.getLogger(__name__)

_THUMB_SIZE = (256, 256)
_MAX_PIXELS = 50_000_000


@dataclass(frozen=True)
class CoverResult:
    path: Path
    media_type: str


class CoverCache:
    """Extract front covers lazily and store small WebP thumbnails."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)

    def get(self, audio_path: Path, etag: str) -> CoverResult | None:
        key = hashlib.sha256(f'{audio_path.resolve()}:{etag}'.encode()).hexdigest()[:24]
        cached = self.directory / f'{key}.webp'
        if cached.is_file():
            return CoverResult(cached, 'image/webp')

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
            image = ImageOps.exif_transpose(image)
            if image.mode not in {'RGB', 'RGBA'}:
                image = image.convert('RGBA' if 'A' in image.getbands() else 'RGB')
            image.thumbnail(_THUMB_SIZE, Image.Resampling.LANCZOS)
            temporary = self.directory / f'.{key}.tmp'
            image.save(temporary, format='WEBP', quality=82, method=4)
            temporary.replace(cached)
            return CoverResult(cached, 'image/webp')
        except Exception as exc:
            logger.warning('Could not build cover thumbnail for %s: %s', audio_path.name, exc)
            return self._store_original(key, tags.cover, tags.cover_mime)

    def _store_original(self, key: str, data: bytes, mime: str | None) -> CoverResult | None:
        media_type = mime or get_image_type(data)
        extension = '.png' if media_type == 'image/png' else '.jpg'
        target = self.directory / f'{key}{extension}'
        if not target.is_file():
            temporary = self.directory / f'.{key}.raw.tmp'
            temporary.write_bytes(data)
            temporary.replace(target)
        return CoverResult(target, media_type)
