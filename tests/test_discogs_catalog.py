from __future__ import annotations

from typing import Any

import pytest

from soundsave.catalogs import CONFIDENT_STATUSES, CompositeCatalog
from soundsave.discogs_catalog import (
    DiscogsCatalog,
    DiscogsClient,
    DiscogsError,
    _artists,
    _genre,
    _image_url,
    _parse_duration,
)
from soundsave.domain import MetadataCandidate, MetadataMatch, TrackQuery


class FakeResponse:
    def __init__(
        self,
        status_code: int = 200,
        *,
        json_data: Any = None,
        headers: dict[str, str] | None = None,
        content: bytes = b'',
    ) -> None:
        self.status_code = status_code
        self._json_data = json_data
        self.headers = headers or {}
        self.content = content

    def json(self) -> Any:
        if self._json_data is None:
            raise ValueError('no json')
        return self._json_data


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict[str, Any] | None]] = []
        self.headers: dict[str, str] = {}

    def get(self, url: str, *, params: dict[str, Any] | None = None, timeout: float | None = None) -> FakeResponse:
        self.calls.append((url, params))
        return self.responses.pop(0)


class FakeDiscogsClient:
    def __init__(
        self,
        *,
        search_results: list[dict[str, Any]] | None = None,
        releases: dict[int, dict[str, Any]] | None = None,
        search_error: DiscogsError | None = None,
        release_error: DiscogsError | None = None,
        image: bytes = b'\xff\xd8\xffcover',
    ) -> None:
        self.search_results = search_results or []
        self.releases = releases or {}
        self.search_error = search_error
        self.release_error = release_error
        self.image = image
        self.search_calls: list[dict[str, Any]] = []
        self.release_calls: list[int] = []
        self.bytes_calls: list[str] = []

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if path == '/database/search':
            self.search_calls.append(params or {})
            if self.search_error is not None:
                raise self.search_error
            return {'results': self.search_results}
        if path.startswith('/releases/'):
            release_id = int(path.rsplit('/', 1)[1])
            self.release_calls.append(release_id)
            if self.release_error is not None:
                raise self.release_error
            return self.releases[release_id]
        if path == '/oauth/identity':
            return {'username': 'tester'}
        raise AssertionError(path)

    def get_bytes(self, url: str) -> bytes:
        self.bytes_calls.append(url)
        return self.image

    def verify(self) -> str:
        return 'tester'


def make_query(
    artist: str = 'The Gathering',
    title: str = 'Saturnine',
    *,
    album: str | None = 'Mandylion',
    duration: float | None = 360.0,
) -> TrackQuery:
    return TrackQuery(artist=artist, title=title, album=album, duration_s=duration)


def make_release(**overrides: Any) -> dict[str, Any]:
    release: dict[str, Any] = {
        'id': 123,
        'title': 'Mandylion',
        'artists': [{'name': 'The Gathering'}],
        'year': 1995,
        'released': '1995-08-22',
        'genres': ['Rock'],
        'styles': ['Gothic Metal'],
        'images': [{'type': 'primary', 'uri': 'https://i.discogs.com/cover.jpg'}],
        'tracklist': [
            {'type_': 'track', 'position': '1', 'title': 'Saturnine', 'duration': '6:00'},
            {'type_': 'track', 'position': '2', 'title': 'Strange Machines', 'duration': '6:00'},
        ],
    }
    release.update(overrides)
    return release


def make_search_result(**overrides: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        'id': 123,
        'type': 'release',
        'title': 'The Gathering - Mandylion',
        'year': 1995,
    }
    result.update(overrides)
    return result


def make_catalog(client: FakeDiscogsClient) -> DiscogsCatalog:
    return DiscogsCatalog(client=client)  # type: ignore[arg-type]


