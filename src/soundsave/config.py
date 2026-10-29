"""Application configuration loaded from environment variables and ``.env``."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


class ConfigError(RuntimeError):
    """Raised when a required setting is missing or has an invalid value."""


def _env(*names: str, default: str | None = None) -> str | None:
    """Return the first non-empty environment variable from *names*."""
    for name in names:
        value = os.getenv(name)
        if value is not None and value.strip():
            return value.strip()
    return default


def _parse_int(name: str, raw: str | None, default: int, minimum: int = 1) -> int:
    if raw is None or raw == '':
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f'{name} must be an integer, got {raw!r}') from exc
    if value < minimum:
        raise ConfigError(f'{name} must be >= {minimum}, got {value}')
    return value


def _parse_float(name: str, raw: str | None, default: float, minimum: float = 0.0) -> float:
    if raw is None or raw == '':
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f'{name} must be a number, got {raw!r}') from exc
    if value < minimum:
        raise ConfigError(f'{name} must be >= {minimum}, got {value}')
    return value


@dataclass(frozen=True)
class Config:
    """Resolved application settings.

    Secrets and paths come from the environment (or a ``.env`` file); command
    line options may override individual fields with :func:`dataclasses.replace`.
    """

    yandex_token: str | None
    zvuk_token: str | None
    playlist_url: str | None
    target_dir: Path
    music_dir: Path | None = None
    main_playlist: str | None = None
    data_dir: Path | None = None
    web_host: str = '127.0.0.1'
    web_port: int = 8765
    max_downloads: int = 20
    min_score: float = 0.85
    duration_tolerance: float = 5.0
    delay: float = 1.0
    discogs_token: str | None = None
    discogs_delay: float = 2.0
    music_dir_configured: bool = False

    @classmethod
    def load(cls, env_file: str | Path | None = None) -> Config:
        """Load settings from *env_file* or the default ``.env`` search path."""
        if env_file is not None:
            load_dotenv(env_file, override=True)
        else:
            load_dotenv()

        music_raw = _env('MUSIC_DIR')
        target_raw = _env('TARGET_DIR')
        main_playlist = _env('MAIN_PLAYLIST')
        if main_playlist and (Path(main_playlist).name != main_playlist or main_playlist in {'.', '..'}):
            raise ConfigError(f'MAIN_PLAYLIST must be a single folder name, got {main_playlist!r}')

        music_dir = Path(music_raw).expanduser() if music_raw else None
        if target_raw:
            target_dir = Path(target_raw).expanduser()
        elif music_dir is not None and main_playlist:
            target_dir = music_dir / main_playlist
        elif music_dir is not None:
            target_dir = music_dir
        else:
            raise ConfigError('TARGET_DIR or MUSIC_DIR is not set')

        if music_dir is None:
            music_dir = target_dir
        if music_raw and target_raw:
            try:
                target_dir.resolve().relative_to(music_dir.resolve())
            except ValueError as exc:
                raise ConfigError('TARGET_DIR must be inside MUSIC_DIR') from exc

        data_raw = _env('DATA_DIR')
        data_dir = Path(data_raw).expanduser() if data_raw else target_dir / '.soundsave'
        web_port = _parse_int('SOUNDSAVE_PORT', _env('SOUNDSAVE_PORT'), default=8765)

        max_downloads = _parse_int(
            'MAX_DOWNLOADS',
            _env('MAX_DOWNLOADS', 'SOUNDSAVE_MAX_DOWNLOADS'),
            default=20,
        )
        min_score = _parse_float(
            'SOUNDSAVE_MIN_SCORE',
            _env('SOUNDSAVE_MIN_SCORE'),
            default=0.85,
        )
        if min_score > 1.0:
            raise ConfigError(f'SOUNDSAVE_MIN_SCORE must be <= 1.0, got {min_score}')
        duration_tolerance = _parse_float(
            'SOUNDSAVE_DURATION_TOLERANCE',
            _env('SOUNDSAVE_DURATION_TOLERANCE'),
            default=5.0,
            minimum=0.1,
        )
        delay = _parse_float(
            'SOUNDSAVE_DELAY',
            _env('SOUNDSAVE_DELAY'),
            default=1.0,
        )
        discogs_delay = _parse_float(
            'DISCOGS_DELAY',
            _env('DISCOGS_DELAY'),
            default=2.0,
            minimum=1.0,
        )

        return cls(
            yandex_token=_env('YANDEX_TOKEN', 'API_KEY'),
            zvuk_token=_env('ZVUK_TOKEN'),
            playlist_url=_env('PLAYLIST_URL'),
            target_dir=target_dir,
            music_dir=music_dir,
            music_dir_configured=music_raw is not None,
            main_playlist=main_playlist,
            data_dir=data_dir,
            web_host=_env('SOUNDSAVE_HOST', default='127.0.0.1') or '127.0.0.1',
            web_port=web_port,
            max_downloads=max_downloads,
            min_score=min_score,
            duration_tolerance=duration_tolerance,
            delay=delay,
            discogs_token=_env('DISCOGS_TOKEN'),
            discogs_delay=discogs_delay,
        )

    def require_yandex(self) -> tuple[str, str]:
        """Return ``(token, playlist_url)`` or raise :class:`ConfigError`."""
        if not self.yandex_token:
            raise ConfigError('YANDEX_TOKEN (or API_KEY) is not set')
        if not self.playlist_url:
            raise ConfigError('PLAYLIST_URL is not set')
        return self.yandex_token, self.playlist_url

    def require_zvuk(self) -> str:
        """Return the Zvuk token or raise :class:`ConfigError`."""
        if not self.zvuk_token:
            raise ConfigError('ZVUK_TOKEN is not set')
        return self.zvuk_token
