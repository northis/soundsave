# Changelog

All notable changes to the organizer CLI are documented here. The vendored
`yandex-music-api` library keeps its own history in [`CHANGES.md`](CHANGES.md).

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [1.1.0] - 2026-10-19

### Changed

- Synced the vendored `yandex-music-api` library from v2.2.0 to v3.2.2
  (65 upstream commits). The organizer code was checked against the new client,
  track, playlist and download-info APIs.
- Organizer releases now use the `organizer-vX.Y.Z` tag namespace. The previous
  `v1.0.0` tag collided with the upstream tag of the same name.

### Added

- `--dry-run` option: fetch the playlist and list the tracks that would be
  downloaded without writing anything to disk.
- `--version` option.
- Unit tests for playlist URL parsing, filename matching, local id detection,
  download-info selection, dry-run mode and the download orchestration.
- This changelog.

### Fixed

- The filename regex now escapes the dot before the numeric id and the `.mp3`
  extension. Behaviour for normal filenames is unchanged.
- `MAX_DOWNLOADS=0` is no longer reported as a missing configuration key;
  non-integer and negative values are rejected with a clear error.
- A playlist with no tracks no longer crashes on `len(playlist.tracks)`.
- A failed lyrics or cover download no longer aborts the whole run and leaves a
  temporary file behind; the track is saved without the optional metadata and a
  warning is logged.

### Verification

- CI: all 21 unit-test jobs pass (Linux, macOS and Windows; Python 3.8–3.14),
  along with the ruff, pyrefly and codegen checks.
- Local test suite with the documented `requirements-dev.txt` setup:
  `2440 passed, 8 skipped` (the skipped tests require the optional Ynison
  extras, which the organizer does not use).
- Organizer tests: see `tests/test_organizer.py`.
- Live read-only check against a 1292-track playlist: playlist fetched, 20/20
  existing tracks matched by filename, no downloads performed.
- The live check also confirmed the reason for the 1.2 roadmap item: a sample of
  50 tracks returned only `mp3 192` (one track additionally had `aac 128`),
  without a 320 kbps option.

## [1.0.0] - 2025-08-31

### Added

- Initial organizer CLI built on `yandex-music-api` v2.2.0.
- Token-based authentication via `.env`.
- Delta download of new playlist tracks, sequential `NNNN. Artist - Title.mp3`
  filenames, ID3 tags, cover art, lyrics and a link to the track.
