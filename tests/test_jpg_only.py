"""JPEG exports in _EXPORT\\JPG_only: the comment and nothing else. Hostile
folder and the real exiftool / cjxl / djxl, as in test_photo_origin_tag.py."""

import shutil
import subprocess

import pytest

import photo_origin_tag as pot
from test_photo_origin_tag import (  # noqa: F401 - _isolated is an autouse fixture
    HOSTILE, _isolated, hostile, make_icc, make_jpeg, make_tiff, real,
    report_rows, run, set_tags, tags, touch)

HAVE_CODECS = shutil.which("cjxl") is not None and shutil.which("djxl") is not None
codecs = pytest.mark.skipif(not HAVE_CODECS, reason="needs cjxl and djxl")


def real_icc():
    """A profile a codec accepts (make_icc's only satisfies exiftool)."""
    from PIL import ImageCms
    return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


def jpg_only(base, stem="DSC_0001", sub="", name=None, label="", extra=(), icc=None):
    """A JPEG export with Exif and XMP, as Capture One writes one."""
    folder = base / "_EXPORT" / "JPG_only"
    if sub:
        folder = folder / sub
    p = make_jpeg(folder / (name or f"{stem}_AdobeRGB_v1.jpg"),
                  icc=icc or make_icc("Adobe RGB (1998)"))
    args = ["-IFD0:Model=NIKON Z f", "-XMP-xmp:Rating=3", *extra]
    if label:
        args.append(f"-XMP-xmp:Label={label}")
    set_tags(p, *args)
    return p


def segments(path):
    """Every JPEG segment but the Exif one, then the scan to the end of file."""
    d = path.read_bytes()
    i, out = 2, [d[:2]]
    while True:
        marker = d[i + 1]
        if marker == 0xDA:
            out.append(d[i:])
            return out
        n = int.from_bytes(d[i + 2:i + 4], "big")
        seg = d[i:i + 2 + n]
        if not (marker == 0xE1 and seg[4:10] == b"Exif\0\0"):
            out.append(seg)
        i += 2 + n


@pytest.fixture
def base(tmp_path, hostile):
    return tmp_path / hostile / "01_Z7"


# ------------------------------------------------------------------ discovery
@real
def test_only_jpegs_below_jpg_only_are_found(base):
    top = jpg_only(base, "DSC_0001")
    deep = jpg_only(base, "DSC_0002", sub="_EXPORT_again/sRGB")
    other = make_jpeg(base / "_EXPORT" / "sRGB" / "DSC_0003_sRGB.jpg")
    similar = make_jpeg(base / "_EXPORT" / "JPG_only_old" / "DSC_0004.jpg")
    outside = make_jpeg(base / "JPG_only" / "DSC_0005.jpg")
    tif = make_tiff(base / "_EXPORT" / "TIFF16" / "DSC_0006.tif")
    got = {p.name for p in pot.find_exports([base])}
    assert got == {top.name, deep.name, tif.name}
    assert not got & {other.name, similar.name, outside.name}


# ------------------------------------------------------------------- applying
@real
def test_the_comment_is_all_that_is_written(base, tmp_path):
    touch(base / "DSC_0001.NEF")
    p = jpg_only(base)
    before = segments(p)
    assert run(base, "--apply") == 0
    rel, comment = tags(p)
    assert comment == "Source: RAW"
    assert rel == []                               # no XMP token, ever
    assert segments(p) == before                   # XMP, ICC, tables, scan: same bytes
    assert not [x for x in p.parent.iterdir() if pot.TEMP_MARK in x.name]
    assert report_rows(tmp_path)[p.name]["action"] == "written"


@real
def test_a_second_run_changes_nothing(base, tmp_path):
    touch(base / "DSC_0001.NEF")
    p = jpg_only(base)
    run(base, "--apply")
    bytes_after_first = p.read_bytes()
    assert run(base, "--apply") == 0
    assert p.read_bytes() == bytes_after_first
    assert report_rows(tmp_path)[p.name]["action"] == "ok"


