# Changelog

All notable changes to Soundsave are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [2.1.7] - 2026-10-28

### Added

- Playback diagnostics: audio, Media Session, visibility/freeze and network
  events plus a 15 s heartbeat are kept in a local journal. Settings can copy
  it or send it to the server (`POST /api/diag`, written to the container log).

### Changed

- Only the track list scrolls; the page itself no longer scrolls, so the top
  bar and the player stay in place on mobile.
- Cleaner refresh, settings, theme and download icons.

## [2.1.6] - 2026-10-28

### Fixed

- The current and the next track are now loaded fully into memory (from the
  offline cache when available) and played from a Blob URL. Android throttles
  the network of a locked page even with an active media session, so a Range
  stream stalled and the music stopped once its buffer ran out.
- The hidden `<audio>` element is really rendered now: Chromium hides `<audio>`
  without `controls` with a `display:none !important` UA rule, so the 2.1.5
  change had no effect.

## [2.1.5] - 2026-10-28

### Fixed

- The hidden `<audio>` element is now rendered as a transparent 1px element
  instead of `display: none`. Some Android Chromium builds stop treating a
  detached media element as active and freeze the page while the screen is off.
- Media Session position is refreshed during playback, and the playback state
  is set explicitly on play/pause.
- Returning to the app waits briefly for Wi-Fi/DNS to recover before resuming
  playback.
- The Cloudflare Access bypass documentation now includes the favicon paths;
  the 401 was unrelated to audio playback.

## [2.1.4] - 2026-10-28

### Added

- A build badge in the top-right corner (and in Settings on narrow screens)
  shows the running release tag and short commit, both linked to GitHub. The
  values come from `/api/version` and the Docker build arguments.
- `/api/version` now returns `short`, `tag`, `tag_url`, `commit_url`,
  `repo_url`, `commit_date` and `dirty` in addition to version and commit.

### Changed

- The PWA and static PNG icons now use the vertically mirrored bass clef that
  the header and favicon already showed. All variants (normal, maskable,
  apple-touch and favicon) are now visually consistent.

## [2.1.3] - 2026-10-28

### Fixed

- Android/PWA playback no longer goes through the service worker for uncached
  tracks. The worker could be frozen with the page while the screen was off,
  cutting the stream after the buffered part ran out. Live playback now goes
  directly to the network; only cached tracks use the worker.
- The audio element is attached to the document, uses `preload="auto"` and no
  longer sets `crossOrigin`; this improves background buffering and Media
  Session behaviour.
- Automatic cache downloads are cancelled when the page is hidden, so locking
  the screen no longer leaves a failed cache download and a stale error marker.
  Stale cache errors are cleaned on startup.
- A failed cache download now shows a retry/refresh icon instead of an
  exclamation mark; the latter was easy to mistake for a playback error.
- If playback was active when the app was hidden, it is resumed when the app
  becomes visible again.

## [2.1.2] - 2026-10-27

### Changed

- PWA icons, favicon, apple-touch icon and the manifest link now carry a
  version query (`?v=2.1.2`), so a new release cannot keep serving the previous
  icon from the service worker or browser cache.
- The manifest is served with `Cache-Control: no-cache`; the favicon redirect
  and service worker precache use the versioned URLs.
- The service worker shell cache was bumped to `v4`.

## [2.1.1] - 2026-10-27

### Changed

- PWA icons and favicon now use a yellow bass clef on a purple gradient. The
  in-app brand mark and empty state use the same clef.
- The PWA manifest splash colors follow the purple icon background.
- The service worker shell cache was bumped so installed apps pick up the new
  icons.

## [2.1.0] - 2026-10-27

### Added

- `soundsave serve` — a FastAPI web player and installable PWA for a library of
  flat playlist subfolders. It streams MP3/AAC/FLAC with HTTP Range, extracts
  embedded covers, sorts by number/title/artist, and keeps the playing track in
  view.
- SQLite library index with startup reconciliation against the music folder,
  incremental rescans after sync and a manual refresh endpoint.
- A sync button for the main playlist only. The web sync downloads Yandex
  streams up to `MAX_DOWNLOADS` and deliberately does not use the Zvuk 320 kbps
  fallback.
- Client-side PWA audio cache (Cache API + IndexedDB LRU index), cache progress
  on the seek bar, offline playback of cached tracks and Media Session support.
- Dockerfile and docker-compose.yml with music/data bind mounts, plus
  `MUSIC_HOST_DIR`/`DATA_HOST_DIR` for host paths.
- `soundsave snapshot` and `deploy/windows/update.ps1`: database snapshots,
  clean-worktree image builds, commit/version labels, health waiting and
  `soundsave:previous` rollback.
- `soundsave tags` — checks the existing archive against Yandex Music and,
  with `--apply`, fills only missing tags, front cover art and lyrics. It never
  downloads audio, renames files or overwrites non-empty values; `TRCK`/`TPOS`
  remain local archive data.
- `soundsave tags` now supports Discogs as a fallback metadata source and
  `--source discogs` for a Discogs-only run. Discogs requests are throttled by
  `DISCOGS_DELAY` and retried on HTTP 429; complete files are skipped unless
  `--no-skip-complete` is given.

### Changed

- `MUSIC_DIR`, `MAIN_PLAYLIST`, `DATA_DIR`, `SOUNDSAVE_HOST` and
  `SOUNDSAVE_PORT` are available in configuration. `TARGET_DIR` remains
  supported as the legacy flat-archive mode.
- `AudioQuality` now carries the sample rate; MP4/M4A tags and covers are read
  by the shared tag reader.
- `soundsave sync` now prefers a genuine 320 kbps MP3 stream from Yandex
  Music and falls back to Zvuk when Yandex only offers a lower bitrate. If
  Zvuk has no confident match or cannot provide 320 kbps, the best Yandex
  stream is used so that the track is still archived.

## [2.0.0] - 2026-10-19

### Changed

- The project is now a standalone application named **Soundsave**. The
  vendored `yandex-music-api` sources were removed; the library is installed
  from PyPI as `yandex-music>=3.2.2,<4`.
- The code moved from a single root script to the `src/soundsave` package with
  a `soundsave` console entry point.
- Python 3.10+ is required.
- Organizer releases are superseded by application releases.

### Added

- `soundsave sync` — the previous organizer behaviour: download new Yandex
  Music playlist tracks with tags, cover art and lyrics.
- `soundsave audit` — scan the archive for tracks below 320 kbps MP3, search
  Zvuk and write CSV/JSON reports. Read-only.
- `soundsave upgrade` — match Zvuk candidates, download a 320 kbps MP3 or FLAC
  stream, validate it, copy the original tags, back up the old file and
  atomically replace it.
- Zvuk provider with direct (non-DRM) `high`/`flac` stream resolution and
  optional `ffmpeg` fallback for FLAC → MP3.
- Quality policies `report`, `best` and `true320`; target formats `mp3`,
  `flac` and `keep`.
- Fuzzy matching with artist/title/duration/album scoring, hard duration and
  version filters, and ambiguity detection.
- SQLite manifest (`.soundsave/manifest.sqlite3`), timestamped backups and a
  temporary download directory under `.soundsave/`.
- Unit tests for matching, quality policy, filenames, manifest, the Zvuk
  provider, the upgrade pipeline and `sync`.

### Removed

- Vendored `yandex_music/` package, upstream docs, examples, codegen scripts
  and the upstream release workflows.
- The `yandex_music_organizer_api.py` root script (use `soundsave sync`).

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
