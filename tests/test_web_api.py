from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip('fastapi')
pytest.importorskip('httpx')

from fastapi.testclient import TestClient

from soundsave.web.app import create_app
from soundsave.web.cover_cache import COVER_CACHE_VERSION, cover_cache_key

from .web_helpers import make_config, make_library, make_mp3


def make_client(
    root: Path,
    *,
    main: str = 'Main',
    yandex_token: str | None = None,
    playlist_url: str | None = None,
) -> TestClient:
    make_library(root)
    return TestClient(
        create_app(
            make_config(
                root,
                main=main,
                yandex_token=yandex_token,
                playlist_url=playlist_url,
            )
        )
    )


def test_playlists_and_tracks(root: Path) -> None:
    with make_client(root) as client:
        response = client.get('/api/playlists')
        assert response.status_code == 200
        data = response.json()
        assert data['main_playlist'] == 'Main'
        assert data['sync_available'] is False
        playlists = data['playlists']
        assert [playlist['name'] for playlist in playlists] == ['Main', 'Chill']
        main = playlists[0]
        assert main['syncable'] is True
        assert main['sync_available'] is False

        response = client.get(f'/api/playlists/{main["id"]}/tracks')
        assert response.status_code == 200
        data = response.json()
        assert data['total'] == 2
        assert [track['title'] for track in data['tracks']] == ['Saturnine', 'Strange Machines']
        track = data['tracks'][0]
        assert track['format'] == 'MP3'
        assert track['sample_rate_hz'] == 44100
        assert track['has_cover'] is True
        assert track['cover_url'].startswith('/api/playlists/')
        assert f'?v={COVER_CACHE_VERSION}-' in track['cover_url']
        assert track['audio_url'].endswith(f'v={track["etag"]}')


def test_library_folder_is_the_main_playlist(root: Path) -> None:
    make_library(root)
    (root / 'music' / 'Main').rename(root / 'music' / 'Library')
    with TestClient(create_app(make_config(root, main='Library'))) as client:
        data = client.get('/api/playlists').json()
        assert data['main_playlist'] == 'Library'
        library = next(playlist for playlist in data['playlists'] if playlist['name'] == 'Library')
        chill = next(playlist for playlist in data['playlists'] if playlist['name'] == 'Chill')
        assert library['syncable'] is True
        assert library['readonly'] is False
        assert chill['syncable'] is False
        assert chill['readonly'] is True


def test_sync_available_reflects_yandex_credentials(root: Path) -> None:
    with make_client(root, yandex_token='token', playlist_url='https://music.yandex.ru/x') as client:
        data = client.get('/api/playlists').json()
        assert data['sync_available'] is True
        assert all(playlist['sync_available'] is True for playlist in data['playlists'])


def test_sync_job_runs_for_the_main_playlist(root: Path, monkeypatch) -> None:
    calls: dict[str, object] = {}

    class Result:
        total_new = 3
        added = 2
        failed = 0
        limit_reached = False

    def fake_run_sync(config, *, allow_zvuk, progress):
        calls['allow_zvuk'] = allow_zvuk
        progress(
            SimpleNamespace(
                phase='downloading',
                total=3,
                completed=1,
                current_artist='Artist',
                current_title='Title',
                added=1,
                failed=0,
                limit_reached=False,
            )
        )
        return Result()

    monkeypatch.setattr('soundsave.web.sync_jobs.run_sync', fake_run_sync)
    with make_client(root, yandex_token='token', playlist_url='https://music.yandex.ru/x') as client:
        main = next(playlist for playlist in client.get('/api/playlists').json()['playlists'] if playlist['syncable'])
        response = client.post(
            f'/api/playlists/{main["id"]}/sync',
            headers={'X-Soundsave-Request': '1'},
        )
        assert response.status_code == 202
        job = response.json()
        for _ in range(200):
            if job['state'] != 'running':
                break
            time.sleep(0.01)
            job = client.get(f'/api/playlists/{main["id"]}/sync').json()
        assert job['state'] == 'done'
        assert job['added'] == 2
        assert job['limit_reached'] is False
        assert calls['allow_zvuk'] is False


