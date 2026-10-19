"""Provider protocol shared by catalogue backends."""

from __future__ import annotations

from typing import Protocol

from ..domain import Candidate, TrackQuery


class ProviderError(RuntimeError):
    """Raised when a provider cannot complete a request."""


class ProviderNotConfiguredError(ProviderError):
    """Raised when a provider is used without its credentials."""


class MusicProvider(Protocol):
    """Minimal interface needed by the audit and upgrade commands."""

    name: str

    def search(self, query: TrackQuery, limit: int = 10) -> list[Candidate]:
        """Return catalogue candidates for *query*."""
        ...

    def get_stream_url(self, candidate: Candidate, quality: str) -> str | None:
        """Return a direct stream URL for ``flac``, ``high`` or ``mid``."""
        ...
