from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / 'deploy' / 'windows' / 'update.ps1'


def test_update_script_is_ascii_only() -> None:
    text = SCRIPT.read_text(encoding='utf-8')
    text.encode('ascii')


def test_update_script_targets_soundsave_paths_and_build_args() -> None:
    text = SCRIPT.read_text(encoding='utf-8')

    assert 'src/soundsave/__init__.py' in text
    assert '"soundsave"' in text
    assert '"snapshot", "--label", "pre-update"' in text
    assert 'SOUNDSAVE_COMMIT' in text
    assert 'soundsave:previous' in text
    assert '--project-directory' in text
    assert 'duosave' not in text.lower()


def test_gitattributes_pins_powershell_line_endings() -> None:
    assert '*.ps1 text eol=crlf' in (REPO / '.gitattributes').read_text(encoding='utf-8')
