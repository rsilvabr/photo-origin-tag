"""pastas_exportar_tiff tests. The scan only reads file NAMES, so the photos
are empty files -- in a HOSTILE folder (Japanese, [ ], a space, %)."""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import pastas_exportar_tiff as pet  # noqa: E402

HOSTILE = "250101_写真フォルダ_テスト [FINAL] 5% off"


@pytest.fixture
def lib(tmp_path, monkeypatch):
    """A fake G:\\ with one year; returns a helper that creates files in
    the hostile event folder."""
    root = tmp_path / "G"
    monkeypatch.setattr(pet, "ROOT", str(root) + "\\")
    monkeypatch.setattr(pet, "YEARS", ("2025",))
    monkeypatch.setenv("PHOTO_ORIGIN_TAG_LOG_DIR", str(tmp_path / "_logs"))
    event = root / "2025" / HOSTILE

    def mk(*parts):
        f = event.joinpath(*parts)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"")
        return f
    return mk


def _cats(rows):
    return {Path(r["key"]).name: r["cat"] for r in rows}


def test_photo_key_drops_profile_version_and_camera():
    assert pet.photo_key("DSC_3000_v1_CHECK_1.jpg") == "dsc_3000"
    assert pet.photo_key("DSC_0566_NXstudio_AdobeRGB_v1.tif") == "dsc_0566_nxstudio"
    assert pet.photo_key("P1010076 (2).jpg") == "p1010076 (2)"
    assert pet.photo_key("DSC_1234_Variant 2.jpg") == "dsc_1234"


def test_photo_key_keeps_the_duplicate_counter():
    # was "dsc" for all of them: one DSC_xxxx (n) TIFF covered every JPG
    assert pet.photo_key("DSC_0002 (2)_ProPhoto-g22.tif") == "dsc_0002 (2)"
    assert pet.photo_key("DSC_0002 (2).jpg") == "dsc_0002 (2)"
    assert pet.photo_key("DSC_0003 (2)_v1_CHECK.jpg") == "dsc_0003 (2)"


def test_one_duplicate_tiff_does_not_cover_another_photo(lib):
    lib("01_Z7", "_EXPORT", "TIFF16", "DSC_0001 (2)_ProPhoto-g22.tif")
    lib("01_Z7", "_EXPORT", "JPG", "DSC_0001 (2).jpg")
    lib("01_Z7", "_EXPORT", "JPG", "DSC_0009 (2).jpg")
    [row] = pet.scan()
    assert row["cat"] == 2
    assert "dsc_0009 (2)" in row["exports"][0]["lines"][0]


def test_categories_with_jpg_only(lib):
    # 01: only JPG_only (loose + a subfolder) -> 7
    lib("01_Z7", "_EXPORT", "JPG_only", "DSC_0001.jpg")
    lib("01_Z7", "_EXPORT", "JPG_only", "sub [x] dir", "DSC_0002 (2).jpg")
    # 02: _EXPORT JPGs also copied into JPG_only -> 7
    lib("02_Z7", "_EXPORT", "JPG", "DSC_0100_AdobeRGB.jpg")
    lib("02_Z7", "_EXPORT", "JPG_only", "DSC_0100.jpg")
    # 03: TIFF ok + JPG_only with another photo -> 7
    lib("03_Z7", "_EXPORT", "TIFF16", "DSC_0200.tif")
    lib("03_Z7", "_EXPORT", "JPG", "DSC_0200.jpg")
    lib("03_Z7", "_EXPORT", "JPG_only", "DSC_0201.jpg")
    # 04: a JPG still missing its TIFF outside JPG_only -> 2
    lib("04_Z7", "_EXPORT", "TIFF16", "DSC_0300.tif")
    lib("04_Z7", "_EXPORT", "JPG", "DSC_0301.jpg")
    lib("04_Z7", "_EXPORT", "JPG_only", "DSC_0302.jpg")
    # 05: no TIFF at all -> 1
    lib("05_Z7", "_EXPORT", "JPG", "DSC_0500.jpg")
    # 06: no TIFF here, but every photo has one in 07 -> 6
    lib("06_Z7", "_EXPORT", "JPG", "DSC_0700_sRGB.jpg")
    lib("07_Z7", "_EXPORT", "TIFF16", "DSC_0700.tif")
    lib("07_Z7", "_EXPORT", "JPG", "DSC_0700.jpg")
    # 08: a leftover sibling _EXPORT_JPG is just another *EXPORT* -> 5
    lib("08_Z7", "_EXPORT", "TIFF16", "DSC_0800.tif")
    lib("08_Z7", "_EXPORT", "JPG", "DSC_0800.jpg")
    lib("08_Z7", "_EXPORT_JPG", "DSC_0801.jpg")

    cats = _cats(pet.scan())
    assert cats == {"01_Z7": 7, "02_Z7": 7, "03_Z7": 7, "04_Z7": 2,
                    "05_Z7": 1, "06_Z7": 6, "08_Z7": 5}


def test_jpg_only_status_counts_photos(lib):
    lib("01_Z7", "_EXPORT", "JPG_only", "AdobeRGB", "DSC_0001_AdobeRGB.jpg")
    lib("01_Z7", "_EXPORT", "JPG_only", "WEB", "DSC_0001_WEB.jpg")
    lib("01_Z7", "_EXPORT", "JPG_only", "WEB", "DSC_0002_WEB.jpg")
    [row] = pet.scan()
    [d] = row["exports"]
    assert row["cat"] == pet.ACCEPTED_CAT
    assert pet.export_status(d) == (
        f"sem TIFF — 2 fotos só em JPG ({pet.JPG_ONLY_FOLDER}, aceito)")


def test_accepted_section_has_no_checkbox_and_files_go_to_logs(lib, tmp_path):
    lib("01_Z7", "_EXPORT", "JPG_only", "DSC_0001.jpg")
    lib("05_Z7", "_EXPORT", "JPG", "DSC_0500.jpg")
    rows = pet.scan()
    page = pet.render(rows, {})
    # one checkbox for "esconder feitos" + one for the category-1 row
    assert page.count('type="checkbox"') == 2
    assert 'class="accepted"' in page and HOSTILE in page

    md = pet.write_md(rows)
    assert md.parent == tmp_path / "_logs"
    text = md.read_text(encoding="utf-8")
    assert f"## {pet.ACCEPTED_CAT}." in text and "01_Z7" in text
    assert pet.state_path().parent == tmp_path / "_logs"
