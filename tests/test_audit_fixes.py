"""The fixes of the 11/11 audit (v0.5.0): see AI_tools/261111_Fable_plan_audit-fixes.md.
Real exiftool, hostile folders, as in test_photo_origin_tag.py. The tests marked
[old fails] were checked to fail against the code of v0.4.0."""

import shutil
from pathlib import Path

import pytest

import photo_origin_tag as pot
from test_jpg_only import jpg_only, segments
from test_photo_origin_tag import (  # noqa: F401 - _isolated is an autouse fixture
    HOSTILE, _isolated, export_of, hostile, make_icc, make_jpeg, make_tiff, real,
    report_rows, run, set_tags, tags, touch)

CRD = "-XMP-crd:CameraProfile=Camera Standard"


def snapshot(root: Path):
    """Every path below root but the logs folder, relative to it."""
    return sorted(str(p.relative_to(root)) for p in root.rglob("*")
                  if "_logs" not in p.relative_to(root).parts)


# ------------------------------------------------------------ A1: % in the path
@real
@pytest.mark.parametrize("folder", ["50%done", "100%final", "x%e y", "a%c", "t%-.2f"])
def test_a_percent_code_in_the_path_creates_nothing_elsewhere(tmp_path, folder):
    """[old fails] exiftool read %d %f %e %c in -o as format codes."""
    base = tmp_path / folder / "01_Zf"
    touch(base / "DSC_0001.JPG")
    p = export_of(base, "DSC_0001", "Green")
    before = snapshot(tmp_path)
    assert run(base, "--apply") == 0
    assert tags(p) == (["jxlphoto-origin:jpeg"], "Source: JPEG")
    after = snapshot(tmp_path)
    assert after == before                       # no new folder, no leftover file
    assert not [x for x in after if pot.TEMP_MARK in x]


@real
def test_a_stale_inplace_temp_of_exiftool_goes_with_the_other_temps(tmp_path):
    base = tmp_path / HOSTILE / "x"
    touch(base / "a.JPG")
    p = export_of(base, "a")
    sfx = pot.EXIFTOOL_INPLACE_SUFFIX
    stale = p.with_name(f"{p.stem}{pot.TEMP_MARK}-4242{p.suffix}{sfx}")
    orphan = p.with_name(f"gone{pot.TEMP_MARK}-1{p.suffix}{sfx}")      # no original
    shutil.copy2(p, stale)
    shutil.copy2(p, orphan)
    assert run(base) == 0                        # a simulation removes nothing
    assert stale.exists()
    assert run(base, "--apply") == 0
    assert not stale.exists() and orphan.exists()
    assert tags(p) == (["jxlphoto-origin:jpeg"], "Source: JPEG")


# ------------------------------------------------------- A2: a JPEG with no Exif
@real
def test_a_jpeg_without_any_exif_gets_its_comment(tmp_path, hostile):
    """[old fails] the new IFD0's mandatory tags were refused as changes."""
    base = tmp_path / hostile / "01_Z7"
    touch(base / "DSC_0001.NEF")
    p = make_jpeg(base / "_EXPORT" / "JPG_only" / "DSC_0001_AdobeRGB_v1.jpg",
                  icc=make_icc("Adobe RGB (1998)"))     # no set_tags: no Exif at all
    assert not [k for k in pot.all_tags(p) if k.startswith(("IFD0:", "ExifIFD:"))]
    before = segments(p)
    assert run(base, "--apply") == 0
    assert tags(p) == ([], "Source: RAW")
    assert segments(p) == before                 # XMP/ICC/tables/scan: same bytes


@real
def test_in_a_jpeg_with_exif_a_changed_resolution_is_still_an_error(
        tmp_path, hostile, monkeypatch):
    base = tmp_path / hostile / "01_Z7"
    touch(base / "DSC_0001.NEF")
    p = jpg_only(base)                           # has Exif (the model)
    before = p.read_bytes()
    real_all = pot.all_tags

    def lying(path):
        t = real_all(path)
        if pot.TEMP_MARK in Path(path).name:
            t["IFD0:XResolution"] = "999"
        return t
    monkeypatch.setattr(pot, "all_tags", lying)
    assert run(base, "--apply") == 1
    assert p.read_bytes() == before
    assert "IFD0:XResolution" in report_rows(tmp_path)[p.name]["reason"]


