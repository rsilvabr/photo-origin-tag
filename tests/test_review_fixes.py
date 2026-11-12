"""The fixes of the 12/11 review (v0.7.0): see AI_tools/261112_Opus_review_bugs-melhorias.md
(F1-F8). Real exiftool, hostile folders, as in test_photo_origin_tag.py. The
tests marked [old fails] were checked to fail against the code of v0.6.0."""

import ctypes
import os
import re
import shutil
import sys
from pathlib import Path

import pytest

import photo_origin_tag as pot
from test_audit_fixes import three_exports
from test_photo_origin_tag import (  # noqa: F401 - _isolated is an autouse fixture
    HOSTILE, _isolated, export_of, make_jpeg, real, report_rows, run, set_tags,
    tags, touch)

windows = pytest.mark.skipif(os.name != "nt", reason="Windows file sharing")


# ------------------------------------------------ F1: a whole drive as the root
@real
def test_a_drive_root_skips_the_recycle_bin_and_system_folders(tmp_path):
    """[old fails] a root of a whole drive walked into $RECYCLE.BIN and System
    Volume Information and tagged what it found there."""
    drive = tmp_path / HOSTILE
    base = drive / "2027" / "270101_x" / "01_Zf"
    touch(base / "DSC_0001.JPG")
    live = export_of(base, "DSC_0001", "Green")
    binned = drive / "$RECYCLE.BIN" / "S-1-5-21-1" / "$R0A1B2C" / "01_Zf"
    touch(binned / "DSC_0002.JPG")
    gone = export_of(binned, "DSC_0002", "Green")
    svi = drive / "System Volume Information" / "x"
    touch(svi / "DSC_0003.JPG")
    hidden = export_of(svi, "DSC_0003", "Green")
    stale = gone.with_name(f"{gone.stem}{pot.TEMP_MARK}-4242{gone.suffix}")
    shutil.copy2(gone, stale)
    assert [p.name for p in pot.find_exports([drive])] == [live.name]
    before = {p: p.read_bytes() for p in (gone, hidden)}
    assert run(drive, "--apply") == 0
    assert tags(live) == (["jxlphoto-origin:jpeg"], "Source: JPEG")
    assert all(p.read_bytes() == b for p, b in before.items())
    assert stale.exists()                        # not even its temps are touched


def test_find_exports_filters_names_before_making_paths(tmp_path, monkeypatch):
    """[old fails] a Path per file: a whole drive (G:, 1.4 million files) held
    every one of them in memory before filtering."""
    folder = tmp_path / "cam" / "_EXPORT" / "TIFF16"
    folder.mkdir(parents=True)
    for i in range(50):
        (folder / f"note_{i}.txt").write_bytes(b"")
    (folder / "a.tif").write_bytes(b"")
    made = [0]

    class CountingPath(type(Path())):
        def __init__(self, *args):
            made[0] += 1
            super().__init__(*args)
    monkeypatch.setattr(pot, "Path", CountingPath)
    assert [p.name for p in pot.find_exports([tmp_path])] == ["a.tif"]
    assert made[0] < 20                          # per folder and per export, not per file


# --------------------------------------------- F2: exiftool missing or broken
@real
@pytest.mark.parametrize("stay_open", [True, False], ids=["stay-open", "spawn"])
@pytest.mark.parametrize("exe", ["exiftool-that-is-not-there", sys.executable],
                         ids=["missing", "broken"])
def test_no_working_exiftool_is_a_usage_error(tmp_path, monkeypatch, capsys,
                                              exe, stay_open):
    """[old fails] every export read as unreadable: exit 1, and a scheduled
    task went on to make masters from untagged exports."""
    base = tmp_path / HOSTILE / "x"
    touch(base / "a.JPG")
    p = export_of(base, "a")
    b = p.read_bytes()
    pot.close_exiftools()                        # the helpers' exiftool would answer
    monkeypatch.setattr(pot, "EXIFTOOL", exe)
    monkeypatch.setattr(pot, "EXIFTOOL_STAY_OPEN", stay_open)
    assert run(base, "--apply") == pot.EXIT_USAGE == 2
    assert "exiftool" in capsys.readouterr().err
    assert p.read_bytes() == b
    assert not list((tmp_path / "_logs").glob("*_report.csv"))   # nothing ran
    pot.close_exiftools()


