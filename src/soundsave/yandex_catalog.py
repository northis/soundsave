"""Yandex Music catalogue access for the tags command."""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from typing import cast

from yandex_music import Client, Track, YandexMusicModel
from yandex_music.exceptions import NotFoundError

from .archive import get_image_type
from .domain import MatchStatus, MetadataCandidate, MetadataMatch, TrackQuery
from .matching import normalize, rank_candidates, score_candidate
from .providers.base import ProviderError

logger = logging.getLogger(__name__)

_BATCH_SIZE = 100
_TRACK_URL_RE = re.compile(r'music\.yandex\.(?:ru|com)/album/(\d+)/track/(\d+)')


class YandexCatalogError(ProviderError):
    """Raised when the Yandex Music catalogue cannot be queried."""


def parse_playlist_url(url: str) -> tuple[str, int]:
    """Return ``(owner, kind)`` from a Yandex Music playlist URL."""
    match = re.match(r'https?://music\.yandex\.ru/users/([^/]+)/playlists/(\d+)', url)
    if match is None:
        raise ValueError(f'Unsupported playlist url: {url}')
    owner, kind = match.groups()
    return owner, int(kind)


def parse_track_url(url: str | None) -> str | None:
    """Return the Yandex Music track id from a track URL, if present."""
    if not url:
        return None
    match = _TRACK_URL_RE.search(url)
    return match.group(2) if match is not None else None


def full_title(obj: YandexMusicModel | None) -> str:
    """Return ``title`` with an optional ``version`` suffix."""
    if obj is None:
        return ''
    result = obj['title'] or ''
    version = obj['version']
    if version:
        result += f' ({version})'
    return result


def track_url(track: Track) -> str | None:
    """Return the canonical Yandex Music URL for *track*."""
    album = track.albums[0] if track.albums else None
    if album is None or album.id is None or track.id is None:
        return None
    return f'https://music.yandex.ru/album/{album.id}/track/{track.id}'


def candidate_from_track(track: Track) -> MetadataCandidate:
    """Map a Yandex Music track to :class:`MetadataCandidate`."""
    album = track.albums[0] if track.albums else None
    album_artists = tuple(artist.name for artist in (album.artists or []) if artist.name) if album else ()
    return MetadataCandidate(
        provider='yandex',
        provider_id=str(track.id),
        title=full_title(track),
        artists=tuple(artist.name for artist in (track.artists or []) if artist.name),
        album=full_title(album) if album is not None else None,
        duration_s=track.duration_ms / 1000 if track.duration_ms else None,
        album_artists=album_artists,
        year=album.year if album is not None else None,
        release_date=album.release_date if album is not None else None,
        genre=album.genre if album is not None else None,
        url=track_url(track),
        lyrics_available=bool(track.lyrics_info and track.lyrics_info.has_available_text_lyrics),
    )


def _key(artist: str, title: str) -> str:
    return f'{normalize(artist)}|{normalize(title)}'


def _fetch_tracks(client: Client, ids: list[str]) -> dict[str, Track]:
    result: dict[str, Track] = {}
    for start in range(0, len(ids), _BATCH_SIZE):
        chunk = ids[start : start + _BATCH_SIZE]
        try:
            fetched = client.tracks(chunk) or []
        except Exception as exc:
            logger.warning('Failed to fetch %d Yandex tracks: %s', len(chunk), exc)
            continue
        for track in fetched:
            if track is not None and track.id is not None:
                result[str(track.id)] = track
    return result


def _search_results(search: object) -> list[Track]:
    tracks = getattr(search, 'tracks', None)
    if tracks is None:
        return []
    results = getattr(tracks, 'results', None)
    return [track for track in results if track is not None] if results else []


def _duration_ok(left: float | None, right: float | None, tolerance: float) -> bool:
    if left is None or right is None:
        return True
    return abs(left - right) <= tolerance


def _matches_any(candidate: MetadataCandidate, queries: list[TrackQuery], tolerance: float) -> bool:
    for query in queries:
        scored = score_candidate(query, candidate, tolerance)
        if (
            scored.artist_score >= 0.80
            and scored.title_score >= 0.80
            and _duration_ok(query.duration_s, candidate.duration_s, tolerance)
        ):
            return True
    return False


def _best_by_duration(
    query: TrackQuery,
    candidates: list[MetadataCandidate],
    tolerance: float,
) -> MetadataMatch:
    scored = [score_candidate(query, candidate, tolerance) for candidate in candidates]
    if len(scored) == 1:
        candidate = scored[0]
        if _duration_ok(query.duration_s, candidate.duration_s, tolerance):
            return MetadataMatch(status='matched_playlist', candidate=candidate, score=candidate.score)
        return MetadataMatch(
            status='no_match',
            candidate=candidate,
            score=candidate.score,
            reason='duration mismatch',
        )
    if query.duration_s is None:
        return MetadataMatch(
            status='ambiguous',
            candidate=scored[0],
            score=scored[0].score,
            reason='multiple playlist candidates',
        )
    with_duration = [item for item in scored if item.duration_s is not None]
    if not with_duration:
        return MetadataMatch(
            status='ambiguous',
            candidate=scored[0],
            score=scored[0].score,
            reason='multiple playlist candidates without duration',
        )
    best = min(with_duration, key=lambda item: abs((item.duration_s or 0.0) - query.duration_s))
    if _duration_ok(query.duration_s, best.duration_s, tolerance):
        return MetadataMatch(status='matched_playlist', candidate=best, score=best.score)
    return MetadataMatch(
        status='ambiguous',
        candidate=best,
        score=best.score,
        reason='duration mismatch',
    )


