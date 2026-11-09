#!/usr/bin/env python3
"""photo-origin-tag: record in each Capture One TIFF export what the photo
came from (RAW, JPEG, 8-bit TIFF or 16-bit TIFF), before jxl-photo turns the
export into a master.

What is written (see SPEC.md):
  * EXIF IFD0 XPComment  "Source: RAW" / "Source: JPEG" / "Source: TIFF 8-bit"
    / "Source: TIFF 16-bit" (Windows Explorer shows it as "Comments"); for a
    JPEG or TIFF original, followed by that original's colour profile name:
    "Source: JPEG, Adobe RGB (1998)", "Source: JPEG, sRGB (EXIF, no ICC)";
  * XMP dc:Relation      jxlphoto-origin:jpeg or jxlphoto-origin:tiff8, only
    where it differs from what jxl-photo's encoder would write by itself
    (tiff16). The encoder inherits a jxlphoto-origin already in its input.

A JPEG export below _EXPORT\\JPG_only (no TIFF of it can be made) gets the
XPComment and nothing else: no XMP, every other JPEG segment byte-identical.
Once it has an archive in _EXPORT\\JXL_jpeg it is refused, because tagging it
would make the JPEG differ from what that archive gives back.

The default run is a simulation; --apply writes. A file is only rewritten
through a temporary copy that exiftool writes next to it, which is checked
(image bytes, ICC profile, every other tag unchanged) before it replaces the
original. Nothing is ever guessed: an export the rules cannot decide is left
alone and listed for review; overrides.csv settles it.
"""

import argparse
import csv
import datetime as _dt
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

# ============================================================================
# SETTINGS (edit here)
# ============================================================================
EXPORT_FOLDER = "_EXPORT"            # exports live below <base>\_EXPORT\...
EXPORT_EXTS = (".tif", ".tiff")
JPG_ONLY_FOLDER = "JPG_only"         # <base>\_EXPORT\JPG_only: JPEG exports (no TIFF possible)
JPG_ARCHIVE_FOLDER = "JXL_jpeg"      # their lossless archives, mirroring the subfolders
RAW_EXTS = (".nef", ".nrw", ".orf", ".cr2", ".cr3", ".crw", ".arw", ".srf",
            ".sr2", ".dng", ".raf", ".rw2", ".pef", ".srw", ".x3f", ".3fr",
            ".iiq", ".erf", ".kdc", ".dcr", ".mrw", ".mos")
# RAW formats whose export carries a machine-readable RAW signal (XMP-crd or
# ExifIFD:SubjectDistance, both only from the NEF's makernotes): their
# RAW+JPEG pair is decided without the label.
SIGNAL_RAW_EXTS = (".nef", ".nrw")
JPEG_EXTS = (".jpg", ".jpeg")
TIFF_EXTS = (".tif", ".tiff")
LABEL_RAW = "Blue"                   # C1 colour label: exported from a RAW
LABEL_NOT_RAW = "Green"              # C1 colour label: exported from JPEG/TIFF
COMMENT_PREFIX = "Source: "
COMMENTS = {
    "raw": "Source: RAW",
    "jpeg": "Source: JPEG",
    "tiff8": "Source: TIFF 8-bit",
    "tiff16": "Source: TIFF 16-bit",
}
# The original's colour profile follows the source type in the comment
# ("Source: JPEG, Adobe RGB (1998)"), for JPEG and TIFF originals only: a RAW
# has no profile of its own (the export's profile is the one C1 chose).
PROFILE_IN_COMMENT = True
PROFILE_SEP = ", "
# No ICC profile in the original: the EXIF colour space tag, when it names one.
PROFILE_EXIF_SRGB = "sRGB (EXIF, no ICC)"            # ColorSpace = 1
PROFILE_EXIF_ADOBE = "Adobe RGB (EXIF, no ICC)"      # ColorSpace = 2, or 65535 + R03
PROFILE_NONE = "no color profile"                    # neither ICC nor EXIF colour space
PROFILE_UNNAMED = "unnamed ICC profile"              # an ICC without a description
PROFILE_MAX_LEN = 120                                # longer descriptions are cut
# jxlphoto-origin value per kind; None = leave it to jxl-photo's encoder,
# which writes tiff16 for a 16-bit TIFF by itself.
ORIGIN_TOKENS = {"raw": None, "tiff16": None, "jpeg": "jpeg", "tiff8": "tiff8"}
ORIGIN_PREFIX = "jxlphoto-origin:"
TOOLKIT_NAMESPACE = "jxlphoto-"
OVERRIDES_FILE = "overrides.csv"     # next to this script unless --overrides
LOG_DIR = "Logs"                     # next to this script; env PHOTO_ORIGIN_TAG_LOG_DIR
EXIFTOOL = "exiftool"
EXIFTOOL_TIMEOUT = 600               # seconds, per call
EXIFTOOL_STAY_OPEN = True            # one exiftool per worker for the whole run
                                     # (a new process per call cost ~0.3 s, 3x per file)
READ_BATCH = 200                     # files per planning-time exiftool read
WRITE_WORKERS = 1                    # files written at once (--workers). Measured on
                                     # one HDD, 40 real exports (6.7 GB): 1 worker 66 s,
                                     # 2 workers 74 s; reading 25 files ahead made no
                                     # difference -- the run already moves ~205 MB/s,
                                     # the disk's limit (2026-11-07)
