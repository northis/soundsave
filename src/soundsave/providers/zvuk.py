"""Zvuk Music catalogue provider built on :mod:`zvuk_music`."""

from __future__ import annotations

import logging

from zvuk_music import Client, Quality, StreamQuality
from zvuk_music.exceptions import ZvukMusicError

from ..domain import Candidate, TrackQuery
from .base import ProviderError, ProviderNotConfiguredError

logger = logging.getLogger(__name__)

_STREAM_QUALITY = {
    'flac': StreamQuality.FLAC,
    'high': StreamQuality.HIGH,
    'mid': StreamQuality.MID,
}

_GRAPHQL_QUALITY = {
    'flac': Quality.FLAC,
    'high': Quality.HIGH,
    'mid': Quality.MID,
}


class ZvukProvider:
    """Search Zvuk and resolve direct (non-DRM) stream URLs."""

    name = 'zvuk'

    def __init__(
        self,
        token: str | None,
        *,
        timeout: float = 15.0,
        rate_limit: int | None = 2,
    ) -> None:
        if not token:
            raise ProviderNotConfiguredError('ZVUK_TOKEN is not set')
        self._client = Client(token=token, timeout=timeout, rate_limit=rate_limit)
        try:
            self._client.init()
        except ZvukMusicError as exc:
            raise ProviderError(f'Zvuk authentication failed: {exc}') from exc

    def search(self, query: TrackQuery, limit: int = 10) -> list[Candidate]:
        """Search Zvuk by ``artist title`` and enrich results with FLAC info."""
        search_query = f'{query.artist} {query.title}'.strip()
        try:
            result = self._client.search(
                search_query,
                limit=limit,
                artists=False,
                releases=False,
                playlists=False,
                podcasts=False,
                episodes=False,
                profiles=False,
                books=False,
            )
        except ZvukMusicError as exc:
            raise ProviderError(f'Zvuk search failed for {search_query!r}: {exc}') from exc

        items = result.tracks.items if result is not None and result.tracks is not None else []
        candidates = [
            Candidate(
                provider=self.name,
                provider_id=item.id,
                title=item.title,
                artists=tuple(artist.title for artist in item.artists),
                album=item.release.title if item.release is not None else None,
                duration_s=float(item.duration) if item.duration else None,
            )
            for item in items
        ]
        if not candidates:
            return []

        try:
            full_tracks = self._client.get_tracks([candidate.provider_id for candidate in candidates])
        except ZvukMusicError as exc:
            logger.warning('Could not fetch full Zvuk track info: %s', exc)
            return candidates

        by_id = {track.id: track for track in full_tracks}
        for candidate in candidates:
            full = by_id.get(candidate.provider_id)
            if full is None:
                continue
            candidate.has_flac = bool(full.has_flac)
            if full.duration:
                candidate.duration_s = float(full.duration)
            if not candidate.artists:
                candidate.artists = tuple(full.artist_names)
            if candidate.album is None and full.release is not None:
                candidate.album = full.release.title
        return candidates

    def get_stream_url(self, candidate: Candidate, quality: str) -> str | None:
        """Resolve a stream URL, returning ``None`` when unavailable.

        The Tiny API returns a direct, non-DRM URL. It is sometimes blocked by
        Zvuk's anti-bot protection, so fall back to the GraphQL stream URLs.
        """
        stream_quality = _STREAM_QUALITY.get(quality)
        graphql_quality = _GRAPHQL_QUALITY.get(quality)
        if stream_quality is None or graphql_quality is None:
            raise ValueError(f'Unknown stream quality: {quality}')

        try:
            direct = self._client.get_direct_stream_url(candidate.provider_id, stream_quality)
            if direct is not None and direct.stream:
                return direct.stream
        except ZvukMusicError as exc:
            logger.debug('Zvuk direct stream %s failed for %s: %s', quality, candidate.provider_id, exc)

        try:
            url = self._client.get_stream_url(candidate.provider_id, graphql_quality)
        except ZvukMusicError as exc:
            logger.debug('Zvuk GraphQL stream %s failed for %s: %s', quality, candidate.provider_id, exc)
            return None
        return url or None
