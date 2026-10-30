from __future__ import annotations

import io
from pathlib import Path

import pytest

pytest.importorskip('PIL')

from PIL import Image

from soundsave.web.cover_cache import COVER_CACHE_VERSION, cover_cache_key
from soundsave.web.covers import CoverCache

from .web_helpers import make_mp3


def make_image(size: tuple[int, int], image_format: str = 'PNG') -> bytes:
    buffer = io.BytesIO()
    Image.new('RGB', size, (120, 40, 40)).save(buffer, format=image_format)
    return buffer.getvalue()


def test_large_cover_is_scaled_to_1000(tmp_path: Path) -> None:
    audio = tmp_path / 'track.mp3'
    make_mp3(audio, 'Title', 'Artist', cover=make_image((2400, 1600)))
    cache = CoverCache(tmp_path / 'covers')

    result = cache.get(audio, 'etag-1')

    assert result is not None
    assert result.media_type == 'image/webp'
    with Image.open(result.path) as image:
        assert image.format == 'WEBP'
        width, height = image.size
        assert max(width, height) == 1000
        assert width / height == pytest.approx(2400 / 1600, rel=0.01)


def test_small_cover_is_served_as_original_bytes(tmp_path: Path) -> None:
    cover = make_image((600, 400))
    audio = tmp_path / 'track.mp3'
    make_mp3(audio, 'Title', 'Artist', cover=cover)
    cache = CoverCache(tmp_path / 'covers')

    result = cache.get(audio, 'etag-1')

    assert result is not None
    assert result.media_type == 'image/png'
    assert result.path.read_bytes() == cover


def test_cover_at_the_limit_is_not_rescaled(tmp_path: Path) -> None:
    cover = make_image((1000, 700))
    audio = tmp_path / 'track.mp3'
    make_mp3(audio, 'Title', 'Artist', cover=cover)
    cache = CoverCache(tmp_path / 'covers')

    result = cache.get(audio, 'etag-1')

    assert result is not None
    assert result.media_type == 'image/png'
    assert result.path.read_bytes() == cover


def test_cover_cache_versions_use_different_keys(tmp_path: Path) -> None:
    audio = tmp_path / 'track.mp3'

    current = cover_cache_key(audio, 'etag-1')
    legacy = cover_cache_key(audio, 'etag-1', version=1)

    assert current != legacy
    assert COVER_CACHE_VERSION > 1
