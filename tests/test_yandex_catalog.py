from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from yandex_music.exceptions import NotFoundError

from soundsave.domain import TrackQuery
from soundsave.yandex_catalog import (
    YandexCatalog,
    YandexCatalogError,
    candidate_from_track,
    full_title,
    parse_playlist_url,
    parse_track_url,
)


class FakeModel(SimpleNamespace):
    """A Yandex Music model stub that supports the ``obj['key']`` accessor."""

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)


class FakeLyrics:
    def __init__(self, text: str) -> None:
        self.text = text

    def fetch_lyrics(self) -> str:
        return self.text


def make_album(
    album_id: int = 10,
    title: str = 'Mandylion',
    *,
    version: str | None = None,
    artist: str = 'The Gathering',
    year: int = 1995,
    release_date: str = '1995-08-22T00:00:00+04:00',
    genre: str = 'gothic metal',
) -> FakeModel:
    return FakeModel(
        id=album_id,
        title=title,
        version=version,
        artists=[FakeModel(name=artist)],
        year=year,
        release_date=release_date,
        genre=genre,
    )


def make_track(
    track_id: int = 123,
    title: str = 'Saturnine',
    *,
    version: str | None = None,
    artist: str = 'The Gathering',
    album: FakeModel | None = None,
    duration_ms: int = 300_000,
    cover_uri: str | None = 'avatars.yandex.net/cover',
    lyrics: str | None = None,
    cover_error: bool = False,
    lyrics_error: bool = False,
    lyrics_not_found: bool = False,
) -> FakeModel:
    def download_cover_bytes(size: str) -> bytes:
        if cover_error:
            raise RuntimeError('cover failed')
        return b'\xff\xd8\xffcover'

    def get_lyrics(format_: str) -> FakeLyrics | None:
        if lyrics_error:
            raise RuntimeError('lyrics failed')
        if lyrics_not_found:
            raise NotFoundError('No lyrics found for track')
        return FakeLyrics(lyrics) if lyrics is not None else None

    return FakeModel(
        id=str(track_id),
        title=title,
        version=version,
        artists=[FakeModel(name=artist)],
        albums=[album or make_album()],
        duration_ms=duration_ms,
        cover_uri=cover_uri,
        lyrics_info=FakeModel(has_available_text_lyrics=lyrics is not None),
        download_cover_bytes=download_cover_bytes,
        get_lyrics=get_lyrics,
    )


class FakeTrackShort:
    def __init__(self, track_id: int) -> None:
        self.id = track_id


class FakePlaylist:
    def __init__(self, track_ids: list[int]) -> None:
        self.tracks = [FakeTrackShort(track_id) for track_id in track_ids]


class FakeClient:
    playlist_track_ids: list[int] = []
    tracks_by_id: dict[str, Any] = {}
    search_results: dict[str, list[Any]] = {}
    fail_playlist = False
    search_calls: list[str] = []

    def __init__(self, token: str | None = None) -> None:
        self.search_calls = []

    def init(self) -> FakeClient:
        return self

    def users_playlists(self, kind: int, owner: str) -> FakePlaylist:
        if self.fail_playlist:
            raise RuntimeError('playlist failed')
        return FakePlaylist(self.playlist_track_ids)

    def tracks(self, ids: list[str]) -> list[Any]:
        return [self.tracks_by_id[str(track_id)] for track_id in ids if str(track_id) in self.tracks_by_id]

    def search(self, text: str, type_: str = 'track') -> FakeModel:
        self.search_calls.append(text)
        results = self.search_results.get(text)
        if results is None:
            return FakeModel(tracks=None)
        return FakeModel(tracks=FakeModel(results=results))


def make_query(
    artist: str = 'The Gathering',
    title: str = 'Saturnine',
    *,
    album: str | None = 'Mandylion',
    duration: float | None = 300.0,
) -> TrackQuery:
    return TrackQuery(artist=artist, title=title, album=album, duration_s=duration)


def make_catalog(tracks: list[Any] | None = None) -> YandexCatalog:
    FakeClient.tracks_by_id = {str(track.id): track for track in (tracks or [])}
    return YandexCatalog(FakeClient(), FakeClient.tracks_by_id)


