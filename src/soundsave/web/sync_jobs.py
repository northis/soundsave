"""Background Yandex sync jobs with polling progress."""

from __future__ import annotations

import logging
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from ..config import Config
from ..sync import SyncProgress, run_sync
from .db import LibraryStore
from .library import LibraryScanner

logger = logging.getLogger(__name__)


class SyncError(RuntimeError):
    """A sync request cannot be started."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class SyncConflictError(SyncError):
    """Another sync is already running."""


@dataclass
class SyncJob:
    playlist_id: int
    state: str = 'running'
    phase: str = 'fetching'
    total: int = 0
    completed: int = 0
    current_artist: str = ''
    current_title: str = ''
    added: int = 0
    failed: int = 0
    limit_reached: bool = False
    error: str | None = None
    started_at: str = ''
    finished_at: str | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


class SyncService:
    """One Yandex sync at a time, with an in-memory status per playlist."""

    def __init__(self, config: Config, store: LibraryStore, scanner: LibraryScanner) -> None:
        self.config = config
        self.store = store
        self.scanner = scanner
        self._lock = threading.RLock()
        self._running: SyncJob | None = None
        self._jobs: dict[int, SyncJob] = {}

    def start(self, playlist_id: int) -> SyncJob:
        playlist = self.store.get_playlist(playlist_id)
        if playlist is None:
            raise SyncError(404, 'Playlist not found')
        if not playlist.syncable:
            raise SyncError(403, 'This playlist is read-only')
        if not self.config.yandex_token or not self.config.playlist_url:
            raise SyncError(503, 'YANDEX_TOKEN and PLAYLIST_URL are required for sync')
        with self._lock:
            if self._running is not None:
                raise SyncConflictError(409, 'Another sync is already running')
            job = SyncJob(playlist_id=playlist_id, started_at=_now())
            self._running = job
            self._jobs[playlist_id] = job
        thread = threading.Thread(target=self._run, args=(job,), name=f'soundsave-sync-{playlist_id}', daemon=True)
        thread.start()
        return job

    def _run(self, job: SyncJob) -> None:
        try:
            result = run_sync(
                self.config,
                allow_zvuk=False,
                progress=lambda progress: self._progress(job, progress),
            )
            self.scanner.reconcile()
            with self._lock:
                job.state = 'done'
                job.phase = 'done'
                job.total = result.total_new
                job.added = result.added
                job.failed = result.failed
                job.limit_reached = result.limit_reached
                job.finished_at = _now()
            self.store.record_sync(job.playlist_id, status='ok')
        except Exception as exc:
            logger.exception('Sync job failed for playlist %s', job.playlist_id)
            with self._lock:
                job.state = 'error'
                job.phase = 'error'
                job.error = str(exc)
                job.finished_at = _now()
            self.store.record_sync(job.playlist_id, status='error', error=str(exc))
        finally:
            with self._lock:
                self._running = None

    def _progress(self, job: SyncJob, progress: SyncProgress) -> None:
        with self._lock:
            job.phase = progress.phase
            job.total = progress.total
            job.completed = progress.completed
            job.current_artist = progress.current_artist
            job.current_title = progress.current_title
            job.added = progress.added
            job.failed = progress.failed
            job.limit_reached = progress.limit_reached

    def status(self, playlist_id: int) -> dict[str, object]:
        with self._lock:
            job = self._jobs.get(playlist_id)
            if job is not None:
                return asdict(job)
        playlist = self.store.get_playlist(playlist_id)
        if playlist is None:
            raise SyncError(404, 'Playlist not found')
        return {
            'playlist_id': playlist_id,
            'state': playlist.last_sync_status or 'idle',
            'phase': 'idle',
            'total': 0,
            'completed': 0,
            'current_artist': '',
            'current_title': '',
            'added': 0,
            'failed': 0,
            'limit_reached': False,
            'error': playlist.last_sync_error,
            'started_at': '',
            'finished_at': playlist.last_synced_at,
        }

    def is_running(self) -> bool:
        with self._lock:
            return self._running is not None
