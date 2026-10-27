"""Command line interface for Soundsave."""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from . import __version__
from .archive import ArchiveError
from .catalogs import CompositeCatalog, MetadataCatalog
from .config import Config, ConfigError
from .discogs_catalog import DiscogsCatalog
from .downloader import DownloadError
from .providers.base import ProviderError
from .providers.zvuk import ZvukProvider
from .quality import QualityPolicy, TargetFormat
from .sync import run_sync
from .tags import TagsOptions, run_tags
from .upgrade import UpgradeOptions, run_upgrade
from .yandex_catalog import YandexCatalog

logger = logging.getLogger('soundsave')


def _add_scan_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('--dir', dest='target_dir', type=Path, help='archive directory (overrides TARGET_DIR)')
    parser.add_argument('--limit', type=int, help='maximum number of tracks to process')
    parser.add_argument('--id', dest='file_id', type=int, help='only process the track with this numeric id')
    parser.add_argument('--min-score', type=float, help='minimum match score (default: 0.85)')
    parser.add_argument(
        '--duration-tolerance',
        type=float,
        help='absolute duration tolerance in seconds (default: 5, plus 2%% of the track length)',
    )
    parser.add_argument('--delay', type=float, help='pause between network operations in seconds (default: 1.0)')
    parser.add_argument('--csv', dest='report_csv', type=Path, help='write a CSV report to this path')
    parser.add_argument('--json', dest='report_json', type=Path, help='write a JSON report to this path')


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        prog='soundsave',
        description='Keep a local Yandex Music playlist archive and upgrade it to 320 kbps MP3 or FLAC.',
    )
    parser.add_argument('--version', action='version', version=f'%(prog)s {__version__}')
    parser.add_argument('--env-file', type=Path, help='path to a .env file')
    parser.add_argument('-v', '--verbose', action='store_true', help='enable debug logging')

    subparsers = parser.add_subparsers(dest='command', required=True)

    sync_parser = subparsers.add_parser('sync', help='download new tracks from the Yandex Music playlist')
    sync_parser.add_argument('--dry-run', action='store_true', help='show what would be downloaded')
    sync_parser.add_argument('--limit', type=int, help='maximum number of tracks to download')

    audit_parser = subparsers.add_parser('audit', help='report tracks below the target quality')
    _add_scan_arguments(audit_parser)

    upgrade_parser = subparsers.add_parser('upgrade', help='replace tracks below the target quality')
    _add_scan_arguments(upgrade_parser)
    upgrade_parser.add_argument('--apply', action='store_true', help='actually download and replace files')
    upgrade_parser.add_argument(
        '--policy',
        choices=[policy.value for policy in QualityPolicy],
        default=QualityPolicy.BEST.value,
        help='quality policy (default: best)',
    )
    upgrade_parser.add_argument(
        '--format',
        dest='target_format',
        choices=[target.value for target in TargetFormat],
        default=TargetFormat.MP3.value,
        help='target format (default: mp3)',
    )
    upgrade_parser.add_argument(
        '--retry-failed',
        action='store_true',
        help='retry tracks that failed during a previous run',
    )

    tags_parser = subparsers.add_parser('tags', help='check and fill missing tags, covers and lyrics')
    _add_scan_arguments(tags_parser)
    tags_parser.add_argument('--apply', action='store_true', help='write missing values into existing files')
    tags_parser.add_argument(
        '--source',
        choices=['yandex', 'discogs', 'both'],
        default='both',
        help='metadata sources (default: both)',
    )
    tags_parser.add_argument('--no-search', action='store_true', help='do not search the metadata catalogues')
    tags_parser.add_argument('--no-lyrics', action='store_true', help='do not fetch or write lyrics')
    tags_parser.add_argument(
        '--no-skip-complete',
        action='store_true',
        help='with --source discogs, query even files that already have all fields',
    )
    tags_parser.add_argument(
        '--discogs-delay',
        type=float,
        help='pause between Discogs requests in seconds (default: 2.0)',
    )

    serve_parser = subparsers.add_parser('serve', help='run the web player and PWA')
    serve_parser.add_argument('--host', help='bind address (overrides SOUNDSAVE_HOST)')
    serve_parser.add_argument('--port', type=int, help='port (overrides SOUNDSAVE_PORT)')
    serve_parser.add_argument('--reload', action='store_true', help='reload on source changes (development)')

    subparsers.add_parser('scan', help='reconcile the web library database with the music folder')

    snapshot_parser = subparsers.add_parser('snapshot', help='copy the web library database to DATA_DIR/backups')
    snapshot_parser.add_argument('--label', default='manual', help='snapshot label (default: manual)')
    snapshot_parser.add_argument('--keep', type=int, default=10, help='number of snapshots to keep (default: 10)')
    return parser


