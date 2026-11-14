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
through a temporary copy next to it (written by exiftool, or copied by Python
and edited in place when the path has a %), which is checked (image bytes, ICC
profile, every other tag unchanged) before it replaces the original. Nothing
is ever guessed: an export the rules cannot decide is left alone and listed
for review; overrides.csv settles it.
"""

import argparse
import atexit
import csv
import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

# ============================================================================
# SETTINGS (edit here)
# ============================================================================
EXPORT_FOLDER = "_EXPORT"            # exports live below <base>\_EXPORT\...
EXPORT_EXTS = (".tif", ".tiff")
# Folders never entered, at any depth (any case): Windows' own, the recycle
# bin, and Capture One's own folder (its settings and cache, never an export;
# the cache is often a junction to another disk, and the walk follows
# junctions). With them skipped a whole drive (G:\) is a safe root, the same
# scope as a sync that encodes every export on it. On the owner's G: the
# CaptureOne folders were 793 000 of the 1.36 million names the walk listed
# (cold walk 68 s -> 48 s, measured 2026-11-15).
SKIP_DIRS = ("$RECYCLE.BIN", "System Volume Information", "CaptureOne")
JPG_ONLY_FOLDER = "JPG_only"         # <base>\_EXPORT\JPG_only: JPEG exports (no TIFF possible)
JPG_ARCHIVE_FOLDER = "JXL_jpeg"      # their lossless archives, mirroring the subfolders
RAW_EXTS = (".nef", ".nrw", ".orf", ".cr2", ".cr3", ".crw", ".arw", ".srf",
            ".sr2", ".dng", ".raf", ".rw2", ".pef", ".srw", ".x3f", ".3fr",
            ".iiq", ".erf", ".kdc", ".dcr", ".mrw", ".mos")
# RAW formats whose export carries a machine-readable RAW signal (XMP-crd or
# ExifIFD:SubjectDistance, taken from the NEF's makernotes): their RAW+JPEG
# pair is decided by it. A JPEG that NX Studio made from the NEF carries the
# XMP-crd too (14 real cases), so the signal only counts while the JPEG beside
# the export does not carry it.
SIGNAL_RAW_EXTS = (".nef", ".nrw")
SIGNAL_TAGS = ["-XMP-crd:all", "-ExifIFD:SubjectDistance"]
# What exiftool must say a readable export is (File:FileType), by extension.
TIFF_FILETYPES = ("TIFF", "BTF")
JPEG_FILETYPES = ("JPEG",)
JPEG_EXTS = (".jpg", ".jpeg")
TIFF_EXTS = (".tif", ".tiff")
LABEL_RAW = "Blue"                   # C1 colour label: exported from a RAW
LABEL_NOT_RAW = "Green"              # C1 colour label: exported from JPEG/TIFF
# A RAW + JPEG + TIFF trio beside the export with a Blue label counts as RAW
# (--trio-blue-raw turns it on for one run). Off by default: the trio is the
# owner's heuristic (the S5Pro DSCF trios, all Blue), not a fact. Green never
# decides a trio: it cannot tell the JPEG from the TIFF.
TRIO_BLUE_IS_RAW = False
COMMENT_PREFIX = "Source: "
COMMENTS = {
    "raw": COMMENT_PREFIX + "RAW",
    "jpeg": COMMENT_PREFIX + "JPEG",
    "tiff8": COMMENT_PREFIX + "TIFF 8-bit",
    "tiff16": COMMENT_PREFIX + "TIFF 16-bit",
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
EXIFTOOL_START_TIMEOUT = 120         # seconds for the `exiftool -ver` check every run starts with
EXIFTOOL_STAY_OPEN = True            # one exiftool per worker for the whole run
                                     # (a new process per call cost ~0.3 s, 3x per file)
READ_BATCH = 200                     # files per planning-time exiftool read
OPEN_AHEAD = 400                     # planning reads: a thread opens (and closes) each
                                     # file up to this many files before exiftool reads
                                     # it; 0 = off. Opening a cold file is what an
                                     # on-access antivirus scans (~19 ms a file on the
                                     # owner's HDD), and exiftool's own work is 5-11 ms
                                     # a file: opened ahead, the two overlap. Cold NEFs
                                     # 36.8 -> 23.4 ms a file, camera JPEGs 25.1 -> 21.6;
                                     # nothing changes when the antivirus already knows
                                     # the file (measured 2026-11-15)
WRITE_WORKERS = 1                    # files written at once (--workers). Measured on
                                     # one HDD, 40 real exports (6.7 GB): 1 worker 66 s,
                                     # 2 workers 74 s; reading 25 files ahead made no
                                     # difference -- the run already moves ~205 MB/s,
                                     # the disk's limit (2026-11-07)
PROGRESS_EVERY = 2                   # seconds between progress updates on a console
PROGRESS_EVERY_LOG = 60              # ... and when the output goes to a file (one line each)
# --max-rewrites: the most writes that change tags an earlier run wrote (a
# comment whose profile changed, a token an override replaces) before --apply
# refuses to write anything (EXIT_TOO_MANY_REWRITES). None = no limit. New
# exports never count. A scheduled run passes a limit: a changed PROFILE_*
# text or another exiftool would otherwise rewrite hundreds of exports in one
# night, and jxl-photo's sync would rebuild every one of their masters.
MAX_REWRITES = None
# One --apply at a time: a lock in the log folder, held by the running process
# (the OS drops it if the process dies, so nothing goes stale). The file stays;
# it only says who held it last.
LOCK_FILE = "photo_origin_tag.lock"
LOCK_BYTE = 1 << 20                  # the byte locked, past the text (a locked byte cannot be read)
TEMP_MARK = "~origin-tag-tmp"        # temporary files carry this in the name
# exiftool's own temporary, when it edits a file in place (a path with a %, see
# write_item): <name><suffix> next to the file, renamed over it when done.
EXIFTOOL_INPLACE_SUFFIX = "_exiftool_tmp"
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
# A JPEG with no Exif at all gets a new IFD0 when the comment is written, and
# exiftool fills it with these mandatory tags: not a change to the file's data.
JPEG_NEW_EXIF_TAGS = {"IFD0:XResolution", "IFD0:YResolution",
                      "IFD0:ResolutionUnit", "IFD0:YCbCrPositioning"}
# Exit codes (a scheduled task stops at EXIT_USAGE and above).
EXIT_OK = 0
EXIT_FILE_ERRORS = 1                 # some file errored and stayed as it was
EXIT_USAGE = 2                       # bad arguments or configuration, no exiftool,
                                     # another --apply running: nothing ran
EXIT_ABORTED = 3                     # the run was aborted (exception, Ctrl+C)
EXIT_TOO_MANY_REWRITES = 4           # more rewrites than --max-rewrites: nothing written
VERSION = "0.8.0"
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


# a -stay_open exiftool does not leave when its stdin closes: without this a
# run that ends any other way than main()'s finally leaves it behind
atexit.register(close_exiftools)


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
    _check_args(args)
    fd, argfile = tempfile.mkstemp(prefix="photo-origin-tag-", suffix=".args")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            for a in (*_ARG_PREFIX, *args):
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


def exiftool_version() -> str:
    """exiftool's version; UsageError when it cannot be run. Without this check
    a missing or broken exiftool reads as every export being unreadable: exit
    1, and a scheduled task would go on to make masters from untagged exports."""
    try:
        rc, so, se = run_exiftool(["-ver"], timeout=EXIFTOOL_START_TIMEOUT)
    except Exception as e:  # noqa: BLE001 - not found, died, timed out: all the same here
        raise UsageError(f"exiftool cannot be run ({EXIFTOOL}): "
                         f"{type(e).__name__}: {e}") from None
    ver = so.strip()
    if rc != 0 or not re.fullmatch(r"\d+(?:\.\d+)+", ver):
        raise UsageError(f"exiftool did not answer with its version ({EXIFTOOL}): "
                         f"{(se or so).strip()[:200] or 'no output'}")
    return ver


# ---------------------------------------------------------------------------
# One --apply at a time
# ---------------------------------------------------------------------------
def _lock_byte(fd):
    if os.name == "nt":
        import msvcrt
        os.lseek(fd, LOCK_BYTE, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)       # OSError when someone holds it
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_byte(fd):
    if os.name == "nt":
        import msvcrt
        os.lseek(fd, LOCK_BYTE, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_UN)


def acquire_run_lock():
    """Take the run lock (LOCK_FILE in the log folder) or raise UsageError when
    another process holds it. Returns what release_run_lock needs."""
    path = log_dir() / LOCK_FILE
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o666)
    except OSError as e:
        raise UsageError(f"cannot open the run lock {path}: {e}") from None
    try:
        _lock_byte(fd)
    except OSError:
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            holder = os.read(fd, 400).decode("utf-8", "replace").strip()
        except OSError:
            holder = ""
        os.close(fd)
        raise UsageError(
            f"another photo-origin-tag --apply is running ({holder or path}): two "
            f"at once compete for the disk and remove each other's temporary "
            f"files. Nothing was done.") from None
    try:
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, f"PID {os.getpid()}, since {_dt.datetime.now():%Y-%m-%d %H:%M:%S}"
                     .encode("utf-8"))
    except OSError:
        pass                                      # only the message would miss it
    return fd


def release_run_lock(fd):
    try:
        _unlock_byte(fd)
    except OSError:
        pass
    os.close(fd)


def _read_chunk(chunk, tags, extra):
    """{key(path): entry} for one exiftool call; raises when the process timed
    out or died, or its answer is not JSON."""
    rc, so, se = run_exiftool(["-j", "-G1", "-n", *extra, *tags,
                               *[str(p) for p in chunk]])
    data = json.loads(so) if so.strip() else []
    return {_key(e.get("SourceFile", "")): e for e in data}


def _open_and_close(path):
    """Open a file for reading and close it, reading nothing: an on-access
    antivirus scans it now, and its file record reaches the cache."""
    with open(path, "rb", buffering=0):
        pass


class _OpenAhead:
    """Open (and close) `paths` in order, in a thread of its own, at most
    `lead` files past the last one exiftool has answered, so that what a cold
    open costs (an antivirus scan, the file record) is paid while exiftool
    parses the files before it. It reads no data and changes no answer: a file
    it cannot open is left to exiftool, which reports it as before."""

    def __init__(self, paths, lead):
        self._paths, self._lead = paths, lead
        self._done, self._stop = 0, False
        self._cv = threading.Condition()
        self._thread = threading.Thread(target=self._run, name="open-ahead", daemon=True)
        self._thread.start()

    def _run(self):
        for i, path in enumerate(self._paths):
            with self._cv:
                while not self._stop and i >= self._done + self._lead:
                    self._cv.wait()
                if self._stop:
                    return
            try:
                _open_and_close(path)
            except Exception:  # noqa: BLE001 - exiftool meets the same file and says why
                pass

    def answered(self, n):
        """exiftool has answered the first n paths."""
        with self._cv:
            self._done = n
            self._cv.notify_all()

    def close(self):
        with self._cv:
            self._stop = True
            self._cv.notify_all()
        self._thread.join()


def read_tags(paths, tags, extra=(), progress=None):
    """{key(path): entry} for every path exiftool answered; a path missing
    from the result could not be read (the caller decides what that means).
    A batch that fails as a whole (timeout, exiftool died, an answer that is
    not JSON) is read again file by file, so one bad file only costs itself.
    The files are opened ahead of exiftool (OPEN_AHEAD) by a thread that
    stops when the read ends, however it ends."""
    out = {}
    paths = list(paths)
    ahead = _OpenAhead(paths, OPEN_AHEAD) if OPEN_AHEAD > 0 and len(paths) > 1 else None
    try:
        for i in range(0, len(paths), READ_BATCH):
            chunk = paths[i:i + READ_BATCH]
            try:
                out.update(_read_chunk(chunk, tags, extra))
            except (TimeoutError, OSError, ValueError):   # ValueError: bad JSON, newline
                if len(chunk) > 1:
                    for p in chunk:
                        try:
                            out.update(_read_chunk([p], tags, extra))
                        except (TimeoutError, OSError, ValueError):
                            pass
            if ahead is not None:
                ahead.answered(i + len(chunk))
            if progress:
                progress(min(i + READ_BATCH, len(paths)), len(paths))
    finally:
        if ahead is not None:
            ahead.close()
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


def _walk(root: Path):
    """(folder, sorted file names) for every folder below root, in a stable
    order, never entering SKIP_DIRS."""
    skip = {s.casefold() for s in SKIP_DIRS}
    for dp, dns, fns in os.walk(root):
        dns[:] = sorted(d for d in dns if d.casefold() not in skip)
        yield Path(dp), sorted(fns)


def clean_stale_temps(roots):
    """Delete the temporary copies a killed run left behind. Only files named
    <stem><TEMP_MARK>-<pid><ext>[EXIFTOOL_INPLACE_SUFFIX] whose original
    <stem><ext> still exists are removed (the original is never at risk: it
    is replaced atomically)."""
    pat = re.compile(r"^(?P<stem>.*)" + re.escape(TEMP_MARK)
                     + r"-\d+(?P<ext>\.[^.]+?)(?:" + re.escape(EXIFTOOL_INPLACE_SUFFIX)
                     + r")?$")
    removed = []
    for root in roots:
        root = Path(root)
        if root.is_file():
            continue
        for folder, names in _walk(root):
            for fn in names:
                m = pat.match(fn) if TEMP_MARK in fn else None
                if m and (folder / (m["stem"] + m["ext"])).exists():
                    try:
                        (folder / fn).unlink()
                        removed.append(folder / fn)
                    except OSError:
                        pass
    return removed


def _is_export_name(fn: str, below) -> bool:
    """A file named fn, in a folder whose names below the last EXPORT_FOLDER
    are `below` (None: no EXPORT_FOLDER above it), is an export to tag."""
    if below is None or TEMP_MARK in fn:
        return False
    ext = os.path.splitext(fn)[1].lower()
    return ext in EXPORT_EXTS or (ext in JPEG_EXTS and bool(below)
                                  and below[0].casefold() == JPG_ONLY_FOLDER.casefold())


def find_exports(roots):
    """Every export below the roots. A whole drive is millions of files: the
    names are filtered before any Path is made."""
    found, seen = [], set()

    def add(p):
        if _key(p) not in seen:
            seen.add(_key(p))
            found.append(p)
    for root in roots:
        root = Path(root)
        if root.is_file():
            if _is_export_name(root.name, _below_export(root)):
                add(root)
            continue
        for folder, names in _walk(root):
            below = _below_export(folder / "_")      # the same for every file in it
            if below is None:
                continue
            for fn in names:
                if _is_export_name(fn, below):
                    add(folder / fn)
    return found


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


class UsageError(Exception):
    """Bad arguments or configuration: the run does not start (EXIT_USAGE)."""


def load_overrides(path: Path):
    """overrides.csv: path,origin,note (UTF-8, header row). A path names a
    file or a folder; the most specific match wins. The same path twice is
    fine with the same origin (the first line stays) and an error otherwise."""
    if not path.exists():
        return []
    out, seen = [], {}
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            rd = csv.DictReader(f)
            names = rd.fieldnames or []
            missing = {"path", "origin"} - set(names)
            if missing:
                hint = ("; the separator must be a comma"
                        if any(";" in (h or "") for h in names) else "")
                raise UsageError(f"{path}: missing column(s) {sorted(missing)}{hint}")
            for n, row in enumerate(rd, start=2):
                p = (row.get("path") or "").strip()
                o = (row.get("origin") or "").strip().lower()
                if not p or p.startswith("#"):
                    continue
                if o not in _KINDS:
                    raise UsageError(f"{path}:{n}: origin {o!r} is not one of "
                                     f"{', '.join(_KINDS)}")
                k = _key(p)
                if k in seen:
                    first = seen[k]
                    if first.origin != o:
                        raise UsageError(
                            f"{path}: line {first.line} says {first.origin} and line "
                            f"{n} says {o} for the same path: {p}")
                    continue                      # same origin twice: the first stays
                seen[k] = Override(k, o, (row.get("note") or "").strip(), n)
                out.append(seen[k])
    except UnicodeDecodeError:
        raise UsageError(f'{path}: not UTF-8 (save it as "CSV UTF-8")') from None
    except csv.Error as e:
        raise UsageError(f"{path}: not a readable CSV ({e})") from None
    except OSError as e:                          # a folder, locked, no permission
        raise UsageError(f"{path}: cannot be read ({e})") from None
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


def _depth_review(bits) -> str:
    """Why an original TIFF's depth decides nothing: not read, or not 8/16."""
    b = " ".join(str(bits or "").split())
    if not b:
        return "cannot read the original TIFF's bit depth"
    return f"the original TIFF is neither 8- nor 16-bit ({b} bits per sample)"


