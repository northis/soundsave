"""Downloading and validating audio files."""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
from pathlib import Path

import requests
from mutagen import File as MutagenFile
from mutagen.flac import FLAC
from mutagen.mp3 import MP3

from .domain import AudioQuality

logger = logging.getLogger(__name__)

USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
)
_CHUNK_SIZE = 1 << 16
_MIN_320_KBPS = 300


class DownloadError(RuntimeError):
    """Raised when a stream cannot be downloaded or validated."""


def has_ffmpeg() -> bool:
    """Return whether ``ffmpeg`` is available in ``PATH``."""
    return shutil.which('ffmpeg') is not None


def download_file(
    url: str,
    destination: Path,
    *,
    timeout: float = 60.0,
    retries: int = 3,
) -> Path:
    """Download *url* to *destination*, retrying transient failures."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    headers = {
        'User-Agent': USER_AGENT,
        'Referer': 'https://zvuk.com/',
        'Accept': '*/*',
    }
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            with requests.get(url, headers=headers, stream=True, timeout=timeout) as response:
                response.raise_for_status()
                with destination.open('wb') as file:
                    for chunk in response.iter_content(_CHUNK_SIZE):
                        if chunk:
                            file.write(chunk)
            if destination.stat().st_size == 0:
                raise DownloadError(f'Empty response for {url}')
        except (requests.RequestException, OSError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(2 ** (attempt - 1))
        else:
            return destination
    raise DownloadError(f'Failed to download {url}: {last_error}')


def probe_audio(path: Path) -> AudioQuality:
    """Read codec and bitrate from an audio file."""
    audio = MutagenFile(path)
    if audio is None:
        raise DownloadError(f'Unrecognised audio file: {path}')
    info = audio.info
    if isinstance(audio, FLAC):
        bitrate = getattr(info, 'bitrate', None)
        return AudioQuality('flac', round(bitrate / 1000) if bitrate else None, lossless=True)
    if isinstance(audio, MP3):
        bitrate = getattr(info, 'bitrate', None)
        return AudioQuality('mp3', round(bitrate / 1000) if bitrate else None)
    suffix = path.suffix.lower().lstrip('.') or 'unknown'
    bitrate = getattr(info, 'bitrate', None)
    return AudioQuality(suffix, round(bitrate / 1000) if bitrate else None)


def verify_audio(
    path: Path,
    *,
    expected_codec: str,
    expected_duration_s: float | None = None,
    duration_tolerance: float = 5.0,
) -> AudioQuality:
    """Validate that *path* is playable and has the expected codec."""
    quality = probe_audio(path)
    if expected_codec == 'flac' and not quality.lossless:
        raise DownloadError(f'Expected FLAC, got {quality.label}')
    if expected_codec == 'mp3':
        if quality.codec != 'mp3':
            raise DownloadError(f'Expected MP3, got {quality.label}')
        if (quality.bitrate_kbps or 0) < _MIN_320_KBPS:
            raise DownloadError(f'Expected 320 kbps MP3, got {quality.label}')

    if expected_duration_s is not None:
        audio = MutagenFile(path)
        actual = float(audio.info.length) if audio is not None and audio.info.length else None
        if actual is not None and abs(actual - expected_duration_s) > duration_tolerance:
            raise DownloadError(
                f'Duration mismatch: expected {expected_duration_s:.1f}s, got {actual:.1f}s',
            )
    return quality


def transcode_flac_to_mp3(source: Path, destination: Path, *, bitrate: int = 320) -> Path:
    """Transcode *source* to MP3 using ``ffmpeg``."""
    ffmpeg = shutil.which('ffmpeg')
    if ffmpeg is None:
        raise DownloadError(
            'ffmpeg is required to transcode FLAC to MP3. Install ffmpeg or use --format flac.',
        )
    command = [
        ffmpeg,
        '-y',
        '-loglevel',
        'error',
        '-i',
        str(source),
        '-vn',
        '-codec:a',
        'libmp3lame',
        '-b:a',
        f'{bitrate}k',
        str(destination),
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        raise DownloadError(f'ffmpeg failed: {exc.stderr.strip()}') from exc
    return destination
