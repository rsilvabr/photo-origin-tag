"""Shared test setup."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import photo_origin_tag  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _close_exiftools_at_the_end():
    """The helpers that call run_exiftool outside main() (set_tags, tags) start
    a persistent `exiftool -stay_open True -@ -` that does not leave when its
    stdin closes: without this one is left behind, parentless, per pytest run."""
    yield
    photo_origin_tag.close_exiftools()