def has_signal(meta: dict) -> bool:
    """The machine-readable RAW signal in an exiftool entry (-G1 keys)."""
    return (any(k.startswith("XMP-crd:") for k in meta)
            or "ExifIFD:SubjectDistance" in meta)


def classify(exts: dict, meta: dict, tiff_bits=None, override=None,
             jpeg_has_signal=False, trio_blue_raw=TRIO_BLUE_IS_RAW) -> Decision:
    """Decide one export.

    exts:            {ext: filename} of the original(s) beside the export (the
                     longest-prefix sibling), {} when none was found.
    meta:            the export's exiftool entry (-G1 keys).
    tiff_bits:       BitsPerSample of the original TIFF, when there is one.
    override:        an Override, which wins over everything.
    jpeg_has_signal: in a NEF+JPEG pair, the JPEG beside carries the RAW signal
                     too (NX Studio copies it), so the export's signal proves
                     nothing and only the label decides.
    trio_blue_raw:   a RAW+JPEG+TIFF trio with a Blue label counts as RAW
                     (TRIO_BLUE_IS_RAW, --trio-blue-raw)."""
    # sidecars (.xmp, ...) beside the original are not originals
    others = sorted(exts.values())
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
    signal = has_signal(meta)

    def review(why):
        return Decision(None, "", why, names)

    if not exts:
        if others:
            return review(f"no original found beside the export (only "
                          f"{', '.join(others)}, not a RAW/JPEG/TIFF)")
        return review("no original found beside the export")
    if raw and jpg and not tif:
        if all(e in SIGNAL_RAW_EXTS for e in raw):
            if label == LABEL_RAW and not signal:
                return review("label Blue but no RAW signal in a NEF+JPEG pair")
            if signal and jpeg_has_signal:
                # the signal is no proof: the JPEG beside has it too
                if label == LABEL_NOT_RAW:
                    return Decision("jpeg", "NEF+JPEG pair, the JPEG carries the "
                                    "signal too, label Green", original=names)
                return review("RAW signal, but the JPEG beside carries it too "
                              "(NX Studio?): needs a Blue or Green label")
            if label == LABEL_NOT_RAW and signal:
                return review("label Green but the export carries a RAW signal")
            if not signal and label != LABEL_NOT_RAW:
                # a missing signal proves nothing (Capture One's WEB recipe
                # drops it): only a Green label makes it a JPEG
                return review("NEF+JPEG pair without a RAW signal or a Green label")
            return Decision("raw" if signal else "jpeg",
                            "NEF+JPEG pair, RAW signal" if signal
                            else "NEF+JPEG pair, no RAW signal, label Green",
                            original=names)
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
                return review(_depth_review(tiff_bits))
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
            return review(_depth_review(tiff_bits))
        if label == LABEL_RAW and k == "tiff8":
            return review("label Blue but the original is an 8-bit TIFF")
        return Decision(k, "only a TIFF beside, TIFF depth", original=names)
    if raw and jpg and tif and trio_blue_raw:
        if label != LABEL_RAW:
            return review("RAW+JPEG+TIFF beside the export: only a Blue label "
                          "decides (Green cannot tell JPEG from TIFF)")
        if all(e in SIGNAL_RAW_EXTS for e in raw) and not signal:
            return review("label Blue but no RAW signal in a NEF+JPEG+TIFF trio")
        return Decision("raw", "RAW+JPEG+TIFF trio, label Blue", original=names)
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
    return PROFILE_NONE if not profile_problem(entry) else None


