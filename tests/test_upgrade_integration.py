from __future__ import annotations

import shutil
from pathlib import Path

from mutagen.id3 import ID3, TALB, TIT2, TPE1

from soundsave.archive import read_local_track, read_tag_data
from soundsave.config import Config
from soundsave.domain import Candidate
from soundsave.manifest import Manifest
from soundsave.upgrade import UpgradeOptions, run_upgrade


def make_mp3(path: Path, *, bitrate_index: int, frame_length: int, frames: int = 20) -> None:
    header = bytes((0xFF, 0xFB, bitrate_index << 4, 0x00))
    path.write_bytes((header + b'\x00' * (frame_length - 4)) * frames)


def write_tags(path: Path) -> None:
    tags = ID3()
    tags.add(TIT2(encoding=3, text='Saturnine'))
    tags.add(TPE1(encoding=3, text=['The Gathering']))
    tags.add(TALB(encoding=3, text='Mandylion'))
    tags.save(path)


class FakeProvider:
    name = 'fake'

    def __init__(self, duration: float) -> None:
        self.duration = duration

    def search(self, query: object, limit: int = 10) -> list[Candidate]:
        return [
            Candidate(
                provider='fake',
                provider_id='123',
                title='Saturnine',
                artists=('The Gathering',),
                album='Mandylion',
                duration_s=self.duration,
            ),
        ]

    def get_stream_url(self, candidate: Candidate, quality: str) -> str:
        return f'https://example.invalid/{quality}'


def test_full_replace_pipeline(tmp_path: Path, monkeypatch: object) -> None:
    archive = tmp_path / 'archive'
    archive.mkdir()
    original = archive / '0001. The Gathering - Saturnine.mp3'
    make_mp3(original, bitrate_index=9, frame_length=417)
    write_tags(original)
    original_duration = read_local_track(original).duration_s

    downloaded = tmp_path / 'downloaded.mp3'
    make_mp3(downloaded, bitrate_index=14, frame_length=1044)

    def fake_download(url: str, destination: Path, **kwargs: object) -> Path:
        shutil.copy2(downloaded, destination)
        return destination

    monkeypatch.setattr('soundsave.downloader.download_file', fake_download)  # type: ignore[attr-defined]

    config = Config(
        yandex_token=None,
        zvuk_token='token',
        playlist_url=None,
        target_dir=archive,
        delay=0.0,
    )
    rows = run_upgrade(
        config,
        FakeProvider(original_duration or 0.5),
        UpgradeOptions(apply=True, delay=0.0),
    )

    assert len(rows) == 1
    assert rows[0].status == 'replaced'
    assert rows[0].new_quality == 'MP3 320 kbps'

    replaced = read_local_track(original)
    assert replaced.quality.bitrate_kbps == 320
    tags = read_tag_data(original)
    assert tags.title == 'Saturnine'
    assert tags.artists == ['The Gathering']

    backup_root = archive / '.soundsave' / 'backups'
    backups = list(backup_root.rglob('*.mp3'))
    assert len(backups) == 1
    assert read_local_track(backups[0]).quality.bitrate_kbps == 128

    with Manifest(archive / '.soundsave' / 'manifest.sqlite3') as manifest:
        record = manifest.get(str(original))
        assert record is not None
        assert record.status == 'replaced'
        assert record.old_bitrate == 128
        assert record.new_bitrate == 320
