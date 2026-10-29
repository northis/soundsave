# Soundsave

Soundsave keeps a local copy of a Yandex Music playlist and upgrades the
archive to a real 320 kbps MP3 (or FLAC) using [Zvuk](https://zvuk.com).

The project started as a fork of
[`MarshalX/yandex-music-api`](https://github.com/MarshalX/yandex-music-api).
Version 2.0 is a standalone application: the library is now a normal
dependency, and the repository contains only the application code.

## What it does

* **`sync`** — fetches a Yandex Music playlist, downloads tracks that are not
  in the local archive yet, tags them and stores them as
  `NNNN. Artist - Title.mp3`. When Yandex Music does not offer a 320 kbps MP3
  stream for a track, Zvuk is searched and the track is downloaded from there.
* **`audit`** — scans the archive, finds tracks below 320 kbps MP3, searches
  Zvuk and reports what could be replaced. Nothing is changed.
* **`upgrade`** — does the same, and with `--apply` downloads a 320 kbps MP3
  (or FLAC) stream, verifies it, copies the original tags, backs up the old
  file and atomically replaces it.
* **`tags`** — checks the archive against Yandex Music (and, optionally,
  Discogs) and, with `--apply`, fills only missing tags, cover art and lyrics in
  the existing files. It never downloads audio, never renames files and never
  overwrites non-empty values.
* **`serve`** — a local web player and installable PWA for a folder of playlist
  subfolders. It streams the original MP3/AAC/FLAC files with HTTP Range, keeps
  a SQLite index, and can run the Yandex sync button for the one main playlist.

Zvuk is used for **real 320 kbps MP3 and FLAC** streams because Yandex Music
currently serves at most 192 kbps MP3. YouTube Music is not used: its ceiling
is 256 kbps AAC/Opus, so it cannot produce a true 320 kbps MP3.

## Requirements

* Python 3.10+
* A Yandex Music account and token for `sync` and `tags`
* A Discogs account and personal access token for the `tags` Discogs fallback
  (optional; without it Discogs is skipped)
* A Zvuk account with a paid subscription and a token for the `sync` 320 kbps
  fallback and for `audit`/`upgrade`
* `ffmpeg` only if you ask for `--format mp3` and a candidate has FLAC but no
  320 kbps MP3 stream

## Install

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate

pip install -e .
# or, for the web player:
pip install -e ".[web]"
cp .env.template .env   # then edit .env
```

For development:

```bash
pip install -e ".[dev]"
pytest
ruff check .
```

## Configuration

All settings live in `.env` (or in the environment). See
[`.env.template`](.env.template) for the full list.

| Variable | Required | Description |
|---|---|---|
| `YANDEX_TOKEN` | for `sync` and `tags` | Yandex Music token (`API_KEY` is accepted as an alias) |
| `PLAYLIST_URL` | for `sync`; optional for `tags` | Playlist URL, e.g. `https://music.yandex.ru/users/USER/playlists/3` |
| `TARGET_DIR` | yes, or use `MUSIC_DIR` | Archive directory; also the main playlist folder |
| `MUSIC_DIR` | for the web player | Library root; every immediate subfolder is a playlist |
| `MAIN_PLAYLIST` | with `MUSIC_DIR` | Folder name of the one playlist that gets the sync button |
| `DATA_DIR` | no | SQLite index and cover cache (default `TARGET_DIR/.soundsave`) |
| `MUSIC_HOST_DIR` | Docker only | Host path mounted at `/music` (Compose does not expand `~`) |
| `DATA_HOST_DIR` | Docker only | Host path mounted at `/app/data` |
| `SOUNDSAVE_HOST`, `SOUNDSAVE_PORT` | no | Web player bind address/port (default `127.0.0.1:8765`) |
| `SOUNDSAVE_BIND_ADDR` | Docker only | Published interface (default `0.0.0.0`) |
| `ZVUK_TOKEN` | for the `sync` fallback and `audit`/`upgrade` | Zvuk token from `https://zvuk.com/api/tiny/profile` |
| `DISCOGS_TOKEN` | for the `tags` Discogs fallback | Discogs personal access token |
| `DISCOGS_DELAY` | no | Minimum pause between Discogs requests in seconds (default 2.0) |
| `MAX_DOWNLOADS` | no | New tracks per `sync` run or web sync button press (default 20) |
| `SOUNDSAVE_MIN_SCORE` | no | Minimum match score, 0..1 (default 0.85) |
| `SOUNDSAVE_DURATION_TOLERANCE` | no | Duration tolerance in seconds (default 5) |
| `SOUNDSAVE_DELAY` | no | Pause between requests in seconds (default 1.0) |

## Commands

```bash
# Show what would be downloaded from the playlist
soundsave sync --dry-run

# Download new tracks
soundsave sync

# Audit the archive (read-only)
soundsave audit
soundsave audit --limit 100 --csv audit.csv --json audit.json

# Plan replacements without changing files
soundsave upgrade --limit 20

# Replace the first 20 tracks with 320 kbps MP3
soundsave upgrade --apply --limit 20

# Keep FLAC where it is available
soundsave upgrade --apply --format flac

# Only one track by its numeric id
soundsave upgrade --apply --id 42

# Check tags, covers and lyrics (read-only)
soundsave tags
soundsave tags --limit 100 --csv tags.csv --json tags.json

# Fill only missing values in the existing files
soundsave tags --apply

# Check another playlist folder instead of TARGET_DIR
soundsave tags --playlist Chill
soundsave tags --playlist Chill --apply

# Discogs only (e.g. after Yandex has already been applied)
soundsave tags --source discogs --apply

# Yandex only (disable the Discogs fallback)
soundsave tags --source yandex --apply

# Skip lyrics or catalogue search
soundsave tags --apply --no-lyrics
soundsave tags --no-search

# Run the web player (web extra required)
soundsave serve

# Reconcile the web index with the music folder without starting the server
soundsave scan

# Copy the web library database to DATA_DIR/backups
soundsave snapshot --label manual

# Export a foobar2000 playlist for a container or a portable player
soundsave export "D:\Music\Playlists\Road trip.fpl" --output "D:\Export\Road trip"
```

`soundsave sync` prefers a 320 kbps MP3 stream from Yandex Music. If Yandex
only returns a lower bitrate, it searches Zvuk and uses the best 320 kbps
stream there. If Zvuk has no confident match or cannot provide 320 kbps, the
best Yandex stream is used as a fallback. Without `ZVUK_TOKEN`, `sync` works
from Yandex Music only.

### Quality policies

| Policy | Behaviour |
|---|---|
| `report` | Only report; `--apply` is ignored |
| `best` (default) | Use 320 kbps MP3; use FLAC when `--format flac`/`keep` |
| `true320` | Require a genuine 320 kbps MP3 stream; 128 kbps is never used |

`--format mp3` is the default and keeps the archive in MP3. `--format flac`
stores FLAC where Zvuk has it. `--format keep` keeps the best source format
(FLAC if available, otherwise MP3).

### Tags

`soundsave tags` matches each file against the configured Yandex Music
playlist first, then searches the Yandex catalogue for files that are no longer
in the playlist. If both fail and `DISCOGS_TOKEN` is set, Discogs is tried as a
fallback. It reports `missing` fields and `conflicts`; `--apply` fills only
missing values:

* title, artists, album, album artist, date, genre and `WOAF` (Yandex only);
* front cover, only if the file has none;
* lyrics, only if the file has none and Yandex reports text lyrics.

`TRCK` and `TPOS` are local archive data and are never read from Yandex or
Discogs and never changed. Existing values, including non-front cover pictures,
are preserved. Writes are atomic: the file is copied to `.soundsave/tmp/`,
retagged, verified with `mutagen` and then replaced.

The default is `--source both`. Use `--source yandex` to disable the Discogs
fallback, or `--source discogs` to query only Discogs (useful after the Yandex
pass has already been applied). With `--source discogs`, files with no fields
that Discogs can fill are skipped; `--no-skip-complete` forces a Discogs query
for every file. Discogs requests are throttled by `DISCOGS_DELAY` (default 2
seconds, i.e. 30 requests/minute against the 60/minute limit) and retried on
HTTP 429 using `Retry-After`. Discogs does not provide lyrics and is not used
for `WOAF`.

`--no-search` skips catalogue search and uses only the Yandex playlist;
`--no-lyrics` skips lyrics. Yandex text lyrics are not available for every
track; those files are reported as `lyrics: unavailable`.

By default `tags` scans `TARGET_DIR`, the main playlist. `--playlist NAME`
checks another immediate subfolder of `MUSIC_DIR` and can be combined with
`--apply` to fill its missing tags and covers. Without a separate `MUSIC_DIR`
the folder is resolved next to `TARGET_DIR`, which matches the legacy flat
layout. `--playlist` cannot be combined with `--dir`.

### Exporting a playlist for a container

`soundsave export` turns a foobar2000 playlist (`.fpl`, or `.m3u8`/`.m3u`
exported from it) into a flat folder with one file per playlist item:

```bash
soundsave export "Road trip.fpl" --output "D:\Export\Road trip" --bitrate 256
soundsave export "Road trip.fpl" --output ./export --dry-run
soundsave export "Road trip.fpl" --output ./export --map "D:\Music=/mnt/music"
```

* Lossy sources (`.mp3`, AAC `.m4a`, `.ogg`, `.opus`, …) are copied as they
  are; their audio is never re-encoded.
* Lossless sources (`.flac`, `.ape`, `.wav`, `.aiff`, ALAC, …) are transcoded
  to AAC in an `.m4a` container with `ffmpeg`, 256 kbps by default.
* `.ape`/`.flac` albums described by a CUE sheet are split at the sheet's
  `INDEX` times. Both a `.cue` file in the playlist and subsong items
  (`album.ape|subsong=N`, as written by foobar2000) are understood.
* Files are named `<playlist number>. <artist> - <title>.<ext>`, with leading
  zeros wide enough to keep the folder sortable (`07. …`, `007. …`). Illegal
  file-name characters are replaced with `_`.
* Every new file gets the playlist position as its track number; copied lossy
  files are retagged too unless `--no-retag` is passed.
* An `<playlist>.m3u8` is written next to the files unless `--no-m3u8` is used.
* `--force` overwrites existing files; otherwise they are skipped. `--dry-run`
  only prints the plan. `--map FROM=TO` rewrites a path prefix, which is useful
  when the playlist was saved on another machine.

`ffmpeg` must be in `PATH` (or passed with `--ffmpeg`) when the playlist
contains lossless tracks. The web container does not include it, so run the
export on the host.

### Matching

Candidates are scored by artist (40%), title (40%), duration (15%) and album
(5%). A candidate must score at least 0.80 for both artist and title, and its
duration must be within `max(5 s, 2%)`. Live, cover, karaoke, instrumental,
remix, acoustic and demo versions are rejected unless the local title mentions
that word. If the two best candidates are within 0.05, the result is
`ambiguous` and is never replaced automatically.

## Safety

* `upgrade` without `--apply` changes nothing.
* `tags` without `--apply` changes nothing. With `--apply` it fills only empty
  values and writes through a verified temporary copy, so a failed write leaves
  the original file untouched.
* The original file is copied to `TARGET_DIR/.soundsave/backups/YYYYMMDD/`
  before replacement.
* Downloads go to `TARGET_DIR/.soundsave/tmp/`, are validated with `mutagen`
  (codec, bitrate, duration) and only then atomically installed.
* `TARGET_DIR/.soundsave/manifest.sqlite3` records the old quality, the
  candidate, the score, the new quality and the backup path. Replaced tracks
  are skipped on subsequent runs; use `--retry-failed` for failed ones.
* Existing ID3/Vorbis tags, cover art and lyrics are copied from the original
  file, so the Yandex metadata is preserved.

## Limitations

* Zvuk is an unofficial API. It may rate-limit, require a subscription, or
  change without notice. The client throttles requests and retries transient
  failures, but this is not a supported integration.
* `tags` depends on the Yandex Music catalogue. Tracks removed from Yandex or
  available only as live versions may be reported as `no_match`. Text lyrics
  are not available for every track.
* Discogs is queried through its public API with a personal token and is
  deliberately slow (`DISCOGS_DELAY`). Discogs images are subject to Discogs'
  terms and the rights of their owners; use them for a personal archive only.
* Downloading audio may violate the terms of service of Yandex Music or Zvuk.
  This tool is intended for a personal archive; you are responsible for how you
  use it.
* The archive scanner works with the flat `NNNN. Artist - Title.ext` layout
  produced by this tool. Nested directories and other naming schemes are not
  scanned.
* `ffmpeg` is only needed for the FLAC → MP3 fallback. Zvuk normally provides a
  320 kbps MP3 stream directly, so the fallback is rare.

## Web player and PWA

The web player is a FastAPI application with vanilla HTML/CSS/JS, SQLite and a
service worker. It reads a flat playlist layout:

```text
MUSIC_DIR/
  Main/                 # main playlist, syncable
    0001. Artist - Title.mp3
  Chill/                # read-only
    ...
```

Every immediate subfolder is a playlist; files inside are expected at the top
level (nested directories are ignored). `TARGET_DIR`/`MAIN_PLAYLIST` selects the
single syncable playlist. The SQLite index is reconciled with disk on container
startup, after sync and when you press "Refresh".

Docker quick start:

```bash
cp .env.template .env
# edit MUSIC_DIR, MUSIC_HOST_DIR, MAIN_PLAYLIST, PLAYLIST_URL, YANDEX_TOKEN
docker compose up -d --build
# open http://localhost:8765 (or the HTTPS Cloudflare hostname for PWA install)
```

`MUSIC_HOST_DIR` and `DATA_HOST_DIR` are Compose-only host paths; the container
uses `/music` and `/app/data`. Docker Compose does not expand `~`, so use an
absolute path or a path relative to the repository.

The sync button is shown only for the main playlist. It downloads new tracks
from Yandex Music up to `MAX_DOWNLOADS` and **does not use Zvuk**: if Yandex
serves 192 kbps, the archive gets 192 kbps. The CLI `soundsave sync` keeps its
Zvuk fallback; the web image does not include `ffmpeg` and does not need
`ZVUK_TOKEN`.

`MAIN_PLAYLIST` must match the folder name exactly: `Library` and `library`
are different folders on Linux. Settings shows the resolved main folder and
whether Yandex sync is configured, and the same line is written to the
container log at startup. Docker Compose passes both `YANDEX_TOKEN` and the
older `API_KEY` alias.

The player:

* streams files with HTTP Range, so seeking and fast starts work;
* shows embedded covers (resized and cached) or a placeholder;
* supports play/pause, previous/next, a full-width horizontal seek bar, shuffle
  within the current playlist, repeat off/all/one and volume;
* sorts by playlist number, title or artist and keeps the playing track in the
  centre unless you have scrolled away;
* uses roomier table columns, wraps the album and title to two lines and shows
  a tooltip with the full text when even that does not fit;
* uses a compact bottom panel on desktop and a two-row panel on mobile;
* opens a large cover preview of the playing track when its row is clicked;
* follows the device theme by default; system/light/dark is switchable;
* shows the running release tag and short commit in the top-right corner
  (also in Settings on narrow screens), linked to the GitHub release and
  commit.

The PWA caches the app shell, API metadata and covers. Playing a track also
downloads it into the local Cache API (progress is shown as a pale blue fill on
the seek bar and stays full once the track is cached), and cached tracks are
marked with a check. The position in the track is shown only by the slider
thumb. The cache uses
IndexedDB for its LRU index, defaults to 2 GB and can be cleared in Settings.
This is client-side storage; the server keeps the original files.

For installation on Android, Cloudflare Access must bypass the manifest, the
icons and the favicon:

```text
/manifest.webmanifest
/static/icons/*
/favicon.ico
/favicon.svg
```

These paths are served without a query string on purpose. Some Access bypass
rules compare the whole URL, so a cache-busting `?v=…` suffix made
`/static/favicon.svg?v=2.1.9` miss the rule and fall back to an Access-protected
path; the browser then reported `missing Cf-Access-Jwt-Assertion header`.
Soundsave itself never reads that header — it is added by Cloudflare Access for
authenticated requests and is missing on bypassed ones. If the error persists,
check that the Access application has a Bypass policy for exactly these paths
(and `/sw.js` if your proxy blocks it) and that no Worker in front of the
container requires the JWT for them.

Do not bypass `/api/*` or `/audio`; those remain behind Access. PWA installation
requires HTTPS (or localhost); a plain `http://LAN-IP:8765` is not a secure
context. iOS has stricter background-audio and storage limits.

Local development:

```bash
pip install -e ".[dev,web]"
soundsave serve --reload
```

The web extra adds FastAPI, Uvicorn and Pillow. The base CLI installation still
works without them.

### Updating on Windows

The host-side updater fast-forwards the working copy, snapshots the database,
builds the image from a clean worktree of the target commit, recreates the
container and waits for `/api/version` to report the new commit. If the new
container does not start, it restores `soundsave:previous`.

```powershell
powershell -ExecutionPolicy Bypass -File deploy\windows\update.ps1
powershell -ExecutionPolicy Bypass -File deploy\windows\update.ps1 -Rollback
```

Useful switches: `-Force` (rebuild even when the commit is already running),
`-SkipSnapshot`, `-KeepImages 5`, `-Branch master`. The image is built with
`SOUNDSAVE_COMMIT`/`SOUNDSAVE_VERSION` labels, so the version endpoint and the
updater can identify the running revision. The working copy is never reset,
cleaned or checked out.

## Development

```bash
pip install -e ".[dev,web]"
pytest
ruff check .
ruff format --check .
```

The package lives in `src/soundsave`. The design notes for the quality upgrade
are in [`docs/proposals/bitrate-upgrade.md`](docs/proposals/bitrate-upgrade.md);
the tag-checking design is in [`docs/proposals/tags.md`](docs/proposals/tags.md),
and the web player/cache design is in
[`docs/proposals/web-player.md`](docs/proposals/web-player.md).

## License

The application code is distributed under the GNU Lesser General Public
License v3.0, inherited from the original `yandex-music-api` fork. The
`zvuk-music` dependency is MIT-licensed.