def profile_problem(entry) -> str:
    """Why exiftool's reading of an original cannot say "no profile", or ""
    when it read the file cleanly. Even a [minor] warning counts (decided
    08/11: "no profile" only of a file read cleanly); the report says which."""
    if entry is None:
        return "original unreadable"
    if entry.get("ExifTool:Error"):
        return f"original unreadable ({_clean_text(entry['ExifTool:Error'])[:80]})"
    ftype = str(entry.get("File:FileType", ""))
    if ftype.upper() not in (*JPEG_FILETYPES, *TIFF_FILETYPES):
        return f"original unreadable (File:FileType {ftype or 'missing'})"
    if entry.get("ExifTool:Warning"):
        return f"original read with a warning ({_clean_text(entry['ExifTool:Warning'])[:80]})"
    return ""


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
    rewrite: bool = False     # the write changes tags an earlier run wrote (--max-rewrites)


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
        if comment_kind(cur_c) is None:
            # not exactly a comment of this script (a "Source: ..." the owner
            # wrote is someone else's text too): never replaced, override or not
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
        # what an earlier run wrote and this write changes (--max-rewrites)
        changed = []
        if it.comment is not None and comment_kind(cur_c) is not None:
            changed.append(f"replaces {cur_c!r}")
        if it.relation_remove:
            changed.append(f"removes {', '.join(it.relation_remove)}")
        if changed:
            it.rewrite = True
            it.reason = "; ".join(changed)


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
    tmp_inplace = tmp.with_name(tmp.name + EXIFTOOL_INPLACE_SUFFIX)
    fingerprint = jpeg_fingerprint if src.suffix.lower() in JPEG_EXTS else image_fingerprint
    try:
        st0 = src.stat()
        fp0 = fingerprint(src)
        tags0 = all_tags(src)
        # exiftool reads %d %f %e %c in the name given to -o as format codes
        # (and %% is no escape), so a path with a % makes it create the copy
        # somewhere else. For those paths Python makes the copy and exiftool
        # edits it in place, which names no output file.
        via_copy = "%" in str(tmp)
        if via_copy:
            shutil.copyfile(src, tmp)
            args = ["-overwrite_original"]
        else:
            args = ["-o", str(tmp)]
        for t in it.relation_remove:
            args.append(f"-XMP-dc:Relation-={t}")
        if it.relation_add:
            args.append(f"-XMP-dc:Relation+={it.relation_add}")
        if it.comment is not None:
            args.append(f"-IFD0:XPComment={it.comment}")
        args.append(str(tmp) if via_copy else str(src))
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
            if not any(k.startswith(("IFD0:", "ExifIFD:")) for k in tags0):
                # no Exif at all: the new IFD0 comes with its mandatory tags
                allowed |= JPEG_NEW_EXIF_TAGS - set(tags0)
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
        for leftover in (tmp, tmp_inplace):
            if leftover.exists():
                try:
                    leftover.unlink()
                except OSError:
                    pass


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
class Progress:
    """N/total and the time left. On a console one line rewritten in place;
    into a file (a scheduled task's log) a plain line now and then."""

    def __init__(self, label):
        self.label, self.t0, self.last = label, time.time(), 0.0
        try:
            self.tty = sys.stdout.isatty()
        except (AttributeError, ValueError):
            self.tty = False

    def __call__(self, n, total, force=False):
        now = time.time()
        every = PROGRESS_EVERY if self.tty else PROGRESS_EVERY_LOG
        if not force and n < total and now - self.last < every:
            return
        self.last = now
        el = now - self.t0
        eta = (el / n * (total - n)) if n else 0
        line = (f"  {self.label}: {n}/{total}  "
                f"{int(el // 60)}:{int(el % 60):02d} elapsed, "
                f"~{int(eta // 60)}:{int(eta % 60):02d} left")
        if not self.tty:
            print(line, flush=True)
            return
        print(f"\r{line}   ", end="", flush=True)
        if n >= total:
            print()


