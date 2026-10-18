# Yandex-Music Organizer (fork)

A lightweight CLI tool that keeps a local copy of your favourite Yandex Music
playlist. It is built on top of the excellent
[`yandex-music-api`](https://github.com/MarshalX/yandex-music-api) library but
works with an **API token** instead of browser cookies. The script downloads
only **new** tracks added to the playlist since the previous run, tags them and
stores them as `NNNN. Artist – Title.mp3` in the folder you choose.

> This repository is a fork of MarshalX/yandex-music-api and therefore contains
> the original library sources. The upstream library is synced to **v3.2.2**;
> its documentation is available in [`README_ORIGINAL.md`](README_ORIGINAL.md).
> The organizer itself is versioned separately — see [`CHANGELOG.md`](CHANGELOG.md).

## Features

* Pure-Python implementation – no external FFmpeg or youtube-dl needed.
* Uses official MarshalX library objects (`Client`, `Track`, …) – API changes
  are picked up by syncing the fork with upstream.
* Embeds full metadata: cover art, lyrics (if available) and a web link to the
  track.
* Respects a configurable per-run download limit (defaults to **20**).
* Filenames are prefixed with a zero-padded sequential id so that new songs are
  always ordered after the existing collection.
* `--dry-run` shows what would be downloaded without writing any files.

## Quick Start

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate

pip install -r requirements-dev.txt

cp .env.template .env            # then edit .env with your values
python yandex_music_organizer_api.py --dry-run
python yandex_music_organizer_api.py
```

## CLI

```text
python yandex_music_organizer_api.py [--dry-run] [--version]
```

| Option      | Description                                              |
|-------------|----------------------------------------------------------|
| `--dry-run` | Fetch the playlist and show new tracks without downloading |
| `--version` | Print the organizer version and exit                     |

## Configuration (.env)

| Variable        | Required | Description                                   |
|-----------------|----------|-----------------------------------------------|
| `API_KEY`       | yes      | Yandex Music personal access token            |
| `PLAYLIST_URL`  | yes      | Full URL of the playlist to mirror            |
| `TARGET_DIR`    | no       | Directory where downloaded tracks are stored  |
| `MAX_DOWNLOADS` | no       | Override hard cap of tracks per execution     |

See `.env.template` shipped with the repo for an example.

## How it works

1. The script reads `.env` and initialises `yandex_music.Client` with your
   token.
2. It fetches the playlist, determines which tracks are *not* present locally
   (based on artist-title match) and applies the per-run limit.
3. Each new track is downloaded (highest-bit-rate MP3), tagged and renamed to
   `NNNN. Artist – Title.mp3` where `NNNN` is the next id.
4. That’s it. Future invocations will pick up only the delta.

## Updating the fork from upstream

The fork tracks `MarshalX/yandex-music-api` through the `upstream` remote:

```bash
git fetch upstream main
git merge upstream/main
```

Do **not** fetch upstream tags into the fork: both repositories historically
used the same `v1.0.0` tag name for different commits. Fork releases use the
`organizer-vX.Y.Z` tag namespace instead, e.g. `organizer-v1.1.0`.

## Roadmap

* **1.2** — audit the archive for tracks below the target quality. YouTube
  Music caps at 256 kbps AAC/Opus, so it is a fallback for perceptual
  improvement, not a source of true 320 kbps MP3; true 320 requires lossless
  sources. See the design proposal in
  [`docs/proposals/bitrate-upgrade.md`](docs/proposals/bitrate-upgrade.md).

## License

This fork inherits the original LGPL-3.0 license of yandex-music-api.
