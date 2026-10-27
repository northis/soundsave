from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """A short alias used by the web tests."""
    return tmp_path