@real
def test_the_header_names_the_exiftool_version(tmp_path, capsys):
    base = tmp_path / HOSTILE / "x"
    touch(base / "a.JPG")
    export_of(base, "a")
    assert run(base) == 0
    assert re.search(r"photo-origin-tag \S+ \| exiftool \d+\.\d+ \| SIMULATION",
                     capsys.readouterr().out)


# ------------------------------------- F3: a report another program holds open
def _hold_open(path: Path):
    """Open path the way Excel holds a CSV: others may read it, nobody may
    write it. Returns the function that closes it."""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = ctypes.c_void_p
    k32.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                                ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32,
                                ctypes.c_void_p]
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    # GENERIC_READ, FILE_SHARE_READ, OPEN_ALWAYS
    h = k32.CreateFileW(str(path), 0x80000000, 0x1, None, 4, 0, None)
    assert h not in (None, ctypes.c_void_p(-1).value), ctypes.get_last_error()
    return lambda: k32.CloseHandle(h)


@real
@windows
def test_a_report_held_open_goes_to_the_log_folder(tmp_path, capsys):
    """[old fails] PermissionError after the files were written: exit 3 (the
    scheduled task stopped the day) and no summary."""
    base, exports = three_exports(tmp_path)
    report = tmp_path / "held open.csv"
    close = _hold_open(report)
    try:
        rc = run(base, "--apply", "--report", report)
    finally:
        close()
    assert rc == 0
    out = capsys.readouterr().out
    assert "Summary (action, origin)" in out
    assert "the report could not be written" in out and "went to" in out
    rows = report_rows(tmp_path)                 # the copy in the log folder
    assert {rows[p.name]["action"] for p in exports} == {"written"}
    assert all(tags(p)[1] == "Source: JPEG" for p in exports)


@real
def test_the_summary_comes_before_the_report(tmp_path, capsys):
    base, exports = three_exports(tmp_path)
    assert run(base) == 0
    out = capsys.readouterr().out
    assert out.index("Summary (action, origin)") < out.index("Report: ")


# ------------------------------------------------ F4: --max-rewrites
OLD_WORDING = "Source: JPEG, sRGB (EXIF only)"   # as if PROFILE_EXIF_SRGB had changed


def _tagged_by_an_older_version(base, stem):
    make_jpeg(base / f"{stem}.JPG", exif_args=["-ExifIFD:ColorSpace#=1"])
    p = export_of(base, stem)
    set_tags(p, f"-IFD0:XPComment={OLD_WORDING}", "-XMP-dc:Relation+=jxlphoto-origin:jpeg")
    return p


@real
def test_max_rewrites_holds_back_a_mass_rewrite(tmp_path, capsys):
    """[old fails] no such option: a changed profile text rewrote every
    export that carried it, in one unattended run."""
    base = tmp_path / HOSTILE / "x"
    olds = [_tagged_by_an_older_version(base, s) for s in ("a", "b", "c")]
    make_jpeg(base / "new.JPG", exif_args=["-ExifIFD:ColorSpace#=1"])
    new = export_of(base, "new")                 # never tagged: not a rewrite
    before = {p: p.read_bytes() for p in (*olds, new)}
    assert run(base, "--apply", "--max-rewrites", 2) == pot.EXIT_TOO_MANY_REWRITES == 4
    assert all(p.read_bytes() == b for p, b in before.items())     # nothing at all
    err = capsys.readouterr().err
    assert "3 planned write(s)" in err and "--max-rewrites 2" in err
    rows = report_rows(tmp_path)
    assert {rows[p.name]["action"] for p in (*olds, new)} == {"write"}
    assert all("not written" in rows[p.name]["reason"] for p in (*olds, new))
    assert f"replaces {OLD_WORDING!r}" in rows[olds[0].name]["reason"]
    assert run(base, "--max-rewrites", 2) == 0   # a simulation only warns
    assert "WARNING: 3 planned write(s)" in capsys.readouterr().out
    assert run(base, "--apply", "--max-rewrites", 3) == 0
    assert all(tags(p) == (["jxlphoto-origin:jpeg"], "Source: JPEG, sRGB (EXIF, no ICC)")
               for p in (*olds, new))