class TestDiscogsCatalogFind:
    def test_matched_release(self) -> None:
        client = FakeDiscogsClient(search_results=[make_search_result()], releases={123: make_release()})
        catalog = make_catalog(client)

        match = catalog.find([make_query()], url=None, search=True, min_score=0.85, tolerance=5.0)

        assert match.status == 'matched_discogs'
        assert match.candidate is not None
        assert match.candidate.provider == 'discogs'
        assert match.candidate.provider_id == '123:0'
        assert match.candidate.title == 'Saturnine'
        assert match.candidate.artists == ('The Gathering',)
        assert match.candidate.album == 'Mandylion'
        assert match.candidate.album_artists == ('The Gathering',)
        assert match.candidate.year == 1995
        assert match.candidate.release_date == '1995-08-22'
        assert match.candidate.genre == 'Gothic Metal'
        assert match.candidate.url is None
        assert match.candidate.lyrics_available is False

    def test_matches_and_fetches_cover_without_local_album(self) -> None:
        client = FakeDiscogsClient(search_results=[make_search_result()], releases={123: make_release()})
        catalog = make_catalog(client)

        match = catalog.find([make_query(album=None)], url=None, search=True, min_score=0.85, tolerance=5.0)

        assert match.status == 'matched_discogs'
        assert match.candidate is not None
        assert match.candidate.album == 'Mandylion'
        assert catalog.has_cover(match.candidate)
        assert catalog.fetch_cover(match.candidate) == (b'\xff\xd8\xffcover', 'image/jpeg')
        assert client.search_calls[0]['artist'] == 'The Gathering'
        assert client.search_calls[0]['track'] == 'Saturnine'

    def test_no_results(self) -> None:
        client = FakeDiscogsClient(search_results=[])
        catalog = make_catalog(client)

        match = catalog.find([make_query()], url=None, search=True, min_score=0.85, tolerance=5.0)

        assert match.status == 'no_match'
        assert len(client.search_calls) == 1

    def test_search_error(self) -> None:
        client = FakeDiscogsClient(search_error=DiscogsError('search failed'))
        catalog = make_catalog(client)

        match = catalog.find([make_query()], url=None, search=True, min_score=0.85, tolerance=5.0)

        assert match.status == 'error'
        assert 'search failed' in match.reason

    def test_release_error(self) -> None:
        client = FakeDiscogsClient(
            search_results=[make_search_result()],
            release_error=DiscogsError('release failed'),
        )
        catalog = make_catalog(client)

        match = catalog.find([make_query()], url=None, search=True, min_score=0.85, tolerance=5.0)

        assert match.status == 'error'
        assert 'release failed' in match.reason

    def test_search_disabled(self) -> None:
        client = FakeDiscogsClient(search_results=[make_search_result()], releases={123: make_release()})
        catalog = make_catalog(client)

        match = catalog.find([make_query()], url=None, search=False, min_score=0.85, tolerance=5.0)

        assert match.status == 'no_match'
        assert client.search_calls == []

    def test_live_version_rejected(self) -> None:
        release = make_release(
            tracklist=[{'type_': 'track', 'position': '1', 'title': 'Saturnine (Live)', 'duration': '6:00'}],
        )
        client = FakeDiscogsClient(search_results=[make_search_result()], releases={123: release})
        catalog = make_catalog(client)

        match = catalog.find([make_query()], url=None, search=True, min_score=0.85, tolerance=5.0)

        assert match.status == 'no_match'

    def test_duplicate_results_are_deduped(self) -> None:
        client = FakeDiscogsClient(
            search_results=[make_search_result(), make_search_result()],
            releases={123: make_release()},
        )
        catalog = make_catalog(client)

        match = catalog.find([make_query()], url=None, search=True, min_score=0.85, tolerance=5.0)

        assert match.status == 'matched_discogs'
        assert client.release_calls == [123]

    def test_filename_variant_used(self) -> None:
        client = FakeDiscogsClient(search_results=[make_search_result()], releases={123: make_release()})
        catalog = make_catalog(client)

        match = catalog.find(
            [make_query(artist='Wrong Artist', title='Wrong Title'), make_query()],
            url=None,
            search=True,
            min_score=0.85,
            tolerance=5.0,
        )

        assert match.status == 'matched_discogs'
        assert len(client.search_calls) == 2


class TestDiscogsArtwork:
    def test_has_cover(self) -> None:
        client = FakeDiscogsClient(search_results=[make_search_result()], releases={123: make_release()})
        catalog = make_catalog(client)
        match = catalog.find([make_query()], url=None, search=True, min_score=0.85, tolerance=5.0)
        assert match.candidate is not None

        assert catalog.has_cover(match.candidate)

    def test_fetch_cover(self) -> None:
        client = FakeDiscogsClient(search_results=[make_search_result()], releases={123: make_release()})
        catalog = make_catalog(client)
        match = catalog.find([make_query()], url=None, search=True, min_score=0.85, tolerance=5.0)
        assert match.candidate is not None

        assert catalog.fetch_cover(match.candidate) == (b'\xff\xd8\xffcover', 'image/jpeg')
        assert client.bytes_calls == ['https://i.discogs.com/cover.jpg']

    def test_no_cover(self) -> None:
        release = make_release(images=[])
        client = FakeDiscogsClient(search_results=[make_search_result()], releases={123: release})
        catalog = make_catalog(client)
        match = catalog.find([make_query()], url=None, search=True, min_score=0.85, tolerance=5.0)
        assert match.candidate is not None

        assert not catalog.has_cover(match.candidate)
        assert catalog.fetch_cover(match.candidate) is None

    def test_fetch_lyrics(self) -> None:
        client = FakeDiscogsClient(search_results=[make_search_result()], releases={123: make_release()})
        catalog = make_catalog(client)
        match = catalog.find([make_query()], url=None, search=True, min_score=0.85, tolerance=5.0)
        assert match.candidate is not None

        assert catalog.fetch_lyrics(match.candidate) is None

    def test_verify(self) -> None:
        client = FakeDiscogsClient()
        catalog = make_catalog(client)

        assert catalog.verify() == 'tester'