TEMP_MARK = "~origin-tag-tmp"        # temporary files carry this in the name
# Tags exiftool may change when it rewrites a TIFF, besides the two we write.
# Anything else differing between the original and the rewritten copy is an
# error and the copy is discarded.
REWRITE_VOLATILE = {
    "XMP-x:XMPToolkit",
    "IFD0:StripOffsets", "IFD0:TileOffsets",
    "IFD1:StripOffsets", "IFD1:TileOffsets",
    "IFD0:ExifOffset", "IFD0:GPSInfo", "IFD0:XMP", "IFD0:IPTC-NAA",
    "ExifIFD:InteropOffset",
}
# In a JPEG, exiftool reports these as positions in the file, and they move when
# the Exif segment grows. What they point to is checked: the thumbnail is a tag
# of its own, and the images after the scan are in the JPEG fingerprint.
JPEG_POSITION_TAGS = {"ThumbnailOffset", "MPImageStart"}
VERSION = "0.4.0"
# ============================================================================

_KINDS = tuple(COMMENTS)


def log_dir() -> Path:
    env = os.environ.get("PHOTO_ORIGIN_TAG_LOG_DIR")
    return Path(env) if env else Path(__file__).resolve().parent / LOG_DIR


def _key(p) -> str:
    """Case-insensitive, separator-normalised key for a Windows path."""
    return os.path.normcase(os.path.normpath(str(p)))


# ---------------------------------------------------------------------------
# exiftool
# ---------------------------------------------------------------------------
_ARG_PREFIX = ["-charset", "filename=utf8", "-api", "WindowsLongPath=1"]


def _check_args(args):
    for a in args:
        if "\n" in str(a) or "\r" in str(a):
            raise ValueError(f"newline in exiftool argument: {a!r}")


class ExifToolProcess:
    """One `exiftool -stay_open True -@ -` kept for the whole run. Each command
    is sent on stdin as UTF-8 argument lines (the same argfile semantics as
    `-@ file`: Japanese, [ ], %, long paths) and ends with -executeN; stdout is
    read up to {readyN} and stderr up to the -echo4 marker. exiftool gives no
    per-command exit code in this mode: an 'Error' line on stderr counts as 1."""

    def __init__(self):
        import queue
        import threading
        self._q = queue
        self.p = subprocess.Popen([EXIFTOOL, "-stay_open", "True", "-@", "-"],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE)
        self.n = 0
        self.out_q, self.err_q = queue.Queue(), queue.Queue()
        for stream, q in ((self.p.stdout, self.out_q), (self.p.stderr, self.err_q)):
            threading.Thread(target=self._pump, args=(stream, q), daemon=True).start()

    @staticmethod
    def _pump(stream, q):
        for line in iter(stream.readline, b""):
            q.put(line)
        q.put(None)                       # the process ended

    def _collect(self, q, marker, deadline):
        lines = []
        while True:
            left = deadline - time.time()
            if left <= 0:
                raise TimeoutError("exiftool did not answer in time")
            try:
                line = q.get(timeout=left)
            except self._q.Empty:
                continue
            if line is None:
                raise OSError("exiftool stopped")
            text = line.decode("utf-8", "replace")
            if text.strip() == marker:
                return "".join(lines)
            lines.append(text)

    def run(self, args, timeout=EXIFTOOL_TIMEOUT):
        _check_args(args)
        self.n += 1
        n = self.n
        payload = [*_ARG_PREFIX, *[str(a) for a in args],
                   "-echo4", f"{{done{n}}}", f"-execute{n}"]
        self.p.stdin.write(("\n".join(payload) + "\n").encode("utf-8"))
        self.p.stdin.flush()
        deadline = time.time() + timeout
        out = self._collect(self.out_q, f"{{ready{n}}}", deadline)
        err = self._collect(self.err_q, f"{{done{n}}}", deadline)
        rc = 1 if any(ln.startswith("Error") for ln in err.splitlines()) else 0
        return rc, out, err

    def close(self):
        try:
            self.p.stdin.write(b"-stay_open\nFalse\n")
            self.p.stdin.flush()
            self.p.wait(timeout=30)
        except Exception:  # noqa: BLE001
            self.p.kill()


_tls = threading.local()


def _thread_exiftool():
    """This thread's persistent exiftool, started on first use."""
    et = getattr(_tls, "et", None)
    if et is None or et.p.poll() is not None:
        et = ExifToolProcess()
        _tls.et = et
        _ALL_PROCS.append(et)
    return et


_ALL_PROCS = []


def close_exiftools():
    while _ALL_PROCS:
        _ALL_PROCS.pop().close()


def run_exiftool(args, timeout=EXIFTOOL_TIMEOUT):
    """Run one exiftool command. Returns (returncode, stdout, stderr).
    With EXIFTOOL_STAY_OPEN the command goes to this thread's persistent
    process; a process that times out or dies is killed and the call raises
    (the caller reports that file as an error, the next call starts a fresh
    process)."""
    if not EXIFTOOL_STAY_OPEN:
        return _run_exiftool_spawn(args, timeout)
    et = _thread_exiftool()
    try:
        return et.run(args, timeout)
    except Exception:
        et.p.kill()
        _tls.et = None
        raise