@real
def test_new_exports_never_count_as_rewrites(tmp_path, capsys):
    base, exports = three_exports(tmp_path)
    assert run(base, "--apply", "--max-rewrites", 0) == 0
    assert all(tags(p)[1] == "Source: JPEG" for p in exports)
    assert "change tags an earlier run wrote" not in capsys.readouterr().out


def test_max_rewrites_cannot_be_negative(capsys):
    assert run("x", "--max-rewrites", -1) == pot.EXIT_USAGE
    assert "--max-rewrites" in capsys.readouterr().err


def test_what_counts_as_a_rewrite():
    path = Path(r"C:\x\cam\_EXPORT\TIFF16\a_v1.tif")

    def plan(meta, kind="jpeg", profile="sRGB (EXIF, no ICC)", overridden=False):
        it = pot.Item(path, pot.Decision(kind, "test"))
        it.profile = profile
        pot.plan_item(it, meta, overridden)
        return it
    assert not plan({}).rewrite                                  # a new export
    tagged = {"XMP-dc:Relation": "jxlphoto-origin:jpeg"}
    it = plan({**tagged, "IFD0:XPComment": OLD_WORDING})
    assert it.action == "write" and it.rewrite and it.reason == f"replaces {OLD_WORDING!r}"
    assert plan({**tagged, "IFD0:XPComment": "Source: JPEG"}).rewrite   # v0.2: gains the profile
    it = plan({"IFD0:XPComment": "Source: RAW"}, overridden=True)      # another kind
    assert it.action == "write" and it.rewrite
    it = plan({"XMP-dc:Relation": "jxlphoto-origin:tiff8"}, overridden=True)
    assert it.rewrite and "removes jxlphoto-origin:tiff8" in it.reason
    it = plan({**tagged, "IFD0:XPComment": "Source: JPEG, sRGB (EXIF, no ICC)"})
    assert it.action == "ok" and not it.rewrite
    it = plan({"IFD0:XPComment": "  "})                           # blank: not ours
    assert it.action == "write" and not it.rewrite


# ------------------------------------- F5: an --overrides file that is not there
@real
def test_an_overrides_file_named_by_hand_must_exist(tmp_path, capsys):
    """[old fails] a typo ran with no overrides, and the rules decided what
    the file existed to correct."""
    base = tmp_path / HOSTILE / "x"
    touch(base / "a.JPG")
    p = export_of(base, "a")
    b = p.read_bytes()
    assert run(base, "--apply", "--overrides", tmp_path / "typo.csv") == 2
    assert "--overrides: no such file" in capsys.readouterr().err
    assert run(base, "--apply", "--overrides", tmp_path) == 2     # a folder
    assert p.read_bytes() == b


def test_an_overrides_path_that_cannot_be_read_is_a_usage_error(tmp_path):
    with pytest.raises(pot.UsageError, match="cannot be read"):
        pot.load_overrides(tmp_path)              # a folder where the file should be


# ---------------------------------------------- F6: one --apply at a time
@real
def test_a_second_apply_exits_2_and_touches_nothing(tmp_path, capsys):
    """[old fails] two --apply ran together: each removed the other's temps."""
    base = tmp_path / HOSTILE / "x"
    touch(base / "a.JPG")
    p = export_of(base, "a")
    stale = p.with_name(f"{p.stem}{pot.TEMP_MARK}-4242{p.suffix}")
    shutil.copy2(p, stale)                        # as if the other run were writing it
    b = p.read_bytes()
    held = pot.acquire_run_lock()                 # the other run
    try:
        assert run(base, "--apply") == pot.EXIT_USAGE
        err = capsys.readouterr().err
        assert "another photo-origin-tag --apply is running" in err
        assert f"PID {os.getpid()}" in err
        assert p.read_bytes() == b and stale.exists()
        assert run(base) == 0                     # a simulation needs no lock
    finally:
        pot.release_run_lock(held)
    assert run(base, "--apply") == 0              # free again
    assert tags(p)[1] == "Source: JPEG" and not stale.exists()


