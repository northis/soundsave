from __future__ import annotations

import io
from pathlib import Path

import pytest

pytest.importorskip('PIL')

from PIL import Image

from soundsave.web.cover_cache import (
    COVER_CACHE_VERSION,
    cover_cache_key,
    cover_content_key,
    playlist_cover_scope,
)
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

    result = cache.get(audio, playlist_folder='Main')

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

    result = cache.get(audio, playlist_folder='Main')

    assert result is not None
    assert result.media_type == 'image/png'
    assert result.path.read_bytes() == cover


def test_cover_at_the_limit_is_not_rescaled(tmp_path: Path) -> None:
    cover = make_image((1000, 700))
    audio = tmp_path / 'track.mp3'
    make_mp3(audio, 'Title', 'Artist', cover=cover)
    cache = CoverCache(tmp_path / 'covers')

    result = cache.get(audio, playlist_folder='Main')

    assert result is not None
    assert result.media_type == 'image/png'
    assert result.path.read_bytes() == cover


def test_identical_covers_in_a_playlist_share_one_file(tmp_path: Path) -> None:
    cover = make_image((600, 400))
    first = tmp_path / 'one.mp3'
    second = tmp_path / 'two.mp3'
    make_mp3(first, 'One', 'Artist', cover=cover)
    make_mp3(second, 'Two', 'Artist', cover=cover)
    cache = CoverCache(tmp_path / 'covers')

    one = cache.get(first, playlist_folder='Main')
    two = cache.get(second, playlist_folder='Main')

    assert one is not None
    assert two is not None
    assert one.path == two.path
    assert one.cover_hash == two.cover_hash
    assert len([path for path in cache.directory.iterdir() if path.is_file()]) == 1


def test_the_same_cover_in_another_playlist_gets_its_own_file(tmp_path: Path) -> None:
    cover = make_image((600, 400))
    audio = tmp_path / 'track.mp3'
    make_mp3(audio, 'Title', 'Artist', cover=cover)
    cache = CoverCache(tmp_path / 'covers')

    main = cache.get(audio, playlist_folder='Main')
    chill = cache.get(audio, playlist_folder='Chill')

    assert main is not None
    assert chill is not None
    assert main.cover_hash == chill.cover_hash
    assert main.path != chill.path
    assert len([path for path in cache.directory.iterdir() if path.is_file()]) == 2


def test_known_cover_hash_reuses_the_file(tmp_path: Path) -> None:
    cover = make_image((600, 400))
    audio = tmp_path / 'track.mp3'
    make_mp3(audio, 'Title', 'Artist', cover=cover)
    cache = CoverCache(tmp_path / 'covers')

    first = cache.get(audio, playlist_folder='Main')
    assert first is not None
    second = cache.get(audio, playlist_folder='Main', cover_hash=first.cover_hash)

    assert second is not None
    assert second.path == first.path


def test_cleanup_legacy_removes_per_track_files(tmp_path: Path) -> None:
    audio = tmp_path / 'track.mp3'
    cache = CoverCache(tmp_path / 'covers')
    legacy = cache.directory / f'{cover_cache_key(audio, "etag-1", version=1)}.webp'
    current = cache.directory / f'{cover_content_key(playlist_cover_scope("Main"), "a" * 64)}.webp'
    legacy.write_bytes(b'legacy')
    current.write_bytes(b'current')

    removed = cache.cleanup_legacy()

    assert removed == 1
    assert not legacy.exists()
    assert current.is_file()


def test_cover_cache_versions_use_different_keys(tmp_path: Path) -> None:
    audio = tmp_path / 'track.mp3'

    current = cover_cache_key(audio, 'etag-1')
    legacy = cover_cache_key(audio, 'etag-1', version=1)

    assert current != legacy
    assert COVER_CACHE_VERSION > 1