def _run_exiftool_spawn(args, timeout=EXIFTOOL_TIMEOUT):
    """Run exiftool with `args` in a UTF-8 argfile (Japanese, [ ], %, long
    paths). Returns (returncode, stdout, stderr)."""
    import tempfile
    fd, argfile = tempfile.mkstemp(prefix="photo-origin-tag-", suffix=".args")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write("-charset\nfilename=utf8\n-api\nWindowsLongPath=1\n")
            for a in args:
                if "\n" in str(a):
                    raise ValueError(f"newline in exiftool argument: {a!r}")
                f.write(f"{a}\n")
        r = subprocess.run([EXIFTOOL, "-@", argfile], capture_output=True,
                           timeout=timeout)
        return (r.returncode, r.stdout.decode("utf-8", "replace"),
                r.stderr.decode("utf-8", "replace"))
    finally:
        try:
            os.unlink(argfile)
        except OSError:
            pass


def read_tags(paths, tags, extra=(), progress=None):
    """{key(path): entry} for every path exiftool answered; a path missing
    from the result could not be read (the caller decides what that means)."""
    out = {}
    paths = list(paths)
    for i in range(0, len(paths), READ_BATCH):
        chunk = paths[i:i + READ_BATCH]
        rc, so, se = run_exiftool(["-j", "-G1", "-n", *extra, *tags,
                                   *[str(p) for p in chunk]])
        try:
            data = json.loads(so) if so.strip() else []
        except json.JSONDecodeError:
            data = []
        for e in data:
            out[_key(e.get("SourceFile", ""))] = e
        if progress:
            progress(min(i + READ_BATCH, len(paths)), len(paths))
    return out


def _as_list(v):
    if v is None:
        return []
    return [str(x) for x in (v if isinstance(v, list) else [v])]


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------
def export_base(path: Path):
    """The folder holding the originals for an export below <base>\\_EXPORT,
    or None when the path has no EXPORT_FOLDER segment."""
    parts = path.parts
    idx = [i for i, s in enumerate(parts[:-1])
           if s.casefold() == EXPORT_FOLDER.casefold()]
    if not idx:
        return None
    return Path(*parts[:idx[-1]])


def _below_export(path: Path):
    """The folder names between the last EXPORT_FOLDER and the file, or None."""
    parts = path.parts
    idx = [i for i, s in enumerate(parts[:-1])
           if s.casefold() == EXPORT_FOLDER.casefold()]
    return list(parts[idx[-1] + 1:-1]) if idx else None


def is_jpg_only_export(path: Path) -> bool:
    """A JPEG directly in, or in a subfolder of, <base>\\_EXPORT\\JPG_only."""
    below = _below_export(path)
    return (path.suffix.lower() in JPEG_EXTS and bool(below)
            and below[0].casefold() == JPG_ONLY_FOLDER.casefold())


def jpg_archive_path(path: Path) -> Path:
    """Where the lossless archive of a JPG_only export lands."""
    return (export_base(path) / EXPORT_FOLDER / JPG_ARCHIVE_FOLDER
            / Path(*_below_export(path)[1:], path.stem + ".jxl"))


def clean_stale_temps(roots):
    """Delete the temporary copies a killed run left behind. Only files named
    <stem><TEMP_MARK>-<pid><ext> whose original <stem><ext> still exists are
    removed (the original is never at risk: it is replaced atomically)."""
    import re
    pat = re.compile(r"^(?P<stem>.*)" + re.escape(TEMP_MARK) + r"-\d+(?P<ext>\.[^.]+)$")
    removed = []
    for root in roots:
        root = Path(root)
        if root.is_file():
            continue
        for dp, dns, fns in os.walk(root):
            for fn in fns:
                m = pat.match(fn)
                if m and (Path(dp) / (m["stem"] + m["ext"])).exists():
                    try:
                        (Path(dp) / fn).unlink()
                        removed.append(Path(dp) / fn)
                    except OSError:
                        pass
    return removed


def find_exports(roots):
    found = []
    for root in roots:
        root = Path(root)
        if root.is_file():
            cands = [root]
        else:
            cands = []
            for dp, dns, fns in os.walk(root):
                dns.sort()
                for fn in sorted(fns):
                    cands.append(Path(dp) / fn)
        for p in cands:
            if (TEMP_MARK not in p.name and export_base(p) is not None
                    and (p.suffix.lower() in EXPORT_EXTS or is_jpg_only_export(p))):
                found.append(p)
    seen, uniq = set(), []
    for p in found:
        if _key(p) not in seen:
            seen.add(_key(p))
            uniq.append(p)
    return uniq


_sib_cache = {}


def siblings(base: Path):
    """{stem.lower(): {ext.lower(): filename}} of the files directly in base."""
    k = _key(base)
    if k not in _sib_cache:
        d = {}
        try:
            for e in os.scandir(base):
                if e.is_file():
                    st, ext = os.path.splitext(e.name)
                    d.setdefault(st.lower(), {})[ext.lower()] = e.name
        except OSError:
            pass
        _sib_cache[k] = d
    return _sib_cache[k]


