"""String normalisation and candidate scoring."""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

from .domain import Candidate, MatchResult, MatchStatus, TrackQuery

_FEAT_RE = re.compile(r'\b(?:feat|ft)\.?\s+', re.IGNORECASE)
_NOISE_RE = re.compile(
    r'\b(?:remaster(?:ed)?|deluxe(?:\s+edition)?|explicit|digital\s+remaster|album\s+version)\b',
    re.IGNORECASE,
)
_PUNCT_RE = re.compile(r'[^\w\s]+', re.UNICODE)
_SPACE_RE = re.compile(r'\s+')
_EXCLUDED_WORDS = ('live', 'cover', 'karaoke', 'instrumental', 'remix', 'acoustic', 'demo')

_ARTIST_WEIGHT = 0.40
_TITLE_WEIGHT = 0.40
_DURATION_WEIGHT = 0.15
_ALBUM_WEIGHT = 0.05

_MIN_FIELD_SCORE = 0.80
_AMBIGUITY_GAP = 0.05


def normalize(text: str) -> str:
    """Normalise *text* for fuzzy comparison."""
    text = unicodedata.normalize('NFKC', text).casefold()
    text = text.replace('&', ' and ')
    text = _FEAT_RE.sub(' ', text)
    text = _NOISE_RE.sub(' ', text)
    text = _PUNCT_RE.sub(' ', text)
    text = _SPACE_RE.sub(' ', text)
    return text.strip()


def similarity(left: str, right: str) -> float:
    """Return a 0..1 similarity of two strings."""
    left_n = normalize(left)
    right_n = normalize(right)
    if not left_n or not right_n:
        return 0.0
    if left_n == right_n:
        return 1.0
    return SequenceMatcher(None, left_n, right_n).ratio()


def artist_similarity(query_artist: str, candidate_artists: tuple[str, ...]) -> float:
    """Compare the query artist with the best matching candidate artist."""
    if not candidate_artists:
        return 0.0
    return max(similarity(query_artist, artist) for artist in candidate_artists)


def duration_similarity(
    query_duration: float | None,
    candidate_duration: float | None,
    tolerance: float,
) -> float:
    """Return how close two durations are, given an absolute *tolerance*."""
    if query_duration is None or candidate_duration is None:
        return 0.5
    delta = abs(query_duration - candidate_duration)
    return max(0.0, 1.0 - delta / tolerance)


def _excluded_version(title: str) -> str | None:
    normalized = normalize(title)
    for word in _EXCLUDED_WORDS:
        if re.search(rf'\b{word}\b', normalized):
            return word
    return None


def score_candidate(query: TrackQuery, candidate: Candidate, tolerance: float) -> Candidate:
    """Fill the score fields of *candidate* and return it."""
    artist = artist_similarity(query.artist, candidate.artists)
    title = similarity(query.title, candidate.title)
    duration = duration_similarity(query.duration_s, candidate.duration_s, tolerance)
    album = similarity(query.album, candidate.album) if query.album and candidate.album else 0.0

    candidate.artist_score = round(artist, 4)
    candidate.title_score = round(title, 4)
    candidate.duration_score = round(duration, 4)
    candidate.album_score = round(album, 4)
    candidate.score = round(
        _ARTIST_WEIGHT * artist + _TITLE_WEIGHT * title + _DURATION_WEIGHT * duration + _ALBUM_WEIGHT * album,
        4,
    )
    return candidate


def rank_candidates(
    query: TrackQuery,
    candidates: list[Candidate],
    *,
    min_score: float = 0.85,
    tolerance: float = 5.0,
    exclude_versions: bool = True,
) -> MatchResult:
    """Score, filter and rank *candidates* for *query*.

    A candidate is accepted only when artist and title each score at least
    0.80, the duration is inside *tolerance*, and (optionally) it is not a
    live/cover/remix version of a track whose title does not mention it.
    """
    scored = [score_candidate(query, candidate, tolerance) for candidate in candidates]
    accepted: list[Candidate] = []

    for candidate in scored:
        reasons: list[str] = []
        if candidate.artist_score < _MIN_FIELD_SCORE:
            reasons.append('artist below 0.80')
        if candidate.title_score < _MIN_FIELD_SCORE:
            reasons.append('title below 0.80')
        if (
            query.duration_s is not None
            and candidate.duration_s is not None
            and (abs(query.duration_s - candidate.duration_s) > tolerance)
        ):
            reasons.append('duration outside tolerance')
        if exclude_versions:
            excluded = _excluded_version(candidate.title)
            if excluded is not None and excluded not in normalize(query.title):
                reasons.append(f'excluded version: {excluded}')

        if reasons:
            candidate.reasons = tuple(reasons)
            continue
        accepted.append(candidate)

    accepted.sort(key=lambda item: item.score, reverse=True)

    if not accepted:
        return MatchResult(
            query=query,
            status=MatchStatus.NO_MATCH,
            alternatives=scored[:5],
            reason='no candidate passed the filters',
        )

    best = accepted[0]
    if best.score < min_score:
        return MatchResult(
            query=query,
            status=MatchStatus.REJECTED,
            candidate=best,
            alternatives=accepted[1:5],
            reason=f'score {best.score:.3f} is below {min_score:.3f}',
        )

    if len(accepted) > 1 and best.score - accepted[1].score < _AMBIGUITY_GAP:
        return MatchResult(
            query=query,
            status=MatchStatus.AMBIGUOUS,
            candidate=best,
            alternatives=accepted[1:5],
            reason='top candidates are too close',
        )

    return MatchResult(
        query=query,
        status=MatchStatus.MATCHED,
        candidate=best,
        alternatives=accepted[1:5],
    )