@real
def test_a_jpeg_original_is_named_with_its_profile(base):
    make_jpeg(base / "DSC_0001.JPG", exif_args=["-ExifIFD:ColorSpace#=1"])
    p = jpg_only(base)
    assert run(base, "--apply") == 0
    rel, comment = tags(p)
    assert comment == "Source: JPEG, sRGB (EXIF, no ICC)"
    assert rel == []                               # not even for a JPEG original


@real
def test_a_file_in_a_subfolder_finds_its_original_at_the_base(base):
    touch(base / "DSC_0001.NEF")
    p = jpg_only(base, sub="C1_Export_WEB")
    assert run(base, "--apply") == 0
    assert tags(p)[1] == "Source: RAW"


@real
def test_a_comment_can_be_added_to_a_jpeg_with_a_thumbnail(base, tmp_path):
    """The thumbnail moves when the Exif grows: its offset is not a change."""
    touch(base / "DSC_0001.NEF")
    thumb = make_jpeg(tmp_path / "thumb.jpg")
    p = jpg_only(base, extra=[f"-ThumbnailImage<={thumb}"])
    assert run(base, "--apply") == 0
    assert tags(p)[1] == "Source: RAW"


# ----------------------------------------- a NEF+JPEG pair: the signal can be gone
def pair(base):
    touch(base / "DSC_0001.NEF")
    make_jpeg(base / "DSC_0001.JPG")


@real
def test_a_web_export_without_the_signal_is_not_called_a_jpeg(base, tmp_path):
    """Capture One's WEB recipe drops XMP-crd, so no signal proves nothing."""
    pair(base)
    p = jpg_only(base)
    before = p.read_bytes()
    assert run(base, "--apply") == 0
    assert p.read_bytes() == before
    assert report_rows(tmp_path)[p.name]["action"] == "review"


@real
def test_a_green_label_settles_a_pair_without_the_signal(base):
    pair(base)
    p = jpg_only(base, label="Green")
    assert run(base, "--apply") == 0
    assert tags(p)[1].startswith("Source: JPEG")


@real
def test_the_signal_still_says_raw(base):
    pair(base)
    p = jpg_only(base, extra=["-ExifIFD:SubjectDistance#=3.2"])
    assert run(base, "--apply") == 0
    assert tags(p)[1] == "Source: RAW"


@real
def test_a_tiff_export_without_the_signal_needs_a_green_label_too(base, tmp_path):
    """Since 0.5.0 a missing signal proves nothing in a TIFF export either."""
    pair(base)
    p = make_tiff(base / "_EXPORT" / "TIFF16" / "DSC_0001_ProPhoto.tif")
    before = p.read_bytes()
    assert run(base, "--apply") == 0
    assert p.read_bytes() == before
    assert report_rows(tmp_path)[p.name]["action"] == "review"


# -------------------------------------------------------------------- refusals
@real
def test_without_an_original_it_is_left_alone(base, tmp_path):
    p = jpg_only(base)
    before = p.read_bytes()
    assert run(base, "--apply") == 0
    assert p.read_bytes() == before
    assert report_rows(tmp_path)[p.name]["action"] == "review"


@real
def test_someone_elses_comment_is_refused(base, tmp_path):
    touch(base / "DSC_0001.NEF")
    p = jpg_only(base, extra=["-IFD0:XPComment=minha nota"])
    before = p.read_bytes()
    run(base, "--apply")
    assert p.read_bytes() == before
    assert report_rows(tmp_path)[p.name]["action"] == "refuse"


@real
@pytest.mark.parametrize("token", ["jxlphoto-origin:jpeg", "jxlphoto-derived:sRGB"])
def test_a_jpeg_jxl_photo_wrote_is_skipped(base, tmp_path, token):
    touch(base / "DSC_0001.NEF")
    p = jpg_only(base, extra=[f"-XMP-dc:Relation+={token}"])
    before = p.read_bytes()
    run(base, "--apply")
    assert p.read_bytes() == before
    assert report_rows(tmp_path)[p.name]["action"] == "skip"