# ------------------------------------------- A3: an unreadable export, simulated
@real
@pytest.mark.parametrize("content", [b"", b"x"], ids=["0-byte", "1-byte"])
def test_an_unreadable_export_is_an_error_in_the_simulation(tmp_path, content):
    """[old fails] the plan said `write raw` and only --apply failed."""
    base = tmp_path / HOSTILE / "x"
    touch(base / "DSC_0001.NEF")
    bad = base / "_EXPORT" / "TIFF16" / "DSC_0001_ProPhoto.tif"
    bad.parent.mkdir(parents=True)
    bad.write_bytes(content)
    assert run(base) == 1
    row = report_rows(tmp_path)[bad.name]
    assert row["action"] == "error" and "could not read the export" in row["reason"]


@real
def test_an_export_that_is_not_the_type_of_its_extension_is_an_error(tmp_path):
    base = tmp_path / HOSTILE / "x"
    touch(base / "DSC_0001.NEF")
    liar = make_jpeg(base / "_EXPORT" / "TIFF16" / "DSC_0001_ProPhoto.tif")
    before = liar.read_bytes()
    assert run(base, "--apply") == 1
    assert liar.read_bytes() == before
    assert "FileType JPEG" in report_rows(tmp_path)[liar.name]["reason"]


# --------------------------------------------------------- A4: relative roots
@real
def test_a_dot_inside_the_export_folder_finds_the_export(tmp_path, monkeypatch):
    """[old fails] '.' has no _EXPORT segment: 0 exports, exit 0, no warning."""
    base = tmp_path / HOSTILE / "01_Zf"
    touch(base / "DSC_0001.JPG")
    p = export_of(base, "DSC_0001", "Green")
    monkeypatch.chdir(p.parent)
    assert run(".") == 0
    assert report_rows(tmp_path)[p.name]["action"] == "write"


@real
def test_a_relative_root_still_matches_an_absolute_override(tmp_path, monkeypatch):
    """[old fails] the override did not match and the automatic rule decided."""
    base = tmp_path / HOSTILE / "01_Zf"
    touch(base / "DSC_0001.JPG")
    p = export_of(base, "DSC_0001", "Green")      # the rules say JPEG
    ov = tmp_path / "ov.csv"
    ov.write_text(f"path,origin,note\n{p},raw,checked\n", encoding="utf-8")
    monkeypatch.chdir(base)
    assert run("_EXPORT", "--apply", "--overrides", ov) == 0
    assert tags(p) == ([], "Source: RAW")


# ------------------------------------------- A5: the owner's "Source: ..." text
@real
def test_a_comment_starting_with_source_is_still_someone_elses(tmp_path):
    """[old fails] with an override it was replaced by `Source: JPEG`."""
    base = tmp_path / HOSTILE / "x"
    touch(base / "a.JPG")
    p = export_of(base, "a")
    set_tags(p, "-IFD0:XPComment=Source: negative scanned by Tanaka-san")
    before = p.read_bytes()
    assert run(base, "--apply") == 0
    assert p.read_bytes() == before and report_rows(tmp_path)[p.name]["action"] == "refuse"
    ov = tmp_path / "ov.csv"
    ov.write_text(f"path,origin,note\n{base},jpeg,all of it\n", encoding="utf-8")
    assert run(base, "--apply", "--overrides", ov) == 0
    assert p.read_bytes() == before and report_rows(tmp_path)[p.name]["action"] == "refuse"


# ------------------------------------------- A6: report always, abort, exit codes
def three_exports(tmp_path, base_name=HOSTILE):
    base = tmp_path / base_name / "01_Zf"
    out = []
    for s in ("DSC_0001", "DSC_0002", "DSC_0003"):
        touch(base / f"{s}.JPG")
        out.append(export_of(base, s, "Green"))
    return base, out