def _override_config(config: Config, args: argparse.Namespace) -> Config:
    overrides: dict[str, object] = {}
    if getattr(args, 'target_dir', None) is not None:
        overrides['target_dir'] = Path(args.target_dir).expanduser()
    if getattr(args, 'min_score', None) is not None:
        overrides['min_score'] = args.min_score
    if getattr(args, 'duration_tolerance', None) is not None:
        overrides['duration_tolerance'] = args.duration_tolerance
    if getattr(args, 'delay', None) is not None:
        overrides['delay'] = args.delay
    if getattr(args, 'discogs_delay', None) is not None:
        overrides['discogs_delay'] = args.discogs_delay
    if overrides:
        return replace(config, **overrides)
    return config


def _print_summary(rows: list[object]) -> None:
    statuses = Counter(getattr(row, 'status', 'unknown') for row in rows)
    for status, count in sorted(statuses.items()):
        logger.info('%s: %d', status, count)


def _run_audit(config: Config, args: argparse.Namespace) -> int:
    provider = ZvukProvider(config.require_zvuk())
    options = UpgradeOptions(
        apply=False,
        policy=QualityPolicy.BEST,
        target_format=TargetFormat.MP3,
        min_score=config.min_score,
        duration_tolerance=config.duration_tolerance,
        limit=args.limit,
        file_id=args.file_id,
        delay=config.delay,
        report_csv=args.report_csv,
        report_json=args.report_json,
    )
    rows = run_upgrade(config, provider, options)
    _print_summary(rows)
    return 0


def _run_upgrade(config: Config, args: argparse.Namespace) -> int:
    policy = QualityPolicy(args.policy)
    apply = bool(args.apply) and policy is not QualityPolicy.REPORT
    provider = ZvukProvider(config.require_zvuk())
    options = UpgradeOptions(
        apply=apply,
        policy=policy,
        target_format=TargetFormat(args.target_format),
        min_score=config.min_score,
        duration_tolerance=config.duration_tolerance,
        limit=args.limit,
        file_id=args.file_id,
        retry_failed=args.retry_failed,
        delay=config.delay,
        report_csv=args.report_csv,
        report_json=args.report_json,
    )
    rows = run_upgrade(config, provider, options)
    _print_summary(rows)
    return 0


def _build_metadata_catalog(config: Config, args: argparse.Namespace) -> MetadataCatalog:
    sources = {'yandex', 'discogs'} if args.source == 'both' else {args.source}
    if args.no_search and sources == {'discogs'}:
        raise ConfigError('--no-search cannot be used with --source discogs')
    if 'discogs' in sources and config.discogs_delay < 1.0:
        raise ConfigError('DISCOGS_DELAY must be at least 1.0 second')

    catalogs: list[MetadataCatalog] = []
    if 'yandex' in sources:
        if not config.yandex_token:
            raise ConfigError('YANDEX_TOKEN (or API_KEY) is not set')
        catalogs.append(YandexCatalog.load(config.yandex_token, config.playlist_url))
    if 'discogs' in sources:
        if not config.discogs_token:
            if sources == {'discogs'}:
                raise ConfigError('DISCOGS_TOKEN is not set')
            logger.warning('DISCOGS_TOKEN is not set; Discogs fallback is disabled')
        else:
            discogs = DiscogsCatalog(config.discogs_token, delay=config.discogs_delay)
            discogs.verify()
            catalogs.append(discogs)

    if not catalogs:
        raise ConfigError('No metadata sources are configured')
    if len(catalogs) == 1:
        return catalogs[0]
    return CompositeCatalog(catalogs[0], catalogs[1])