def _unreadable_export(path: Path, entry):
    """Why an export cannot be planned, or "" when exiftool read it as the
    kind of file its extension says. For a file it cannot parse exiftool
    answers with an Error, or with another File:FileType, not with nothing."""
    if entry is None:
        return "no answer"
    if entry.get("ExifTool:Error"):
        return str(entry["ExifTool:Error"])[:80]
    expected = (JPEG_FILETYPES if path.suffix.lower() in JPEG_EXTS
                else TIFF_FILETYPES)
    ftype = str(entry.get("File:FileType", ""))
    if ftype.upper() not in expected:
        return f"File:FileType {ftype or 'missing'}, expected {'/'.join(expected)}"
    return ""


def _jpegs_with_signal(items, overrides):
    """{key(export path): bool} for the exports whose RAW signal needs a second
    opinion: NEF+JPEG pairs where the export carries the signal, there is no
    override and no label says Blue (a Blue label decides without it). True
    when a JPEG beside it carries the signal too. A JPEG exiftool cannot read
    counts as not carrying it."""
    suspects, jpegs = {}, {}
    for it, m, exts, tpath in items:
        if m is None or find_override(it.path, overrides) is not None:
            continue
        raw = [e for e in exts if e in RAW_EXTS]
        jpg = sorted(e for e in exts if e in JPEG_EXTS)
        if (raw and jpg and not any(e in TIFF_EXTS for e in exts)
                and all(e in SIGNAL_RAW_EXTS for e in raw)
                and has_signal(m) and it.label != LABEL_RAW):
            files = [export_base(it.path) / exts[e] for e in jpg]
            suspects[_key(it.path)] = [_key(f) for f in files]
            jpegs.update({_key(f): f for f in files})
    if not jpegs:
        return {}
    got = read_tags(list(jpegs.values()), SIGNAL_TAGS, extra=["-fast2"],
                    progress=Progress("reading JPEGs beside a RAW signal"))
    return {k: any(has_signal(got.get(j, {})) for j in js)
            for k, js in suspects.items()}


