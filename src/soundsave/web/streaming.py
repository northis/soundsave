"""HTTP Range responses for audio files."""

from __future__ import annotations

import os
from email.utils import formatdate
from pathlib import Path
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import Response, StreamingResponse

from .metadata import etag_for_stat, mime_for_path

_CHUNK_SIZE = 64 * 1024


class RangeNotSatisfiableError(ValueError):
    """The requested byte range is invalid."""


def parse_range(header: str | None, size: int) -> tuple[int, int] | None:
    """Parse one ``bytes=`` range; return ``None`` for no/unsupported header."""
    if not header or not header.lower().startswith('bytes='):
        return None
    spec = header[6:].strip()
    if not spec or ',' in spec:
        raise RangeNotSatisfiableError(header)
    start_raw, separator, end_raw = spec.partition('-')
    if not separator:
        raise RangeNotSatisfiableError(header)
    try:
        if not start_raw:
            if not end_raw:
                raise RangeNotSatisfiableError(header)
            suffix = int(end_raw)
            if suffix <= 0 or size <= 0:
                raise RangeNotSatisfiableError(header)
            start = max(0, size - suffix)
            end = size - 1
        else:
            start = int(start_raw)
            end = int(end_raw) if end_raw else size - 1
    except ValueError as exc:
        raise RangeNotSatisfiableError(header) from exc
    if size <= 0 or start < 0 or start >= size or end < start:
        raise RangeNotSatisfiableError(header)
    return start, min(end, size - 1)


def _etag(stat: os.stat_result) -> str:
    return f'"{etag_for_stat(int(stat.st_size), int(stat.st_mtime_ns))}"'


def _if_none_match(request: Request, etag: str) -> bool:
    header = request.headers.get('if-none-match')
    if not header:
        return False
    return header.strip() == '*' or etag in {part.strip() for part in header.split(',')}


def _if_range_matches(request: Request, etag: str, last_modified: str) -> bool:
    header = request.headers.get('if-range')
    if not header:
        return True
    return header.strip() in {etag, last_modified}


def _chunks(path: Path, start: int, length: int) -> object:
    with path.open('rb') as file:
        file.seek(start)
        remaining = length
        while remaining > 0:
            data = file.read(min(_CHUNK_SIZE, remaining))
            if not data:
                return
            remaining -= len(data)
            yield data


def stream_audio(path: Path, request: Request, *, download: bool = False) -> Response:
    """Return a 200/206/304/416 response for *path*."""
    stat = path.stat()
    size = int(stat.st_size)
    etag = _etag(stat)
    last_modified = formatdate(stat.st_mtime, usegmt=True)
    media_type = mime_for_path(path)
    base_headers = {
        'Accept-Ranges': 'bytes',
        'ETag': etag,
        'Last-Modified': last_modified,
        'Cache-Control': 'private, max-age=3600',
        'X-Content-Type-Options': 'nosniff',
    }
    if _if_none_match(request, etag):
        return Response(status_code=304, headers=base_headers)

    range_header = None if download else request.headers.get('range')
    if range_header and not _if_range_matches(request, etag, last_modified):
        range_header = None
    try:
        requested = parse_range(range_header, size)
    except RangeNotSatisfiableError:
        return Response(
            status_code=416,
            headers={**base_headers, 'Content-Range': f'bytes */{size}', 'Content-Length': '0'},
        )

    start, end = requested if requested is not None else (0, size - 1)
    length = max(0, end - start + 1) if size else 0
    headers = {
        **base_headers,
        'Content-Type': media_type,
        'Content-Length': str(length),
    }
    if requested is not None:
        headers['Content-Range'] = f'bytes {start}-{end}/{size}'
    if download:
        headers['Content-Disposition'] = f"attachment; filename*=UTF-8''{quote(path.name)}"

    status_code = 206 if requested is not None else 200
    if request.method == 'HEAD' or length == 0:
        return Response(status_code=status_code, headers=headers)
    return StreamingResponse(
        _chunks(path, start, length),
        status_code=status_code,
        headers=headers,
        media_type=None,
    )
