# Web player and PWA

## Goal

A personal web player for a local music library. The library root is mounted
into the container; every immediate subfolder is a playlist. Files inside a
playlist folder are flat (nested directories are ignored). One playlist is the
syncable Yandex Music mirror; the rest are read-only.

## Stack

- FastAPI + Uvicorn.
- Vanilla HTML/CSS/JS modules, no frontend build step.
- SQLite as a disposable index of the music folder.
- Cache API + IndexedDB in the browser for local audio.
- Docker Compose with music/data bind mounts.

## Library model

```text
MUSIC_DIR/
  Main/
    0001. Artist - Title.mp3
  Chill/
    01 - Artist - Title.m4a
```

- `TARGET_DIR` (or `MUSIC_DIR` + `MAIN_PLAYLIST`) identifies the only syncable
  playlist.
- `TARGET_DIR` alone remains the legacy flat-archive mode: the target is the
  single playlist.
- `library.sqlite3` lives in `DATA_DIR` and contains `playlists`, `tracks` and
  `meta` tables.
- The track row stores the filename, display number, natural-sort position,
  title, artists JSON, album, duration, format, bitrate, sample rate, size,
  mtime, etag and cover presence.
- Disk is the source of truth. The index is reconciled:
  - synchronously in the FastAPI lifespan before the first request;
  - after a successful sync;
  - on `POST /api/refresh` (with `force=1` to re-read unchanged tags).
- Reconciliation compares `size_bytes` and `mtime_ns`; only new/changed files
  are read with `mutagen`. Missing files are deleted from the index. Missing
  playlist folders are marked `missing`.
- A track is resolved for streaming by its database id; raw paths never come
  from the client.

## HTTP API

- `GET /api/playlists` — playlists, syncable flag, counts, last sync status.
- `GET /api/playlists/{id}/tracks` — all metadata for the virtual list.
- `GET /api/playlists/{id}/tracks/{track}/cover` — 256 px WebP cover or 404.
- `GET|HEAD /api/playlists/{id}/tracks/{track}/audio` — 200/206/304/416 Range
  response. `?download=1` forces a full 200 response for the cache manager.
- `POST /api/playlists/{id}/sync` — background Yandex sync, main playlist only.
- `GET /api/playlists/{id}/sync` — job status for polling.
- `POST /api/refresh` — reconcile the index.
- `GET /api/health`, `GET /api/version`.

Writes require `X-Soundsave-Request: 1` and a same-origin `Origin` when present.
Cloudflare Access is the outer authentication layer; the app has no login.

## UI

- Dense desktop table: number, cover, two-line title/artist, album, format,
  bitrate, sample rate, duration, cache state.
- Mobile rows collapse to cover + two-line title/artist + a metadata line.
- The list is virtualized; only visible rows are in the DOM. Metadata for the
  whole playlist is loaded once in v1, so sorting and finding the playing track
  are instant. If playlists grow to tens of thousands of tracks, add
  server-side `limit/offset` and bidirectional page loading.
- The current track is centred on load, track changes (while the user has not
  scrolled) and after sorting. A "back to current" button appears after a
  manual scroll.
- The player is outside the scroll container (`grid-template-areas`), so it
  stays at the bottom. Mobile uses two rows plus safe-area padding.
- Theme is system/light/dark; an inline script applies it before CSS to avoid a
  flash.
- Media Session exposes title, artist, cover and transport actions.

## Audio cache

Chosen v1: HTTP Range streaming plus an explicit full-file cache.

1. `<audio>` streams the file from the server with Range requests.
2. The cache manager fetches `/audio?...&download=1` (full 200), reads
   `response.body` chunks, reports progress, and stores a clone in Cache API.
3. The service worker serves the cached full response; for Range requests it
   slices the cached Blob into a 206.
4. The seek bar shows played progress in the accent colour and cache download
   progress as a paler overlay. A check icon marks fully cached tracks.
5. The index (URL, etag, size, last-used) lives in IndexedDB; eviction is LRU
   with a default 2 GB limit. The user can clear the cache in Settings.

The first play downloads the file twice (stream + cache). On a home network
this is acceptable for v1. A later service-worker tee can feed both the audio
element and the cache from one response. If resumable chunk downloads become
important, use Range chunks in IndexedDB and assemble a Blob. HLS/fMP4 with
ffmpeg is deliberately out of scope: it adds transcoding and CPU for no benefit
on a local MP3/AAC library.

AAC is supported as `.m4a` (preferred) and raw `.aac` best-effort. M4A files
with `moov` at the end may start more slowly; a faststart remux can be added
later if needed.

## Sync

- The web sync calls `run_sync(..., allow_zvuk=False)`.
- It downloads new tracks from Yandex up to `MAX_DOWNLOADS`.
- It does not call Zvuk and does not upgrade 192 kbps to 320 kbps. The UI shows
  the real bitrate. The CLI `soundsave sync` keeps the Zvuk fallback.
- One sync job at a time; progress is polled, then the index is reconciled.

## PWA / deployment

- Manifest, icons, service worker and an offline app shell.
- PWA install requires HTTPS. Cloudflare Access must bypass
  `/manifest.webmanifest` and `/static/icons/*` (and `/sw.js` if the proxy
  blocks it); API and audio stay protected.
- Docker mounts `MUSIC_HOST_DIR` at `/music` and `DATA_HOST_DIR` at
  `/app/data`. Compose does not expand `~`.
- The web image does not include `ffmpeg` and does not need `ZVUK_TOKEN`.
