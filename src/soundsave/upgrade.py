"""Audit the archive and replace low-quality tracks from an external provider."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path

from .archive import copy_tags, scan_archive
from .config import Config
from .domain import AudioQuality, Candidate, LocalTrack, MatchResult, MatchStatus, TrackQuery
from .downloader import (
    DownloadError,
    download_file,
    has_ffmpeg,
    transcode_flac_to_mp3,
    verify_audio,
)
from .manifest import Manifest, ManifestRecord
from .matching import rank_candidates
from .providers.base import MusicProvider, ProviderError
from .quality import (
    QualityPolicy,
    TargetFormat,
    describe_target,
    has_better_quality,
    is_below_target,
)
from .replacer import backup_file, install_file
from .report import ReportRow, write_csv, write_json

logger = logging.getLogger(__name__)


@dataclass
class UpgradeOptions:
    """Options for :func:`run_upgrade`."""

    apply: bool = False
    policy: QualityPolicy = QualityPolicy.BEST
    target_format: TargetFormat = TargetFormat.MP3
    min_score: float = 0.85
    duration_tolerance: float = 5.0
    limit: int | None = None
    file_id: int | None = None
    retry_failed: bool = False
    delay: float = 1.0
    report_csv: Path | None = None
    report_json: Path | None = None


def _tolerance(track: LocalTrack, options: UpgradeOptions) -> float:
    if track.duration_s is None:
        return options.duration_tolerance
    return max(options.duration_tolerance, track.duration_s * 0.02)


def _match_row(track: LocalTrack, status: str, match: MatchResult | None = None) -> ReportRow:
    candidate = match.candidate if match is not None else None
    return ReportRow(
        path=str(track.path),
        file_id=track.file_id,
        artist=track.artist,
        title=track.title,
        album=track.album,
        duration_s=track.duration_s,
        current_quality=track.quality.label,
        status=status,
        candidate_provider=candidate.provider if candidate is not None else None,
        candidate_id=candidate.provider_id if candidate is not None else None,
        candidate_title=candidate.title if candidate is not None else None,
        candidate_artist=candidate.artist if candidate is not None else None,
        candidate_duration_s=candidate.duration_s if candidate is not None else None,
        score=candidate.score if candidate is not None else None,
        has_flac=candidate.has_flac if candidate is not None else None,
    )


def _record(
    track: LocalTrack,
    status: str,
    *,
    match: MatchResult | None = None,
    quality: AudioQuality | None = None,
    backup_path: Path | None = None,
    error: str | None = None,
) -> ManifestRecord:
    candidate = match.candidate if match is not None else None
    return ManifestRecord(
        path=str(track.path),
        status=status,
        file_id=track.file_id,
        artist=track.artist,
        title=track.title,
        album=track.album,
        duration_s=track.duration_s,
        old_codec=track.quality.codec,
        old_bitrate=track.quality.bitrate_kbps,
        provider=candidate.provider if candidate is not None else None,
        candidate_id=candidate.provider_id if candidate is not None else None,
        candidate_title=candidate.title if candidate is not None else None,
        candidate_artist=candidate.artist if candidate is not None else None,
        candidate_duration_s=candidate.duration_s if candidate is not None else None,
        score=candidate.score if candidate is not None else None,
        has_flac=candidate.has_flac if candidate is not None else None,
        new_codec=quality.codec if quality is not None else None,
        new_bitrate=quality.bitrate_kbps if quality is not None else None,
        backup_path=str(backup_path) if backup_path is not None else None,
        error=error,
    )


def _download_best(
    provider: MusicProvider,
    candidate: Candidate,
    *,
    target_format: TargetFormat,
    temporary_dir: Path,
    stem: str,
    duration_tolerance: float,
) -> tuple[Path, AudioQuality, str]:
    """Download the best stream for *candidate* and return path, quality, extension."""
    duration = candidate.duration_s

    if target_format is TargetFormat.FLAC:
        if not candidate.has_flac:
            raise DownloadError('FLAC is not available for this candidate')
        url = provider.get_stream_url(candidate, 'flac')
        if not url:
            raise DownloadError('The provider did not return a FLAC stream')
        path = download_file(url, temporary_dir / f'{stem}.flac')
        quality = verify_audio(
            path,
            expected_codec='flac',
            expected_duration_s=duration,
            duration_tolerance=duration_tolerance,
        )
        return path, quality, 'flac'

    if target_format is TargetFormat.KEEP and candidate.has_flac:
        url = provider.get_stream_url(candidate, 'flac')
        if url:
            path = download_file(url, temporary_dir / f'{stem}.flac')
            quality = verify_audio(
                path,
                expected_codec='flac',
                expected_duration_s=duration,
                duration_tolerance=duration_tolerance,
            )
            return path, quality, 'flac'

    url = provider.get_stream_url(candidate, 'high')
    if url:
        path = download_file(url, temporary_dir / f'{stem}.mp3')
        quality = verify_audio(
            path,
            expected_codec='mp3',
            expected_duration_s=duration,
            duration_tolerance=duration_tolerance,
        )
        return path, quality, 'mp3'

    if candidate.has_flac and has_ffmpeg():
        url = provider.get_stream_url(candidate, 'flac')
        if not url:
            raise DownloadError('The provider did not return a 320 kbps MP3 or FLAC stream')
        flac_path = download_file(url, temporary_dir / f'{stem}.flac')
        mp3_path = temporary_dir / f'{stem}.mp3'
        transcode_flac_to_mp3(flac_path, mp3_path)
        flac_path.unlink(missing_ok=True)
        quality = verify_audio(
            mp3_path,
            expected_codec='mp3',
            expected_duration_s=duration,
            duration_tolerance=duration_tolerance,
        )
        return mp3_path, quality, 'mp3'

    if candidate.has_flac:
        raise DownloadError(
            'Only FLAC is available and ffmpeg is missing; install ffmpeg or use --format flac',
        )
    raise DownloadError('The provider did not return a 320 kbps MP3 or FLAC stream')


def _apply_track(
    track: LocalTrack,
    candidate: Candidate,
    provider: MusicProvider,
    *,
    options: UpgradeOptions,
    workspace: Path,
) -> tuple[Path, AudioQuality, Path]:
    temporary_dir = workspace / 'tmp'
    temporary_dir.mkdir(parents=True, exist_ok=True)
    stem = f'{track.file_id if track.file_id is not None else "track"}'
    temporary, quality, extension = _download_best(
        provider,
        candidate,
        target_format=options.target_format,
        temporary_dir=temporary_dir,
        stem=stem,
        duration_tolerance=_tolerance(track, options),
    )
    try:
        copy_tags(track.path, temporary)
        backup_path = backup_file(track.path, workspace / 'backups')
        final_path = install_file(temporary, track.path, extension=extension)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return final_path, quality, backup_path


def run_upgrade(config: Config, provider: MusicProvider, options: UpgradeOptions) -> list[ReportRow]:
    """Run the audit/upgrade pipeline and return report rows."""
    workspace = config.target_dir / '.soundsave'
    workspace.mkdir(parents=True, exist_ok=True)
    rows: list[ReportRow] = []
    processed = 0
    replaced = 0

    logger.info(
        'Target: %s; policy: %s; archive: %s',
        describe_target(options.target_format),
        options.policy.value,
        config.target_dir,
    )

    with Manifest(workspace / 'manifest.sqlite3') as manifest:
        tracks = scan_archive(config.target_dir, file_id=options.file_id)
        total = len(tracks)
        for index, track in enumerate(tracks, start=1):
            if not is_below_target(track, policy=options.policy, target_format=options.target_format):
                rows.append(_match_row(track, 'ok'))
                continue

            if options.limit is not None and processed >= options.limit:
                logger.info('Reached the limit of %d tracks', options.limit)
                break
            processed += 1

            previous = manifest.get(str(track.path))
            if previous is not None and previous.status == 'replaced' and not options.retry_failed:
                rows.append(_match_row(track, 'skipped'))
                logger.info('[%d/%d] %s — already replaced, skipping', index, total, track.path.name)
                continue

            query = TrackQuery(
                artist=track.artist,
                title=track.title,
                album=track.album,
                duration_s=track.duration_s,
            )
            tolerance = _tolerance(track, options)
            try:
                candidates = provider.search(query, limit=10)
            except ProviderError as exc:
                logger.error('[%d/%d] %s — provider error: %s', index, total, track.path.name, exc)
                record = _record(track, 'failed', error=str(exc))
                manifest.save(record)
                rows.append(_match_row(track, 'failed'))
                rows[-1].error = str(exc)
                continue

            match = rank_candidates(
                query,
                candidates,
                min_score=options.min_score,
                tolerance=tolerance,
            )

            if match.status is not MatchStatus.MATCHED:
                logger.info(
                    '[%d/%d] %s — %s: %s',
                    index,
                    total,
                    track.path.name,
                    match.status.value,
                    match.reason,
                )
                record = _record(track, match.status.value, match=match)
                manifest.save(record)
                rows.append(_match_row(track, match.status.value, match))
                if options.delay:
                    time.sleep(options.delay)
                continue

            candidate = match.candidate
            assert candidate is not None
            if not has_better_quality(candidate, target_format=options.target_format):
                logger.info('[%d/%d] %s — no suitable quality', index, total, track.path.name)
                record = _record(track, 'no_flac', match=match)
                manifest.save(record)
                rows.append(_match_row(track, 'no_flac', match))
                if options.delay:
                    time.sleep(options.delay)
                continue

            if not options.apply:
                logger.info(
                    '[%d/%d] %s — planned: %s (%s, score %.3f)',
                    index,
                    total,
                    track.path.name,
                    candidate.artist,
                    candidate.title,
                    candidate.score,
                )
                record = _record(track, 'planned', match=match)
                manifest.save(record)
                rows.append(_match_row(track, 'planned', match))
                if options.delay:
                    time.sleep(options.delay)
                continue

            try:
                final_path, quality, backup_path = _apply_track(
                    track,
                    candidate,
                    provider,
                    options=options,
                    workspace=workspace,
                )
            except Exception as exc:
                logger.error('[%d/%d] %s — failed: %s', index, total, track.path.name, exc)
                record = _record(track, 'failed', match=match, error=str(exc))
                manifest.save(record)
                row = _match_row(track, 'failed', match)
                row.error = str(exc)
                rows.append(row)
                if options.delay:
                    time.sleep(options.delay)
                continue

            replaced += 1
            record = _record(track, 'replaced', match=match, quality=quality, backup_path=backup_path)
            manifest.save(record)
            row = _match_row(track, 'replaced', match)
            row.new_quality = quality.label
            row.backup_path = str(backup_path)
            rows.append(row)
            logger.info(
                '[%d/%d] %s — replaced with %s (%s)',
                index,
                total,
                final_path.name,
                quality.label,
                backup_path.name,
            )
            if options.delay:
                time.sleep(options.delay)

    if options.report_csv is not None:
        write_csv(options.report_csv, rows)
        logger.info('CSV report written to %s', options.report_csv)
    if options.report_json is not None:
        write_json(options.report_json, rows)
        logger.info('JSON report written to %s', options.report_json)

    logger.info(
        'Done: %d below-target tracks processed, %d replaced, %d rows total.',
        processed,
        replaced,
        len(rows),
    )
    return rows
