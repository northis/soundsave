"""Metadata catalogue protocol and fallback composition."""

from __future__ import annotations

from typing import Protocol

from .domain import MetadataCandidate, MetadataMatch, TrackQuery

CONFIDENT_STATUSES = frozenset(
    {
        'matched_woaf',
        'matched_playlist',
        'matched_search',
        'matched_discogs',
    },
)


class MetadataCatalog(Protocol):
    """Interface shared by Yandex Music and Discogs catalogues."""

    def find(
        self,
        queries: list[TrackQuery],
        *,
        url: str | None,
        search: bool,
        min_score: float,
        tolerance: float,
    ) -> MetadataMatch:
        """Return the best match for one local file."""
        ...

    def has_cover(self, candidate: MetadataCandidate) -> bool:
        """Return whether the catalogue has a cover for *candidate*."""
        ...

    def fetch_cover(self, candidate: MetadataCandidate) -> tuple[bytes, str] | None:
        """Download the cover for *candidate*."""
        ...

    def fetch_lyrics(self, candidate: MetadataCandidate) -> str | None:
        """Download text lyrics for *candidate*, if the catalogue has them."""
        ...


class CompositeCatalog:
    """Try the primary catalogue first and fall back to the second one."""

    def __init__(self, primary: MetadataCatalog, fallback: MetadataCatalog) -> None:
        self.primary = primary
        self.fallback = fallback

    def find(
        self,
        queries: list[TrackQuery],
        *,
        url: str | None,
        search: bool,
        min_score: float,
        tolerance: float,
    ) -> MetadataMatch:
        primary = self.primary.find(
            queries,
            url=url,
            search=search,
            min_score=min_score,
            tolerance=tolerance,
        )
        if primary.status in CONFIDENT_STATUSES or not search:
            return primary
        fallback = self.fallback.find(
            queries,
            url=url,
            search=search,
            min_score=min_score,
            tolerance=tolerance,
        )
        if fallback.status in CONFIDENT_STATUSES:
            return fallback
        if primary.status == 'ambiguous':
            return primary
        if fallback.status == 'ambiguous':
            return fallback
        if primary.status == 'error':
            return primary
        if fallback.status == 'error':
            return fallback
        return primary

    def has_cover(self, candidate: MetadataCandidate) -> bool:
        return self._catalog(candidate).has_cover(candidate)

    def fetch_cover(self, candidate: MetadataCandidate) -> tuple[bytes, str] | None:
        return self._catalog(candidate).fetch_cover(candidate)

    def fetch_lyrics(self, candidate: MetadataCandidate) -> str | None:
        return self._catalog(candidate).fetch_lyrics(candidate)

    def _catalog(self, candidate: MetadataCandidate) -> MetadataCatalog:
        if candidate.provider == 'discogs':
            return self.fallback
        return self.primary
