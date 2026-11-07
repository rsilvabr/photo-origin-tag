"""photo-origin-tag tests. Every file test runs in a HOSTILE folder (Japanese,
[ ], a space, %) with the real exiftool: the tags are what exiftool writes,
so a mocked exiftool would prove nothing."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import tifffile

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import photo_origin_tag as pot  # noqa: E402

HAVE_EXIFTOOL = shutil.which("exiftool") is not None
real = pytest.mark.skipif(not HAVE_EXIFTOOL, reason="needs exiftool")
HOSTILE = "写真フォルダ_テスト [FINAL] 5% off"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOTO_ORIGIN_TAG_LOG_DIR", str(tmp_path / "_logs"))
    pot._sib_cache.clear()


def _icc():
    # a blob exiftool cannot parse: only its bytes matter (an export's ICC)
    return bytes(range(256)) * 4


def make_icc(desc: str) -> bytes:
    """A minimal ICC v4 profile whose only tag is its description (mluc,
    UTF-16BE): enough for exiftool's ProfileDescription."""
    import struct
    text = desc.encode("utf-16-be")
    mluc = b"mluc" + b"\0" * 4 + struct.pack(">II", 1, 12) + b"enUS" \
        + struct.pack(">II", len(text), 28) + text
    mluc += b"\0" * (-len(mluc) % 4)
    tag_table = struct.pack(">I", 1) + b"desc" + struct.pack(">II", 144, len(mluc))
    size = 128 + len(tag_table) + len(mluc)
    header = (struct.pack(">I", size) + b"\0" * 4 + struct.pack(">I", 0x04200000)
              + b"mntrRGB XYZ " + b"\0" * 12 + b"acsp" + b"\0" * 88)
    assert len(header) == 128
    return header + tag_table + mluc


def make_tiff(path: Path, bits=16, compression=None, icc=True):
    """icc: True = an unparseable blob, bytes = that profile, False = none."""
    path.parent.mkdir(parents=True, exist_ok=True)
    dt = np.uint16 if bits == 16 else np.uint8
    rng = np.random.default_rng(len(path.name))
    a = rng.integers(0, np.iinfo(dt).max, size=(48, 64, 3), dtype=dt)
    blob = _icc() if icc is True else (icc or None)
    extratags = [(34675, 7, len(blob), blob, False)] if blob else []
    tifffile.imwrite(str(path), a, photometric="rgb", compression=compression,
                     extratags=extratags, metadata=None)
    return path


def make_jpeg(path: Path, icc=None, exif_args=()):
    """A real JPEG original: icc = profile bytes, exif_args = exiftool
    assignments (e.g. '-ExifIFD:ColorSpace#=1')."""
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    kw = {"icc_profile": icc} if icc else {}
    Image.new("RGB", (16, 12), (40, 80, 120)).save(str(path), "JPEG", **kw)
    if exif_args:
        set_tags(path, *exif_args)
    return path


def set_tags(path: Path, *args):
    rc, so, se = pot.run_exiftool(["-overwrite_original", *args, str(path)])
    assert rc == 0, se


def tags(path: Path):
    rc, so, se = pot.run_exiftool(["-j", "-G1", "-n", "-XMP-dc:Relation",
                                   "-IFD0:XPComment", str(path)])
    e = json.loads(so)[0]
    return pot._as_list(e.get("XMP-dc:Relation")), e.get("IFD0:XPComment")