@real
def test_ctrl_c_in_the_middle_still_writes_the_report(tmp_path, monkeypatch):
    """[old fails] no report, and the exception escaped main."""
    base, exports = three_exports(tmp_path)
    real_write, calls = pot.write_item, []

    def write_then_interrupt(it):
        calls.append(it.path)
        if len(calls) == 2:
            raise KeyboardInterrupt
        real_write(it)
    monkeypatch.setattr(pot, "write_item", write_then_interrupt)
    try:
        rc = run(base, "--apply")
    except KeyboardInterrupt:
        pytest.fail("Ctrl+C escaped main()")
    assert rc == pot.EXIT_ABORTED == 3
    rows = report_rows(tmp_path)
    assert [rows[p.name]["action"] for p in exports] == ["written", "write", "write"]
    assert all("not attempted" in rows[p.name]["reason"] for p in exports[1:])
    assert tags(exports[0])[1] == "Source: JPEG"
    assert tags(exports[1]) == ([], None) and tags(exports[2]) == ([], None)
    assert not list(base.rglob(f"*{pot.TEMP_MARK}*"))


@real
def test_an_abort_with_two_workers_still_writes_the_report(tmp_path, monkeypatch):
    base, exports = three_exports(tmp_path)
    real_write, calls = pot.write_item, []

    def write_or_interrupt(it):
        calls.append(it.path)
        if len(calls) == 2:
            raise KeyboardInterrupt
        real_write(it)
    monkeypatch.setattr(pot, "write_item", write_or_interrupt)
    assert run(base, "--apply", "--workers", 2) == pot.EXIT_ABORTED
    rows = report_rows(tmp_path)
    assert {r["action"] for r in rows.values()} <= {"written", "write"}
    assert all("not attempted" in r["reason"] for r in rows.values()
               if r["action"] == "write")
    assert not list(base.rglob(f"*{pot.TEMP_MARK}*"))
    assert not pot._ALL_PROCS


@real
def test_an_unexpected_exception_while_writing_exits_3_with_the_report(
        tmp_path, monkeypatch, capsys):
    base, exports = three_exports(tmp_path)

    def boom(it):
        raise RuntimeError("boom")
    monkeypatch.setattr(pot, "write_item", boom)
    assert run(base, "--apply") == pot.EXIT_ABORTED
    assert "RuntimeError: boom" in capsys.readouterr().err
    assert {r["action"] for r in report_rows(tmp_path).values()} == {"write"}


@real
def test_a_batch_exiftool_cannot_read_is_read_file_by_file(tmp_path, monkeypatch):
    base, exports = three_exports(tmp_path)
    poison = exports[1]
    real_run = pot.run_exiftool

    def flaky(args, **kw):
        if any(poison.name in str(a) for a in args):
            raise TimeoutError("exiftool did not answer in time")
        return real_run(args, **kw)
    monkeypatch.setattr(pot, "run_exiftool", flaky)
    assert run(base) == 1                        # the poisoned one is an error
    rows = report_rows(tmp_path)
    assert rows[exports[0].name]["action"] == "write"
    assert rows[exports[2].name]["action"] == "write"
    assert rows[poison.name]["action"] == "error"
    assert "could not read" in rows[poison.name]["reason"]


@real
def test_a_run_with_nothing_to_write_leaves_no_empty_log(tmp_path):
    base, exports = three_exports(tmp_path)
    assert run(base, "--apply") == 0
    for f in (tmp_path / "_logs").glob("*_written.log"):
        f.unlink()
    assert run(base, "--apply") == 0             # all `ok` now
    assert not list((tmp_path / "_logs").glob("*_written.log"))


