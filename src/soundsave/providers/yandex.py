"""Yandex Music provider for the upgrade command."""

from __future__ import annotations

import logging

from yandex_music import Client, DownloadInfo
from yandex_music.exceptions import YandexMusicError

from ..domain import Candidate, TrackQuery
from ..quality import TARGET_MP3_BITRATE
from ..yandex_catalog import candidate_from_track
from .base import ProviderError, ProviderNotConfiguredError

logger = logging.getLogger(__name__)

_QUALITIES = frozenset({'flac', 'high', 'mid'})


def _choose_info(infos: list[DownloadInfo], quality: str) -> DownloadInfo | None:
    """Pick the download info for *quality*, ignoring previews."""
    if quality == 'flac':
        return next((info for info in infos if info.codec == 'flac'), None)
    mp3 = [info for info in infos if info.codec == 'mp3' and not info.preview]
    if quality == 'high':
        mp3 = [info for info in mp3 if info.bitrate_in_kbps >= TARGET_MP3_BITRATE]
    return max(mp3, key=lambda info: info.bitrate_in_kbps, default=None)


class YandexProvider:
    """Search Yandex Music and resolve direct 320 kbps MP3 stream URLs."""

    name = 'yandex'

    def __init__(self, token: str | None) -> None:
        if not token:
            raise ProviderNotConfiguredError('YANDEX_TOKEN (or API_KEY) is not set')
        try:
            self._client = Client(token=token).init()
        except Exception as exc:
            raise ProviderError(f'Yandex Music authentication failed: {exc}') from exc

    def search(self, query: TrackQuery, limit: int = 10) -> list[Candidate]:
        """Search Yandex Music by ``artist title``."""
        text = f'{query.artist} {query.title}'.strip()
        if not text:
            return []
        try:
            result = self._client.search(text, type_='track')
        except YandexMusicError as exc:
            raise ProviderError(f'Yandex Music search failed for {text!r}: {exc}') from exc
        tracks = getattr(getattr(result, 'tracks', None), 'results', None) or []
        return [candidate_from_track(track) for track in tracks[:limit]]

    def get_stream_url(self, candidate: Candidate, quality: str) -> str | None:
        """Resolve a direct stream URL, returning ``None`` when unavailable."""
        if quality not in _QUALITIES:
            raise ValueError(f'Unknown stream quality: {quality}')
        try:
            infos = self._client.tracks_download_info(candidate.provider_id) or []
        except YandexMusicError as exc:
            logger.debug('Yandex download info %s failed for %s: %s', quality, candidate.provider_id, exc)
            return None
        info = _choose_info(infos, quality)
        if info is None:
            return None
        try:
            return info.get_direct_link()
        except YandexMusicError as exc:
            logger.debug('Yandex direct link %s failed for %s: %s', quality, candidate.provider_id, exc)
            return None
