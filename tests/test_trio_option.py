"""--trio-blue-raw (v0.6.0, plan item A11): a RAW+JPEG+TIFF trio beside the
export with a Blue label counts as RAW, only when the option is on. Real
exiftool, hostile folders, as in test_photo_origin_tag.py."""

import pytest

import photo_origin_tag as pot
from test_audit_fixes import CRD
from test_photo_origin_tag import (  # noqa: F401 - _isolated is an autouse fixture
    _isolated, export_of, hostile, real, report_rows, run, set_tags, tags, touch)


def T(exts, label="", signal=False, on=True):
    meta = {"XMP-xmp:Label": label} if label else {}
    if signal:
        meta["ExifIFD:SubjectDistance"] = 3.2
    return pot.classify({e: "f" + e for e in exts}, meta, "16", None,
                        trio_blue_raw=on)


TRIO_RAF = [".raf", ".jpg", ".tif"]
TRIO_NEF = [".nef", ".jpg", ".tif"]


def test_the_option_is_off_by_default():
    assert pot.TRIO_BLUE_IS_RAW is False
    d = pot.classify({e: "f" + e for e in TRIO_RAF}, {"XMP-xmp:Label": "Blue"}, "16")
    assert d.kind is None and "unexpected originals" in d.review


def test_a_blue_trio_is_raw_with_the_option():
    d = T(TRIO_RAF, "Blue")
    assert d.kind == "raw" and d.by == "RAW+JPEG+TIFF trio, label Blue"
    assert T(TRIO_NEF, "Blue", signal=True).kind == "raw"


def test_a_blue_nef_trio_without_the_signal_goes_to_review():
    d = T(TRIO_NEF, "Blue", signal=False)
    assert d.kind is None and "no RAW signal" in d.review


@pytest.mark.parametrize("label", ["Green", "Pink", ""])
def test_only_blue_decides_a_trio(label):
    d = T(TRIO_RAF, label, signal=True)
    assert d.kind is None and "only a Blue label decides" in d.review


def test_an_override_still_wins_over_the_trio():
    ov = pot.Override("x", "jpeg", "", 2)
    d = pot.classify({e: "f" + e for e in TRIO_RAF}, {"XMP-xmp:Label": "Blue"},
                     "16", ov, trio_blue_raw=True)
    assert d.kind == "jpeg"


@real
def test_the_option_on_the_command_line(tmp_path, hostile):
    base = tmp_path / hostile / "S5Pro"
    for ext in (".RAF", ".JPG", ".TIF"):
        touch(base / f"DSCF0042{ext}")
    p = export_of(base, "DSCF0042", label="Blue")
    green = export_of(base / "g", "DSCF0043", label="Green")
    for ext in (".RAF", ".JPG", ".TIF"):
        touch(base / "g" / f"DSCF0043{ext}")
    b, bg = p.read_bytes(), green.read_bytes()
    assert run(base, "--apply") == 0                     # off: both in review
    rows = report_rows(tmp_path)
    assert rows[p.name]["action"] == "review" and p.read_bytes() == b
    assert run(base, "--apply", "--trio-blue-raw") == 0
    rows = report_rows(tmp_path)
    assert rows[p.name]["action"] == "written"
    assert rows[p.name]["decided_by"] == "RAW+JPEG+TIFF trio, label Blue"
    assert tags(p) == ([], "Source: RAW")
    assert rows[green.name]["action"] == "review" and green.read_bytes() == bg


@real
def test_a_blue_nef_trio_with_the_signal_on_the_command_line(tmp_path):
    base = tmp_path / "写真 [x] 5% off" / "Zf"
    for ext in (".NEF", ".JPG", ".TIF"):
        touch(base / f"DSC_0007{ext}")
    p = export_of(base, "DSC_0007", label="Blue")
    q = export_of(base / "n", "DSC_0008", label="Blue")
    for ext in (".NEF", ".JPG", ".TIF"):
        touch(base / "n" / f"DSC_0008{ext}")
    set_tags(p, CRD)                                     # p has the signal, q not
    assert run(base, "--apply", "--trio-blue-raw") == 0
    rows = report_rows(tmp_path)
    assert tags(p) == ([], "Source: RAW")
    assert rows[q.name]["action"] == "review"
    assert "no RAW signal" in rows[q.name]["reason"]
