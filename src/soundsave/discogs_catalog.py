"""Discogs metadata catalogue with a conservative request throttle."""

from __future__ import annotations

import logging
import re
import time
from typing import Any, cast

import requests

from .archive import get_image_type
from .domain import MatchStatus, MetadataCandidate, MetadataMatch, TrackQuery
from .matching import rank_candidates, score_candidate
from .providers.base import ProviderError

logger = logging.getLogger(__name__)

_BASE_URL = 'https://api.discogs.com'
_USER_AGENT = 'Soundsave/2.0 +https://github.com/northis/soundsave'
_ARTIST_DISAMBIGUATION_RE = re.compile(r'\s+\(\d+\)$')


class DiscogsError(ProviderError):
    """Raised when Discogs cannot complete a request."""


class DiscogsClient:
    """Small Discogs API client with a fixed minimum request interval."""

    def __init__(
        self,
        token: str,
        *,
        delay: float = 2.0,
        timeout: float = 15.0,
        retries: int = 3,
    ) -> None:
        self._session = requests.Session()
        self._session.headers.update(
            {
                'User-Agent': _USER_AGENT,
                'Authorization': f'Discogs token={token}',
            },
        )
        self._delay = delay
        self._timeout = timeout
        self._retries = retries
        self._last_request = 0.0

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Return a JSON object from the Discogs API."""
        url = f'{_BASE_URL}{path}'
        for attempt in range(1, self._retries + 1):
            self._throttle()
            try:
                response = self._session.get(url, params=params, timeout=self._timeout)
            except requests.RequestException as exc:
                raise DiscogsError(f'Discogs request failed: {exc}') from exc
            if response.status_code == 429:
                self._sleep_after_429(response)
                if attempt == self._retries:
                    break
                continue
            self._raise_for_status(response)
            try:
                return response.json()
            except ValueError as exc:
                raise DiscogsError('Discogs returned invalid JSON') from exc
        raise DiscogsError('Discogs rate limit: retries exhausted')

    def get_bytes(self, url: str) -> bytes:
        """Download an image without sending the API token to the CDN."""
        for attempt in range(1, self._retries + 1):
            self._throttle()
            try:
                response = requests.get(
                    url,
                    headers={'User-Agent': _USER_AGENT},
                    timeout=self._timeout,
                )
            except requests.RequestException as exc:
                raise DiscogsError(f'Discogs image download failed: {exc}') from exc
            if response.status_code == 429:
                self._sleep_after_429(response)
                if attempt == self._retries:
                    break
                continue
            self._raise_for_status(response)
            return response.content
        raise DiscogsError('Discogs image rate limit: retries exhausted')

    def verify(self) -> str:
        """Check the token and return the Discogs username."""
        data = self.get('/oauth/identity')
        username = data.get('username')
        return str(username) if username else ''

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < self._delay:
            time.sleep(self._delay - elapsed)
        self._last_request = time.monotonic()

    def _sleep_after_429(self, response: requests.Response) -> None:
        raw = response.headers.get('Retry-After')
        try:
            seconds = float(raw) if raw is not None else self._delay * 2
        except ValueError:
            seconds = self._delay * 2
        time.sleep(max(seconds, self._delay))
        self._last_request = time.monotonic()

    @staticmethod
    def _raise_for_status(response: requests.Response) -> None:
        if response.status_code in {401, 403}:
            raise DiscogsError('Discogs authentication failed; check DISCOGS_TOKEN')
        if response.status_code == 404:
            raise DiscogsError('Discogs resource not found')
        if response.status_code >= 400:
            raise DiscogsError(f'Discogs returned HTTP {response.status_code}')


class DiscogsCatalog:
    """Discogs release catalogue used as a metadata fallback."""

    def __init__(
        self,
        token: str | None = None,
        *,
        delay: float = 2.0,
        timeout: float = 15.0,
        max_releases: int = 3,
        search_limit: int = 5,
        client: DiscogsClient | None = None,
    ) -> None:
        self._client = client or DiscogsClient(token or '', delay=delay, timeout=timeout)
        self._max_releases = max_releases
        self._search_limit = search_limit
        self._releases: dict[int, dict[str, Any]] = {}

    def verify(self) -> str:
        """Check the Discogs token and return the username."""
        return self._client.verify()

    def find(
        self,
        queries: list[TrackQuery],
        *,
        url: str | None,  # noqa: ARG002 - Discogs has no Yandex WOAF cache
        search: bool,
        min_score: float,
        tolerance: float,
    ) -> MetadataMatch:
        """Find the best Discogs track for one local file."""
        if not search:
            return MetadataMatch(status='no_match', reason='Discogs search disabled')
        last_reason = 'no Discogs results'
        for query in queries:
            try:
                results = self._search(query)
            except DiscogsError as exc:
                return MetadataMatch(status='error', reason=str(exc))
            if not results:
                continue
            candidates: list[MetadataCandidate] = []
            had_error = False
            for result in results[: self._max_releases]:
                release_id = result.get('id')
                if not isinstance(release_id, int):
                    continue
                try:
                    release = self._release(release_id)
                except DiscogsError as exc:
                    had_error = True
                    last_reason = str(exc)
                    continue
                candidate = _match_release(query, release, tolerance)
                if candidate is not None:
                    candidates.append(candidate)
            if not candidates:
                if had_error:
                    return MetadataMatch(status='error', reason=last_reason)
                continue
            ranked = rank_candidates(query, candidates, min_score=min_score, tolerance=tolerance)
            if ranked.status is MatchStatus.MATCHED and ranked.candidate is not None:
                return MetadataMatch(
                    status='matched_discogs',
                    candidate=cast('MetadataCandidate', ranked.candidate),
                    score=ranked.candidate.score,
                )
            if ranked.status is MatchStatus.AMBIGUOUS and ranked.candidate is not None:
                return MetadataMatch(
                    status='ambiguous',
                    candidate=cast('MetadataCandidate', ranked.candidate),
                    score=ranked.candidate.score,
                    reason=ranked.reason,
                )
            if ranked.reason:
                last_reason = ranked.reason
        return MetadataMatch(status='no_match', reason=last_reason)

    def has_cover(self, candidate: MetadataCandidate) -> bool:
        """Return whether the Discogs release has a primary image."""
        release = self._release_for(candidate)
        return release is not None and _image_url(release) is not None

    def fetch_cover(self, candidate: MetadataCandidate) -> tuple[bytes, str] | None:
        """Download the primary image for *candidate*."""
        release = self._release_for(candidate)
        if release is None:
            return None
        url = _image_url(release)
        if url is None:
            return None
        data = self._client.get_bytes(url)
        return data, get_image_type(data)

    def fetch_lyrics(self, candidate: MetadataCandidate) -> str | None:  # noqa: ARG002
        """Discogs does not provide lyrics."""
        return None

    def _search(self, query: TrackQuery) -> list[dict[str, Any]]:
        params = {
            'artist': query.artist,
            'track': query.title,
            'type': 'release',
            'per_page': self._search_limit,
        }
        data = self._client.get('/database/search', params)
        results = data.get('results')
        if not isinstance(results, list):
            return []
        unique: list[dict[str, Any]] = []
        seen: set[tuple[object, object]] = set()
        for result in results:
            if not isinstance(result, dict) or result.get('type') != 'release':
                continue
            release_id = result.get('id')
            if not isinstance(release_id, int):
                continue
            key = (result.get('title'), result.get('year'))
            if key in seen:
                continue
            seen.add(key)
            unique.append(result)
        return unique

    def _release(self, release_id: int) -> dict[str, Any]:
        cached = self._releases.get(release_id)
        if cached is not None:
            return cached
        data = self._client.get(f'/releases/{release_id}')
        self._releases[release_id] = data
        return data

    def _release_for(self, candidate: MetadataCandidate) -> dict[str, Any] | None:
        release_id = _release_id(candidate)
        if release_id is None:
            return None
        try:
            return self._release(release_id)
        except DiscogsError:
            return None


def _match_release(
    query: TrackQuery,
    release: dict[str, Any],
    tolerance: float,
) -> MetadataCandidate | None:
    release_id = release.get('id')
    if not isinstance(release_id, int):
        return None
    release_title = _text(release.get('title'))
    release_artists = _artists(release.get('artists'))
    release_year = release.get('year') if isinstance(release.get('year'), int) else None
    release_date = _text(release.get('released'))
    genre = _genre(release)
    tracklist = release.get('tracklist')
    if not isinstance(tracklist, list):
        return None

    best: MetadataCandidate | None = None
    best_score = 0.0
    for index, track in enumerate(tracklist):
        if not isinstance(track, dict) or track.get('type_') != 'track':
            continue
        title = _text(track.get('title'))
        if not title:
            continue
        artists = _artists(track.get('artists')) or release_artists
        candidate = MetadataCandidate(
            provider='discogs',
            provider_id=f'{release_id}:{index}',
            title=title,
            artists=artists,
            album=release_title,
            duration_s=_parse_duration(track.get('duration')),
            album_artists=release_artists,
            year=release_year,
            release_date=release_date,
            genre=genre,
            url=None,
            lyrics_available=False,
        )
        score_candidate(query, candidate, tolerance)
        if candidate.score > best_score:
            best_score = candidate.score
            best = candidate

    if best is None:
        return None
    if best.artist_score < 0.80 or best.title_score < 0.80:
        return None
    if not _duration_ok(query.duration_s, best.duration_s, tolerance):
        return None
    return best


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _artists(items: object) -> tuple[str, ...]:
    if not isinstance(items, list):
        return ()
    names: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = _text(item.get('name'))
        if not name:
            continue
        name = _ARTIST_DISAMBIGUATION_RE.sub('', name).strip()
        if name:
            names.append(name)
    return tuple(names)


def _genre(release: dict[str, Any]) -> str | None:
    for key in ('styles', 'genres'):
        values = release.get(key)
        if isinstance(values, list) and values:
            text = _text(values[0])
            if text:
                return text
    return None


def _image_url(release: dict[str, Any]) -> str | None:
    images = release.get('images')
    if isinstance(images, list):
        primary = next(
            (image for image in images if isinstance(image, dict) and image.get('type') == 'primary'),
            None,
        )
        if primary is not None:
            url = _text(primary.get('uri')) or _text(primary.get('resource_url'))
            if url:
                return url
        for image in images:
            if isinstance(image, dict):
                url = _text(image.get('uri')) or _text(image.get('resource_url'))
                if url:
                    return url
    return _text(release.get('cover_image'))


def _parse_duration(value: object) -> float | None:
    text = _text(value)
    if not text:
        return None
    parts = text.split(':')
    if not all(part.isdigit() for part in parts):
        return None
    numbers = [int(part) for part in parts]
    if len(numbers) == 2:
        return float(numbers[0] * 60 + numbers[1])
    if len(numbers) == 3:
        return float(numbers[0] * 3600 + numbers[1] * 60 + numbers[2])
    return None


def _duration_ok(left: float | None, right: float | None, tolerance: float) -> bool:
    if left is None or right is None:
        return True
    return abs(left - right) <= tolerance


def _release_id(candidate: MetadataCandidate) -> int | None:
    try:
        return int(candidate.provider_id.split(':', 1)[0])
    except (ValueError, IndexError):
        return None