def match_original(export_stem: str, sibs: dict):
    """The sibling stem that is the LONGEST prefix of the export's stem
    (equal, or followed by '_'), or None."""
    s = export_stem.lower()
    cands = [k for k in sibs if s == k or s.startswith(k + "_")]
    return max(cands, key=len) if cands else None


# ---------------------------------------------------------------------------
# Overrides
# ---------------------------------------------------------------------------
@dataclass
class Override:
    path: str
    origin: str
    note: str
    line: int
    used: int = 0


def load_overrides(path: Path):
    """overrides.csv: path,origin,note (UTF-8, header row). A path names a
    file or a folder; the most specific match wins."""
    if not path.exists():
        return []
    out = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        rd = csv.DictReader(f)
        missing = {"path", "origin"} - set(rd.fieldnames or [])
        if missing:
            raise SystemExit(f"{path}: missing column(s) {sorted(missing)}")
        for n, row in enumerate(rd, start=2):
            p = (row.get("path") or "").strip()
            o = (row.get("origin") or "").strip().lower()
            if not p or p.startswith("#"):
                continue
            if o not in _KINDS:
                raise SystemExit(f"{path}:{n}: origin {o!r} is not one of "
                                 f"{', '.join(_KINDS)}")
            out.append(Override(_key(p), o, (row.get("note") or "").strip(), n))
    return out


def find_override(path: Path, overrides):
    k = _key(path)
    best = None
    for ov in overrides:
        if k == ov.path or k.startswith(ov.path.rstrip("\\/") + os.sep):
            if best is None or len(ov.path) > len(best.path):
                best = ov
    return best


# ---------------------------------------------------------------------------
# Classification (pure: no I/O)
# ---------------------------------------------------------------------------
@dataclass
class Decision:
    kind: str = None          # raw / jpeg / tiff8 / tiff16, None = undecided
    by: str = ""              # how it was decided
    review: str = ""          # why it needs a human, when kind is None
    original: str = ""        # the original's file name(s)


def _depth_kind(bits):
    b = str(bits or "").split()
    if b and b[0] == "16":
        return "tiff16"
    if b and b[0] == "8":
        return "tiff8"
    return None


def classify(exts: dict, meta: dict, tiff_bits=None, override=None,
             jpeg_export=False) -> Decision:
    """Decide one export.

    exts:        {ext: filename} of the original(s) beside the export (the
                 longest-prefix sibling), {} when none was found.
    meta:        the export's exiftool entry (-G1 keys).
    tiff_bits:   BitsPerSample of the original TIFF, when there is one.
    override:    an Override, which wins over everything.
    jpeg_export: the export is a JPEG, whose recipe may have dropped the RAW
                 signal (Capture One's WEB recipe does), so a missing signal
                 proves nothing without a Green label."""
    # sidecars (.xmp, ...) beside the original are not originals
    exts = {e: n for e, n in exts.items()
            if e in RAW_EXTS or e in JPEG_EXTS or e in TIFF_EXTS}
    names = "+".join(sorted(exts.values()))
    if override is not None:
        return Decision(override.origin, f"override (line {override.line})",
                        original=names)
    label = str(meta.get("XMP-xmp:Label", "") or "")
    raw = sorted(e for e in exts if e in RAW_EXTS)
    jpg = [e for e in exts if e in JPEG_EXTS]
    tif = sorted(e for e in exts if e in TIFF_EXTS)
    signal = (any(k.startswith("XMP-crd:") for k in meta)
              or "ExifIFD:SubjectDistance" in meta)

    def review(why):
        return Decision(None, "", why, names)

    if not exts:
        return review("no original found beside the export")
    if raw and jpg and not tif:
        if all(e in SIGNAL_RAW_EXTS for e in raw):
            kind = "raw" if signal else "jpeg"
            if label == LABEL_RAW and kind != "raw":
                return review("label Blue but no RAW signal in a NEF+JPEG pair")
            if label == LABEL_NOT_RAW and kind == "raw":
                return review("label Green but the export carries a RAW signal")
            if jpeg_export and not signal and label != LABEL_NOT_RAW:
                return review("NEF+JPEG pair, JPEG export without a RAW signal "
                              "or a Green label (its recipe may drop metadata)")
            return Decision(kind, "NEF+JPEG pair, RAW signal" if signal
                            else "NEF+JPEG pair, no RAW signal", original=names)
        if label == LABEL_RAW:
            return Decision("raw", "RAW+JPEG pair, label Blue", original=names)
        if label == LABEL_NOT_RAW:
            return Decision("jpeg", "RAW+JPEG pair, label Green", original=names)
        return review(f"RAW+JPEG pair without a Blue/Green label ({label or 'none'})")
    if raw and tif and not jpg:
        if label == LABEL_RAW:
            return Decision("raw", "RAW+TIFF pair, label Blue", original=names)
        if label == LABEL_NOT_RAW:
            k = _depth_kind(tiff_bits)
            if k is None:
                return review(f"cannot read the original TIFF's bit depth ({tiff_bits})")
            return Decision(k, "RAW+TIFF pair, label Green, TIFF depth",
                            original=names)
        return review(f"RAW+TIFF pair without a Blue/Green label ({label or 'none'})")
    if raw and not jpg and not tif:
        if label == LABEL_NOT_RAW:
            return review("label Green but only a RAW beside the export")
        return Decision("raw", "only a RAW beside", original=names)
    if jpg and not raw and not tif:
        if label == LABEL_RAW:
            return review("label Blue but only a JPEG beside the export")
        return Decision("jpeg", "only a JPEG beside", original=names)
    if tif and not raw and not jpg:
        k = _depth_kind(tiff_bits)
        if k is None:
            return review(f"cannot read the original TIFF's bit depth ({tiff_bits})")
        if label == LABEL_RAW and k == "tiff8":
            return review("label Blue but the original is an 8-bit TIFF")
        return Decision(k, "only a TIFF beside, TIFF depth", original=names)
    return review(f"unexpected originals beside the export: {names}")


