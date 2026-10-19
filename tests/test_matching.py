from __future__ import annotations

from soundsave.domain import Candidate, MatchStatus, TrackQuery
from soundsave.matching import normalize, rank_candidates, similarity


def make_candidate(
    *,
    title: str = 'Saturnine',
    artist: str = 'The Gathering',
    album: str | None = 'Mandylion',
    duration: float | None = 300.0,
    provider_id: str = '1',
) -> Candidate:
    return Candidate(
        provider='zvuk',
        provider_id=provider_id,
        title=title,
        artists=(artist,),
        album=album,
        duration_s=duration,
    )


class TestNormalize:
    def test_lowercases_and_strips_punctuation(self) -> None:
        assert normalize('The Gathering — Saturnine!') == 'the gathering saturnine'

    def test_removes_feat_and_remaster(self) -> None:
        assert normalize('Song (feat. Someone) [Remastered]') == 'song someone'

    def test_collapses_spaces(self) -> None:
        assert normalize('  A   B  ') == 'a b'


class TestSimilarity:
    def test_identical(self) -> None:
        assert similarity('Кино', 'кино') == 1.0

    def test_different(self) -> None:
        assert similarity('Кино', 'Ария') < 0.5

    def test_empty(self) -> None:
        assert similarity('', 'x') == 0.0


class TestRankCandidates:
    def test_exact_match(self) -> None:
        query = TrackQuery('The Gathering', 'Saturnine', 'Mandylion', 300.0)
        result = rank_candidates(query, [make_candidate()])
        assert result.status is MatchStatus.MATCHED
        assert result.candidate is not None
        assert result.candidate.score == 1.0

    def test_low_artist_score_is_rejected(self) -> None:
        query = TrackQuery('The Gathering', 'Saturnine', None, 300.0)
        result = rank_candidates(query, [make_candidate(artist='Another Band')])
        assert result.status is MatchStatus.NO_MATCH

    def test_duration_outside_tolerance_is_rejected(self) -> None:
        query = TrackQuery('The Gathering', 'Saturnine', None, 300.0)
        result = rank_candidates(query, [make_candidate(duration=400.0)], tolerance=5.0)
        assert result.status is MatchStatus.NO_MATCH

    def test_live_version_is_excluded(self) -> None:
        query = TrackQuery('The Gathering', 'Saturnine', None, 300.0)
        result = rank_candidates(query, [make_candidate(title='Saturnine (Live)')])
        assert result.status is MatchStatus.NO_MATCH

    def test_live_version_is_allowed_when_query_mentions_it(self) -> None:
        query = TrackQuery('The Gathering', 'Saturnine (Live)', None, 300.0)
        result = rank_candidates(query, [make_candidate(title='Saturnine (Live)')])
        assert result.status is MatchStatus.MATCHED

    def test_close_candidates_are_ambiguous(self) -> None:
        query = TrackQuery('The Gathering', 'Saturnine', None, 300.0)
        first = make_candidate(provider_id='1')
        second = make_candidate(provider_id='2')
        result = rank_candidates(query, [first, second])
        assert result.status is MatchStatus.AMBIGUOUS
        assert result.candidate is not None
        assert len(result.alternatives) == 1

    def test_below_min_score_is_rejected(self) -> None:
        query = TrackQuery('The Gathering', 'Saturnine', None, 300.0)
        result = rank_candidates(query, [make_candidate(title='Saturnina')], min_score=0.99)
        assert result.status is MatchStatus.REJECTED

    def test_no_candidates(self) -> None:
        query = TrackQuery('The Gathering', 'Saturnine')
        result = rank_candidates(query, [])
        assert result.status is MatchStatus.NO_MATCH
