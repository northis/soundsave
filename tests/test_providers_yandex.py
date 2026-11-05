from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from soundsave.domain import Candidate, TrackQuery
from soundsave.providers.base import ProviderNotConfiguredError
from soundsave.providers.yandex import YandexProvider


class FakeModel(SimpleNamespace):
    """A Yandex Music model stub that supports the ``obj['key']`` accessor."""

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)


class FakeDownloadInfo:
    def __init__(
        self,
        codec: str = 'mp3',
        bitrate_in_kbps: int = 320,
        *,
        preview: bool = False,
    ) -> None:
        self.codec = codec
        self.bitrate_in_kbps = bitrate_in_kbps
        self.preview = preview

    def get_direct_link(self) -> str:
        return f'https://example.invalid/{self.codec}/{self.bitrate_in_kbps}'


def make_track(track_id: int = 123, title: str = 'Saturnine') -> FakeModel:
    album = FakeModel(
        id=10,
        title='Mandylion',
        version=None,
        artists=[FakeModel(name='The Gathering')],
        year=1995,
        release_date='1995-08-22T00:00:00+04:00',
        genre='gothic metal',
    )
    return FakeModel(
        id=track_id,
        title=title,
        version=None,
        artists=[FakeModel(name='The Gathering')],
        albums=[album],
        duration_ms=300_000,
        lyrics_info=None,
    )


class FakeClient:
    def __init__(self, token: str | None = None, **_: object) -> None:
        self.token = token
        self.search_text: str | None = None
        self.search_type: str | None = None
        self.infos: list[FakeDownloadInfo] = []

    def init(self) -> FakeClient:
        return self

    def search(self, text: str, type_: str = 'all') -> object:
        self.search_text = text
        self.search_type = type_
        return SimpleNamespace(tracks=SimpleNamespace(results=[make_track()]))

    def tracks_download_info(self, track_id: str) -> list[FakeDownloadInfo]:
        assert track_id == '123'
        return self.infos


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> YandexProvider:
    monkeypatch.setattr('soundsave.providers.yandex.Client', FakeClient)
    return YandexProvider('token')


class TestYandexProvider:
    def test_requires_token(self) -> None:
        with pytest.raises(ProviderNotConfiguredError):
            YandexProvider(None)

    def test_search_maps_candidates(self, provider: YandexProvider) -> None:
        candidates = provider.search(TrackQuery('The Gathering', 'Saturnine'), limit=5)

        assert len(candidates) == 1
        candidate = candidates[0]
        assert candidate.provider == 'yandex'
        assert candidate.provider_id == '123'
        assert candidate.title == 'Saturnine'
        assert candidate.artists == ('The Gathering',)
        assert candidate.album == 'Mandylion'
        assert candidate.duration_s == 300.0
        assert candidate.has_flac is False

    def test_search_uses_artist_and_title(self, provider: YandexProvider) -> None:
        provider.search(TrackQuery('The Gathering', 'Saturnine'))

        assert provider._client.search_text == 'The Gathering Saturnine'
        assert provider._client.search_type == 'track'

    def test_get_stream_url_prefers_320_mp3(self, provider: YandexProvider) -> None:
        provider._client.infos = [
            FakeDownloadInfo(bitrate_in_kbps=192),
            FakeDownloadInfo(bitrate_in_kbps=320),
        ]
        candidate = Candidate(provider='yandex', provider_id='123', title='Saturnine', artists=('The Gathering',))

        assert provider.get_stream_url(candidate, 'high') == 'https://example.invalid/mp3/320'

    def test_get_stream_url_skips_previews_and_low_bitrate(self, provider: YandexProvider) -> None:
        provider._client.infos = [
            FakeDownloadInfo(bitrate_in_kbps=320, preview=True),
            FakeDownloadInfo(bitrate_in_kbps=192),
        ]
        candidate = Candidate(provider='yandex', provider_id='123', title='Saturnine', artists=('The Gathering',))

        assert provider.get_stream_url(candidate, 'high') is None

    def test_get_stream_url_flac(self, provider: YandexProvider) -> None:
        provider._client.infos = [
            FakeDownloadInfo(codec='mp3', bitrate_in_kbps=320),
            FakeDownloadInfo(codec='flac', bitrate_in_kbps=0),
        ]
        candidate = Candidate(provider='yandex', provider_id='123', title='Saturnine', artists=('The Gathering',))

        assert provider.get_stream_url(candidate, 'flac') == 'https://example.invalid/flac/0'

    def test_unknown_quality(self, provider: YandexProvider) -> None:
        candidate = Candidate(provider='yandex', provider_id='123', title='Saturnine', artists=('The Gathering',))

        with pytest.raises(ValueError, match='Unknown stream quality'):
            provider.get_stream_url(candidate, 'ultra')