def test_audio_range_head_and_download(root: Path) -> None:
    with make_client(root) as client:
        main = client.get('/api/playlists').json()['playlists'][0]
        track = client.get(f'/api/playlists/{main["id"]}/tracks').json()['tracks'][0]
        url = f'/api/playlists/{main["id"]}/tracks/{track["id"]}/audio'

        partial = client.get(url, headers={'Range': 'bytes=0-9'})
        assert partial.status_code == 206
        assert partial.headers['content-range'] == f'bytes 0-9/{track["size_bytes"]}'
        assert len(partial.content) == 10

        head = client.head(url)
        assert head.status_code == 200
        assert head.headers['accept-ranges'] == 'bytes'
        assert head.content == b''

        invalid = client.get(url, headers={'Range': 'bytes=999999-'})
        assert invalid.status_code == 416
        assert invalid.headers['content-range'] == f'bytes */{track["size_bytes"]}'

        full = client.get(url, headers={'Range': 'bytes=0-9'}, params={'download': '1'})
        assert full.status_code == 200
        assert len(full.content) == track['size_bytes']


def test_small_cover_is_served_unmodified(root: Path) -> None:
    with make_client(root) as client:
        main = client.get('/api/playlists').json()['playlists'][0]
        tracks = client.get(f'/api/playlists/{main["id"]}/tracks').json()['tracks']
        covered = next(track for track in tracks if track['has_cover'])

        response = client.get(covered['cover_url'])

        assert response.status_code == 200
        assert response.headers['content-type'] == 'image/png'
        assert response.content.startswith(b'\x89PNG')


def test_diag_upload_logs_client_journal(root: Path, caplog) -> None:
    with make_client(root) as client:
        assert client.post('/api/diag', json={'text': 'x'}).status_code == 403
        with caplog.at_level('INFO'):
            response = client.post(
                '/api/diag',
                json={'text': 'audio:pause pos=12.0'},
                headers={'X-Soundsave-Request': '1'},
            )
        assert response.status_code == 200
        assert 'audio:pause pos=12.0' in caplog.text


def test_writes_require_csrf_header_and_readonly_playlists(root: Path) -> None:
    with make_client(root) as client:
        playlists = client.get('/api/playlists').json()['playlists']
        main = next(playlist for playlist in playlists if playlist['syncable'])
        chill = next(playlist for playlist in playlists if not playlist['syncable'])

        assert client.post(f'/api/playlists/{chill["id"]}/sync').status_code == 403
        assert client.post(f'/api/playlists/{main["id"]}/sync').status_code == 403
        assert (
            client.post(
                f'/api/playlists/{chill["id"]}/sync',
                headers={'X-Soundsave-Request': '1'},
            ).status_code
            == 403
        )
        assert (
            client.post(
                f'/api/playlists/{main["id"]}/sync',
                headers={'X-Soundsave-Request': '1'},
            ).status_code
            == 503
        )


def test_refresh_picks_up_new_files(root: Path) -> None:
    with make_client(root) as client:
        main = client.get('/api/playlists').json()['playlists'][0]
        make_mp3(root / 'music' / 'Main' / '0003. New - Track.mp3', 'Track', 'New')

        response = client.post('/api/refresh', headers={'X-Soundsave-Request': '1'})

        assert response.status_code == 200
        assert response.json()['report']['added'] == 1
        tracks = client.get(f'/api/playlists/{main["id"]}/tracks').json()
        assert tracks['total'] == 3