class TestDiscogsHelpers:
    def test_parse_duration(self) -> None:
        assert _parse_duration('6:00') == 360.0
        assert _parse_duration('1:02:03') == 3723.0
        assert _parse_duration('') is None
        assert _parse_duration('abc') is None
        assert _parse_duration('6') is None
        assert _parse_duration(None) is None

    def test_artists_strip_disambiguation(self) -> None:
        assert _artists([{'name': 'Nirvana (2)'}]) == ('Nirvana',)
        assert _artists([{'name': 'The Gathering'}]) == ('The Gathering',)
        assert _artists(None) == ()

    def test_genre_prefers_style(self) -> None:
        assert _genre({'styles': ['Gothic Metal'], 'genres': ['Rock']}) == 'Gothic Metal'
        assert _genre({'genres': ['Rock']}) == 'Rock'
        assert _genre({}) is None

    def test_image_url_prefers_primary(self) -> None:
        release = {
            'images': [
                {'type': 'secondary', 'uri': 'https://i.discogs.com/back.jpg'},
                {'type': 'primary', 'uri': 'https://i.discogs.com/front.jpg'},
            ],
        }
        assert _image_url(release) == 'https://i.discogs.com/front.jpg'

    def test_image_url_falls_back_to_cover_image(self) -> None:
        assert _image_url({'cover_image': 'https://i.discogs.com/cover.jpg'}) == 'https://i.discogs.com/cover.jpg'


class TestDiscogsClient:
    def test_retries_after_429(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sleeps: list[float] = []
        monkeypatch.setattr('soundsave.discogs_catalog.time.sleep', sleeps.append)
        client = DiscogsClient('token', delay=0.0)
        client._session = FakeSession(
            [
                FakeResponse(429, headers={'Retry-After': '7'}),
                FakeResponse(200, json_data={'ok': True}),
            ],
        )  # type: ignore[assignment]

        assert client.get('/database/search') == {'ok': True}
        assert sleeps == [7.0]

    def test_auth_error(self) -> None:
        client = DiscogsClient('token', delay=0.0)
        client._session = FakeSession([FakeResponse(401)])  # type: ignore[assignment]

        with pytest.raises(DiscogsError, match='authentication'):
            client.get('/oauth/identity')

    def test_not_found(self) -> None:
        client = DiscogsClient('token', delay=0.0)
        client._session = FakeSession([FakeResponse(404)])  # type: ignore[assignment]

        with pytest.raises(DiscogsError, match='not found'):
            client.get('/releases/123')

    def test_server_error(self) -> None:
        client = DiscogsClient('token', delay=0.0)
        client._session = FakeSession([FakeResponse(500)])  # type: ignore[assignment]

        with pytest.raises(DiscogsError, match='HTTP 500'):
            client.get('/releases/123')

    def test_verify(self) -> None:
        client = DiscogsClient('token', delay=0.0)
        client._session = FakeSession([FakeResponse(200, json_data={'username': 'tester'})])  # type: ignore[assignment]

        assert client.verify() == 'tester'

    def test_get_bytes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            'soundsave.discogs_catalog.requests.get',
            lambda *args, **kwargs: FakeResponse(200, content=b'image'),
        )
        client = DiscogsClient('token', delay=0.0)

        assert client.get_bytes('https://i.discogs.com/cover.jpg') == b'image'


class FakeCatalog:
    def __init__(
        self,
        result: MetadataMatch,
        *,
        cover: tuple[bytes, str] | None = None,
        lyrics: str | None = None,
    ) -> None:
        self.result = result
        self.cover = cover
        self.lyrics = lyrics
        self.find_calls = 0
        self.cover_calls = 0
        self.lyrics_calls = 0

    def find(
        self,
        queries: list[TrackQuery],
        *,
        url: str | None,
        search: bool,
        min_score: float,
        tolerance: float,
    ) -> MetadataMatch:
        self.find_calls += 1
        return self.result

    def has_cover(self, candidate: MetadataCandidate) -> bool:
        return self.cover is not None

    def fetch_cover(self, candidate: MetadataCandidate) -> tuple[bytes, str] | None:
        self.cover_calls += 1
        return self.cover

    def fetch_lyrics(self, candidate: MetadataCandidate) -> str | None:
        self.lyrics_calls += 1
        return self.lyrics