class YandexCatalog:
    """Yandex Music playlist and search catalogue."""

    def __init__(self, client: Client, tracks: dict[str, Track]) -> None:
        self.client = client
        self.tracks = tracks
        self.by_id = {track_id: candidate_from_track(track) for track_id, track in tracks.items()}
        self.by_key: dict[str, list[MetadataCandidate]] = defaultdict(list)
        for candidate in self.by_id.values():
            self.by_key[_key(candidate.artist, candidate.title)].append(candidate)
        self.candidates = list(self.by_id.values())

    @classmethod
    def load(cls, token: str, playlist_url: str | None) -> YandexCatalog:
        """Create a catalogue and preload the configured playlist."""
        try:
            client = Client(token=token).init()
        except Exception as exc:
            raise YandexCatalogError(f'Failed to initialize the Yandex Music client: {exc}') from exc
        tracks: dict[str, Track] = {}
        if playlist_url:
            try:
                owner, kind = parse_playlist_url(playlist_url)
                playlist = client.users_playlists(kind, owner)
                ids = [str(item.id) for item in (playlist.tracks or []) if item is not None and item.id is not None]
                tracks = _fetch_tracks(client, ids)
            except Exception as exc:
                logger.warning('Failed to load the Yandex Music playlist: %s', exc)
        return cls(client, tracks)

    def find(
        self,
        queries: list[TrackQuery],
        *,
        url: str | None,
        search: bool,
        min_score: float,
        tolerance: float,
    ) -> MetadataMatch:
        """Find the best catalogue match for one local file."""
        if url:
            track_id = parse_track_url(url)
            if track_id:
                candidate = self._candidate(track_id)
                if candidate is not None and _matches_any(candidate, queries, tolerance):
                    return MetadataMatch(status='matched_woaf', candidate=candidate, score=1.0)
        for query in queries:
            match = self._find_in_playlist(query, tolerance)
            if match is not None:
                return match
        if search:
            return self._search(queries, min_score=min_score, tolerance=tolerance)
        return MetadataMatch(status='no_match', reason='not found in the playlist')

    def has_cover(self, candidate: MetadataCandidate) -> bool:
        """Return whether Yandex Music has a cover for *candidate*."""
        track = self._get_track(candidate.provider_id)
        return track is not None and track.cover_uri is not None

    def fetch_cover(self, candidate: MetadataCandidate) -> tuple[bytes, str] | None:
        """Download the original cover for *candidate*."""
        track = self._get_track(candidate.provider_id)
        if track is None or track.cover_uri is None:
            return None
        try:
            data = track.download_cover_bytes(size='orig')
        except Exception as exc:
            raise YandexCatalogError(f'Failed to download cover for track {candidate.provider_id}: {exc}') from exc
        if not data:
            return None
        return data, get_image_type(data)

    def fetch_lyrics(self, candidate: MetadataCandidate) -> str | None:
        """Download the text lyrics for *candidate*, if available."""
        if not candidate.lyrics_available:
            return None
        track = self._get_track(candidate.provider_id)
        if track is None:
            return None
        try:
            lyrics = track.get_lyrics(format_='TEXT')
        except NotFoundError:
            return None
        except Exception as exc:
            raise YandexCatalogError(f'Failed to download lyrics for track {candidate.provider_id}: {exc}') from exc
        if lyrics is None:
            return None
        text = lyrics.fetch_lyrics()
        return text.strip() or None

    def _candidate(self, track_id: str) -> MetadataCandidate | None:
        track = self._get_track(track_id)
        return candidate_from_track(track) if track is not None else None

    def _get_track(self, track_id: str) -> Track | None:
        track = self.tracks.get(track_id)
        if track is not None:
            return track
        fetched = _fetch_tracks(self.client, [track_id])
        self.tracks.update(fetched)
        return fetched.get(track_id)

    def _find_in_playlist(self, query: TrackQuery, tolerance: float) -> MetadataMatch | None:
        candidates = self.by_key.get(_key(query.artist, query.title), [])
        if candidates:
            return _best_by_duration(query, candidates, tolerance)
        best: MetadataCandidate | None = None
        best_score = 0.0
        for candidate in self.candidates:
            score_candidate(query, candidate, tolerance)
            if candidate.score > best_score:
                best_score = candidate.score
                best = candidate
        if best is not None and best_score >= 0.90 and _duration_ok(query.duration_s, best.duration_s, tolerance):
            return MetadataMatch(status='matched_playlist', candidate=best, score=best.score)
        return None

    def _search(self, queries: list[TrackQuery], *, min_score: float, tolerance: float) -> MetadataMatch:
        last_reason = 'no search results'
        for query in queries:
            text = ' '.join(part for part in (query.artist, query.title) if part)
            if not text:
                continue
            try:
                search = self.client.search(text, type_='track')
            except Exception as exc:
                return MetadataMatch(status='error', reason=str(exc))
            results = _search_results(search)
            if not results:
                continue
            last_reason = 'no candidate passed the filters'
            candidates = [candidate_from_track(track) for track in results]
            result = rank_candidates(query, candidates, min_score=min_score, tolerance=tolerance)
            if result.status is MatchStatus.MATCHED and result.candidate is not None:
                matched = cast('MetadataCandidate', result.candidate)
                full = self._candidate(matched.provider_id)
                if full is not None:
                    matched = full
                return MetadataMatch(status='matched_search', candidate=matched, score=result.candidate.score)
            if result.status is MatchStatus.AMBIGUOUS and result.candidate is not None:
                return MetadataMatch(
                    status='ambiguous',
                    candidate=cast('MetadataCandidate', result.candidate),
                    score=result.candidate.score,
                    reason=result.reason,
                )
            if result.reason:
                last_reason = result.reason
        return MetadataMatch(status='no_match', reason=last_reason)
