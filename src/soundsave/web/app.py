"""FastAPI application for the Soundsave web player."""

from __future__ import annotations

import logging
import mimetypes
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict
from pathlib import Path
from typing import Annotated
from urllib.parse import quote, urlparse

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from ..config import Config
from .buildinfo import build_info
from .covers import CoverCache
from .db import LibraryStore, PlaylistRow, TrackRow
from .library import LibraryError, LibraryScanner
from .settings import WebSettings
from .streaming import stream_audio
from .sync_jobs import SyncError, SyncService

logger = logging.getLogger(__name__)

STATIC = Path(__file__).resolve().parent / 'static'

# Windows' registry can map .js to text/plain; browsers then refuse module
# scripts because of strict MIME checking. Pin the web types explicitly.
mimetypes.add_type('text/javascript', '.js')
mimetypes.add_type('text/css', '.css')
mimetypes.add_type('image/svg+xml', '.svg')
mimetypes.add_type('image/webp', '.webp')
mimetypes.add_type('application/manifest+json', '.webmanifest')


def require_write(request: Request) -> None:
    """Reject cross-site writes even though Cloudflare Access is the outer gate."""
    if request.headers.get('x-soundsave-request') != '1':
        raise HTTPException(status_code=403, detail='Missing X-Soundsave-Request header')
    origin = request.headers.get('origin')
    if origin and urlparse(origin).netloc != request.headers.get('host'):
        raise HTTPException(status_code=403, detail='Cross-origin request rejected')


def _playlist_json(playlist: PlaylistRow, *, sync_available: bool) -> dict[str, object]:
    return {
        'id': playlist.id,
        'name': playlist.name,
        'folder_name': playlist.folder_name,
        'syncable': playlist.syncable,
        'readonly': not playlist.syncable,
        'track_count': playlist.track_count,
        'last_scanned_at': playlist.last_scanned_at,
        'last_scan_error': playlist.last_scan_error,
        'last_synced_at': playlist.last_synced_at,
        'last_sync_status': playlist.last_sync_status,
        'last_sync_error': playlist.last_sync_error,
        'sync_available': sync_available,
    }


def _track_json(playlist_id: int, track: TrackRow) -> dict[str, object]:
    version = quote(track.etag)
    return {
        'id': track.id,
        'number': track.number,
        'position': track.position,
        'title': track.title,
        'artists': track.artists,
        'artist': ', '.join(track.artists),
        'album': track.album,
        'duration_s': track.duration_s,
        'format': track.format,
        'bitrate_kbps': track.bitrate_kbps,
        'sample_rate_hz': track.sample_rate_hz,
        'size_bytes': track.size_bytes,
        'etag': track.etag,
        'has_cover': track.has_cover,
        'cover_url': f'/api/playlists/{playlist_id}/tracks/{track.id}/cover?v={version}' if track.has_cover else None,
        'audio_url': f'/api/playlists/{playlist_id}/tracks/{track.id}/audio?v={version}',
    }


def _warn_if_main_folder_is_new(settings: WebSettings) -> None:
    """Point at the likely misconfiguration when MAIN_PLAYLIST creates a folder."""
    if not settings.is_multi_playlist or settings.target_dir.exists():
        return
    existing: list[str] = []
    with suppress(OSError):
        existing = sorted(
            entry.name for entry in settings.music_dir.iterdir() if entry.is_dir() and not entry.name.startswith('.')
        )
    logger.warning(
        'Main playlist folder %r does not exist and will be created. '
        'Set MAIN_PLAYLIST to the folder that should be synced%s.',
        settings.target_dir.name,
        f' (existing folders: {", ".join(existing)})' if existing else '',
    )


