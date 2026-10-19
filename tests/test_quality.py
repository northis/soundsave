from __future__ import annotations

from pathlib import Path

from soundsave.domain import AudioQuality, Candidate, LocalTrack
from soundsave.quality import QualityPolicy, TargetFormat, is_below_target


def make_track(codec: str = 'mp3', bitrate: int | None = 192, lossless: bool = False) -> LocalTrack:
    return LocalTrack(
        path=Path('0001. Artist - Title.mp3'),
        file_id=1,
        artist='Artist',
        title='Title',
        album=None,
        duration_s=200.0,
        quality=AudioQuality(codec, bitrate, lossless),
    )


class TestIsBelowTarget:
    def test_mp3_192_is_below(self) -> None:
        assert is_below_target(make_track('mp3', 192), target_format=TargetFormat.MP3)

    def test_mp3_320_is_not_below(self) -> None:
        assert not is_below_target(make_track('mp3', 320), target_format=TargetFormat.MP3)

    def test_mp3_vbr_311_is_not_below(self) -> None:
        assert not is_below_target(make_track('mp3', 311), target_format=TargetFormat.MP3)

    def test_flac_is_not_below_for_mp3_target(self) -> None:
        assert not is_below_target(make_track('flac', None, lossless=True), target_format=TargetFormat.MP3)

    def test_flac_is_not_below_for_flac_target(self) -> None:
        assert not is_below_target(make_track('flac', None, lossless=True), target_format=TargetFormat.FLAC)

    def test_mp3_is_below_for_flac_target(self) -> None:
        assert is_below_target(make_track('mp3', 320), target_format=TargetFormat.FLAC)

    def test_aac_is_below_for_mp3_target(self) -> None:
        assert is_below_target(make_track('aac', 256), target_format=TargetFormat.MP3)

    def test_policy_does_not_change_archive_threshold(self) -> None:
        track = make_track('mp3', 192)
        for policy in QualityPolicy:
            assert is_below_target(track, policy=policy, target_format=TargetFormat.MP3)


def test_candidate_has_flac() -> None:
    candidate = Candidate(provider='zvuk', provider_id='1', title='Title', artists=('Artist',))
    assert candidate.has_flac is False