class TestHelpers:
    def test_parse_playlist_url(self) -> None:
        assert parse_playlist_url('https://music.yandex.ru/users/alice/playlists/42') == ('alice', 42)

    def test_parse_track_url(self) -> None:
        assert parse_track_url('https://music.yandex.ru/album/10/track/123') == '123'
        assert parse_track_url('https://music.yandex.com/album/10/track/123') == '123'
        assert parse_track_url('https://example.com/track/123') is None
        assert parse_track_url(None) is None

    def test_full_title(self) -> None:
        assert full_title(FakeModel(title='Song', version=None)) == 'Song'
        assert full_title(FakeModel(title='Song', version='Live')) == 'Song (Live)'
        assert full_title(None) == ''

    def test_candidate_from_track(self) -> None:
        track = make_track(lyrics='line 1')

        candidate = candidate_from_track(track)

        assert candidate.provider == 'yandex'
        assert candidate.provider_id == '123'
        assert candidate.title == 'Saturnine'
        assert candidate.artists == ('The Gathering',)
        assert candidate.album == 'Mandylion'
        assert candidate.duration_s == 300.0
        assert candidate.album_artists == ('The Gathering',)
        assert candidate.year == 1995
        assert candidate.release_date == '1995-08-22T00:00:00+04:00'
        assert candidate.genre == 'gothic metal'
        assert candidate.url == 'https://music.yandex.ru/album/10/track/123'
        assert candidate.lyrics_available is True


class TestLoad:
    def test_builds_playlist_index(self, monkeypatch: pytest.MonkeyPatch) -> None:
        first = make_track(1)
        second = make_track(2, 'Strange Machines')
        FakeClient.playlist_track_ids = [1, 2]
        FakeClient.tracks_by_id = {'1': first, '2': second}
        FakeClient.fail_playlist = False
        monkeypatch.setattr('soundsave.yandex_catalog.Client', FakeClient)

        catalog = YandexCatalog.load('token', 'https://music.yandex.ru/users/alice/playlists/42')

        assert set(catalog.tracks) == {'1', '2'}
        assert len(catalog.candidates) == 2

    def test_playlist_failure_is_not_fatal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        FakeClient.playlist_track_ids = [1]
        FakeClient.tracks_by_id = {'1': make_track(1)}
        FakeClient.fail_playlist = True
        monkeypatch.setattr('soundsave.yandex_catalog.Client', FakeClient)

        catalog = YandexCatalog.load('token', 'https://music.yandex.ru/users/alice/playlists/42')

        assert catalog.tracks == {}

    def test_client_failure_is_wrapped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class BrokenClient:
            def __init__(self, token: str | None = None) -> None:
                raise RuntimeError('no network')

        monkeypatch.setattr('soundsave.yandex_catalog.Client', BrokenClient)

        with pytest.raises(YandexCatalogError, match='Failed to initialize'):
            YandexCatalog.load('token', None)


