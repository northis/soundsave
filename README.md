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
* **`tags`** — checks the archive against Yandex Music and, with `--apply`,
  fills only missing tags, cover art and lyrics in the existing files. It never
  downloads audio, never renames files and never overwrites non-empty values.

Zvuk is used for **real 320 kbps MP3 and FLAC** streams because Yandex Music
currently serves at most 192 kbps MP3. YouTube Music is not used: its ceiling
is 256 kbps AAC/Opus, so it cannot produce a true 320 kbps MP3.

## Requirements

* Python 3.10+
* A Yandex Music account and token for `sync` and `tags`
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
| `TARGET_DIR` | yes | Archive directory |
| `ZVUK_TOKEN` | for the `sync` fallback and `audit`/`upgrade` | Zvuk token from `https://zvuk.com/api/tiny/profile` |
| `MAX_DOWNLOADS` | no | New tracks per `sync` run (default 20) |
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

# Skip lyrics or catalogue search
soundsave tags --apply --no-lyrics
soundsave tags --no-search
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
in the playlist. It reports `missing` fields and `conflicts`; `--apply` fills
only missing values:

* title, artists, album, album artist, date, genre and `WOAF`;
* front cover, only if the file has none;
* lyrics, only if the file has none and Yandex reports text lyrics.

`TRCK` and `TPOS` are local archive data and are never read from Yandex or
changed. Existing values, including non-front cover pictures, are preserved.
Writes are atomic: the file is copied to `.soundsave/tmp/`, retagged, verified
with `mutagen` and then replaced.

`--no-search` skips the catalogue search and uses only the playlist;
`--no-lyrics` skips lyrics. Yandex text lyrics are not available for every
track; those files are reported as `lyrics: unavailable`.

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
* Downloading audio may violate the terms of service of Yandex Music or Zvuk.
  This tool is intended for a personal archive; you are responsible for how you
  use it.
* The archive scanner works with the flat `NNNN. Artist - Title.ext` layout
  produced by this tool. Nested directories and other naming schemes are not
  scanned.
* `ffmpeg` is only needed for the FLAC → MP3 fallback. Zvuk normally provides a
  320 kbps MP3 stream directly, so the fallback is rare.

## Development

```bash
pip install -e ".[dev]"
pytest
ruff check .
ruff format --check .
```

The package lives in `src/soundsave`. The design notes for the quality upgrade
are in [`docs/proposals/bitrate-upgrade.md`](docs/proposals/bitrate-upgrade.md);
the tag-checking design is in [`docs/proposals/tags.md`](docs/proposals/tags.md).

## License

The application code is distributed under the GNU Lesser General Public
License v3.0, inherited from the original `yandex-music-api` fork. The
`zvuk-music` dependency is MIT-licensed.
