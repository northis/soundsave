from __future__ import annotations

from types import SimpleNamespace

import pytest
from zvuk_music.exceptions import BotDetectedError

from soundsave.domain import Candidate, TrackQuery
from soundsave.providers.base import ProviderNotConfiguredError
from soundsave.providers.zvuk import ZvukProvider


class FakeClient:
    def __init__(self, token: str | None = None, **_: object) -> None:
        self.token = token
        self.search_query: str | None = None

    def init(self) -> FakeClient:
        return self

    def search(self, query: str, **_: object) -> object:
        self.search_query = query
        return SimpleNamespace(
            tracks=SimpleNamespace(
                items=[
                    SimpleNamespace(
                        id='123',
                        title='Saturnine',
                        duration=300,
                        artists=[SimpleNamespace(title='The Gathering')],
                        release=SimpleNamespace(title='Mandylion'),
                    ),
                ],
            ),
        )

    def get_tracks(self, ids: list[str]) -> list[object]:
        assert ids == ['123']
        return [
            SimpleNamespace(
                id='123',
                has_flac=True,
                duration=301,
                artist_names=['The Gathering'],
                release=SimpleNamespace(title='Mandylion'),
            ),
        ]

    def get_direct_stream_url(self, track_id: str, quality: object) -> object:
        return SimpleNamespace(stream=f'https://example.invalid/{track_id}/{quality.value}')

    def get_stream_url(self, track_id: str, quality: object) -> str:
        return f'https://graphql.example.invalid/{track_id}/{quality.value}'


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> ZvukProvider:
    monkeypatch.setattr('soundsave.providers.zvuk.Client', FakeClient)
    return ZvukProvider('token')


class TestZvukProvider:
    def test_requires_token(self) -> None:
        with pytest.raises(ProviderNotConfiguredError):
            ZvukProvider(None)

    def test_search_maps_candidates(self, provider: ZvukProvider) -> None:
        candidates = provider.search(TrackQuery('The Gathering', 'Saturnine'), limit=5)
        assert len(candidates) == 1
        candidate = candidates[0]
        assert candidate.provider == 'zvuk'
        assert candidate.provider_id == '123'
        assert candidate.title == 'Saturnine'
        assert candidate.artists == ('The Gathering',)
        assert candidate.album == 'Mandylion'
        assert candidate.duration_s == 301.0
        assert candidate.has_flac is True

    def test_search_uses_artist_and_title(self, provider: ZvukProvider) -> None:
        provider.search(TrackQuery('The Gathering', 'Saturnine'))
        assert provider._client.search_query == 'The Gathering Saturnine'

    def test_get_stream_url(self, provider: ZvukProvider) -> None:
        candidate = Candidate(provider='zvuk', provider_id='123', title='Saturnine', artists=('The Gathering',))
        assert provider.get_stream_url(candidate, 'high') == 'https://example.invalid/123/high'
        assert provider.get_stream_url(candidate, 'flac') == 'https://example.invalid/123/flac'

    def test_unknown_quality(self, provider: ZvukProvider) -> None:
        candidate = Candidate(provider='zvuk', provider_id='123', title='Saturnine', artists=('The Gathering',))
        with pytest.raises(ValueError, match='Unknown stream quality'):
            provider.get_stream_url(candidate, 'ultra')

    def test_falls_back_to_graphql_when_direct_is_blocked(
        self,
        provider: ZvukProvider,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def blocked(*args: object, **kwargs: object) -> None:
            raise BotDetectedError('blocked')

        monkeypatch.setattr(provider._client, 'get_direct_stream_url', blocked)
        candidate = Candidate(provider='zvuk', provider_id='123', title='Saturnine', artists=('The Gathering',))

        assert provider.get_stream_url(candidate, 'high') == 'https://graphql.example.invalid/123/high'