def build_items(exports, overrides, trio_blue_raw=TRIO_BLUE_IS_RAW):
    p = Progress("reading exports")
    meta = read_tags(exports, ["-XMP-xmp:Label", *SIGNAL_TAGS, "-XMP-dc:Relation",
                               "-IFD0:XPComment", "-IFD0:Model",
                               "-File:FileType", "-ExifTool:Error"],
                     extra=["-fast2"], progress=p)
    items, origs = [], {}
    for x in exports:
        it = Item(x)
        m = meta.get(_key(x))
        unreadable = _unreadable_export(x, m)
        if unreadable:
            it.action = "error"
            it.reason = f"exiftool could not read the export ({unreadable})"
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
    jpeg_signal = _jpegs_with_signal(items, overrides)
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
        it.decision = classify(exts, m, tb, ov,
                               jpeg_has_signal=jpeg_signal.get(_key(it.path), False),
                               trio_blue_raw=trio_blue_raw)
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
        if src is not None and it.profile is None and it.action in ("write", "ok"):
            final = (it.comment if it.comment is not None
                     else str(m.get("IFD0:XPComment", "") or ""))
            kept = final.startswith(COMMENTS[it.decision.kind] + PROFILE_SEP)
            note = (f"{profile_problem(orig_meta.get(_key(src)))}: "
                    + ("the profile an earlier run recorded is kept" if kept
                       else "its colour profile is not recorded"))
            it.reason = f"{it.reason}; {note}" if it.reason else note
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
    ap.add_argument("--trio-blue-raw", action="store_true",
                    default=TRIO_BLUE_IS_RAW,
                    help="a RAW+JPEG+TIFF trio beside the export with a Blue "
                         "label counts as RAW (default: review)")
    ap.add_argument("--max-rewrites", type=int, default=MAX_REWRITES, metavar="N",
                    help="with --apply, write nothing (exit 4) when more than N "
                         "writes would change tags an earlier run wrote; new "
                         "exports never count (default: no limit)")
    a = ap.parse_args(argv)
    if a.workers < 1:
        print("ERROR: --workers must be at least 1", file=sys.stderr)
        return EXIT_USAGE
    if a.max_rewrites is not None and a.max_rewrites < 0:
        print("ERROR: --max-rewrites must be 0 or more", file=sys.stderr)
        return EXIT_USAGE
    try:
        return _main(a)
    except UsageError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return EXIT_USAGE
    except KeyboardInterrupt:
        print("\nERROR: interrupted (Ctrl+C): the run was aborted", file=sys.stderr)
        return EXIT_ABORTED
    except Exception:  # noqa: BLE001 - the run is over either way: say so, exit 3
        traceback.print_exc()
        print("ERROR: the run was aborted by an unexpected error", file=sys.stderr)
        return EXIT_ABORTED
    finally:
        close_exiftools()


