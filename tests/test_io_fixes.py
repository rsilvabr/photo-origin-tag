"""The disk-time fixes of 15/11/2026 (see the I/O report in AI_tools/). Real
exiftool, hostile folders, as in test_photo_origin_tag.py. The tests marked
[old fails] were checked to fail against the code of v0.7.0."""

import os
import shutil
import threading

import pytest

import photo_origin_tag as pot
from test_photo_origin_tag import (  # noqa: F401 - _isolated is an autouse fixture
    HOSTILE, HOSTILE_NO_PERCENT, _isolated, export_of, make_jpeg, real, run, tags,
    touch)

windows = pytest.mark.skipif(os.name != "nt", reason="Windows junctions")


# ------------------------------------- the walk: Capture One's own folders
def _junction(link, target):
    import _winapi
    _winapi.CreateJunction(str(target), str(link))


@real
@pytest.mark.parametrize("top", [HOSTILE, HOSTILE_NO_PERCENT], ids=["percent", "no-percent"])
def test_capture_one_folders_are_not_entered(tmp_path, top):
    """[old fails] the walk went into every CaptureOne folder, Capture One's
    settings and cache, where no export ever is: on the owner's G: 251 000 of
    the names it listed, and through the 435 Cache junctions to another disk
    542 000 more (a cold walk 68 s instead of 48 s)."""
    drive = tmp_path / top
    base = drive / "2027" / "270101_x" / "01_Zf"
    touch(base / "DSC_0001.JPG")
    live = export_of(base, "DSC_0001", "Green")
    c1 = base / "CaptureOne"
    touch(c1 / "Settings153" / "DSC_0001.JPG.cos")
    # nothing Capture One writes there is an export; an export-like file in
    # it, at any depth and in any case of the name, is not reached either
    inside = c1 / "Settings153" / "x"
    touch(inside / "DSC_0002.JPG")
    hidden = export_of(inside, "DSC_0002", "Green")
    other_case = drive / "2027" / "270102_y" / "CAPTUREONE" / "z"
    touch(other_case / "DSC_0003.JPG")
    hidden2 = export_of(other_case, "DSC_0003", "Green")
    stale = hidden.with_name(f"{hidden.stem}{pot.TEMP_MARK}-4242{hidden.suffix}")
    shutil.copy2(hidden, stale)
    assert [p.name for p in pot.find_exports([drive])] == [live.name]
    before = {p: p.read_bytes() for p in (hidden, hidden2)}
    assert run(drive, "--apply") == 0
    assert tags(live) == (["jxlphoto-origin:jpeg"], "Source: JPEG")
    assert all(p.read_bytes() == b for p, b in before.items())
    assert stale.exists()                        # not even its temps are touched


@real
@windows
def test_a_cache_junction_inside_capture_one_is_not_followed(tmp_path):
    """[old fails] the walk followed CaptureOne\\Cache junctions (the owner's
    tool moves every Capture One cache to another disk that way) and listed
    everything on the other side."""
    drive = tmp_path / HOSTILE
    base = drive / "2027" / "270101_x" / "01_Zf"
    touch(base / "DSC_0001.JPG")
    live = export_of(base, "DSC_0001", "Green")
    elsewhere = tmp_path / "other disk" / "C1 cache [x] 5%"
    touch(elsewhere / "DSC_0009.JPG")
    far = export_of(elsewhere, "DSC_0009", "Green")
    (base / "CaptureOne").mkdir()
    _junction(base / "CaptureOne" / "Cache", elsewhere)
    assert (base / "CaptureOne" / "Cache" / "_EXPORT").is_dir()   # the junction works
    seen = [folder for folder, _ in pot._walk(drive)]
    assert not any("Cache" in folder.parts for folder in seen)
    assert [p.name for p in pot.find_exports([drive])] == [live.name]
    before = far.read_bytes()
    assert run(drive, "--apply") == 0
    assert far.read_bytes() == before


def test_skip_dirs_holds_capture_one():
    """The setting the walk reads (any case, at any depth)."""
    assert "captureone" in {s.casefold() for s in pot.SKIP_DIRS}