# ---------------------------------------------------------------------------
# The original's colour profile (pure: no I/O)
# ---------------------------------------------------------------------------
PROFILE_TAGS = ["-ICC_Profile", "-ICC_Profile:ProfileDescription",
                "-ExifIFD:ColorSpace", "-InteropIFD:InteropIndex",
                "-File:FileType", "-ExifTool:Warning", "-ExifTool:Error"]


def source_file(kind, exts: dict):
    """The original file name whose profile the comment names: the JPEG for
    'jpeg', the TIFF for 'tiff8'/'tiff16', None for 'raw' or when no such
    original is beside the export (an override may name a kind that is)."""
    if kind == "jpeg":
        pick = sorted(e for e in exts if e in JPEG_EXTS)
    elif kind in ("tiff8", "tiff16"):
        pick = sorted(e for e in exts if e in TIFF_EXTS)
    else:
        return None
    return exts[pick[0]] if pick else None


def _clean_text(s: str) -> str:
    s = "".join(ch if ch.isprintable() else " " for ch in str(s))
    s = " ".join(s.split())
    if len(s) > PROFILE_MAX_LEN:
        s = s[:PROFILE_MAX_LEN - 3].rstrip() + "..."
    return s


def profile_label(entry):
    """The colour profile name of an original from its exiftool entry (-G1
    -n, PROFILE_TAGS), or None when the original could not be read. The ICC
    profile wins (it is what an application uses); without one, the EXIF
    colour space tag. "No profile" is only said of a file exiftool read
    cleanly: finding nothing in a file it could not parse (wrong type, a
    format error, a truncated file) is None, never a guess."""
    if entry is None:
        return None
    if "ICC_Profile:ICC_Profile" in entry:
        return _clean_text(entry.get("ICC_Profile:ProfileDescription") or "") \
            or PROFILE_UNNAMED
    cs = str(entry.get("ExifIFD:ColorSpace", "")).strip()
    interop = str(entry.get("InteropIFD:InteropIndex", "")).strip().upper()
    if cs == "1":
        return PROFILE_EXIF_SRGB
    if cs == "2" or (cs == "65535" and interop == "R03"):
        return PROFILE_EXIF_ADOBE
    clean = (str(entry.get("File:FileType", "")).upper() in ("JPEG", "TIFF")
             and not entry.get("ExifTool:Error")
             and not entry.get("ExifTool:Warning"))
    return PROFILE_NONE if clean else None


def comment_for(kind, profile=None) -> str:
    c = COMMENTS[kind]
    if PROFILE_IN_COMMENT and profile and kind != "raw":
        c += PROFILE_SEP + profile
    return c


def comment_kind(text: str):
    """The kind a comment this script wrote names ('jpeg', ...), or None."""
    for k, c in COMMENTS.items():
        if text == c or text.startswith(c + PROFILE_SEP):
            return k
    return None


# ---------------------------------------------------------------------------
# Planning: what to write
# ---------------------------------------------------------------------------
@dataclass
class Item:
    path: Path
    decision: Decision = field(default_factory=Decision)
    label: str = ""
    model: str = ""
    action: str = ""          # write / ok / review / refuse / skip / error
    reason: str = ""
    relation_add: str = None  # token to add
    relation_remove: list = field(default_factory=list)
    comment: str = None       # XPComment to write
    profile: str = None       # the original's colour profile, None = not read


