"""CSV and JSON reports for the audit and upgrade commands."""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path


@dataclass
class ReportRow:
    """One row of an audit/upgrade report."""

    path: str
    file_id: int | None
    artist: str
    title: str
    album: str | None
    duration_s: float | None
    current_quality: str
    status: str
    candidate_provider: str | None = None
    candidate_id: str | None = None
    candidate_title: str | None = None
    candidate_artist: str | None = None
    candidate_duration_s: float | None = None
    score: float | None = None
    has_flac: bool | None = None
    new_quality: str | None = None
    backup_path: str | None = None
    error: str | None = None


def write_csv(path: Path, rows: list[object], row_type: type = ReportRow) -> Path:
    """Write *rows* to *path* as CSV."""
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = [field.name for field in fields(row_type)]
    with path.open('w', newline='', encoding='utf-8') as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))
    return path


def write_json(path: Path, rows: list[object]) -> Path:
    """Write *rows* to *path* as JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([asdict(row) for row in rows], ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8',
    )
    return path