def test_a_lock_file_a_killed_run_left_is_no_lock(tmp_path):
    logs = tmp_path / "_logs"
    logs.mkdir()
    (logs / pot.LOCK_FILE).write_text("PID 99999, since 2026-01-01 00:00:00",
                                      encoding="utf-8")
    fd = pot.acquire_run_lock()                   # nobody holds it: taken
    pot.release_run_lock(fd)
    text = (logs / pot.LOCK_FILE).read_text(encoding="utf-8")
    assert text.startswith(f"PID {os.getpid()}, since ") and "99999" not in text


# ------------------------------------------------------------- F8: small ones
def test_profile_label_takes_a_bigtiff_and_names_its_problem():
    """[old fails] a BigTIFF original read cleanly was not "no profile"."""
    assert pot.profile_label({"File:FileType": "BTF"}) == pot.PROFILE_NONE
    assert pot.profile_problem({"File:FileType": "JPEG"}) == ""
    assert pot.profile_problem(None) == "original unreadable"
    assert pot.profile_problem({"File:FileType": "TXT"}) == \
        "original unreadable (File:FileType TXT)"
    assert pot.profile_problem({"ExifTool:Error": "File is empty"}) == \
        "original unreadable (File is empty)"
    warn = {"File:FileType": "JPEG", "ExifTool:Warning": "[minor] Possibly incorrect maker notes"}
    assert pot.profile_label(warn) is None        # decided 08/11: only a clean read
    assert pot.profile_problem(warn) == \
        "original read with a warning ([minor] Possibly incorrect maker notes)"


def test_a_tiff_of_another_depth_says_so():
    """[old fails] "cannot read the bit depth (32 32 32)": it was read."""
    d = pot.classify({".tif": "s.tif"}, {}, tiff_bits="32 32 32")
    assert d.kind is None
    assert d.review == "the original TIFF is neither 8- nor 16-bit (32 32 32 bits per sample)"
    d = pot.classify({".orf": "s.orf", ".tif": "s.tif"}, {"XMP-xmp:Label": "Green"})
    assert d.review == "cannot read the original TIFF's bit depth"


@real
def test_an_original_read_with_a_warning_is_named_in_the_report(tmp_path, monkeypatch):
    base = tmp_path / HOSTILE / "x"
    make_jpeg(base / "a.JPG")                     # no ICC, no colour space
    p = export_of(base, "a")
    real_read = pot.read_tags

    def warned(paths, tag_args, extra=(), progress=None):
        out = real_read(paths, tag_args, extra, progress)
        for k, e in out.items():
            if k.endswith("a.jpg"):
                e["ExifTool:Warning"] = "[minor] Possibly incorrect maker notes"
        return out
    monkeypatch.setattr(pot, "read_tags", warned)
    assert run(base, "--apply") == 0
    assert tags(p)[1] == "Source: JPEG"           # not "no color profile"
    assert report_rows(tmp_path)[p.name]["reason"] == (
        "original read with a warning ([minor] Possibly incorrect maker notes): "
        "its colour profile is not recorded")
    set_tags(p, "-IFD0:XPComment=Source: JPEG, Adobe RGB (1998)")   # an earlier run's
    assert run(base, "--apply") == 0
    row = report_rows(tmp_path)[p.name]
    assert row["action"] == "ok"
    assert row["reason"].endswith("the profile an earlier run recorded is kept")


def test_progress_into_a_file_prints_plain_lines(capsys):
    p = pot.Progress("reading x")                 # pytest's capture is no console
    p(1, 3)
    p(2, 3)                                       # within PROGRESS_EVERY_LOG: silent
    p(3, 3)
    out = capsys.readouterr().out
    assert "\r" not in out and out.count("reading x:") == 2