def _run_tags(config: Config, args: argparse.Namespace) -> int:
    catalog = _build_metadata_catalog(config, args)
    options = TagsOptions(
        apply=args.apply,
        search=not args.no_search,
        lyrics=not args.no_lyrics,
        skip_complete=args.source == 'discogs' and not args.no_skip_complete,
        min_score=config.min_score,
        duration_tolerance=config.duration_tolerance,
        limit=args.limit,
        file_id=args.file_id,
        delay=config.delay,
        report_csv=args.report_csv,
        report_json=args.report_json,
    )
    rows = run_tags(config, catalog, options)
    _print_summary(rows)
    return 0


def _run_serve(config: Config, args: argparse.Namespace) -> int:
    try:
        import uvicorn
    except ImportError as exc:
        raise ConfigError('Install the web dependencies with: pip install "soundsave[web]"') from exc

    host = args.host or config.web_host
    port = args.port or config.web_port
    if args.reload:
        uvicorn.run(
            'soundsave.web.app:create_app_from_env',
            factory=True,
            host=host,
            port=port,
            reload=True,
        )
    else:
        from .web.app import create_app

        uvicorn.run(create_app(config), host=host, port=port)
    return 0


def _run_scan(config: Config) -> int:
    from .web.db import LibraryStore
    from .web.library import LibraryScanner
    from .web.settings import WebSettings

    settings = WebSettings.from_config(config)
    settings.ensure()
    store = LibraryStore(settings.db_path)
    store.init()
    report = LibraryScanner(settings, store).reconcile()
    logger.info(
        'Scanned %d playlists, %d tracks: +%d ~%d -%d =%d in %.2fs',
        report.playlists,
        report.tracks,
        report.added,
        report.updated,
        report.removed,
        report.unchanged,
        report.elapsed_s,
    )
    return 0


def _run_snapshot(config: Config, args: argparse.Namespace) -> int:
    import re
    import sqlite3
    from datetime import datetime, timezone

    from .web.settings import WebSettings

    if args.keep < 1:
        raise ValueError('--keep must be >= 1')
    settings = WebSettings.from_config(config)
    settings.ensure()
    database = settings.db_path
    if not database.exists():
        logger.info('No library database at %s; nothing to snapshot', database)
        return 0
    label = re.sub(r'[^A-Za-z0-9_-]+', '-', args.label).strip('-') or 'manual'
    backup_dir = settings.data_dir / 'backups'
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    destination = backup_dir / f'{label}-{stamp}.sqlite3'
    source = sqlite3.connect(database)
    target = sqlite3.connect(destination)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    logger.info('Snapshot written to %s', destination)
    snapshots = sorted(
        backup_dir.glob(f'{label}-*.sqlite3'),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for old in snapshots[args.keep :]:
        old.unlink(missing_ok=True)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )

    try:
        config = _override_config(Config.load(args.env_file), args)
        if args.command == 'sync':
            run_sync(config, dry_run=args.dry_run, limit=args.limit)
            return 0
        if args.command == 'audit':
            return _run_audit(config, args)
        if args.command == 'upgrade':
            return _run_upgrade(config, args)
        if args.command == 'tags':
            return _run_tags(config, args)
        if args.command == 'serve':
            return _run_serve(config, args)
        if args.command == 'scan':
            return _run_scan(config)
        if args.command == 'snapshot':
            return _run_snapshot(config, args)
    except (ConfigError, ProviderError, ArchiveError, DownloadError, ValueError) as exc:
        logger.error('%s', exc)
        return 1
    except KeyboardInterrupt:
        logger.error('Interrupted')
        return 130
    except Exception:
        logger.exception('Unexpected error')
        return 1

    parser.print_help()
    return 2


if __name__ == '__main__':
    sys.exit(main())