def touch(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    return path


def export_of(base: Path, stem: str, label="Green", **kw):
    p = make_tiff(base / "_EXPORT" / "TIFF16" / f"{stem}_ProPhoto-g22_v1.tif", **kw)
    args = ["-IFD0:Model=NIKON Z f"]
    if label:
        args.append(f"-XMP-xmp:Label={label}")
    set_tags(p, *args)
    return p


def run(*args):
    return pot.main([str(a) for a in args])


def report_rows(tmp_path):
    import csv
    rep = sorted((tmp_path / "_logs").glob("*_report.csv"))[-1]
    with open(rep, encoding="utf-8-sig") as f:
        return {Path(r["path"]).name: r for r in csv.DictReader(f)}


# ---------------------------------------------------------------- pure rules
def C(exts, label="", signal=False, bits=None, ov=None):
    meta = {"XMP-xmp:Label": label} if label else {}
    if signal:
        meta["ExifIFD:SubjectDistance"] = 3.2
    return pot.classify({e: "f" + e for e in exts}, meta, bits, ov)


def test_match_original_takes_the_longest_prefix():
    sibs = {"dsc_1211": {}, "dsc_1211_nxstudio": {}, "dsc_12": {}}
    assert pot.match_original("DSC_1211_NXstudio_ProPhoto-g22_v1", sibs) == "dsc_1211_nxstudio"
    assert pot.match_original("DSC_1211_ProPhoto", sibs) == "dsc_1211"
    assert pot.match_original("DSC_12110_x", sibs) is None   # not at a '_' boundary


def test_nef_jpeg_pair_is_decided_by_the_machine_signal():
    assert C([".nef", ".jpg"], "Blue", signal=True).kind == "raw"
    assert C([".nef", ".jpg"], "Green").kind == "jpeg"
    assert C([".nef", ".jpg"], "Pink", signal=True).kind == "raw"   # rejected label
    assert C([".nef", ".jpg"], "", signal=False).kind == "jpeg"
    crd = pot.classify({".nef": "a", ".jpg": "b"},
                       {"XMP-xmp:Label": "Blue", "XMP-crd:CameraProfile": "x"})
    assert crd.kind == "raw"


def test_nef_jpeg_pair_contradicting_the_label_goes_to_review():
    assert C([".nef", ".jpg"], "Green", signal=True).kind is None
    assert C([".nef", ".jpg"], "Blue", signal=False).kind is None


def test_other_raw_jpeg_pair_is_decided_by_the_label():
    assert C([".raf", ".jpg"], "Blue").kind == "raw"
    assert C([".raf", ".jpg"], "Green").kind == "jpeg"
    d = C([".raf", ".jpg"], "Pink")
    assert d.kind is None and "label" in d.review


def test_raw_tiff_pair_blue_is_raw_green_is_the_tiff_depth():
    assert C([".orf", ".tif"], "Blue", bits="16 16 16").kind == "raw"
    assert C([".orf", ".tif"], "Green", bits="16 16 16").kind == "tiff16"
    assert C([".orf", ".tif"], "Green", bits="8 8 8").kind == "tiff8"
    assert C([".orf", ".tif"], "", bits="16 16 16").kind is None


def test_single_original_rules():
    assert C([".nef"], "Blue").kind == "raw"
    assert C([".nef"], "").kind == "raw"
    assert C([".nef"], "Green").kind is None
    assert C([".jpg"], "Green").kind == "jpeg"
    assert C([".jpg"], "Blue").kind is None
    assert C([".tif"], "Green", bits="8 8 8").kind == "tiff8"
    assert C([".tif"], "Green", bits=16).kind == "tiff16"
    assert C([".tif"], "Blue", bits="16 16 16").kind == "tiff16"
    assert C([".tif"], "Blue", bits="8 8 8").kind is None
    assert C([".tif"], "Green", bits=None).kind is None


def test_a_sidecar_is_not_an_original():
    assert C([".jpg", ".xmp"], "Green").kind == "jpeg"
    d = C([".xmp"], "Green")
    assert d.kind is None and "no original" in d.review


def test_never_guesses():
    assert C([]).kind is None
    assert C([".jpg", ".tif"], "Green", bits="16").kind is None
    assert C([".nef", ".jpg", ".tif"], "Blue", signal=True).kind is None


def test_override_wins():
    ov = pot.Override("x", "jpeg", "", 2)
    assert C([".nef", ".jpg"], "Blue", signal=True, ov=ov).kind == "jpeg"
    assert C([], ov=ov).kind == "jpeg"


def test_override_most_specific_match(tmp_path):
    f = tmp_path / "ov.csv"
    a = tmp_path / "A"
    f.write_text(f"path,origin,note\n{a},raw,folder\n{a / 'x' / 'p.tif'},jpeg,file\n"
                 f"# comment,raw,\n", encoding="utf-8")
    ovs = pot.load_overrides(f)
    assert len(ovs) == 2
    assert pot.find_override(a / "x" / "p.tif", ovs).origin == "jpeg"
    assert pot.find_override(a / "x" / "q.tif", ovs).origin == "raw"
    assert pot.find_override(tmp_path / "AB" / "q.tif", ovs) is None


def test_override_rejects_an_unknown_origin(tmp_path):
    f = tmp_path / "ov.csv"
    f.write_text("path,origin\nC:\\x,png\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        pot.load_overrides(f)


# ---------------------------------------------------------------- real files
@pytest.fixture
def library(tmp_path):
    """A hostile camera folder with one export per rule."""
    base = tmp_path / HOSTILE / "01_Zf"
    for s in ("DSC_0001", "DSC_0002"):
        touch(base / f"{s}.NEF"); touch(base / f"{s}.JPG")
    touch(base / "DSC_0003.JPG")
    make_tiff(base / "DSC_0004_NXstudio.TIF", bits=16,
              icc=make_icc("LargeRGB-elle-V4-g22.icc"))
    make_tiff(base / "DSC_0005.tif", bits=8, icc=False)
    touch(base / "DSC_0006.NEF"); touch(base / "DSC_0006.JPG")
    e = {
        "raw": export_of(base, "DSC_0001", "Blue"),
        "pairjpeg": export_of(base, "DSC_0002", "Green"),
        "jpeg": export_of(base, "DSC_0003", "Green"),
        "tiff16": export_of(base, "DSC_0004_NXstudio", "Green"),
        "tiff8": export_of(base, "DSC_0005", "Green"),
        "contra": export_of(base, "DSC_0006", "Blue"),   # Blue, no RAW signal
        "orphan": export_of(base, "DSC_0099", "Green"),
    }
    set_tags(e["raw"], "-ExifIFD:SubjectDistance=3.5")
    return base, e


@real
def test_simulation_writes_nothing(library, tmp_path):
    base, e = library
    before = {k: p.read_bytes() for k, p in e.items()}
    assert run(tmp_path / HOSTILE) == 0
    assert all(e[k].read_bytes() == b for k, b in before.items())
    rows = report_rows(tmp_path)
    assert rows[e["raw"].name]["origin"] == "raw"
    assert rows[e["jpeg"].name]["action"] == "write"
    assert rows[e["orphan"].name]["action"] == "review"


@real
def test_apply_writes_the_right_tags(library, tmp_path):
    base, e = library
    assert run(tmp_path / HOSTILE, "--apply") == 0
    assert tags(e["raw"]) == ([], "Source: RAW")
    assert tags(e["pairjpeg"]) == (["jxlphoto-origin:jpeg"], "Source: JPEG")
    assert tags(e["jpeg"]) == (["jxlphoto-origin:jpeg"], "Source: JPEG")
    assert tags(e["tiff16"]) == ([], "Source: TIFF 16-bit, LargeRGB-elle-V4-g22.icc")
    assert tags(e["tiff8"]) == (["jxlphoto-origin:tiff8"],
                                "Source: TIFF 8-bit, no color profile")
    assert tags(e["contra"]) == ([], None)
    assert tags(e["orphan"]) == ([], None)
    assert not list((tmp_path / HOSTILE).rglob(f"*{pot.TEMP_MARK}*"))
    rows = report_rows(tmp_path)
    assert rows[e["contra"].name]["action"] == "review"
    assert rows[e["jpeg"].name]["action"] == "written"


@real
def test_apply_keeps_image_bytes_icc_and_other_tags(library, tmp_path):
    base, e = library
    set_tags(e["jpeg"], "-XMP-dc:Rights=Owner", "-ExifIFD:LensModel=NIKKOR Z 40mm")
    fp0 = pot.image_fingerprint(e["jpeg"])
    t0 = pot.all_tags(e["jpeg"])
    assert run(tmp_path / HOSTILE, "--apply") == 0
    assert pot.image_fingerprint(e["jpeg"]) == fp0
    t1 = pot.all_tags(e["jpeg"])
    assert t1["XMP-dc:Rights"] == "Owner"
    assert t1["ExifIFD:LensModel"] == "NIKKOR Z 40mm"
    assert pot.tag_differences(t0, t1, pot.REWRITE_VOLATILE | {
        "XMP-dc:Relation", "IFD0:XPComment"}) == []


@real
def test_second_run_is_a_no_op(library, tmp_path):
    base, e = library
    assert run(tmp_path / HOSTILE, "--apply") == 0
    m = {k: (p.stat().st_mtime_ns, p.read_bytes()) for k, p in e.items()}
    assert run(tmp_path / HOSTILE, "--apply") == 0
    assert {k: (p.stat().st_mtime_ns, p.read_bytes()) for k, p in e.items()} == m
    rows = report_rows(tmp_path)
    assert rows[e["jpeg"].name]["action"] == "ok"


@real
def test_zip_compressed_export(tmp_path):
    base = tmp_path / HOSTILE / "02_C5050"
    touch(base / "P1010001.JPG")
    p = export_of(base, "P1010001", "Green", compression="zlib")
    fp0 = pot.image_fingerprint(p)
    assert run(base, "--apply") == 0
    assert tags(p) == (["jxlphoto-origin:jpeg"], "Source: JPEG")
    assert pot.image_fingerprint(p) == fp0
    assert tifffile.TiffFile(str(p)).pages[0].compression == 8


@real
def test_refuses_a_foreign_comment(tmp_path):
    base = tmp_path / HOSTILE / "x"
    touch(base / "a.JPG")
    p = export_of(base, "a")
    set_tags(p, "-IFD0:XPComment=my note")
    b = p.read_bytes()
    assert run(base, "--apply") == 0
    assert p.read_bytes() == b
    assert report_rows(tmp_path)[p.name]["action"] == "refuse"


@real
def test_refuses_another_origin_unless_overridden(tmp_path):
    base = tmp_path / HOSTILE / "x"
    touch(base / "a.JPG")
    p = export_of(base, "a")
    set_tags(p, "-XMP-dc:Relation+=jxlphoto-origin:tiff8")
    b = p.read_bytes()
    assert run(base, "--apply") == 0
    assert p.read_bytes() == b
    assert report_rows(tmp_path)[p.name]["action"] == "refuse"
    ov = tmp_path / "ov.csv"
    ov.write_text(f"path,origin,note\n{p},jpeg,checked\n", encoding="utf-8")
    assert run(base, "--apply", "--overrides", ov) == 0
    assert tags(p) == (["jxlphoto-origin:jpeg"], "Source: JPEG")


@real
def test_override_settles_a_review_and_relabels_our_comment(tmp_path):
    base = tmp_path / HOSTILE / "S5Pro"
    touch(base / "_DSF0022.RAF"); touch(base / "_DSF0022.JPG")
    p = export_of(base, "_DSF0022", label="Pink")
    assert run(base, "--apply") == 0
    assert report_rows(tmp_path)[p.name]["action"] == "review"
    ov = tmp_path / "ov.csv"
    ov.write_text(f"path,origin,note\n{base},jpeg,owner checked\n", encoding="utf-8")
    assert run(base, "--apply", "--overrides", ov) == 0
    assert tags(p) == (["jxlphoto-origin:jpeg"], "Source: JPEG")
    ov.write_text(f"path,origin,note\n{base},raw,changed my mind\n", encoding="utf-8")
    assert run(base, "--apply", "--overrides", ov) == 0
    assert tags(p) == ([], "Source: RAW")


@real
def test_skips_a_tiff_jxl_photo_wrote(tmp_path):
    base = tmp_path / HOSTILE / "x"
    touch(base / "a.JPG")
    p = export_of(base, "a")
    set_tags(p, "-XMP-dc:Relation+=jxlphoto-src:0123456789abcdef")
    b = p.read_bytes()
    assert run(base, "--apply") == 0
    assert p.read_bytes() == b
    assert report_rows(tmp_path)[p.name]["action"] == "skip"


@real
def test_a_failed_verification_leaves_the_original(tmp_path, monkeypatch):
    base = tmp_path / HOSTILE / "x"
    touch(base / "a.JPG")
    p = export_of(base, "a")
    b = p.read_bytes()
    real_fp = pot.image_fingerprint
    monkeypatch.setattr(pot, "image_fingerprint",
                        lambda q: ("bad", "bad") if pot.TEMP_MARK in Path(q).name
                        else real_fp(q))
    assert run(base, "--apply") == 1
    assert p.read_bytes() == b
    assert not list(base.rglob(f"*{pot.TEMP_MARK}*"))
    assert "image data" in report_rows(tmp_path)[p.name]["reason"]


@real
def test_an_export_edited_meanwhile_is_not_replaced(tmp_path, monkeypatch):
    base = tmp_path / HOSTILE / "x"
    touch(base / "a.JPG")
    p = export_of(base, "a")
    real_run = pot.run_exiftool

    def run_and_edit(args, **kw):
        r = real_run(args, **kw)
        if args and args[0] == "-o":
            with open(p, "ab") as f:      # the owner saves the file meanwhile
                f.write(b"\0")
        return r
    monkeypatch.setattr(pot, "run_exiftool", run_and_edit)
    assert run(base, "--apply") == 1
    assert tags(p) == ([], None)
    assert "changed while" in report_rows(tmp_path)[p.name]["reason"]


@real
def test_a_killed_runs_temp_is_removed_and_never_taken_for_an_export(tmp_path):
    base = tmp_path / HOSTILE / "x"
    touch(base / "a.JPG")
    p = export_of(base, "a")
    stale = p.with_name(f"{p.stem}{pot.TEMP_MARK}-4242{p.suffix}")
    shutil.copy2(p, stale)
    orphan = p.with_name(f"gone{pot.TEMP_MARK}-1{p.suffix}")   # no original
    shutil.copy2(p, orphan)
    assert run(base) == 0                 # a simulation removes nothing
    assert stale.exists()
    assert pot.TEMP_MARK not in "".join(report_rows(tmp_path))
    assert run(base, "--apply") == 0
    assert not stale.exists() and orphan.exists()
    assert tags(p) == (["jxlphoto-origin:jpeg"], "Source: JPEG")
    log = list((tmp_path / "_logs").glob("*_written.log"))
    assert log and "written" in log[0].read_text(encoding="utf-8")


@real
@pytest.mark.parametrize("workers,stay_open", [(2, True), (1, False)])
def test_workers_and_spawn_mode_write_the_same(library, tmp_path, monkeypatch,
                                                workers, stay_open):
    base, e = library
    monkeypatch.setattr(pot, "EXIFTOOL_STAY_OPEN", stay_open)
    assert run(tmp_path / HOSTILE, "--apply", "--workers", workers) == 0
    assert not pot._ALL_PROCS                  # every exiftool closed at the end
    assert tags(e["raw"]) == ([], "Source: RAW")
    assert tags(e["jpeg"]) == (["jxlphoto-origin:jpeg"], "Source: JPEG")
    assert tags(e["tiff8"]) == (["jxlphoto-origin:tiff8"],
                                "Source: TIFF 8-bit, no color profile")
    assert not list((tmp_path / HOSTILE).rglob(f"*{pot.TEMP_MARK}*"))
    pot.close_exiftools()


@real
def test_a_dead_exiftool_is_replaced(tmp_path):
    base = tmp_path / HOSTILE / "x"
    touch(base / "a.JPG")
    p = export_of(base, "a")
    rc, so, se = pot.run_exiftool(["-j", "-IFD0:Model", str(p)])
    assert rc == 0 and "NIKON Z f" in so
    pot._thread_exiftool().p.kill()            # dies between two commands
    pot._thread_exiftool().p.wait()
    rc, so, se = pot.run_exiftool(["-j", "-IFD0:Model", str(p)])
    assert rc == 0 and "NIKON Z f" in so       # a fresh process answered
    rc, so, se = pot.run_exiftool(["-j", str(tmp_path / "missing.tif")])
    assert rc == 1 and "Error" in se           # an error is still an error
    pot.close_exiftools()


@real
def test_long_path(tmp_path):
    deep = tmp_path / HOSTILE
    while len(str(deep)) < 250:
        deep = deep / "長いフォルダ名 [x]"
    touch(deep / "a.JPG")
    p = export_of(deep, "a")
    assert len(str(p)) > 260
    assert run(deep, "--apply") == 0
    assert tags(p) == (["jxlphoto-origin:jpeg"], "Source: JPEG")


@real
def test_unused_override_is_reported(tmp_path, capsys):
    base = tmp_path / HOSTILE / "x"
    touch(base / "a.JPG")
    export_of(base, "a")
    ov = tmp_path / "ov.csv"
    ov.write_text(f"path,origin\n{tmp_path / 'typo'},raw\n", encoding="utf-8")
    run(base, "--overrides", ov)
    assert "matched nothing" in capsys.readouterr().out


# ------------------------------------------------- the original's profile
def test_profile_label_rules():
    L = pot.profile_label
    icc = "(Binary data 544 bytes, use -b option to extract)"
    assert L(None) is None
    assert L({"ICC_Profile:ICC_Profile": icc,
              "ICC_Profile:ProfileDescription": "Adobe RGB (1998)",
              "ExifIFD:ColorSpace": 1}) == "Adobe RGB (1998)"        # ICC wins
    assert L({"ICC_Profile:ICC_Profile": icc}) == pot.PROFILE_UNNAMED
    assert L({"File:FileType": "JPEG", "ExifIFD:ColorSpace": 1}) == pot.PROFILE_EXIF_SRGB
    assert L({"File:FileType": "JPEG", "ExifIFD:ColorSpace": 65535,
              "InteropIFD:InteropIndex": "R03"}) == pot.PROFILE_EXIF_ADOBE
    assert L({"File:FileType": "JPEG", "ExifIFD:ColorSpace": 2}) == pot.PROFILE_EXIF_ADOBE
    assert L({"File:FileType": "JPEG", "ExifIFD:ColorSpace": 65535}) == pot.PROFILE_NONE
    assert L({"File:FileType": "TIFF"}) == pot.PROFILE_NONE
    # nothing found in a file exiftool could not parse is not "no profile"
    assert L({"File:FileType": "TXT"}) is None
    assert L({"File:FileType": "JPEG", "ExifTool:Warning": "JPEG format error"}) is None
    assert L({}) is None
    odd = L({"ICC_Profile:ICC_Profile": icc,
             "ICC_Profile:ProfileDescription": "a\nb\tc  " + "x" * 300})
    assert odd.startswith("a b c x") and len(odd) == pot.PROFILE_MAX_LEN
    assert "\n" not in odd


def test_comment_text():
    assert pot.comment_for("jpeg", "sRGB IEC61966-2.1") == "Source: JPEG, sRGB IEC61966-2.1"
    assert pot.comment_for("raw", "Adobe RGB (1998)") == "Source: RAW"
    assert pot.comment_for("tiff8", None) == "Source: TIFF 8-bit"
    assert pot.comment_kind("Source: TIFF 16-bit, ProPhoto RGB") == "tiff16"
    assert pot.comment_kind("Source: TIFF 8-bit") == "tiff8"
    assert pot.comment_kind("Source: JPEGX") is None
    assert pot.comment_kind("my note") is None


def test_source_file_follows_the_decided_kind():
    exts = {".nef": "a.NEF", ".jpg": "a.JPG", ".tif": "a.TIF"}
    assert pot.source_file("jpeg", exts) == "a.JPG"
    assert pot.source_file("tiff16", exts) == "a.TIF"
    assert pot.source_file("raw", exts) is None
    assert pot.source_file("jpeg", {".nef": "a.NEF"}) is None   # override, no JPEG


ADOBE = "Adobe RGB (1998)"


@real
def test_the_comment_names_the_originals_profile(tmp_path):
    base = tmp_path / HOSTILE / "01_mixed"
    make_jpeg(base / "A_icc.JPG", icc=make_icc(ADOBE))
    make_jpeg(base / "B_exif_srgb.JPG", exif_args=["-ExifIFD:ColorSpace#=1"])
    make_jpeg(base / "C_dcf_adobe.JPG", exif_args=["-ExifIFD:ColorSpace#=65535",
                                                   "-InteropIFD:InteropIndex=R03"])
    make_jpeg(base / "D_nothing.JPG")
    make_jpeg(base / "E_jp.JPG", icc=make_icc("カスタム プロファイル [v2]"))
    make_tiff(base / "F_tif.tif", bits=16, icc=make_icc("ProPhoto RGB"))
    touch(base / "G_raw.NEF")                       # NEF+JPEG exported from the RAW
    make_jpeg(base / "G_raw.JPG", icc=make_icc(ADOBE))
    e = {s: export_of(base, s, "Blue" if s == "G_raw" else "Green")
         for s in ("A_icc", "B_exif_srgb", "C_dcf_adobe", "D_nothing", "E_jp",
                   "F_tif", "G_raw")}
    set_tags(e["G_raw"], "-ExifIFD:SubjectDistance=3.5")
    assert run(base, "--apply") == 0
    assert tags(e["A_icc"])[1] == "Source: JPEG, Adobe RGB (1998)"
    assert tags(e["B_exif_srgb"])[1] == "Source: JPEG, sRGB (EXIF, no ICC)"
    assert tags(e["C_dcf_adobe"])[1] == "Source: JPEG, Adobe RGB (EXIF, no ICC)"
    assert tags(e["D_nothing"])[1] == "Source: JPEG, no color profile"
    assert tags(e["E_jp"])[1] == "Source: JPEG, カスタム プロファイル [v2]"
    assert tags(e["F_tif"]) == ([], "Source: TIFF 16-bit, ProPhoto RGB")
    assert tags(e["G_raw"]) == ([], "Source: RAW")   # C1's profile, not the JPEG's
    rows = report_rows(tmp_path)
    assert rows[e["A_icc"].name]["profile"] == ADOBE
    assert rows[e["G_raw"].name]["profile"] == ""


@real
def test_an_unreadable_original_gets_no_profile_and_keeps_an_earlier_one(tmp_path):
    base = tmp_path / HOSTILE / "x"
    touch(base / "a.JPG")                          # not a JPEG exiftool can read
    p = export_of(base, "a")
    assert run(base, "--apply") == 0
    assert tags(p) == (["jxlphoto-origin:jpeg"], "Source: JPEG")
    assert "unreadable" in report_rows(tmp_path)[p.name]["reason"]
    set_tags(p, "-IFD0:XPComment=Source: JPEG, Adobe RGB (1998)")
    b = p.read_bytes()
    assert run(base, "--apply") == 0               # still unreadable: keep it
    assert p.read_bytes() == b
    assert report_rows(tmp_path)[p.name]["action"] == "ok"


@real
def test_a_v02_comment_gains_the_profile_without_an_override(tmp_path):
    base = tmp_path / HOSTILE / "x"
    make_jpeg(base / "a.JPG", icc=make_icc(ADOBE))
    p = export_of(base, "a")
    set_tags(p, "-IFD0:XPComment=Source: JPEG", "-XMP-dc:Relation+=jxlphoto-origin:jpeg")
    assert run(base, "--apply") == 0
    assert tags(p) == (["jxlphoto-origin:jpeg"], "Source: JPEG, Adobe RGB (1998)")
    # the original re-saved in another profile: the comment follows it
    make_jpeg(base / "a.JPG", icc=make_icc("sRGB IEC61966-2.1"))
    assert run(base, "--apply") == 0
    assert tags(p)[1] == "Source: JPEG, sRGB IEC61966-2.1"
    assert run(base, "--apply") == 0
    assert report_rows(tmp_path)[p.name]["action"] == "ok"


@real
def test_another_source_type_with_a_profile_still_needs_an_override(tmp_path):
    base = tmp_path / HOSTILE / "x"
    make_jpeg(base / "a.JPG", icc=make_icc(ADOBE))
    p = export_of(base, "a")
    set_tags(p, "-IFD0:XPComment=Source: TIFF 8-bit, Adobe RGB (1998)")
    b = p.read_bytes()
    assert run(base, "--apply") == 0
    assert p.read_bytes() == b
    assert report_rows(tmp_path)[p.name]["action"] == "refuse"


# ---------------------------------------------------------- end to end
JXL_PHOTO = REPO.parent / "jxl-photo"
HAVE_CODECS = all(shutil.which(t) for t in ("cjxl", "djxl", "exiftool", "magick"))


@pytest.mark.skipif(not (HAVE_CODECS and (JXL_PHOTO / "jxl_tiff_encoder.py").exists()),
                    reason="needs jxl-photo beside this repo and cjxl/djxl/magick")
def test_jxl_photo_master_inherits_origin_and_comment(tmp_path):
    base = tmp_path / HOSTILE / "01_C5050"
    make_jpeg(base / "P1010001.JPG", exif_args=["-ExifIFD:ColorSpace#=1"])
    p = export_of(base, "P1010001", "Green")
    assert run(base, "--apply") == 0
    env = dict(os.environ, JXLPHOTO_LOG_DIR=str(tmp_path / "_jxlogs"),
               JXLPHOTO_JXL_THUMB="0")
    r = subprocess.run([sys.executable, str(JXL_PHOTO / "jxl_tiff_encoder.py"),
                        str(p.parent), "--mode", "0", "--delete-confirm-off"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    jxl = p.with_suffix(".jxl")
    assert jxl.exists(), r.stdout[-2000:]
    rc, so, se = pot.run_exiftool(["-j", "-G1", "-n", "-XMP-dc:Relation",
                                   "-XPComment", str(jxl)])
    e = json.loads(so)[0]
    assert "jxlphoto-origin:jpeg" in pot._as_list(e.get("XMP-dc:Relation"))
    comments = [v for k, v in e.items() if k.endswith(":XPComment")]
    assert comments == ["Source: JPEG, sRGB (EXIF, no ICC)"], e
