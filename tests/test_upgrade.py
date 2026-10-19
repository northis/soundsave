from __future__ import annotations

from pathlib import Path

from soundsave.config import Config
from soundsave.domain import AudioQuality, Candidate, LocalTrack
from soundsave.manifest import Manifest
from soundsave.upgrade import UpgradeOptions, run_upgrade


class FakeProvider:
    name = 'fake'

    def __init__(self, candidates: list[Candidate]) -> None:
        self.candidates = candidates
        self.searches = 0

    def search(self, query: object, limit: int = 10) -> list[Candidate]:
        self.searches += 1
        return self.candidates

    def get_stream_url(self, candidate: Candidate, quality: str) -> str:
        return 'https://example.invalid/stream'


def make_track(path: Path, *, bitrate: int = 192, file_id: int = 1) -> LocalTrack:
    return LocalTrack(
        path=path,
        file_id=file_id,
        artist='The Gathering',
        title='Saturnine',
        album='Mandylion',
        duration_s=300.0,
        quality=AudioQuality('mp3', bitrate),
    )


def make_candidate(provider_id: str = '123', artist: str = 'The Gathering') -> Candidate:
    return Candidate(
        provider='fake',
        provider_id=provider_id,
        title='Saturnine',
        artists=(artist,),
        album='Mandylion',
        duration_s=300.0,
        has_flac=True,
    )


def make_config(tmp_path: Path) -> Config:
    return Config(
        yandex_token=None,
        zvuk_token='token',
        playlist_url=None,
        target_dir=tmp_path,
        delay=0.0,
    )


def make_options(**overrides: object) -> UpgradeOptions:
    values: dict[str, object] = {'apply': False, 'delay': 0.0}
    values.update(overrides)
    return UpgradeOptions(**values)  # type: ignore[arg-type]


class TestRunUpgrade:
    def test_audit_plans_without_changes(
        self,
        tmp_path: Path,
        monkeypatch: object,
    ) -> None:
        track = make_track(tmp_path / '0001. The Gathering - Saturnine.mp3')
        track.path.write_bytes(b'old')
        monkeypatch.setattr('soundsave.upgrade.scan_archive', lambda *args, **kwargs: [track])  # type: ignore[attr-defined]

        def fail_apply(*args: object, **kwargs: object) -> None:
            raise AssertionError('_apply_track must not be called without --apply')

        monkeypatch.setattr('soundsave.upgrade._apply_track', fail_apply)  # type: ignore[attr-defined]

        rows = run_upgrade(make_config(tmp_path), FakeProvider([make_candidate()]), make_options())

        assert len(rows) == 1
        assert rows[0].status == 'planned'
        assert rows[0].candidate_id == '123'
        assert track.path.read_bytes() == b'old'

    def test_apply_replaces_and_updates_manifest(
        self,
        tmp_path: Path,
        monkeypatch: object,
    ) -> None:
        track = make_track(tmp_path / '0001. The Gathering - Saturnine.mp3')
        track.path.write_bytes(b'old')
        backup = tmp_path / '.soundsave' / 'backups' / '20260101' / track.path.name
        monkeypatch.setattr('soundsave.upgrade.scan_archive', lambda *args, **kwargs: [track])  # type: ignore[attr-defined]

        def fake_apply(*args: object, **kwargs: object) -> tuple[Path, AudioQuality, Path]:
            track.path.write_bytes(b'new')
            return track.path, AudioQuality('mp3', 320), backup

        monkeypatch.setattr('soundsave.upgrade._apply_track', fake_apply)  # type: ignore[attr-defined]

        rows = run_upgrade(
            make_config(tmp_path),
            FakeProvider([make_candidate()]),
            make_options(apply=True),
        )

        assert len(rows) == 1
        assert rows[0].status == 'replaced'
        assert rows[0].new_quality == 'MP3 320 kbps'
        assert rows[0].backup_path == str(backup)
        assert track.path.read_bytes() == b'new'

        with Manifest(tmp_path / '.soundsave' / 'manifest.sqlite3') as manifest:
            record = manifest.get(str(track.path))
            assert record is not None
            assert record.status == 'replaced'
            assert record.new_bitrate == 320

    def test_ok_track_is_not_searched(
        self,
        tmp_path: Path,
        monkeypatch: object,
    ) -> None:
        track = make_track(tmp_path / '0001. The Gathering - Saturnine.mp3', bitrate=320)
        track.path.write_bytes(b'old')
        monkeypatch.setattr('soundsave.upgrade.scan_archive', lambda *args, **kwargs: [track])  # type: ignore[attr-defined]
        provider = FakeProvider([make_candidate()])

        rows = run_upgrade(make_config(tmp_path), provider, make_options())

        assert len(rows) == 1
        assert rows[0].status == 'ok'
        assert provider.searches == 0

    def test_no_match(self, tmp_path: Path, monkeypatch: object) -> None:
        track = make_track(tmp_path / '0001. The Gathering - Saturnine.mp3')
        track.path.write_bytes(b'old')
        monkeypatch.setattr('soundsave.upgrade.scan_archive', lambda *args, **kwargs: [track])  # type: ignore[attr-defined]

        rows = run_upgrade(
            make_config(tmp_path),
            FakeProvider([make_candidate(artist='Another Band')]),
            make_options(),
        )

        assert rows[0].status == 'no_match'

    def test_ambiguous(self, tmp_path: Path, monkeypatch: object) -> None:
        track = make_track(tmp_path / '0001. The Gathering - Saturnine.mp3')
        track.path.write_bytes(b'old')
        monkeypatch.setattr('soundsave.upgrade.scan_archive', lambda *args, **kwargs: [track])  # type: ignore[attr-defined]

        rows = run_upgrade(
            make_config(tmp_path),
            FakeProvider([make_candidate('1'), make_candidate('2')]),
            make_options(),
        )

        assert rows[0].status == 'ambiguous'

    def test_limit_stops_after_n_tracks(self, tmp_path: Path, monkeypatch: object) -> None:
        first = make_track(tmp_path / '0001. The Gathering - Saturnine.mp3', file_id=1)
        second = make_track(tmp_path / '0002. The Gathering - Saturnine.mp3', file_id=2)
        first.path.write_bytes(b'old')
        second.path.write_bytes(b'old')
        monkeypatch.setattr('soundsave.upgrade.scan_archive', lambda *args, **kwargs: [first, second])  # type: ignore[attr-defined]

        rows = run_upgrade(
            make_config(tmp_path),
            FakeProvider([make_candidate()]),
            make_options(limit=1),
        )

        assert len(rows) == 1
