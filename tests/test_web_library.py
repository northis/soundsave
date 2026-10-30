from __future__ import annotations

import os
from pathlib import Path

import pytest
from mutagen.id3 import ID3, TIT2

from soundsave.archive import read_tag_data
from soundsave.web.cover_cache import cover_cache_key
from soundsave.web.db import LibraryStore
from soundsave.web.library import LibraryError, LibraryScanner
from soundsave.web.metadata import read_library_metadata
from soundsave.web.settings import WebSettings

from .web_helpers import COVER, make_config, make_library, make_mp3


def make_scanner(root: Path) -> tuple[LibraryScanner, LibraryStore]:
    config = make_config(root)
    settings = WebSettings.from_config(config)
    store = LibraryStore(settings.db_path)
    store.init()
    return LibraryScanner(settings, store), store


def test_playlists_are_flat_subfolders(root: Path) -> None:
    make_library(root)
    scanner, store = make_scanner(root)

    report = scanner.reconcile()

    playlists = store.list_playlists()
    assert report.playlists == 2
    assert [playlist.name for playlist in playlists] == ['Main', 'Chill']
    assert playlists[0].syncable is True
    assert playlists[1].syncable is False
    assert playlists[0].track_count == 2
    assert playlists[1].track_count == 1


def test_track_order_number_and_metadata(root: Path) -> None:
    make_library(root)
    scanner, store = make_scanner(root)
    scanner.reconcile()
    main = store.get_syncable_playlist()
    assert main is not None

    tracks = store.tracks_for_playlist(main.id)

    assert [track.filename for track in tracks] == [
        '0001. The Gathering - Saturnine.mp3',
        '0002. The Gathering - Strange Machines.mp3',
    ]
    assert [track.position for track in tracks] == [1, 2]
    assert [track.number for track in tracks] == [1, 2]
    assert tracks[0].title == 'Saturnine'
    assert tracks[0].artists == ['The Gathering']
    assert tracks[0].format == 'MP3'
    assert tracks[0].bitrate_kbps == 128
    assert tracks[0].sample_rate_hz == 44100
    assert tracks[0].has_cover is True


def test_reconciliation_adds_updates_and_removes(root: Path) -> None:
    make_library(root)
    scanner, store = make_scanner(root)
    scanner.reconcile()
    main = store.get_syncable_playlist()
    assert main is not None

    make_mp3(root / 'music' / 'Main' / '0003. New - Track.mp3', 'Track', 'New')
    report = scanner.reconcile()
    assert report.added == 1
    assert len(store.tracks_for_playlist(main.id)) == 3

    (root / 'music' / 'Main' / '0002. The Gathering - Strange Machines.mp3').unlink()
    report = scanner.reconcile()
    assert report.removed == 1
    assert len(store.tracks_for_playlist(main.id)) == 2


def test_changed_file_is_reread(root: Path) -> None:
    make_library(root)
    scanner, store = make_scanner(root)
    scanner.reconcile()
    main = store.get_syncable_playlist()
    assert main is not None
    path = root / 'music' / 'Main' / '0001. The Gathering - Saturnine.mp3'

    make_mp3(path, 'Changed', 'The Gathering')
    os.utime(path, None)
    report = scanner.reconcile()
    assert report.updated == 1
    assert store.tracks_for_playlist(main.id)[0].title == 'Changed'


def test_nested_directories_are_ignored(root: Path) -> None:
    make_library(root)
    nested = root / 'music' / 'Main' / 'nested'
    nested.mkdir()
    make_mp3(nested / '0001. Nested - Track.mp3', 'Track', 'Nested')
    scanner, store = make_scanner(root)

    report = scanner.reconcile()

    main = store.get_syncable_playlist()
    assert main is not None
    assert main.track_count == 2
    assert any('nested directories are ignored' in warning for warning in report.warnings)


def test_root_files_are_ignored_in_multi_playlist_mode(root: Path) -> None:
    make_library(root)
    make_mp3(root / 'music' / 'root.mp3', 'Root')
    scanner, store = make_scanner(root)

    scanner.reconcile()

    main = store.get_syncable_playlist()
    assert main is not None
    assert main.track_count == 2


def test_flat_legacy_mode_uses_target_as_single_playlist(root: Path) -> None:
    music = root / 'music'
    music.mkdir()
    make_mp3(music / '0001. A - One.mp3', 'One', 'A')
    config = make_config(root, main=None)
    settings = WebSettings.from_config(config)
    store = LibraryStore(settings.db_path)
    store.init()

    report = LibraryScanner(settings, store).reconcile()

    assert report.playlists == 1
    playlist = store.list_playlists()[0]
    assert playlist.folder_name == '.'
    assert playlist.syncable is True
    assert playlist.track_count == 1


def test_missing_playlist_is_marked(root: Path) -> None:
    make_library(root)
    scanner, store = make_scanner(root)
    scanner.reconcile()
    (root / 'music' / 'Chill').rename(root / 'music' / 'Chill-gone')

    scanner.reconcile()

    names = {playlist.name for playlist in store.list_playlists()}
    assert names == {'Main', 'Chill-gone'}
    all_names = {playlist.name for playlist in store.list_playlists(include_missing=True)}
    assert 'Chill' in all_names
    chill = next(playlist for playlist in store.list_playlists(include_missing=True) if playlist.name == 'Chill')
    assert chill.missing is True


def test_cover_presence_without_loading_bytes(root: Path) -> None:
    path = root / 'music' / 'Main' / '0001. A - Covered.mp3'
    path.parent.mkdir(parents=True)
    make_mp3(path, 'Covered', 'A', cover=COVER)
    data = read_tag_data(path, include_cover=False)

    assert data.has_cover is True
    assert data.cover is None