@real
def test_usage_errors_exit_2(tmp_path, capsys):
    base, exports = three_exports(tmp_path)
    assert run(tmp_path / "nowhere") == pot.EXIT_USAGE == 2
    assert "not found" in capsys.readouterr().err
    bad = tmp_path / "bad.csv"
    bad.write_text("path,origin\nC:\\x,png\n", encoding="utf-8")
    assert run(base, "--apply", "--overrides", bad) == 2
    assert "ERROR:" in capsys.readouterr().err
    assert tags(exports[0]) == ([], None)        # nothing ran
    assert run(base, "--workers", 0) == 2


# ---------------------------------------------------------- A7: overrides.csv
def test_an_overrides_file_in_shift_jis_is_a_usage_error(tmp_path):
    """[old fails] UnicodeDecodeError with a traceback."""
    f = tmp_path / "ov.csv"
    f.write_bytes("path,origin,note\nC:\\写真\\x,raw,メモ\n".encode("shift_jis"))
    with pytest.raises(pot.UsageError, match="UTF-8"):
        pot.load_overrides(f)


def test_the_same_path_twice(tmp_path):
    """[old fails] the first line won without a word."""
    f = tmp_path / "ov.csv"
    f.write_text("path,origin,note\nC:\\x\\a.tif,raw,one\nc:/x/A.TIF,jpeg,two\n",
                 encoding="utf-8")
    with pytest.raises(pot.UsageError, match=r"line 2.*line 3"):
        pot.load_overrides(f)
    f.write_text("path,origin,note\nC:\\x\\a.tif,raw,one\nc:/x/A.TIF,raw,two\n",
                 encoding="utf-8")
    ovs = pot.load_overrides(f)
    assert len(ovs) == 1 and ovs[0].line == 2 and ovs[0].note == "one"


def test_a_semicolon_separated_overrides_file_says_so(tmp_path):
    f = tmp_path / "ov.csv"
    f.write_text("path;origin;note\nC:\\x;raw;n\n", encoding="utf-8")
    with pytest.raises(pot.UsageError, match="separator must be a comma"):
        pot.load_overrides(f)


# ---------------------------------------------- A9: why there is no original
def test_the_reason_names_a_sidecar_that_hid_the_original():
    d = pot.classify({".xmp": "DSC_0003_edit.xmp"}, {"XMP-xmp:Label": "Green"})
    assert d.kind is None
    assert "DSC_0003_edit.xmp" in d.review and "not a RAW/JPEG/TIFF" in d.review
    assert pot.classify({}, {}).review == "no original found beside the export"


@real
def test_a_longer_sidecar_beside_the_original_is_explained(tmp_path):
    base = tmp_path / HOSTILE / "01_Zf"
    touch(base / "DSC_0003.NEF")
    touch(base / "DSC_0003_edit.xmp")
    p = export_of(base, "DSC_0003_edit", "Green")
    assert run(base) == 0
    row = report_rows(tmp_path)[p.name]
    assert row["action"] == "review" and "DSC_0003_edit.xmp" in row["reason"]


# ------------------------------------------ A10: Green needed, TIFF and JPEG
def C(exts, label="", signal=False, jpeg_has_signal=False):
    meta = {"XMP-xmp:Label": label} if label else {}
    if signal:
        meta["ExifIFD:SubjectDistance"] = 3.2
    return pot.classify({e: "f" + e for e in exts}, meta,
                        jpeg_has_signal=jpeg_has_signal)


def test_a_nef_jpeg_pair_without_the_signal_needs_the_green_label():
    assert C([".nef", ".jpg"], "").kind is None
    assert C([".nef", ".jpg"], "Pink").kind is None
    assert "Green label" in C([".nef", ".jpg"], "").review
    assert C([".nef", ".jpg"], "Green").kind == "jpeg"
    assert C([".nef", ".jpg"], "Blue").kind is None          # Blue and no signal
    assert C([".nef", ".jpg"], "", signal=True).kind == "raw"
    assert C([".nef", ".jpg"], "Pink", signal=True).kind == "raw"
    assert C([".nef", ".jpg"], "Green", signal=True).kind is None