def _write_all(items, workers, stamp):
    """Write every item the plan marked `write`, logging each. If the run is
    cut short (Ctrl+C, any exception) the items not written keep `write` with
    a reason saying they were not attempted, and the exception goes on."""
    todo = [it for it in items if it.action == "write"]
    if not todo:
        return                                # no empty per-file log
    logf = log_dir() / f"{stamp}_written.log"
    logf.parent.mkdir(parents=True, exist_ok=True)
    p = Progress("writing")
    try:
        with open(logf, "a", encoding="utf-8") as lg:
            def done(n, it):
                lg.write(f"{_dt.datetime.now():%H:%M:%S}\t{it.action}\t"
                         f"{it.decision.kind}\t{it.reason}\t{it.path}\n")
                lg.flush()
                if it.action == "error":
                    print(f"\n  ERROR {it.path}: {it.reason}")
                p(n, len(todo))
            if workers == 1:
                for n, it in enumerate(todo, 1):
                    write_item(it)
                    done(n, it)
            else:
                from concurrent.futures import ThreadPoolExecutor, as_completed
                ex = ThreadPoolExecutor(max_workers=workers)
                try:
                    futs = {ex.submit(write_item, it): it for it in todo}
                    for n, f in enumerate(as_completed(futs), 1):
                        f.result()        # write_item reports its own errors
                        done(n, futs[f])
                except BaseException:
                    # queued files are dropped, the ones running finish
                    ex.shutdown(wait=True, cancel_futures=True)
                    raise
                ex.shutdown(wait=True)
        print(f"  per-file log: {logf}")
    except BaseException:
        for it in todo:
            if it.action == "write":
                it.reason = "not attempted: the run was interrupted"
        raise