class TestFind:
    def test_woaf_match(self) -> None:
        catalog = make_catalog([make_track()])

        match = catalog.find(
            [make_query()],
            url='https://music.yandex.ru/album/10/track/123',
            search=False,
            min_score=0.85,
            tolerance=5.0,
        )

        assert match.status == 'matched_woaf'
        assert match.candidate is not None
        assert match.candidate.provider_id == '123'
        assert match.score == 1.0

    def test_woaf_mismatch_falls_back(self) -> None:
        catalog = make_catalog([make_track(title='Another Song')])

        match = catalog.find(
            [make_query()],
            url='https://music.yandex.ru/album/10/track/123',
            search=False,
            min_score=0.85,
            tolerance=5.0,
        )

        assert match.status == 'no_match'

    def test_playlist_exact_match(self) -> None:
        catalog = make_catalog([make_track()])

        match = catalog.find([make_query()], url=None, search=False, min_score=0.85, tolerance=5.0)

        assert match.status == 'matched_playlist'
        assert match.candidate is not None
        assert match.candidate.provider_id == '123'

    def test_playlist_fuzzy_match(self) -> None:
        catalog = make_catalog([make_track()])

        match = catalog.find(
            [make_query(title='Saturnin')],
            url=None,
            search=False,
            min_score=0.85,
            tolerance=5.0,
        )

        assert match.status == 'matched_playlist'
        assert match.candidate is not None

    def test_playlist_ambiguous_without_duration(self) -> None:
        catalog = make_catalog([make_track(1), make_track(2)])

        match = catalog.find(
            [make_query(duration=None)],
            url=None,
            search=False,
            min_score=0.85,
            tolerance=5.0,
        )

        assert match.status == 'ambiguous'

    def test_playlist_duplicate_uses_duration(self) -> None:
        catalog = make_catalog([make_track(1, duration_ms=300_000), make_track(2, duration_ms=400_000)])

        match = catalog.find(
            [make_query(duration=400.0)],
            url=None,
            search=False,
            min_score=0.85,
            tolerance=5.0,
        )

        assert match.status == 'matched_playlist'
        assert match.candidate is not None
        assert match.candidate.provider_id == '2'

    def test_search_match(self) -> None:
        track = make_track()
        FakeClient.search_results = {'The Gathering Saturnine': [track]}
        catalog = YandexCatalog(FakeClient(), {})
        catalog.tracks['123'] = track

        match = catalog.find([make_query()], url=None, search=True, min_score=0.85, tolerance=5.0)

        assert match.status == 'matched_search'
        assert match.candidate is not None
        assert match.candidate.provider_id == '123'
        assert catalog.client.search_calls == ['The Gathering Saturnine']

    def test_search_without_results(self) -> None:
        FakeClient.search_results = {}
        catalog = YandexCatalog(FakeClient(), {})

        match = catalog.find([make_query()], url=None, search=True, min_score=0.85, tolerance=5.0)

        assert match.status == 'no_match'

    def test_search_disabled(self) -> None:
        catalog = make_catalog([])

        match = catalog.find([make_query()], url=None, search=False, min_score=0.85, tolerance=5.0)

        assert match.status == 'no_match'

    def test_search_error(self) -> None:
        class BrokenClient(FakeClient):
            def search(self, text: str, type_: str = 'track') -> FakeModel:
                raise RuntimeError('search failed')

        catalog = YandexCatalog(BrokenClient(), {})

        match = catalog.find([make_query()], url=None, search=True, min_score=0.85, tolerance=5.0)

        assert match.status == 'error'
        assert 'search failed' in match.reason


class TestArtworkAndLyrics:
    def test_has_cover(self) -> None:
        catalog = make_catalog([make_track()])
        candidate = catalog.by_id['123']

        assert catalog.has_cover(candidate)

    def test_has_no_cover(self) -> None:
        catalog = make_catalog([make_track(cover_uri=None)])
        candidate = catalog.by_id['123']

        assert not catalog.has_cover(candidate)

    def test_fetch_cover(self) -> None:
        catalog = make_catalog([make_track()])
        candidate = catalog.by_id['123']

        assert catalog.fetch_cover(candidate) == (b'\xff\xd8\xffcover', 'image/jpeg')

    def test_fetch_cover_error(self) -> None:
        catalog = make_catalog([make_track(cover_error=True)])
        candidate = catalog.by_id['123']

        with pytest.raises(YandexCatalogError, match='Failed to download cover'):
            catalog.fetch_cover(candidate)

    def test_fetch_lyrics(self) -> None:
        catalog = make_catalog([make_track(lyrics='line 1')])
        candidate = catalog.by_id['123']

        assert catalog.fetch_lyrics(candidate) == 'line 1'

    def test_fetch_lyrics_unavailable(self) -> None:
        catalog = make_catalog([make_track()])
        candidate = catalog.by_id['123']

        assert catalog.fetch_lyrics(candidate) is None

    def test_fetch_lyrics_error(self) -> None:
        catalog = make_catalog([make_track(lyrics='line 1', lyrics_error=True)])
        candidate = catalog.by_id['123']

        with pytest.raises(YandexCatalogError, match='Failed to download lyrics'):
            catalog.fetch_lyrics(candidate)

    def test_fetch_lyrics_not_found(self) -> None:
        catalog = make_catalog([make_track(lyrics='line 1', lyrics_not_found=True)])
        candidate = catalog.by_id['123']

        assert catalog.fetch_lyrics(candidate) is None
