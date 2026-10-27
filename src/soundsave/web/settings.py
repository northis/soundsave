"""Filesystem layout and web-specific settings."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..config import Config, ConfigError


@dataclass(frozen=True)
class WebSettings:
    """Resolved paths for the web library.

    ``music_dir == target_dir`` is the legacy flat-archive mode: the archive
    root itself is the single playlist. Otherwise every immediate subdirectory
    of ``music_dir`` is a playlist, and ``target_dir`` is the one syncable
    playlist.
    """

    music_dir: Path
    target_dir: Path
    data_dir: Path
    host: str = '127.0.0.1'
    port: int = 8765
    main_playlist: str | None = None

    @classmethod
    def from_config(cls, config: Config) -> WebSettings:
        music_dir = (config.music_dir or config.target_dir).expanduser().resolve()
        target_dir = config.target_dir.expanduser().resolve()
        data_dir = (config.data_dir or (target_dir / '.soundsave')).expanduser().resolve()
        if target_dir != music_dir:
            try:
                target_dir.relative_to(music_dir)
            except ValueError as exc:
                raise ConfigError('TARGET_DIR must be inside MUSIC_DIR') from exc
        return cls(
            music_dir=music_dir,
            target_dir=target_dir,
            data_dir=data_dir,
            host=config.web_host,
            port=config.web_port,
            main_playlist=config.main_playlist,
        )

    @property
    def db_path(self) -> Path:
        return self.data_dir / 'library.sqlite3'

    @property
    def covers_dir(self) -> Path:
        return self.data_dir / 'covers'

    @property
    def is_multi_playlist(self) -> bool:
        return self.target_dir != self.music_dir

    @property
    def main_playlist_name(self) -> str:
        if self.main_playlist:
            return self.main_playlist
        return self.target_dir.name or 'Main'

    def ensure(self) -> None:
        """Validate the music root and create the writable directories."""
        if not self.music_dir.exists():
            raise ConfigError(f'MUSIC_DIR does not exist: {self.music_dir}')
        if not self.music_dir.is_dir():
            raise ConfigError(f'MUSIC_DIR is not a directory: {self.music_dir}')
        if self.target_dir != self.music_dir:
            if self.target_dir.parent != self.music_dir:
                raise ConfigError('TARGET_DIR must be an immediate subfolder of MUSIC_DIR')
            self.target_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.covers_dir.mkdir(parents=True, exist_ok=True)

    def playlist_path(self, folder_name: str) -> Path:
        """Resolve a playlist folder name, keeping it inside ``music_dir``."""
        if folder_name == '.':
            return self.music_dir
        if not folder_name or Path(folder_name).name != folder_name:
            raise ValueError(f'Invalid playlist folder: {folder_name!r}')
        path = (self.music_dir / folder_name).resolve()
        try:
            path.relative_to(self.music_dir)
        except ValueError as exc:
            raise ValueError(f'Playlist escapes MUSIC_DIR: {folder_name!r}') from exc
        return path
