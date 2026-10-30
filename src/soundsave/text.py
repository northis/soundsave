"""Encoding-tolerant text decoding and conservative mojibake repair."""

from __future__ import annotations

import re
import unicodedata

_UTF8_BOM = b'\xef\xbb\xbf'
_UTF16_LE_BOM = b'\xff\xfe'
_UTF16_BE_BOM = b'\xfe\xff'
_UTF32_LE_BOM = b'\xff\xfe\x00\x00'
_UTF32_BE_BOM = b'\x00\x00\xfe\xff'

_CYRILLIC = re.compile(r'[А-Яа-яЁё]')
_LETTER = re.compile(r'[^\W\d_]', re.UNICODE)
# Characters that usually appear when UTF-8 or cp1251 bytes were decoded with
# a single-byte Latin codec (Windows-1252/Latin-1).
_MOJIBAKE_MARKERS = frozenset('ÃÂÐÑÌÍÎÏÒÓÔÕÖ×ØÙÚÛÜÝÞßðñòóôõö÷øùúûüýþ')
_CYRILLIC_REPAIR_RATIO = 0.3


def decode_text(raw: bytes) -> str:
    """Decode playlist, CUE and similar text files.

    Handles UTF-8 (with or without a BOM), UTF-16/UTF-32 with a BOM, UTF-16
    without a BOM when the NUL pattern is obvious, and falls back to
    Windows-1251 for Cyrillic text saved by old Windows tools.
    """
    if not raw:
        return ''
    if raw.startswith((_UTF32_LE_BOM, _UTF32_BE_BOM)):
        return raw.decode('utf-32')
    if raw.startswith((_UTF16_LE_BOM, _UTF16_BE_BOM)):
        return raw.decode('utf-16')
    if raw.startswith(_UTF8_BOM):
        return raw.decode('utf-8-sig')

    utf16 = _decode_utf16_without_bom(raw)
    if utf16 is not None:
        return utf16

    for encoding in ('utf-8', 'cp1251'):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode('latin-1')


def repair_mojibake(text: str) -> str:
    """Undo common UTF-8/cp1251-as-Latin-1 mojibake without touching clean text."""
    if not text or _cyrillic_ratio(text) >= 0.5:
        return text
    if not any(char in _MOJIBAKE_MARKERS for char in text):
        return text

    candidates: list[str] = []
    for source_encoding in ('latin-1', 'cp1252'):
        try:
            raw = text.encode(source_encoding)
        except UnicodeEncodeError:
            continue
        for target_encoding in ('utf-8', 'cp1251'):
            try:
                candidate = raw.decode(target_encoding)
            except UnicodeDecodeError:
                continue
            if candidate == text or _cyrillic_ratio(candidate) < _CYRILLIC_REPAIR_RATIO:
                continue
            if _has_bad_characters(candidate):
                continue
            candidates.append(candidate)
    if not candidates:
        return text
    return max(candidates, key=_cyrillic_ratio)


def _decode_utf16_without_bom(raw: bytes) -> str | None:
    if len(raw) < 4 or len(raw) % 2:
        return None
    if raw.count(0) < 2:
        return None

    best: tuple[float, str] | None = None
    for encoding in ('utf-16-le', 'utf-16-be'):
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        if '\x00' in text or _has_bad_characters(text):
            continue
        score = _text_quality(text)
        if best is None or score > best[0]:
            best = (score, text)
    if best is None or best[0] < 0.6:
        return None
    return best[1]


def _text_quality(text: str) -> float:
    if not text:
        return 0.0
    meaningful = sum(
        1 for char in text if char in '\t\n\r' or 0x20 <= ord(char) < 0x7F or 0x00C0 <= ord(char) <= 0x04FF
    )
    return meaningful / len(text)


def _cyrillic_ratio(text: str) -> float:
    letters = _LETTER.findall(text)
    if not letters:
        return 0.0
    return sum(1 for char in letters if _CYRILLIC.match(char)) / len(letters)


def _has_bad_characters(text: str) -> bool:
    return any(unicodedata.category(char) in {'Cc', 'Cs', 'Co', 'Cn'} and char not in '\t\n\r' for char in text)