def _write_report_somewhere(items, report: Path, stamp: str):
    """Write the report; the path written, or None. One that cannot go where
    asked (open in Excel, a folder that is gone) goes to the log folder: a
    report never turns a finished run into an aborted one."""
    try:
        write_report(items, report)
        return report
    except OSError as e:
        print(f"WARNING: the report could not be written to {report} ({e})")
    alt = log_dir() / f"{stamp}_report.csv"
    if _key(alt) == _key(report):
        alt = log_dir() / f"{stamp}_report_{os.getpid()}.csv"
    try:
        write_report(items, alt)
        print(f"WARNING: the report went to {alt} instead")
        return alt
    except OSError as e:
        print(f"ERROR: no report could be written ({e}); the summary above is all "
              f"there is", file=sys.stderr)
        return None


def _finish(items, overrides, report, apply, stamp):
    """The summary and the report, whatever happened to the run. The summary
    comes first: it is on the screen (or in the log) even when the report
    cannot be written."""
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
    rewrites = sum(1 for it in items if it.rewrite and it.action in ("write", "written"))
    if rewrites:
        print(f"\n{rewrites} of the writes change tags an earlier run wrote "
              f"(the report's reason says what each replaces)")
    rev = [it for it in items if it.action in ("review", "refuse", "error")]
    if rev:
        print(f"\n{len(rev)} file(s) need attention (all in the report):")
        for it in rev[:40]:
            print(f"  {it.action:<7} {it.reason} | {it.path}")
        if len(rev) > 40:
            print(f"  ... {len(rev) - 40} more")
    jpg_review = sum(1 for it in items
                     if it.action == "review" and is_jpg_only_export(it.path))
    if jpg_review:
        print(f"\n{jpg_review} JPEG(s) below {JPG_ONLY_FOLDER} stay without a "
              f"comment; once archived in {JPG_ARCHIVE_FOLDER} they cannot be tagged")
    print()
    written = _write_report_somewhere(items, report, stamp)
    if written is not None:
        print(f"Report: {written}")
    if not apply and any(it.action == "write" for it in items):
        print("Simulation: nothing was written. Run again with --apply.")


