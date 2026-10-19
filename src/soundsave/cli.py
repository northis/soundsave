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
from .config import Config, ConfigError
from .downloader import DownloadError
from .providers.base import ProviderError
from .providers.zvuk import ZvukProvider
from .quality import QualityPolicy, TargetFormat
from .sync import run_sync
from .upgrade import UpgradeOptions, run_upgrade

logger = logging.getLogger('soundsave')


def _add_scan_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('--dir', dest='target_dir', type=Path, help='archive directory (overrides TARGET_DIR)')
    parser.add_argument('--limit', type=int, help='maximum number of below-target tracks to process')
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
