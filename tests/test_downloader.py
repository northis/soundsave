from __future__ import annotations

from pathlib import Path

import pytest

from soundsave.downloader import DownloadError, probe_audio, verify_audio


def make_mp3(path: Path, *, bitrate_index: int, frame_length: int, frames: int = 20) -> None:
    header = bytes((0xFF, 0xFB, bitrate_index << 4, 0x00))
    path.write_bytes((header + b'\x00' * (frame_length - 4)) * frames)


def test_probe_mp3_320(tmp_path: Path) -> None:
    path = tmp_path / 'track.mp3'
    make_mp3(path, bitrate_index=14, frame_length=1044)

    quality = probe_audio(path)
    assert quality.codec == 'mp3'
    assert quality.bitrate_kbps == 320
    assert quality.lossless is False


def test_verify_accepts_320(tmp_path: Path) -> None:
    path = tmp_path / 'track.mp3'
    make_mp3(path, bitrate_index=14, frame_length=1044)

    quality = verify_audio(path, expected_codec='mp3')
    assert quality.bitrate_kbps == 320


def test_verify_rejects_128(tmp_path: Path) -> None:
    path = tmp_path / 'track.mp3'
    make_mp3(path, bitrate_index=9, frame_length=417)

    with pytest.raises(DownloadError, match='Expected 320 kbps MP3'):
        verify_audio(path, expected_codec='mp3')


def test_verify_rejects_wrong_codec(tmp_path: Path) -> None:
    path = tmp_path / 'track.mp3'
    make_mp3(path, bitrate_index=14, frame_length=1044)

    with pytest.raises(DownloadError, match='Expected FLAC'):
        verify_audio(path, expected_codec='flac')


def test_probe_rejects_unknown_file(tmp_path: Path) -> None:
    path = tmp_path / 'track.bin'
    path.write_bytes(b'not audio')

    with pytest.raises(DownloadError, match='Unrecognised audio file'):
        probe_audio(path)