def create_app(config: Config, settings: WebSettings | None = None) -> FastAPI:
    """Build the app; the caller may pass explicit settings in tests."""
    resolved = settings or WebSettings.from_config(config)
    store = LibraryStore(resolved.db_path)
    scanner = LibraryScanner(resolved, store)
    covers = CoverCache(resolved.covers_dir)
    sync = SyncService(config, store, scanner)
    sync_available = bool(config.yandex_token and config.playlist_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        _warn_if_main_folder_is_new(resolved)
        resolved.ensure()
        store.init()
        scanner.reconcile()
        logger.info(
            'Web library: music=%s, main playlist=%r, sync=%s',
            resolved.music_dir,
            resolved.main_playlist_name,
            'configured' if sync_available else 'not configured (set YANDEX_TOKEN or API_KEY and PLAYLIST_URL)',
        )
        app.state.settings = resolved
        app.state.store = store
        app.state.scanner = scanner
        app.state.covers = covers
        app.state.sync = sync
        yield

    app = FastAPI(title='Soundsave', lifespan=lifespan)
    app.state.settings = resolved
    app.state.store = store
    app.state.scanner = scanner
    app.state.covers = covers
    app.state.sync = sync
    app.state.sync_available = sync_available

    @app.get('/')
    def index() -> FileResponse:
        return FileResponse(STATIC / 'index.html', media_type='text/html', headers={'Cache-Control': 'no-cache'})

    # PWA assets that Cloudflare Access bypasses must be reachable without a
    # query string: some Access rules compare the full URL, and a redirect to
    # `/static/...` would send the browser to a protected path anyway.
    @app.get('/favicon.ico')
    def favicon() -> FileResponse:
        return FileResponse(
            STATIC / 'icons' / 'apple-touch-icon-180.png',
            media_type='image/png',
            headers={'Cache-Control': 'public, max-age=86400'},
        )

    @app.get('/favicon.svg')
    def favicon_svg() -> FileResponse:
        return FileResponse(
            STATIC / 'favicon.svg',
            media_type='image/svg+xml',
            headers={'Cache-Control': 'public, max-age=86400'},
        )

    @app.get('/manifest.webmanifest')
    def manifest() -> FileResponse:
        return FileResponse(
            STATIC / 'manifest.webmanifest',
            media_type='application/manifest+json',
            headers={'Cache-Control': 'no-cache'},
        )

    @app.get('/sw.js')
    def service_worker() -> FileResponse:
        return FileResponse(
            STATIC / 'sw.js',
            media_type='application/javascript',
            headers={'Cache-Control': 'no-cache', 'Service-Worker-Allowed': '/'},
        )

    app.mount('/static', StaticFiles(directory=STATIC), name='static')

    @app.get('/api/health')
    def health() -> dict[str, str]:
        return {'status': 'ok'}

    @app.get('/api/version')
    def version() -> dict[str, str | bool]:
        return build_info()

    @app.get('/api/playlists')
    def playlists() -> JSONResponse:
        rows = store.list_playlists()
        return JSONResponse(
            {
                'playlists': [_playlist_json(row, sync_available=sync_available) for row in rows],
                'main_playlist': resolved.main_playlist_name,
                'sync_available': sync_available,
            },
            headers={'Cache-Control': 'no-store'},
        )

    @app.post('/api/diag', dependencies=[Depends(require_write)])
    async def diag_upload(request: Request) -> dict[str, bool]:
        """Write a client playback journal into the server log."""
        try:
            payload = await request.json()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail='Invalid JSON') from exc
        text = str(payload.get('text', '') if isinstance(payload, dict) else '')[:200_000]
        logger.info('Client diagnostics:\n%s', text)
        return {'ok': True}

    @app.post('/api/refresh', dependencies=[Depends(require_write)])
    def refresh(force: bool = False) -> JSONResponse:
        try:
            report = scanner.reconcile(force=force)
        except Exception as exc:
            logger.exception('Library refresh failed')
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return JSONResponse({'ok': True, 'report': asdict(report)})

    def _playlist_or_404(playlist_id: int) -> PlaylistRow:
        playlist = store.get_playlist(playlist_id)
        if playlist is None:
            raise HTTPException(status_code=404, detail='Playlist not found')
        return playlist

    def _track_or_404(playlist_id: int, track_id: int) -> TrackRow:
        track = store.get_track(playlist_id, track_id)
        if track is None:
            raise HTTPException(status_code=404, detail='Track not found')
        return track

    @app.post('/api/playlists/{playlist_id}/refresh', dependencies=[Depends(require_write)])
    def refresh_playlist(playlist_id: int, force: bool = False) -> JSONResponse:
        playlist = _playlist_or_404(playlist_id)
        try:
            report = scanner.reconcile_playlist(playlist.folder_name, force=force)
        except LibraryError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception('Playlist refresh failed')
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return JSONResponse({'ok': True, 'report': asdict(report)})

    @app.get('/api/playlists/{playlist_id}/tracks')
    def tracks(
        playlist_id: int,
        limit: Annotated[int | None, Query(ge=1, le=10000)] = None,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> JSONResponse:
        playlist = _playlist_or_404(playlist_id)
        rows = store.tracks_for_playlist(playlist_id)
        total = len(rows)
        if limit is not None:
            rows = rows[offset : offset + limit]
        return JSONResponse(
            {
                'playlist': _playlist_json(playlist, sync_available=sync_available),
                'tracks': [_track_json(playlist_id, track) for track in rows],
                'total': total,
                'offset': offset,
            },
            headers={'Cache-Control': 'no-store'},
        )

    @app.get('/api/playlists/{playlist_id}/tracks/{track_id}/cover')
    def cover(playlist_id: int, track_id: int) -> Response:
        playlist = _playlist_or_404(playlist_id)
        track = _track_or_404(playlist_id, track_id)
        if not track.has_cover:
            raise HTTPException(status_code=404, detail='No embedded cover')
        try:
            path = scanner.track_path(playlist, track)
        except LibraryError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        result = covers.get(path, track.etag)
        if result is None:
            raise HTTPException(status_code=404, detail='No embedded cover')
        return FileResponse(
            result.path,
            media_type=result.media_type,
            headers={'Cache-Control': 'private, max-age=31536000, immutable', 'ETag': f'"{track.etag}"'},
        )

    @app.api_route('/api/playlists/{playlist_id}/tracks/{track_id}/audio', methods=['GET', 'HEAD'])
    def audio(playlist_id: int, track_id: int, request: Request) -> Response:
        playlist = _playlist_or_404(playlist_id)
        track = _track_or_404(playlist_id, track_id)
        try:
            path = scanner.track_path(playlist, track)
        except LibraryError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        download = request.query_params.get('download') == '1'
        return stream_audio(path, request, download=download)

    @app.post('/api/playlists/{playlist_id}/sync', dependencies=[Depends(require_write)])
    def start_sync(playlist_id: int) -> JSONResponse:
        try:
            job = sync.start(playlist_id)
        except SyncError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        return JSONResponse(asdict(job), status_code=202)

    @app.get('/api/playlists/{playlist_id}/sync')
    def sync_status(playlist_id: int) -> JSONResponse:
        try:
            return JSONResponse(sync.status(playlist_id), headers={'Cache-Control': 'no-store'})
        except SyncError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    return app


def create_app_from_env() -> FastAPI:
    """Uvicorn factory used by ``soundsave serve --reload``."""
    return create_app(Config.load())
