from __future__ import annotations

from pathlib import Path

from soundsave.manifest import Manifest, ManifestRecord


def make_record(path: str, status: str = 'planned') -> ManifestRecord:
    return ManifestRecord(
        path=path,
        status=status,
        file_id=1,
        artist='Artist',
        title='Title',
        old_codec='mp3',
        old_bitrate=192,
    )


class TestManifest:
    def test_save_and_get(self, tmp_path: Path) -> None:
        with Manifest(tmp_path / 'manifest.sqlite3') as manifest:
            record = make_record('/music/0001. Artist - Title.mp3')
            manifest.save(record)

            loaded = manifest.get(record.path)
            assert loaded is not None
            assert loaded.status == 'planned'
            assert loaded.artist == 'Artist'
            assert loaded.old_bitrate == 192
            assert loaded.updated_at

    def test_upsert_updates_status(self, tmp_path: Path) -> None:
        with Manifest(tmp_path / 'manifest.sqlite3') as manifest:
            record = make_record('/music/0001. Artist - Title.mp3')
            manifest.save(record)

            record.status = 'replaced'
            record.new_codec = 'mp3'
            record.new_bitrate = 320
            record.has_flac = True
            manifest.save(record)

            loaded = manifest.get(record.path)
            assert loaded is not None
            assert loaded.status == 'replaced'
            assert loaded.new_bitrate == 320
            assert loaded.has_flac is True

    def test_all_and_delete(self, tmp_path: Path) -> None:
        with Manifest(tmp_path / 'manifest.sqlite3') as manifest:
            manifest.save(make_record('/music/0001. A - B.mp3'))
            manifest.save(make_record('/music/0002. C - D.mp3'))
            assert len(manifest.all()) == 2

            manifest.delete('/music/0001. A - B.mp3')
            assert len(manifest.all()) == 1

    def test_missing_record(self, tmp_path: Path) -> None:
        with Manifest(tmp_path / 'manifest.sqlite3') as manifest:
            assert manifest.get('/music/nope.mp3') is None
