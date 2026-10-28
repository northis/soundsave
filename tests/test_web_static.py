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
    assert all('?v=' not in icon['src'] for icon in manifest['icons'])
    assert '#facc15' in favicon
    assert '#2e1065' in favicon
    assert 'href="/favicon.svg"' in index
    assert '/static/icons/apple-touch-icon-180.png' in index
    assert 'href="/manifest.webmanifest"' in index
    assert 'icon-bass-clef' in index
    assert 'brand-clef' in index
    assert 'build-badge' in index
    assert 'settings-version' in index
    assert 'settings-commit' in index


def test_index_has_theme_boot_script_and_pwa_links() -> None:
    index = (STATIC / 'index.html').read_text(encoding='utf-8')

    assert 'soundsave:theme' in index
    assert 'prefers-color-scheme' in index
    assert '/manifest.webmanifest' in index
    assert '/static/app.js' in index


def test_player_has_vertical_seek_shuffle_and_tooltips() -> None:
    index = (STATIC / 'index.html').read_text(encoding='utf-8')
    style = (STATIC / 'style.css').read_text(encoding='utf-8')
    app = (STATIC / 'app.js').read_text(encoding='utf-8')

    assert 'aria-orientation="vertical"' in index
    assert 'seek-played' not in index
    assert 'seek-played' not in style
    assert 'shuffle-btn' in index
    assert 'icon-shuffle' in index
    assert 'function toggleShuffle' in app
    assert 'function buildQueue' in app
    assert 'setupTooltips' in app
    assert 'data-tooltip' in app
    assert 'function setupTooltips' in app
    assert 'box-shadow: 0 0 0 2px var(--panel)' not in style
    assert '-webkit-line-clamp: 2' in style
    assert '.tooltip' in style
    assert 'event.clientY' in app


def test_service_worker_caches_shell_and_audio() -> None:
    worker = (STATIC / 'sw.js').read_text(encoding='utf-8')
    player = (STATIC / 'player.js').read_text(encoding='utf-8')
    cache = (STATIC / 'cache.js').read_text(encoding='utf-8')

    assert 'soundsave-shell-v12' in worker
    assert 'soundsave-audio-v1' in worker
    assert "download') === '1'" in worker
    assert "stream') === '1'" in worker
    assert 'parseRange' in worker
    assert "preload = 'auto'" in player
    assert 'document.body.appendChild(this.audio)' in player
    assert "position: 'fixed'" in player
    assert "opacity: '0'" in player
    assert "display = 'none'" not in player
    assert 'this.audio.controls = true' in player
    assert "diag('session:pause')" in player
    assert '/static/diag.js?v=' in (STATIC / 'sw.js').read_text(encoding='utf-8')
    assert 'diag-send' in (STATIC / 'index.html').read_text(encoding='utf-8')
    assert 'cover-preview' in (STATIC / 'index.html').read_text(encoding='utf-8')
    app = (STATIC / 'app.js').read_text(encoding='utf-8')
    assert 'function openPreview' in app
    assert 'renderPreview(track)' in app
    assert 'activateTrack(track)' in app
    assert 'URL.createObjectURL(blob)' in player
    assert '_prefetchNext' in player
    assert 'loadBlob: loadTrackBlob' in (STATIC / 'app.js').read_text(encoding='utf-8')
    assert 'cancelActive' in cache
    assert "visibilityState !== 'visible'" in cache


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