def _main(a):
    if a.apply:
        try:
            import tifffile  # noqa: F401 - the write check needs it
        except ImportError:
            raise UsageError("--apply needs the Python package tifffile "
                             "(pip install tifffile)") from None
    for r in a.roots:
        if not Path(r).exists():
            raise UsageError(f"not found: {r}")
    # a file named by hand that is not there is a typo, not "no overrides"
    # (the rules would then decide what the file existed to correct)
    if a.overrides and not Path(a.overrides).is_file():
        raise UsageError(f"--overrides: no such file: {a.overrides}")
    # absolute, so that the _EXPORT segment and the overrides' paths are seen
    # (abspath, not resolve(): resolve() follows junctions and turns a mapped
    # drive letter into a UNC path, and the overrides' G:\... would not match)
    roots = [os.path.abspath(r) for r in a.roots]
    ov_path = Path(a.overrides) if a.overrides else \
        Path(__file__).resolve().parent / OVERRIDES_FILE
    overrides = load_overrides(ov_path)
    ver = exiftool_version()
    lock = acquire_run_lock() if a.apply else None
    try:
        return _run(a, roots, ov_path, overrides, ver)
    finally:
        if lock is not None:
            release_run_lock(lock)


def _run(a, roots, ov_path, overrides, ver):
    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    report = Path(a.report) if a.report else log_dir() / f"{stamp}_report.csv"
    limit = a.max_rewrites
    print(f"photo-origin-tag {VERSION} | exiftool {ver}"
          f" | {'APPLY' if a.apply else 'SIMULATION'}"
          f" | overrides: {len(overrides)} ({ov_path})"
          f"{' | trio Blue = RAW' if a.trio_blue_raw else ''}"
          f"{f' | max rewrites {limit}' if limit is not None else ''}")
    if a.apply:
        for t in clean_stale_temps(roots):
            print(f"  removed a temporary file left by an interrupted run: {t}")
    print("  finding exports ...", flush=True)
    exports = find_exports(roots)
    print(f"  {len(exports)} export(s) below {EXPORT_FOLDER}")
    items = build_items(exports, overrides, trio_blue_raw=a.trio_blue_raw)

    rewrites = sum(1 for it in items if it.action == "write" and it.rewrite)
    held = limit is not None and rewrites > limit
    try:
        if a.apply and held:
            for it in items:
                if it.action == "write":
                    note = f"not written: {rewrites} rewrites, more than --max-rewrites {limit}"
                    it.reason = f"{it.reason}; {note}" if it.reason else note
        elif a.apply:
            _write_all(items, a.workers, stamp)
    finally:
        _finish(items, overrides, report, a.apply, stamp)
    if held:
        msg = (f"{rewrites} planned write(s) change tags an earlier run wrote, "
               f"more than --max-rewrites {limit}")
        if a.apply:
            print(f"ERROR: {msg}: nothing was written. Check what each one "
                  f"replaces (the report's reason), then run without "
                  f"--max-rewrites, after a backup: the masters made from those "
                  f"exports will be rebuilt.", file=sys.stderr)
            return EXIT_TOO_MANY_REWRITES
        print(f"WARNING: {msg}: with --apply and that limit nothing would be written.")
    return EXIT_FILE_ERRORS if any(it.action == "error" for it in items) else EXIT_OK


if __name__ == "__main__":
    # UTF-8 on both streams: a scheduled task sends them to one log file
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    sys.exit(main())
