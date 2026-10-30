from __future__ import annotations

from soundsave.text import decode_text, repair_mojibake

TEXT = 'Исполнитель - Название'


def test_decode_utf8_with_and_without_bom() -> None:
    assert decode_text(TEXT.encode('utf-8')) == TEXT
    assert decode_text(b'\xef\xbb\xbf' + TEXT.encode('utf-8')) == TEXT


def test_decode_windows_1251() -> None:
    assert decode_text(TEXT.encode('cp1251')) == TEXT


def test_decode_utf16_with_and_without_bom() -> None:
    assert decode_text(TEXT.encode('utf-16')) == TEXT
    assert decode_text(TEXT.encode('utf-16-le')) == TEXT
    assert decode_text(TEXT.encode('utf-16-be')) == TEXT


def test_repair_utf8_read_as_latin1() -> None:
    broken = 'Москва'.encode().decode('latin-1')

    assert repair_mojibake(broken) == 'Москва'


def test_repair_cp1251_read_as_latin1() -> None:
    broken = 'Москва'.encode('cp1251').decode('latin-1')

    assert repair_mojibake(broken) == 'Москва'


def test_repair_leaves_clean_text_alone() -> None:
    assert repair_mojibake('Москва') == 'Москва'
    assert repair_mojibake('El niño') == 'El niño'
    assert repair_mojibake('The Gathering') == 'The Gathering'
    assert repair_mojibake('') == ''
