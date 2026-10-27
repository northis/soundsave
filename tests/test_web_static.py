from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
STATIC = REPO / 'src' / 'soundsave' / 'web' / 'static'


def test_manifest_is_valid_and_has_icons() -> None:
    manifest = json.loads((STATIC / 'manifest.webmanifest').read_text(encoding='utf-8'))

    assert manifest['display'] == 'standalone'
    assert manifest['start_url'] == '/'
    assert manifest['theme_color']
    assert {icon['sizes'] for icon in manifest['icons']} >= {'192x192', '512x512'}


def test_committed_icons_exist() -> None:
    for name in (
        'icon-192.png',
        'icon-512.png',
        'maskable-512.png',
        'apple-touch-icon-180.png',
    ):
        path = STATIC / 'icons' / name
        assert path.is_file()
        assert path.stat().st_size > 1000


def test_icons_use_the_purple_and_yellow_palette() -> None:
    manifest = json.loads((STATIC / 'manifest.webmanifest').read_text(encoding='utf-8'))
    favicon = (STATIC / 'favicon.svg').read_text(encoding='utf-8')
    index = (STATIC / 'index.html').read_text(encoding='utf-8')

    assert manifest['background_color'] == '#2e1065'
    assert manifest['theme_color'] == '#2e1065'
    assert all(icon['src'].endswith('?v=2.1.2') for icon in manifest['icons'])
    assert '#facc15' in favicon
    assert '#2e1065' in favicon
    assert '/static/favicon.svg?v=2.1.2' in index
    assert '/static/icons/apple-touch-icon-180.png?v=2.1.2' in index
    assert '/manifest.webmanifest?v=2.1.2' in index
    assert 'icon-bass-clef' in index
    assert 'brand-clef' in index


def test_index_has_theme_boot_script_and_pwa_links() -> None:
    index = (STATIC / 'index.html').read_text(encoding='utf-8')

    assert 'soundsave:theme' in index
    assert 'prefers-color-scheme' in index
    assert '/manifest.webmanifest' in index
    assert '/static/app.js' in index


def test_service_worker_caches_shell_and_audio() -> None:
    worker = (STATIC / 'sw.js').read_text(encoding='utf-8')

    assert 'soundsave-shell-v4' in worker
    assert 'soundsave-audio-v1' in worker
    assert "download') === '1'" in worker
    assert 'parseRange' in worker


def test_docker_files_mount_music_and_data() -> None:
    compose = (REPO / 'docker-compose.yml').read_text(encoding='utf-8')
    dockerfile = (REPO / 'Dockerfile').read_text(encoding='utf-8')

    assert '${MUSIC_HOST_DIR:-./music}:/music' in compose
    assert '${DATA_HOST_DIR:-./data}:/app/data' in compose
    assert 'MUSIC_DIR: /music' in compose
    assert 'MAIN_PLAYLIST' in compose
    assert 'soundsave", "serve' in dockerfile
    assert 'EXPOSE 8765' in dockerfile
    assert 'ffmpeg' not in dockerfile
    for name in ('SOUNDSAVE_COMMIT', 'SOUNDSAVE_COMMIT_DATE', 'SOUNDSAVE_VERSION', 'SOUNDSAVE_CREATED'):
        assert f'ARG {name}=' in dockerfile
        assert f'{name}: "${{{name}:-}}"' in compose
    for label in (
        'org.opencontainers.image.source',
        'org.opencontainers.image.revision',
        'org.opencontainers.image.version',
        'org.opencontainers.image.created',
    ):
        assert label in dockerfile
