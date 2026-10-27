"""Small library fixtures shared by the web tests."""

from __future__ import annotations

import base64
from pathlib import Path

from mutagen.id3 import APIC, ID3, TALB, TIT2, TPE1

from soundsave.config import Config

# A 1x1 PNG; Pillow accepts it and the cover endpoint can build a thumbnail.
COVER = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=',
)


def make_mp3(
    path: Path,
    title: str,
    artist: str = 'Artist',
    *,
    album: str | None = 'Album',
    cover: bytes | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = bytes((0xFF, 0xFB, 0x90, 0x00))
    path.write_bytes((header + b'\x00' * 413) * 20)
    tags = ID3()
    tags.add(TIT2(encoding=3, text=title))
    tags.add(TPE1(encoding=3, text=[artist]))
    if album:
        tags.add(TALB(encoding=3, text=album))
    if cover:
        tags.add(APIC(encoding=3, mime='image/png', type=3, data=cover))
    tags.save(path)


def make_library(root: Path) -> tuple[Path, Path, Path]:
    music = root / 'music'
    main = music / 'Main'
    chill = music / 'Chill'
    main.mkdir(parents=True)
    chill.mkdir()
    make_mp3(main / '0002. The Gathering - Strange Machines.mp3', 'Strange Machines', 'The Gathering')
    make_mp3(main / '0001. The Gathering - Saturnine.mp3', 'Saturnine', 'The Gathering', cover=COVER)
    make_mp3(chill / '01 - Someone - Something.mp3', 'Something', 'Someone')
    return music, main, chill


def make_config(
    root: Path,
    *,
    main: str | None = 'Main',
    yandex_token: str | None = None,
    playlist_url: str | None = None,
) -> Config:
    music = root / 'music'
    target = music / main if main else music
    return Config(
        yandex_token=yandex_token,
        zvuk_token=None,
        playlist_url=playlist_url,
        target_dir=target,
        music_dir=music,
        data_dir=root / 'data',
    )
