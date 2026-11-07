#!/usr/bin/env python3
"""photo-origin-tag: record in each Capture One TIFF export what the photo
came from (RAW, JPEG, 8-bit TIFF or 16-bit TIFF), before jxl-photo turns the
export into a master.

What is written (see SPEC.md):
  * EXIF IFD0 XPComment  "Source: RAW" / "Source: JPEG" / "Source: TIFF 8-bit"
    / "Source: TIFF 16-bit" (Windows Explorer shows it as "Comments");
  * XMP dc:Relation      jxlphoto-origin:jpeg or jxlphoto-origin:tiff8, only
    where it differs from what jxl-photo's encoder would write by itself
    (tiff16). The encoder inherits a jxlphoto-origin already in its input.

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
import time
from dataclasses import dataclass, field
from pathlib import Path

# ============================================================================
# SETTINGS (edit here)
# ============================================================================
EXPORT_FOLDER = "_EXPORT"            # exports live below <base>\_EXPORT\...
EXPORT_EXTS = (".tif", ".tiff")
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
# jxlphoto-origin value per kind; None = leave it to jxl-photo's encoder,
# which writes tiff16 for a 16-bit TIFF by itself.
ORIGIN_TOKENS = {"raw": None, "tiff16": None, "jpeg": "jpeg", "tiff8": "tiff8"}
ORIGIN_PREFIX = "jxlphoto-origin:"
TOOLKIT_NAMESPACE = "jxlphoto-"
OVERRIDES_FILE = "overrides.csv"     # next to this script unless --overrides
LOG_DIR = "Logs"                     # next to this script; env PHOTO_ORIGIN_TAG_LOG_DIR
EXIFTOOL = "exiftool"
EXIFTOOL_TIMEOUT = 600               # seconds, per call
READ_BATCH = 200                     # files per planning-time exiftool read
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
VERSION = "0.1.0"
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
def run_exiftool(args, timeout=EXIFTOOL_TIMEOUT):
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
            if (p.suffix.lower() in EXPORT_EXTS and TEMP_MARK not in p.name
                    and export_base(p) is not None):
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


def classify(exts: dict, meta: dict, tiff_bits=None, override=None) -> Decision:
    """Decide one export.

    exts:      {ext: filename} of the original(s) beside the export (the
               longest-prefix sibling), {} when none was found.
    meta:      the export's exiftool entry (-G1 keys).
    tiff_bits: BitsPerSample of the original TIFF, when there is one.
    override:  an Override, which wins over everything."""
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


def plan_item(it: Item, meta: dict, overridden: bool):
    """Fill it.action and what to write, from the export's current tags."""
    d = it.decision
    if d.kind is None:
        it.action, it.reason = "review", d.review
        return
    rel = _as_list(meta.get("XMP-dc:Relation"))
    tokens_other = [t for t in rel if t.startswith(TOOLKIT_NAMESPACE)
                    and not t.startswith(ORIGIN_PREFIX)]
    if tokens_other:
        it.action = "skip"
        it.reason = (f"carries jxl-photo marker(s) {tokens_other[:2]}: "
                     f"not a Capture One export")
        return
    want_tok = ORIGIN_TOKENS[d.kind]
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
    want_c = COMMENTS[d.kind]
    cur_c = str(meta.get("IFD0:XPComment", "") or "")
    if cur_c.strip() and cur_c != want_c:
        if not cur_c.startswith(COMMENT_PREFIX):
            it.action = "refuse"
            it.reason = f"XPComment already holds someone else's text: {cur_c[:60]!r}"
            return
        if not overridden:
            it.action = "refuse"
            it.reason = (f"XPComment says {cur_c!r}, rules say {want_c!r}: "
                         f"only an override replaces it")
            return
    if cur_c != want_c:
        it.comment = want_c
    if it.comment is None and it.relation_add is None and not it.relation_remove:
        it.action, it.reason = "ok", "already tagged"
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
    try:
        st0 = src.stat()
        fp0 = image_fingerprint(src)
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
        if image_fingerprint(tmp) != fp0:
            raise OSError("image data or ICC profile changed in the rewrite")
        tags1 = all_tags(tmp)
        diff = tag_differences(tags0, tags1, REWRITE_VOLATILE
                               | {"XMP-dc:Relation", "IFD0:XPComment"})
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
        it.action, it.reason = "written", ""
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
    bits = read_tags(list(origs.values()), ["-IFD0:BitsPerSample"],
                     extra=["-fast2"], progress=p2) if origs else {}
    for it, m, exts, tpath in items:
        if m is None:
            continue
        ov = find_override(it.path, overrides)
        if ov is not None:
            ov.used += 1
        tb = None
        if tpath is not None:
            e = bits.get(_key(tpath))
            tb = e.get("IFD0:BitsPerSample") if e else None
        it.decision = classify(exts, m, tb, ov)
        plan_item(it, m, ov is not None)
    return [t[0] for t in items]


def write_report(items, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["action", "origin", "decided_by", "reason", "label",
                    "model", "original", "path"])
        for it in items:
            w.writerow([it.action, it.decision.kind or "", it.decision.by,
                        it.reason, it.label, it.model, it.decision.original,
                        str(it.path)])


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
    a = ap.parse_args(argv)

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
            for n, it in enumerate(todo, 1):
                write_item(it)
                lg.write(f"{_dt.datetime.now():%H:%M:%S}\t{it.action}\t"
                         f"{it.decision.kind}\t{it.reason}\t{it.path}\n")
                lg.flush()
                if it.action == "error":
                    print(f"\n  ERROR {it.path}: {it.reason}")
                p(n, len(todo))
        print(f"  per-file log: {logf}")

    write_report(items, report)
    from collections import Counter
    c = Counter((it.action, it.decision.kind or "-") for it in items)
    print("\nSummary (action, origin): count")
    for (act, kind), n in sorted(c.items()):
        print(f"  {act:<8} {kind:<7} {n}")
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