def plan_item(it: Item, meta: dict, overridden: bool):
    """Fill it.action and what to write, from the export's current tags.
    it.profile (set by the caller) is the original's colour profile, or None
    when it was not read (RAW, or the original is unreadable)."""
    d = it.decision
    if d.kind is None:
        it.action, it.reason = "review", d.review
        return
    jpeg_export = is_jpg_only_export(it.path)
    rel = _as_list(meta.get("XMP-dc:Relation"))
    # a JPEG is never given a token, so any token in one came from jxl-photo
    tokens_other = [t for t in rel if t.startswith(TOOLKIT_NAMESPACE)
                    and (jpeg_export or not t.startswith(ORIGIN_PREFIX))]
    if tokens_other:
        it.action = "skip"
        it.reason = (f"carries jxl-photo marker(s) {tokens_other[:2]}: "
                     f"not a Capture One export")
        return
    want_tok = None if jpeg_export else ORIGIN_TOKENS[d.kind]
    want_full = ORIGIN_PREFIX + want_tok if want_tok else None
    have = [t for t in rel if t.startswith(ORIGIN_PREFIX)]
    # what jxl-photo would record when no token is written: tiff16
    if want_full is None:
        bad = [t for t in have if t != ORIGIN_PREFIX + "tiff16"]
    else:
        bad = [t for t in have if t != want_full]
    if bad:
        if not overridden:
            it.action = "refuse"
            it.reason = (f"already carries {bad[0]}, rules say {d.kind}: "
                         f"only an override replaces it")
            return
        it.relation_remove = bad
    if want_full and want_full not in have:
        it.relation_add = want_full
    want_c = comment_for(d.kind, it.profile)
    cur_c = str(meta.get("IFD0:XPComment", "") or "")
    if cur_c.strip() and cur_c != want_c:
        if not cur_c.startswith(COMMENT_PREFIX):
            it.action = "refuse"
            it.reason = f"XPComment already holds someone else's text: {cur_c[:60]!r}"
            return
        same_kind = comment_kind(cur_c) == d.kind
        if same_kind and it.profile is None and d.kind != "raw":
            # the original could not be read this time: keep the profile an
            # earlier run recorded rather than dropping it
            want_c = cur_c
        elif not same_kind and not overridden:
            # the same source type with another profile is a fact read from
            # the original, rewritten freely; another source type is a rule
            # decision, replaced only by an override
            it.action = "refuse"
            it.reason = (f"XPComment says {cur_c!r}, rules say {want_c!r}: "
                         f"only an override replaces it")
            return
    if cur_c != want_c:
        it.comment = want_c
    if it.comment is None and it.relation_add is None and not it.relation_remove:
        it.action, it.reason = "ok", "already tagged"
    elif jpeg_export and jpg_archive_path(it.path).exists():
        it.action = "refuse"
        it.reason = (f"already archived in {JPG_ARCHIVE_FOLDER}: tagging would "
                     f"make the JPEG differ from what its JXL gives back")
    else:
        it.action = "write"


# ---------------------------------------------------------------------------
# Verified rewrite
# ---------------------------------------------------------------------------
def image_fingerprint(path: Path):
    """(sha256 of every IFD's raw strip/tile bytes, sha256 of the ICC
    profiles): read without decompressing, so a ZIP TIFF costs no decode."""
    import tifffile
    h_img, h_icc = hashlib.sha256(), hashlib.sha256()
    with tifffile.TiffFile(str(path)) as tf, open(path, "rb") as fh:
        for page in tf.pages:
            for off, cnt in zip(page.dataoffsets, page.databytecounts):
                fh.seek(off)
                left = cnt
                while left:
                    b = fh.read(min(left, 1 << 22))
                    if not b:
                        raise OSError(f"short read at {off}")
                    h_img.update(b)
                    left -= len(b)
            icc = page.tags.get(34675)
            h_icc.update(bytes(icc.value) if icc is not None else b"-")
            h_icc.update(b"|")
    return h_img.hexdigest(), h_icc.hexdigest()


def jpeg_fingerprint(path: Path):
    """(sha256 of everything but the Exif segment, sha256 of the ICC segments):
    the XPComment lives in the Exif segment, so every other byte of the file,
    from the XMP to the scan and any trailer, has to come through unchanged."""
    d = Path(path).read_bytes()
    if d[:2] != b"\xff\xd8":
        raise OSError("not a JPEG (no SOI marker)")
    h_all, h_icc = hashlib.sha256(d[:2]), hashlib.sha256()
    i = 2
    while True:
        if i + 4 > len(d) or d[i] != 0xFF:
            raise OSError(f"malformed JPEG segment at byte {i}")
        marker = d[i + 1]
        if marker == 0xFF:                # fill byte before a marker
            h_all.update(d[i:i + 1])
            i += 1
            continue
        if marker == 0xDA:                # start of scan: the rest, whole
            h_all.update(d[i:])
            return h_all.hexdigest(), h_icc.hexdigest()
        end = i + 2 + int.from_bytes(d[i + 2:i + 4], "big")
        if end > len(d):
            raise OSError(f"truncated JPEG segment at byte {i}")
        seg = d[i:end]
        if not (marker == 0xE1 and seg[4:10] == b"Exif\0\0"):
            h_all.update(seg)
        if marker == 0xE2 and seg[4:16] == b"ICC_PROFILE\0":
            h_icc.update(seg)
        i = end


def all_tags(path: Path):
    rc, so, se = run_exiftool(["-j", "-G1", "-a", "-n", "-b",
                               "--File:all", "--System:all",
                               "--Composite:all", "--ExifTool:all",
                               str(path)])
    data = json.loads(so) if so.strip() else []
    if rc != 0 or not data:
        raise OSError(f"exiftool could not read the tags: {se.strip()[:200]}")
    e = data[0]
    e.pop("SourceFile", None)
    return e


def tag_differences(before: dict, after: dict, allowed):
    keys = set(before) | set(after)
    return sorted(k for k in keys
                  if k not in allowed and before.get(k) != after.get(k))


