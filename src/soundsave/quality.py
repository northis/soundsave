"""Quality policies for the archive audit and upgrade."""

from __future__ import annotations

from enum import Enum

from .domain import Candidate, LocalTrack


class QualityPolicy(str, Enum):
    """How aggressively the archive should be upgraded."""

    REPORT = 'report'
    BEST = 'best'
    TRUE320 = 'true320'


class TargetFormat(str, Enum):
    """Target container/codec for replaced files."""

    KEEP = 'keep'
    MP3 = 'mp3'
    FLAC = 'flac'


TARGET_MP3_BITRATE = 320
# VBR 320 files often report an average bitrate below 320. Treat anything at
# or above 300 kbps as already good enough.
MIN_ACCEPTABLE_MP3_BITRATE = 300
LOSSLESS_CODECS = frozenset({'flac', 'alac', 'wav', 'aiff'})


def is_lossless(track: LocalTrack) -> bool:
    """Return whether the local file is lossless."""
    return track.quality.lossless or track.quality.codec.lower() in LOSSLESS_CODECS


def is_below_target(
    track: LocalTrack,
    *,
    policy: QualityPolicy = QualityPolicy.BEST,
    target_format: TargetFormat = TargetFormat.MP3,
) -> bool:
    """Return whether *track* should be considered for replacement.

    ``REPORT`` and ``BEST`` treat any lossy file below 320 kbps MP3 as a
    candidate. ``TRUE320`` is stricter only in the sense that the provider must
    offer a genuine 320 kbps stream; the archive-side check is the same.
    """
    del policy  # the archive-side threshold is the same for all policies
    if target_format is TargetFormat.FLAC:
        return not is_lossless(track)

    if is_lossless(track):
        return False

    if track.quality.codec.lower() == 'mp3':
        return (track.quality.bitrate_kbps or 0) < MIN_ACCEPTABLE_MP3_BITRATE

    # A non-MP3 lossy file (AAC/Opus/Vorbis) is not a 320 kbps MP3 archive item.
    return True


def has_better_quality(candidate: Candidate, *, target_format: TargetFormat) -> bool:
    """Return whether *candidate* can satisfy *target_format*."""
    if target_format is TargetFormat.FLAC:
        return candidate.has_flac
    return True


def describe_target(target_format: TargetFormat) -> str:
    """Return a human-readable description of the target quality."""
    if target_format is TargetFormat.FLAC:
        return 'FLAC (lossless)'
    if target_format is TargetFormat.MP3:
        return f'MP3 {TARGET_MP3_BITRATE} kbps'
    return f'best available (FLAC or MP3 {TARGET_MP3_BITRATE} kbps)'
