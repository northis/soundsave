"""Small immutable data objects shared by the application."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


@dataclass(frozen=True)
class AudioQuality:
    """Codec and bitrate of an audio file."""

    codec: str
    bitrate_kbps: int | None = None
    lossless: bool = False

    @property
    def label(self) -> str:
        if self.lossless:
            return f'{self.codec.upper()} (lossless)'
        if self.bitrate_kbps is None:
            return self.codec.upper()
        return f'{self.codec.upper()} {self.bitrate_kbps} kbps'


@dataclass(frozen=True)
class TrackQuery:
    """Metadata used to find a track in an external catalogue."""

    artist: str
    title: str
    album: str | None = None
    duration_s: float | None = None


@dataclass
class LocalTrack:
    """A file in the local archive."""

    path: Path
    file_id: int | None
    artist: str
    title: str
    album: str | None
    duration_s: float | None
    quality: AudioQuality


@dataclass
class Candidate:
    """A track found in an external catalogue."""

    provider: str
    provider_id: str
    title: str
    artists: tuple[str, ...]
    album: str | None = None
    duration_s: float | None = None
    has_flac: bool = False
    score: float = 0.0
    artist_score: float = 0.0
    title_score: float = 0.0
    duration_score: float = 0.0
    album_score: float = 0.0
    reasons: tuple[str, ...] = ()

    @property
    def artist(self) -> str:
        return ', '.join(self.artists)


@dataclass
class MetadataCandidate(Candidate):
    """A track found in a metadata catalogue."""

    album_artists: tuple[str, ...] = ()
    year: int | None = None
    release_date: str | None = None
    genre: str | None = None
    url: str | None = None
    lyrics_available: bool = False


@dataclass
class MetadataMatch:
    """Result of looking up one local file in a metadata catalogue."""

    status: str
    candidate: MetadataCandidate | None = None
    score: float = 0.0
    reason: str = ''


class MatchStatus(str, Enum):
    """Outcome of matching a local track against catalogue candidates."""

    MATCHED = 'matched'
    AMBIGUOUS = 'ambiguous'
    NO_MATCH = 'no_match'
    REJECTED = 'rejected'


@dataclass
class MatchResult:
    """Result of ranking candidates for one local track."""

    query: TrackQuery
    status: MatchStatus
    candidate: Candidate | None = None
    alternatives: list[Candidate] = field(default_factory=list)
    reason: str = ''
