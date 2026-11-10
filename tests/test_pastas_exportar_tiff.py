"""pastas_exportar_tiff tests. The scan only reads file NAMES, so the photos
are empty files -- in a HOSTILE folder (Japanese, [ ], a space, %)."""

import http.client
import json
import sys
import threading
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


# ------------------------------------------------- audit fixes (v0.5.0, part B)
def test_photo_key_keeps_the_photo_number_when_a_generic_token_follows_it():
    # [fails on v0.4.0] "has a space" / "looks like a camera" used to cut the
    # frame number itself: all of these collapsed to "dsc" / "img" / "foto"
    assert pet.photo_key("DSC_1234 1.tif") == "dsc_1234 1"
    assert pet.photo_key("DSC_1234 1_ProPhoto.tif") == "dsc_1234 1"
    assert pet.photo_key("IMG_E1234.jpg") == "img_e1234"
    assert pet.photo_key("foto_x100_sRGB.jpg") == "foto_x100"
    # the generic rules still cut once the name has its digit
    assert pet.photo_key("foto_sRGB.jpg") == "foto"
    assert pet.photo_key("DSC_0001_D810.jpg") == "dsc_0001"
    assert pet.photo_key("DSC_0001_NIKON Z 7.jpg") == "dsc_0001"


def test_one_tiff_does_not_cover_a_jpg_of_another_numbered_copy(lib):
    # [fails on v0.4.0] both keys were "dsc": the folder looked complete
    lib("01_Z7", "_EXPORT", "TIFF16", "DSC_0001 1.tif")
    lib("01_Z7", "_EXPORT", "JPG", "DSC_0001 1.jpg")
    lib("01_Z7", "_EXPORT", "JPG", "DSC_0009 1.jpg")
    [row] = pet.scan()
    assert row["cat"] == 2
    assert "dsc_0009 1" in row["exports"][0]["lines"][0]


def test_a_missing_disk_is_an_error_and_writes_no_list(lib, tmp_path, monkeypatch, capsys):
    # [fails on v0.4.0] scan() said "0 folders" and main() wrote an empty .md
    monkeypatch.setattr(pet, "ROOT", str(tmp_path / "not there") + "\\")
    monkeypatch.setattr(sys, "argv", ["pastas_exportar_tiff.py", "--no-web"])
    with pytest.raises(pet.RootNotFound):
        pet.scan()
    assert pet.main() == 2
    assert "ERROR" in capsys.readouterr().err
    assert list((tmp_path / "_logs").glob("*.md")) == []


def test_a_missing_disk_in_a_rescan_keeps_the_previous_rows(lib, monkeypatch):
    lib("05_Z7", "_EXPORT", "JPG", "DSC_0500.jpg")
    pet.rescan()
    before = list(pet._rows)
    assert before
    monkeypatch.setattr(pet, "ROOT", str(Path(pet.ROOT).parent / "gone") + "\\")
    with pytest.raises(pet.RootNotFound):
        pet.rescan()
    assert pet._rows == before


def test_request_allowed():
    port = pet.PORT
    assert pet.request_allowed(f"127.0.0.1:{port}", None)
    assert pet.request_allowed(f"localhost:{port}", None)
    assert pet.request_allowed(f"127.0.0.1:{port}", f"http://127.0.0.1:{port}")
    assert pet.request_allowed(f"localhost:{port}", f"http://localhost:{port}")
    # wrong host / port, or no Host at all (DNS rebinding)
    assert not pet.request_allowed("evil.example", None)
    assert not pet.request_allowed(f"evil.example:{port}", None)
    assert not pet.request_allowed(f"127.0.0.1:{port + 1}", None)
    assert not pet.request_allowed("127.0.0.1", None)
    assert not pet.request_allowed(None, None)
    # a page from another site (or a sandboxed one: "null", or an empty one)
    assert not pet.request_allowed(f"127.0.0.1:{port}", "https://evil.example")
    assert not pet.request_allowed(f"127.0.0.1:{port}", "http://evil.example")
    assert not pet.request_allowed(f"127.0.0.1:{port}", f"https://127.0.0.1:{port}")
    assert not pet.request_allowed(f"127.0.0.1:{port}", "null")
    assert not pet.request_allowed(f"127.0.0.1:{port}", "")


@pytest.fixture
def server(tmp_path, monkeypatch):
    """The real Handler on a free port (PORT patched to it)."""
    monkeypatch.setenv("PHOTO_ORIGIN_TAG_LOG_DIR", str(tmp_path / "_logs"))
    srv = pet.Server(("127.0.0.1", 0), pet.Handler)
    port = srv.server_address[1]
    monkeypatch.setattr(pet, "PORT", port)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield port
    srv.shutdown()
    srv.server_close()
    t.join(5)


def _post(port, path, body=b"", headers=None):
    h = {"Host": f"127.0.0.1:{port}", **(headers or {})}
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("POST", path, body=body, headers=h)
    r = c.getresponse()
    r.read()
    c.close()
    return r.status


def test_the_server_refuses_a_foreign_origin_and_bad_json(server, tmp_path):
    state = tmp_path / "_logs" / pet.STATE_NAME
    good = json.dumps({"a": "2026-11-11"}).encode()
    assert _post(server, "/state", good, {"Origin": "https://evil.example"}) == 403
    assert _post(server, "/state", good, {"Host": "evil.example"}) == 403
    assert _post(server, "/rescan", b"", {"Origin": "https://evil.example"}) == 403
    assert not state.exists()
    assert _post(server, "/state", b"{not json") == 400
    assert _post(server, "/state", b"\xff\xfe") == 400
    assert _post(server, "/state", b"[1]") == 400
    assert _post(server, "/open", b"[1]") == 400
    assert not state.exists()
    assert _post(server, "/state", good, {"Origin": f"http://127.0.0.1:{server}"}) == 204
    assert json.loads(state.read_text(encoding="utf-8")) == {"a": "2026-11-11"}


def test_the_server_answers_503_for_a_rescan_with_the_disk_gone(server, lib, monkeypatch):
    lib("05_Z7", "_EXPORT", "JPG", "DSC_0500.jpg")
    assert _post(server, "/rescan") == 204
    before = list(pet._rows)
    monkeypatch.setattr(pet, "ROOT", str(Path(pet.ROOT).parent / "gone") + "\\")
    assert _post(server, "/rescan") == 503
    assert pet._rows == before


def test_a_second_instance_cannot_open_the_same_port(server):
    with pytest.raises(OSError):
        pet.Server(("127.0.0.1", server), pet.Handler)


def test_main_exits_2_before_scanning_when_the_port_is_taken(server, lib, monkeypatch, capsys):
    def no_scan():
        raise AssertionError("scanned although the port was taken")
    monkeypatch.setattr(pet, "rescan", no_scan)
    monkeypatch.setattr(sys, "argv", ["pastas_exportar_tiff.py"])
    assert pet.main() == 2
    assert "in use" in capsys.readouterr().err