@real
def test_a_tiff_export_of_a_nef_jpeg_pair_without_the_signal(tmp_path, hostile):
    """[old fails] no label, and a rejected (Pink) one, were decided as JPEG."""
    base = tmp_path / hostile / "01_Zf"
    out = {}
    for stem, label in (("DSC_0001", ""), ("DSC_0002", "Green"), ("DSC_0003", "Pink")):
        touch(base / f"{stem}.NEF")
        touch(base / f"{stem}.JPG")
        out[label] = export_of(base, stem, label)
    assert run(base, "--apply") == 0
    rows = report_rows(tmp_path)
    assert rows[out[""].name]["action"] == "review"
    assert rows[out["Pink"].name]["action"] == "review"
    assert rows[out["Green"].name]["action"] == "written"
    assert tags(out["Green"]) == (["jxlphoto-origin:jpeg"], "Source: JPEG")
    assert tags(out[""]) == ([], None) and tags(out["Pink"]) == ([], None)


# ------------------------------- A12: the JPEG beside carries the signal too
def test_has_signal():
    assert pot.has_signal({"XMP-crd:CameraProfile": "x"})
    assert pot.has_signal({"ExifIFD:SubjectDistance": 3.2})
    assert not pot.has_signal({"XMP-xmp:Label": "Green", "IFD0:Model": "m"})


def test_the_signal_does_not_count_when_the_jpeg_beside_has_it():
    both = dict(signal=True, jpeg_has_signal=True)
    assert C([".nef", ".jpg"], "", **both).kind is None
    assert "needs a Blue or Green label" in C([".nef", ".jpg"], "", **both).review
    assert C([".nef", ".jpg"], "Pink", **both).kind is None
    d = C([".nef", ".jpg"], "Green", **both)
    assert d.kind == "jpeg" and "carries the signal too" in d.by
    assert C([".nef", ".jpg"], "", signal=True).kind == "raw"       # as before


@real
def test_a_signal_the_jpeg_beside_also_carries_is_no_proof(tmp_path, hostile):
    """[old fails] the export's signal said RAW whatever the JPEG had."""
    base = tmp_path / hostile / "01_Zf"
    cases = {}
    for stem, label, jpeg_signal in (
            ("A_nolabel", "", True), ("B_green", "Green", True),
            ("C_blue", "Blue", True), ("D_plain_jpeg", "", False)):
        touch(base / f"{stem}.NEF")
        make_jpeg(base / f"{stem}.JPG", icc=make_icc("Adobe RGB (1998)"),
                  exif_args=[CRD] if jpeg_signal else ())
        cases[stem] = export_of(base, stem, label)
        set_tags(cases[stem], CRD)               # every export carries the signal
    touch(base / "E_unreadable.NEF")
    touch(base / "E_unreadable.JPG")             # a JPEG exiftool cannot read
    cases["E_unreadable"] = export_of(base, "E_unreadable", "")
    set_tags(cases["E_unreadable"], CRD)
    assert run(base, "--apply") == 0
    rows = report_rows(tmp_path)
    assert rows[cases["A_nolabel"].name]["action"] == "review"
    assert tags(cases["A_nolabel"]) == ([], None)
    assert tags(cases["B_green"]) == (["jxlphoto-origin:jpeg"],
                                      "Source: JPEG, Adobe RGB (1998)")
    assert tags(cases["C_blue"]) == ([], "Source: RAW")
    assert tags(cases["D_plain_jpeg"]) == ([], "Source: RAW")        # as before
    assert tags(cases["E_unreadable"]) == ([], "Source: RAW")        # counts as no signal


# ------------------------------------------ A13: the JPG_only review line
@real
def test_the_summary_counts_the_jpg_only_jpegs_left_in_review(tmp_path, hostile, capsys):
    base = tmp_path / hostile / "01_Z7"
    jpg_only(base)                               # no original beside it: review
    assert run(base) == 0
    out = capsys.readouterr().out
    assert (f"1 JPEG(s) below {pot.JPG_ONLY_FOLDER} stay without a comment; once "
            f"archived in {pot.JPG_ARCHIVE_FOLDER} they cannot be tagged") in out