def write_item(it: Item):
    """Rewrite one export through a verified temporary copy. Sets action to
    'written' or 'error'."""
    src = it.path
    tmp = src.with_name(f"{src.stem}{TEMP_MARK}-{os.getpid()}{src.suffix}")
    fingerprint = jpeg_fingerprint if src.suffix.lower() in JPEG_EXTS else image_fingerprint
    try:
        st0 = src.stat()
        fp0 = fingerprint(src)
        tags0 = all_tags(src)
        args = ["-o", str(tmp)]
        for t in it.relation_remove:
            args.append(f"-XMP-dc:Relation-={t}")
        if it.relation_add:
            args.append(f"-XMP-dc:Relation+={it.relation_add}")
        if it.comment is not None:
            args.append(f"-IFD0:XPComment={it.comment}")
        args.append(str(src))
        rc, so, se = run_exiftool(args)
        if rc != 0 or not tmp.exists():
            raise OSError(f"exiftool failed: {(se or so).strip()[:300]}")
        if fingerprint(tmp) != fp0:
            raise OSError("image data or ICC profile changed in the rewrite")
        tags1 = all_tags(tmp)
        allowed = REWRITE_VOLATILE | {"XMP-dc:Relation", "IFD0:XPComment"}
        if fingerprint is jpeg_fingerprint:
            allowed |= {k for k in {*tags0, *tags1}
                        if k.rsplit(":", 1)[-1] in JPEG_POSITION_TAGS}
        diff = tag_differences(tags0, tags1, allowed)
        if diff:
            raise OSError(f"unexpected tag change(s): {', '.join(diff[:6])}")
        rel1 = _as_list(tags1.get("XMP-dc:Relation"))
        rel0 = [t for t in _as_list(tags0.get("XMP-dc:Relation"))
                if t not in it.relation_remove]
        want_rel = rel0 + ([it.relation_add] if it.relation_add
                           and it.relation_add not in rel0 else [])
        if rel1 != want_rel:
            raise OSError(f"dc:Relation reads back {rel1}, expected {want_rel}")
        want_c = it.comment if it.comment is not None else tags0.get("IFD0:XPComment")
        if (tags1.get("IFD0:XPComment") or None) != (want_c or None):
            raise OSError(f"XPComment reads back {tags1.get('IFD0:XPComment')!r}")
        st1 = src.stat()
        if (st1.st_size, st1.st_mtime_ns) != (st0.st_size, st0.st_mtime_ns):
            raise OSError("the export changed while it was being tagged")
        os.replace(tmp, src)
        it.action = "written"             # a planning note stays in the report
    except Exception as e:  # noqa: BLE001 - every failure is reported per file
        it.action, it.reason = "error", f"{type(e).__name__}: {e}"
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
class Progress:
    def __init__(self, label):
        self.label, self.t0, self.last = label, time.time(), 0.0

    def __call__(self, n, total, force=False):
        now = time.time()
        if not force and n < total and now - self.last < 2:
            return
        self.last = now
        el = now - self.t0
        eta = (el / n * (total - n)) if n else 0
        print(f"\r  {self.label}: {n}/{total}  "
              f"{int(el // 60)}:{int(el % 60):02d} elapsed, "
              f"~{int(eta // 60)}:{int(eta % 60):02d} left   ",
              end="", flush=True)
        if n >= total:
            print()


def build_items(exports, overrides):
    p = Progress("reading exports")
    meta = read_tags(exports, ["-XMP-xmp:Label", "-XMP-crd:all",
                               "-ExifIFD:SubjectDistance", "-XMP-dc:Relation",
                               "-IFD0:XPComment", "-IFD0:Model"],
                     extra=["-fast2"], progress=p)
    items, origs = [], {}
    for x in exports:
        it = Item(x)
        m = meta.get(_key(x))
        if m is None:
            it.action, it.reason = "error", "exiftool could not read the export"
            items.append((it, None, None, None))
            continue
        it.label = str(m.get("XMP-xmp:Label", "") or "")
        it.model = str(m.get("IFD0:Model", "") or "")
        base = export_base(x)
        sibs = siblings(base)
        stem = match_original(x.stem, sibs)
        exts = dict(sibs.get(stem, {})) if stem else {}
        tif = sorted(e for e in exts if e in TIFF_EXTS)
        tpath = base / exts[tif[0]] if tif else None
        if tpath is not None:
            origs[_key(tpath)] = tpath
        items.append((it, m, exts, tpath))
    p2 = Progress("reading original TIFFs")
    # one read gives the TIFF's depth (for the rules) and its profile
    orig_meta = read_tags(list(origs.values()),
                          ["-IFD0:BitsPerSample", *PROFILE_TAGS],
                          extra=["-fast2"], progress=p2) if origs else {}
    decided = []
    for it, m, exts, tpath in items:
        if m is None:
            continue
        ov = find_override(it.path, overrides)
        if ov is not None:
            ov.used += 1
        tb = None
        if tpath is not None:
            e = orig_meta.get(_key(tpath))
            tb = e.get("IFD0:BitsPerSample") if e else None
        it.decision = classify(exts, m, tb, ov, is_jpg_only_export(it.path))
        src = source_file(it.decision.kind, exts) if PROFILE_IN_COMMENT else None
        decided.append((it, m, ov, export_base(it.path) / src if src else None))
    # the profile of the JPEG originals the rules picked (read only for those)
    jpgs = {_key(s): s for _, _, _, s in decided
            if s is not None and _key(s) not in orig_meta}
    if jpgs:
        orig_meta.update(read_tags(list(jpgs.values()), PROFILE_TAGS,
                                   extra=["-fast2"],
                                   progress=Progress("reading original JPEGs")))
    for it, m, ov, src in decided:
        if src is not None:
            it.profile = profile_label(orig_meta.get(_key(src)))
        plan_item(it, m, ov is not None)
        if (src is not None and it.profile is None
                and it.action in ("write", "ok") and not it.reason):
            it.reason = "original unreadable: its colour profile is not recorded"
    return [t[0] for t in items]