def make_match(status: str, provider: str = 'yandex') -> MetadataMatch:
    candidate = None
    if status in CONFIDENT_STATUSES or status == 'ambiguous':
        candidate = MetadataCandidate(
            provider=provider,
            provider_id='1',
            title='Saturnine',
            artists=('The Gathering',),
        )
    return MetadataMatch(status=status, candidate=candidate, score=0.9 if candidate else 0.0)


class TestCompositeCatalog:
    def test_primary_match_skips_fallback(self) -> None:
        primary = FakeCatalog(make_match('matched_playlist'))
        fallback = FakeCatalog(make_match('matched_discogs', provider='discogs'))
        composite = CompositeCatalog(primary, fallback)

        match = composite.find([make_query()], url=None, search=True, min_score=0.85, tolerance=5.0)

        assert match.status == 'matched_playlist'
        assert fallback.find_calls == 0

    def test_fallback_used_on_no_match(self) -> None:
        primary = FakeCatalog(make_match('no_match'))
        fallback = FakeCatalog(make_match('matched_discogs', provider='discogs'))
        composite = CompositeCatalog(primary, fallback)

        match = composite.find([make_query()], url=None, search=True, min_score=0.85, tolerance=5.0)

        assert match.status == 'matched_discogs'
        assert fallback.find_calls == 1

    def test_primary_ambiguous_wins_over_no_match(self) -> None:
        primary = FakeCatalog(make_match('ambiguous'))
        fallback = FakeCatalog(make_match('no_match'))
        composite = CompositeCatalog(primary, fallback)

        match = composite.find([make_query()], url=None, search=True, min_score=0.85, tolerance=5.0)

        assert match.status == 'ambiguous'
        assert fallback.find_calls == 1

    def test_fallback_error_is_returned(self) -> None:
        primary = FakeCatalog(make_match('no_match'))
        fallback = FakeCatalog(make_match('error'))
        composite = CompositeCatalog(primary, fallback)

        match = composite.find([make_query()], url=None, search=True, min_score=0.85, tolerance=5.0)

        assert match.status == 'error'

    def test_primary_error_is_returned(self) -> None:
        primary = FakeCatalog(make_match('error'))
        fallback = FakeCatalog(make_match('no_match'))
        composite = CompositeCatalog(primary, fallback)

        match = composite.find([make_query()], url=None, search=True, min_score=0.85, tolerance=5.0)

        assert match.status == 'error'
        assert fallback.find_calls == 1

    def test_search_disabled_skips_fallback(self) -> None:
        primary = FakeCatalog(make_match('no_match'))
        fallback = FakeCatalog(make_match('matched_discogs', provider='discogs'))
        composite = CompositeCatalog(primary, fallback)

        match = composite.find([make_query()], url=None, search=False, min_score=0.85, tolerance=5.0)

        assert match.status == 'no_match'
        assert fallback.find_calls == 0

    def test_delegates_cover_by_provider(self) -> None:
        primary = FakeCatalog(make_match('matched_playlist'), cover=(b'primary', 'image/jpeg'))
        fallback = FakeCatalog(make_match('matched_discogs', provider='discogs'), cover=(b'fallback', 'image/jpeg'))
        composite = CompositeCatalog(primary, fallback)
        primary_candidate = make_match('matched_playlist').candidate
        fallback_candidate = make_match('matched_discogs', provider='discogs').candidate
        assert primary_candidate is not None
        assert fallback_candidate is not None

        assert composite.has_cover(primary_candidate)
        assert composite.fetch_cover(primary_candidate) == (b'primary', 'image/jpeg')
        assert composite.has_cover(fallback_candidate)
        assert composite.fetch_cover(fallback_candidate) == (b'fallback', 'image/jpeg')

    def test_delegates_lyrics_by_provider(self) -> None:
        primary = FakeCatalog(make_match('matched_playlist'), lyrics='primary lyrics')
        fallback = FakeCatalog(make_match('matched_discogs', provider='discogs'), lyrics=None)
        composite = CompositeCatalog(primary, fallback)
        primary_candidate = make_match('matched_playlist').candidate
        fallback_candidate = make_match('matched_discogs', provider='discogs').candidate
        assert primary_candidate is not None
        assert fallback_candidate is not None

        assert composite.fetch_lyrics(primary_candidate) == 'primary lyrics'
        assert composite.fetch_lyrics(fallback_candidate) is None
