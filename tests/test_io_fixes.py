"""The disk-time fixes of 15/11/2026 (see the I/O report in AI_tools/). Real
exiftool, hostile folders, as in test_photo_origin_tag.py. The tests marked
[old fails] were checked to fail against the code of v0.7.0."""

import os
import shutil
import threading

import pytest

import photo_origin_tag as pot
from test_photo_origin_tag import (  # noqa: F401 - _isolated, hostile: fixtures
    HOSTILE, HOSTILE_NO_PERCENT, _isolated, export_of, hostile, real, run, tags,
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


# ------------------------------ planning reads: the files opened ahead
def _open_ahead_threads():
    return [t for t in threading.enumerate() if t.name == "open-ahead" and t.is_alive()]


@real
def test_planning_reads_open_each_file_ahead_in_a_thread(tmp_path, monkeypatch, hostile):
    """[old fails] each file was opened only when exiftool read it: the scan an
    on-access antivirus makes of a cold file (~19 ms on the owner's HDD) and
    exiftool's parsing of the file before it (5-11 ms) took turns. Now a
    thread opens the next files while exiftool works (cold NEFs 36.8 -> 23.4
    ms a file)."""
    base = tmp_path / hostile / "x"
    files = [export_of(base, f"DSC_{i:04d}", "Green") for i in range(7)]
    opened = []

    def spy(path):
        opened.append((str(path), threading.current_thread() is not threading.main_thread()))
    monkeypatch.setattr(pot, "_open_and_close", spy, raising=False)
    monkeypatch.setattr(pot, "READ_BATCH", 2)
    monkeypatch.setattr(pot, "OPEN_AHEAD", 3, raising=False)
    got = pot.read_tags(files, ["-XMP-xmp:Label"], extra=["-fast2"])
    assert [p for p, _ in opened] == [str(f) for f in files]   # each once, in order
    assert all(in_thread for _, in_thread in opened)
    assert set(got) == {pot._key(f) for f in files}
    assert not _open_ahead_threads()


@real
def test_opening_ahead_stays_within_its_lead(tmp_path, monkeypatch):
    """[old fails] the thread never runs more than OPEN_AHEAD files past what
    exiftool has answered (a long run must not open the whole drive first)."""
    base = tmp_path / HOSTILE / "x"
    files = [export_of(base, f"DSC_{i:04d}", "Green") for i in range(8)]
    opened, seen = [], []
    monkeypatch.setattr(pot, "_open_and_close", opened.append, raising=False)
    monkeypatch.setattr(pot, "READ_BATCH", 1)
    monkeypatch.setattr(pot, "OPEN_AHEAD", 2, raising=False)
    real_chunk = pot._read_chunk

    def chunk(c, tags, extra):
        seen.append((len(opened), files.index(c[0])))
        return real_chunk(c, tags, extra)
    monkeypatch.setattr(pot, "_read_chunk", chunk)
    pot.read_tags(files, ["-XMP-xmp:Label"])
    assert len(opened) == len(files)
    assert all(n <= i + 2 for n, i in seen), seen


@real
def test_opening_ahead_changes_no_answer(tmp_path, monkeypatch, hostile):
    """The answers are exiftool's, with the files opened ahead or not: one that
    is not there or is not a TIFF reads as before, and the thread's own
    failure to open it is nobody's concern."""
    base = tmp_path / hostile / "x"
    files = [export_of(base, f"DSC_{i:04d}", lbl)
             for i, lbl in enumerate(["Green", "Blue", None, "Green"])]
    gone = base / "_EXPORT" / "TIFF16" / "not there_v1.tif"
    one_byte = touch(base / "_EXPORT" / "TIFF16" / "one byte_v1.tif")
    paths = [*files[:2], gone, one_byte, *files[2:]]
    question = ["-XMP-xmp:Label", "-File:FileType", "-ExifTool:Error"]
    monkeypatch.setattr(pot, "READ_BATCH", 2)
    monkeypatch.setattr(pot, "OPEN_AHEAD", 0, raising=False)
    without = pot.read_tags(paths, question, extra=["-fast2"])
    monkeypatch.setattr(pot, "OPEN_AHEAD", 3, raising=False)
    tried = []
    real_open = getattr(pot, "_open_and_close", None)

    def spy(path):
        tried.append(str(path))
        if real_open is not None:
            real_open(path)                  # raises for the missing one
    monkeypatch.setattr(pot, "_open_and_close", spy, raising=False)
    ahead = pot.read_tags(paths, question, extra=["-fast2"])
    assert ahead == without
    assert pot._key(gone) not in ahead
    assert tried == [str(p) for p in paths]   # it went on past the missing one
    assert not _open_ahead_threads()


@real
def test_opening_ahead_leaves_no_thread_when_the_read_is_cut_short(tmp_path, monkeypatch):
    """Ctrl+C (or any exception) in the middle of a read: the thread is stopped
    and gone when read_tags lets the exception go on."""
    base = tmp_path / HOSTILE / "x"
    files = [export_of(base, f"DSC_{i:04d}", "Green") for i in range(5)]
    monkeypatch.setattr(pot, "READ_BATCH", 1)
    monkeypatch.setattr(pot, "OPEN_AHEAD", 2, raising=False)
    calls = [0]
    real_chunk = pot._read_chunk

    def cut(c, tags, extra):
        calls[0] += 1
        if calls[0] == 2:
            raise KeyboardInterrupt
        return real_chunk(c, tags, extra)
    monkeypatch.setattr(pot, "_read_chunk", cut)
    with pytest.raises(KeyboardInterrupt):
        pot.read_tags(files, ["-XMP-xmp:Label"])
    assert not _open_ahead_threads()