def write_report(items, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["action", "origin", "decided_by", "reason", "label",
                    "model", "original", "profile", "path"])
        for it in items:
            w.writerow([it.action, it.decision.kind or "", it.decision.by,
                        it.reason, it.label, it.model, it.decision.original,
                        it.profile or "", str(it.path)])


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Tag Capture One TIFF exports with what they came from "
                    "(simulation unless --apply).")
    ap.add_argument("roots", nargs="+", help="folders (or files) to scan")
    ap.add_argument("--apply", action="store_true",
                    help="write the tags (default: simulation only)")
    ap.add_argument("--overrides", default=None,
                    help=f"overrides CSV (default: {OVERRIDES_FILE} next to "
                         f"the script)")
    ap.add_argument("--report", default=None, help="report CSV path")
    ap.add_argument("--workers", type=int, default=WRITE_WORKERS,
                    help=f"files written at once (default {WRITE_WORKERS})")
    a = ap.parse_args(argv)
    if a.workers < 1:
        print("ERROR: --workers must be at least 1", file=sys.stderr)
        return 2
    try:
        return _main(a)
    finally:
        close_exiftools()


def _main(a):

    if a.apply:
        try:
            import tifffile  # noqa: F401 - the write check needs it
        except ImportError:
            print("ERROR: --apply needs the Python package tifffile "
                  "(pip install tifffile)", file=sys.stderr)
            return 2
    for r in a.roots:
        if not Path(r).exists():
            print(f"ERROR: not found: {r}", file=sys.stderr)
            return 2
    ov_path = Path(a.overrides) if a.overrides else \
        Path(__file__).resolve().parent / OVERRIDES_FILE
    overrides = load_overrides(ov_path)
    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    report = Path(a.report) if a.report else log_dir() / f"{stamp}_report.csv"

    print(f"photo-origin-tag {VERSION} | {'APPLY' if a.apply else 'SIMULATION'}"
          f" | overrides: {len(overrides)} ({ov_path})")
    if a.apply:
        for t in clean_stale_temps(a.roots):
            print(f"  removed a temporary file left by an interrupted run: {t}")
    print("  finding exports ...", flush=True)
    exports = find_exports(a.roots)
    print(f"  {len(exports)} export(s) below {EXPORT_FOLDER}")
    items = build_items(exports, overrides)

    if a.apply:
        todo = [it for it in items if it.action == "write"]
        logf = log_dir() / f"{stamp}_written.log"
        logf.parent.mkdir(parents=True, exist_ok=True)
        p = Progress("writing")
        with open(logf, "a", encoding="utf-8") as lg:
            def done(n, it):
                lg.write(f"{_dt.datetime.now():%H:%M:%S}\t{it.action}\t"
                         f"{it.decision.kind}\t{it.reason}\t{it.path}\n")
                lg.flush()
                if it.action == "error":
                    print(f"\n  ERROR {it.path}: {it.reason}")
                p(n, len(todo))
            if a.workers == 1:
                for n, it in enumerate(todo, 1):
                    write_item(it)
                    done(n, it)
            else:
                from concurrent.futures import ThreadPoolExecutor, as_completed
                with ThreadPoolExecutor(max_workers=a.workers) as ex:
                    futs = {ex.submit(write_item, it): it for it in todo}
                    for n, f in enumerate(as_completed(futs), 1):
                        f.result()        # write_item reports its own errors
                        done(n, futs[f])
        print(f"  per-file log: {logf}")

    write_report(items, report)
    from collections import Counter
    c = Counter((it.action, it.decision.kind or "-") for it in items)
    print("\nSummary (action, origin): count")
    for (act, kind), n in sorted(c.items()):
        print(f"  {act:<8} {kind:<7} {n}")
    pc = Counter((it.decision.kind, it.profile or "(not read)") for it in items
                 if it.decision.kind not in (None, "raw")
                 and it.action in ("write", "written", "ok"))
    if pc:
        print("\nColour profile of the originals (origin, profile): count")
        for (kind, prof), n in sorted(pc.items()):
            print(f"  {kind:<7} {prof}  {n}")
    for ov in overrides:
        if not ov.used:
            print(f"  WARNING: override line {ov.line} matched nothing: {ov.path}")
    rev = [it for it in items if it.action in ("review", "refuse", "error")]
    if rev:
        print(f"\n{len(rev)} file(s) need attention (all in the report):")
        for it in rev[:40]:
            print(f"  {it.action:<7} {it.reason} | {it.path}")
        if len(rev) > 40:
            print(f"  ... {len(rev) - 40} more")
    print(f"\nReport: {report}")
    if not a.apply and any(it.action == "write" for it in items):
        print("Simulation: nothing was written. Run again with --apply.")
    return 1 if any(it.action == "error" for it in items) else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