@real
@pytest.mark.parametrize("sub", ["", "C1_Export_WEB"])
def test_a_jpeg_already_archived_is_refused(base, tmp_path, sub):
    """Tagging it would make the JPEG differ from what its JXL gives back."""
    touch(base / "DSC_0001.NEF")
    p = jpg_only(base, sub=sub)
    archive = base / "_EXPORT" / pot.JPG_ARCHIVE_FOLDER / sub / f"{p.stem}.jxl"
    touch(archive)
    before = p.read_bytes()
    run(base, "--apply")
    assert p.read_bytes() == before
    row = report_rows(tmp_path)[p.name]
    assert row["action"] == "refuse"
    assert pot.JPG_ARCHIVE_FOLDER in row["reason"]


@real
def test_an_archive_of_another_subfolder_does_not_refuse(base):
    touch(base / "DSC_0001.NEF")
    p = jpg_only(base, sub="C1_Export_WEB")
    touch(base / "_EXPORT" / pot.JPG_ARCHIVE_FOLDER / "AdobeRGB" / f"{p.stem}.jxl")
    assert run(base, "--apply") == 0
    assert tags(p)[1] == "Source: RAW"


# --------------------------------------------------------------- the write gate
@real
def test_a_rewrite_that_touches_anything_else_is_discarded(base, tmp_path, monkeypatch):
    touch(base / "DSC_0001.NEF")
    p = jpg_only(base)
    before = p.read_bytes()
    real_fp = pot.jpeg_fingerprint
    calls = []

    def lying(path):
        calls.append(path)
        img, icc = real_fp(path)
        return (img + "x" if len(calls) > 1 else img), icc

    monkeypatch.setattr(pot, "jpeg_fingerprint", lying)
    assert run(base, "--apply") == 1
    assert p.read_bytes() == before
    assert not [x for x in p.parent.iterdir() if pot.TEMP_MARK in x.name]
    assert report_rows(tmp_path)[p.name]["action"] == "error"


@real
def test_the_fingerprint_ignores_only_the_exif_segment(base):
    p = jpg_only(base)
    fp0 = pot.jpeg_fingerprint(p)
    set_tags(p, "-IFD0:XPComment=x")
    assert pot.jpeg_fingerprint(p) == fp0
    set_tags(p, "-XMP-xmp:Label=Red")               # another segment
    assert pot.jpeg_fingerprint(p)[0] != fp0[0]
    fp1 = pot.jpeg_fingerprint(p)
    d = bytearray(p.read_bytes())
    d[-10] ^= 0xFF                                  # the scan
    p.write_bytes(bytes(d))
    assert pot.jpeg_fingerprint(p)[0] != fp1[0]


def test_the_fingerprint_rejects_a_file_that_is_not_a_jpeg(tmp_path):
    p = tmp_path / HOSTILE / "x.jpg"
    touch(p)
    with pytest.raises(OSError):
        pot.jpeg_fingerprint(p)


# ------------------------------------------------------------- the whole point
@real
@codecs
def test_jpeg_to_jxl_and_back_is_bit_exact_and_the_comment_reaches_the_jxl(base):
    touch(base / "DSC_0001.NEF")
    p = jpg_only(base, icc=real_icc())
    assert run(base, "--apply") == 0
    tagged = p.read_bytes()
    jxl = p.with_suffix(".jxl")
    back = p.with_name(p.stem + "_back.jpg")
    subprocess.run(["cjxl", str(p), str(jxl), "--lossless_jpeg=1",
                    "--compress_boxes=0"], check=True, capture_output=True)
    subprocess.run(["djxl", str(jxl), str(back), "--reconstruct_jpeg"],
                   check=True, capture_output=True)
    assert back.read_bytes() == tagged
    assert tags(jxl)[1] == "Source: RAW"