def test_playlist_refresh_rescans_only_that_playlist(root: Path) -> None:
    with make_client(root) as client:
        playlists = client.get('/api/playlists').json()['playlists']
        main = next(playlist for playlist in playlists if playlist['syncable'])
        chill = next(playlist for playlist in playlists if playlist['name'] == 'Chill')
        make_mp3(root / 'music' / 'Chill' / '0002. New - Track.mp3', 'Track', 'New')
        make_mp3(root / 'music' / 'Main' / '0003. New - Main.mp3', 'Main', 'New')

        response = client.post(
            f'/api/playlists/{chill["id"]}/refresh',
            headers={'X-Soundsave-Request': '1'},
        )

        assert response.status_code == 200
        assert response.json()['report']['added'] == 1
        assert client.get(f'/api/playlists/{chill["id"]}/tracks').json()['total'] == 2
        assert client.get(f'/api/playlists/{main["id"]}/tracks').json()['total'] == 2


def test_playlist_refresh_can_drop_cached_covers(root: Path) -> None:
    audio = root / 'music' / 'Chill' / '01 - Someone - Something.mp3'
    with make_client(root) as client:
        chill = next(
            playlist for playlist in client.get('/api/playlists').json()['playlists'] if playlist['name'] == 'Chill'
        )
        track = client.get(f'/api/playlists/{chill["id"]}/tracks').json()['tracks'][0]
        cache_file = root / 'data' / 'covers' / f'{cover_cache_key(audio, track["etag"])}.webp'
        cache_file.write_bytes(b'cached')

        response = client.post(
            f'/api/playlists/{chill["id"]}/refresh',
            params={'covers': 'true'},
            headers={'X-Soundsave-Request': '1'},
        )

        assert response.status_code == 200
        assert response.json()['report']['covers'] == 1
        assert not cache_file.exists()


def test_startup_reconciles_database_with_disk(root: Path) -> None:
    with make_client(root) as client:
        main = client.get('/api/playlists').json()['playlists'][0]
        assert client.get(f'/api/playlists/{main["id"]}/tracks').json()['total'] == 2
    (root / 'music' / 'Main' / '0001. The Gathering - Saturnine.mp3').unlink()

    with TestClient(create_app(make_config(root))) as client:
        main = client.get('/api/playlists').json()['playlists'][0]
        assert client.get(f'/api/playlists/{main["id"]}/tracks').json()['total'] == 1


def test_unknown_track_is_404(root: Path) -> None:
    with make_client(root) as client:
        main = client.get('/api/playlists').json()['playlists'][0]
        response = client.get(f'/api/playlists/{main["id"]}/tracks/999999/audio')
        assert response.status_code == 404


def test_version_reports_build_metadata(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('SOUNDSAVE_COMMIT', 'abcdef123456')
    monkeypatch.setenv('SOUNDSAVE_VERSION', '2.1.0-test')
    with make_client(root) as client:
        data = client.get('/api/version').json()

    assert data['version'] == '2.1.0-test'
    assert data['commit'] == 'abcdef123456'
    assert data['short'] == 'abcdef1'
    assert data['tag'] == 'v2.1.0-test'
    assert data['tag_url'].endswith('/releases/tag/v2.1.0-test')
    assert data['commit_url'].endswith('/commit/abcdef123456')


def test_pwa_assets_are_served(root: Path) -> None:
    with make_client(root) as client:
        index = client.get('/')
        assert index.status_code == 200
        assert 'soundsave:theme' in index.text
        assert '/manifest.webmanifest' in index.text

        manifest = client.get('/manifest.webmanifest')
        assert manifest.status_code == 200
        assert manifest.headers['content-type'].startswith('application/manifest+json')
        assert manifest.headers['cache-control'] == 'no-cache'

        worker = client.get('/sw.js')
        assert worker.status_code == 200
        assert worker.headers['service-worker-allowed'] == '/'

        icon = client.get('/static/icons/icon-192.png')
        assert icon.status_code == 200
        assert icon.headers['content-type'] == 'image/png'

        favicon = client.get('/favicon.ico')
        assert favicon.status_code == 200
        assert favicon.headers['content-type'] == 'image/png'
        assert favicon.headers['cache-control'] == 'public, max-age=86400'

        icon_svg = client.get('/favicon.svg')
        assert icon_svg.status_code == 200
        assert icon_svg.headers['content-type'] == 'image/svg+xml'