def test_metadata_falls_back_to_filename(root: Path) -> None:
    path = root / 'music' / 'Main' / '0007. Fallback Artist - Fallback Title.mp3'
    path.parent.mkdir(parents=True)
    path.write_bytes((bytes((0xFF, 0xFB, 0x90, 0x00)) + b'\x00' * 413) * 10)

    metadata = read_library_metadata(path)

    assert metadata.title == 'Fallback Title'
    assert metadata.artists == ['Fallback Artist']
    assert metadata.format == 'MP3'
    assert metadata.duration_s is not None


def test_reconcile_playlist_leaves_other_playlists_untouched(root: Path) -> None:
    make_library(root)
    scanner, store = make_scanner(root)
    scanner.reconcile()
    main = store.get_syncable_playlist()
    chill = next(playlist for playlist in store.list_playlists() if playlist.name == 'Chill')
    assert main is not None

    make_mp3(root / 'music' / 'Main' / '0001. The Gathering - Saturnine.mp3', 'Changed', 'The Gathering')
    os.utime(root / 'music' / 'Main' / '0001. The Gathering - Saturnine.mp3', None)
    make_mp3(root / 'music' / 'Chill' / '0002. Someone - New.mp3', 'New', 'Someone')

    report = scanner.reconcile_playlist('Chill')

    assert report.playlists == 1
    assert report.added == 1
    assert [track.filename for track in store.tracks_for_playlist(chill.id)] == [
        '01 - Someone - Something.mp3',
        '0002. Someone - New.mp3',
    ]
    assert store.tracks_for_playlist(main.id)[0].title == 'Saturnine'


def test_reconcile_playlist_detects_renamed_files(root: Path) -> None:
    make_library(root)
    scanner, store = make_scanner(root)
    scanner.reconcile()
    chill = next(playlist for playlist in store.list_playlists() if playlist.name == 'Chill')
    old = root / 'music' / 'Chill' / '01 - Someone - Something.mp3'
    old.rename(root / 'music' / 'Chill' / '0001. Someone - Something Else.mp3')

    report = scanner.reconcile_playlist('Chill', force=True)

    assert report.removed == 1
    assert report.added == 1
    assert [track.filename for track in store.tracks_for_playlist(chill.id)] == [
        '0001. Someone - Something Else.mp3',
    ]


def test_reconcile_playlist_force_rereads_unchanged_file(root: Path) -> None:
    make_library(root)
    scanner, store = make_scanner(root)
    scanner.reconcile()
    main = store.get_syncable_playlist()
    assert main is not None
    path = root / 'music' / 'Main' / '0001. The Gathering - Saturnine.mp3'
    before = path.stat()

    # Same-length title, so the file keeps its size; restoring mtime makes the
    # stat identical and hides the change from an incremental scan.
    tags = ID3(path)
    tags.setall('TIT2', [TIT2(encoding=3, text='Saturnina')])
    tags.save(path)
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    after = path.stat()
    assert (after.st_size, after.st_mtime_ns) == (before.st_size, before.st_mtime_ns)

    report = scanner.reconcile()
    assert report.updated == 0
    assert store.tracks_for_playlist(main.id)[0].title == 'Saturnine'

    report = scanner.reconcile_playlist('Main', force=True)

    assert report.updated == 2
    assert report.added == 0
    assert store.tracks_for_playlist(main.id)[0].title == 'Saturnina'


def test_reconcile_playlist_rejects_unknown_folders(root: Path) -> None:
    make_library(root)
    scanner, _ = make_scanner(root)
    scanner.reconcile()

    with pytest.raises(LibraryError, match='does not exist'):
        scanner.reconcile_playlist('Missing')


def test_reconcile_playlist_keeps_disappeared_playlists_visible(root: Path) -> None:
    make_library(root)
    scanner, store = make_scanner(root)
    scanner.reconcile()
    (root / 'music' / 'Chill').rename(root / 'music' / 'Chill-gone')

    report = scanner.reconcile_playlist('Main', force=True)

    assert report.playlists == 1
    assert {playlist.name for playlist in store.list_playlists()} == {'Main', 'Chill'}
    chill = next(playlist for playlist in store.list_playlists() if playlist.name == 'Chill')
    assert chill.missing is False


def test_reconcile_playlist_drops_cached_covers_on_request(root: Path) -> None:
    make_library(root)
    scanner, store = make_scanner(root)
    scanner.reconcile()
    main = store.get_syncable_playlist()
    assert main is not None
    track = store.tracks_for_playlist(main.id)[0]
    path = scanner.settings.playlist_path(main.folder_name) / track.filename

    current = scanner.settings.covers_dir / f'{cover_cache_key(path, track.etag)}.webp'
    legacy = scanner.settings.covers_dir / f'{cover_cache_key(path, track.etag, version=1)}.webp'
    current.write_bytes(b'current')
    legacy.write_bytes(b'legacy')

    report = scanner.reconcile_playlist('Main', refresh_covers=True)

    assert report.covers == 2
    assert not current.exists()
    assert not legacy.exists()


def test_reconcile_playlist_rejects_subfolders_in_flat_mode(root: Path) -> None:
    music = root / 'music'
    music.mkdir()
    make_mp3(music / '0001. A - One.mp3', 'One', 'A')
    config = make_config(root, main=None)
    settings = WebSettings.from_config(config)
    store = LibraryStore(settings.db_path)
    store.init()
    scanner = LibraryScanner(settings, store)
    scanner.reconcile()

    report = scanner.reconcile_playlist('.', force=True)
    assert report.tracks == 1

    with pytest.raises(LibraryError, match='single flat playlist'):
        scanner.reconcile_playlist('Chill')
