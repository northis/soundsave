from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip('fastapi')
pytest.importorskip('httpx')

from fastapi.testclient import TestClient

from soundsave.web.app import create_app

from .web_helpers import make_config, make_library, make_mp3


def make_client(root: Path) -> TestClient:
    make_library(root)
    return TestClient(create_app(make_config(root)))


def test_playlists_and_tracks(root: Path) -> None:
    with make_client(root) as client:
        response = client.get('/api/playlists')
        assert response.status_code == 200
        playlists = response.json()['playlists']
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
        assert track['audio_url'].endswith(f'v={track["etag"]}')


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


def test_cover_is_served_as_webp(root: Path) -> None:
    with make_client(root) as client:
        main = client.get('/api/playlists').json()['playlists'][0]
        tracks = client.get(f'/api/playlists/{main["id"]}/tracks').json()['tracks']
        covered = next(track for track in tracks if track['has_cover'])

        response = client.get(covered['cover_url'])

        assert response.status_code == 200
        assert response.headers['content-type'] == 'image/webp'
        assert response.content.startswith(b'RIFF')


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

        favicon = client.get('/favicon.ico', follow_redirects=False)
        assert favicon.status_code in {307, 308}
        assert favicon.headers['location'].endswith('/static/favicon.svg?v=2.1.7')
