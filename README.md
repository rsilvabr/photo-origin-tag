# photo-origin-tag

Records in each Capture One TIFF export **what the photo came from** (RAW,
JPEG, 8-bit TIFF or 16-bit TIFF) before [jxl-photo](../jxl-photo) turns the
export into a JXL master. jxl-photo carries both tags into the master by
itself.

Private: the detection rules depend on one workflow (Capture One, NX Studio,
OM Workspace, Fujifilm's converter, the `<camera>\_EXPORT\TIFF16` layout).

## What it writes

| Origin | EXIF `XPComment` (Explorer: "Comments") | XMP `dc:Relation` |
|---|---|---|
| RAW | `Source: RAW` | nothing (jxl-photo writes `jxlphoto-origin:tiff16`) |
| 16-bit TIFF | `Source: TIFF 16-bit, <profile>` | nothing (same) |
| 8-bit TIFF | `Source: TIFF 8-bit, <profile>` | `jxlphoto-origin:tiff8` |
| JPEG | `Source: JPEG, <profile>` | `jxlphoto-origin:jpeg` |

`<profile>` is the colour profile of the ORIGINAL JPEG/TIFF (the export's own
profile is the one Capture One chose): its ICC profile's name
(`Adobe RGB (1998)`, `Nikon Adobe RGB 4.0.0.3001`, `LargeRGB-elle-V4-g22.icc`),
or, when the original has no ICC, its EXIF colour space: `sRGB (EXIF, no ICC)`
or `Adobe RGB (EXIF, no ICC)` (ColorSpace 2, or Uncalibrated + DCF `R03`, how
many cameras mark an Adobe RGB JPEG). `no color profile` when the original has
neither; nothing after the type when the original could not be read (the
report says so, and a later run keeps a profile recorded earlier). A RAW gets
no profile. A comment of an earlier version (`Source: JPEG`) gains the profile
on the next run, without an override; another source type still needs one.

Nothing else changes: the image bytes, the ICC profile and every other tag are
checked before and after each write. The file's date does change, on purpose:
jxl-photo's sync then re-encodes the master.

## How it decides

1. The original beside the export decides: the file in `<base>` whose name is
   the longest prefix of the export's name (`DSC_1211_NXstudio_ProPhoto-g22_v1.tif`
   → `DSC_1211_NXstudio.*`). Only a RAW → RAW, only a JPEG → JPEG, only a
   TIFF → its bit depth.
2. NEF+JPEG pair: `XMP-crd` or `ExifIFD:SubjectDistance` in the export (they
   only come from the NEF) → RAW, otherwise JPEG.
3. Other RAW+JPEG pairs (Fuji RAF) and RAW+TIFF pairs (Olympus ORF + OM
   Workspace TIFF): the Capture One label decides (Blue = RAW, Green = JPEG or
   the TIFF's depth).
4. `overrides.csv` wins over everything.
5. It never guesses: no original, an unexpected mix, or a label that
   contradicts the file → left alone and listed for review.

## Use

```
py photo_origin_tag.py G:\2024 G:\2025 G:\2026            # simulation
py photo_origin_tag.py G:\2024 G:\2025 G:\2026 --apply    # write
```

The report (CSV, with a `profile` column) and the per-file log land in
`Logs\`; the summary also counts the originals per colour profile. A second run changes
nothing: an export that already carries the right tags is reported `ok` and not
rewritten, so a re-run only reads the headers (~3 min for 7 000 exports).

## Speed

One exiftool stays open for the whole run (`-stay_open`); exiftool is never
started per file. Writing rewrites each TIFF once, so the run is bound by the
disk: measured on one HDD with 40 real exports (6.7 GB), 66 s -- about 205 MB/s
of reads plus writes, the drive's limit (v0.1.0, three exiftool processes per
file: 87 s). `--workers 2` was slower on that disk (74 s), and reading 25 files
ahead before writing them made no difference, so the default stays at one.
Estimate for 1.07 TB of exports on that drive: about 3 hours. `presets_LOCAL\JXL 9 - ORIGEM + MASTERS + COPIAS.bat` runs the
simulation, asks, tags, then SYNC PHOTOS, JPG ONLY, SRGB and JPG MOBILE.

Refusals (never overwritten): a different `jxlphoto-origin` or a `Source:`
comment the rules disagree with (an override replaces those), any other
`XPComment` text (never), a TIFF carrying jxl-photo markers (a decoded master,
not an export), a file that changed while it was being tagged.

## What is still missing a TIFF: `pastas_exportar_tiff.py`

The step before the tags: which camera folders still lack the TIFF export a
master needs. It walks every `*EXPORT*` folder in `G:\2024-2026`, matches the
exported JPGs to the TIFFs by photo (the name without profile, version or
camera), and opens a checklist in the browser: each row a camera folder with
something missing, in 6 categories, a click opens it in Explorer, the checks
persist between scans.

```
py pastas_exportar_tiff.py            # scan + checklist page (Ctrl+C ends it)
py pastas_exportar_tiff.py --no-web   # scan + the .md list only
```

Shortcut: `presets_LOCAL\EXPORT - FALTA TIFF.bat`. It only reads the disk.
The list (`<YYMMDD>_pastas_para_exportar_tiff.md`) and the checks
(`pastas_exportar_tiff.state.json`) land in `Logs\`.

Photos whose TIFF can no longer be exported (the Capture One edit is lost)
go in `<camera>\_EXPORT\JPG_only`, loose or in subfolders: they are listed
apart, in section 7 ("accepted"), without a checkbox, and the same JPG
anywhere else in the event no longer counts as missing. jxl-photo's
"JPG ONLY" preset archives them losslessly into `_EXPORT\JXL_jpeg`. Tag them
BEFORE that (next section).

## JPEG exports in `_EXPORT\JPG_only`

The same run also finds these JPEGs and writes the `XPComment` into them, and
nothing else: no `jxlphoto-origin`, no XMP. jxl-photo's lossless archive
carries the comment and still gives the tagged JPEG back bit for bit.

- Every byte outside the Exif segment (XMP, ICC, tables, the image, anything
  after it) is checked unchanged before the temporary copy replaces the JPEG.
- A JPEG that already has an archive in `_EXPORT\JXL_jpeg` is refused: tagging
  it would make it differ from what that archive gives back.
- A JPEG carrying any `jxlphoto-*` marker is skipped (jxl-photo wrote it).
- NEF+JPEG pair: a JPEG export without the RAW signal needs a Green label to
  count as JPEG. Capture One's WEB recipe drops `XMP-crd` and sometimes all the
  EXIF, so a missing signal proves nothing there: without the label it goes to
  review and stays without a comment (`overrides.csv` settles it).

## Tests

```
python -m pytest tests -q
```

Real exiftool, every file test in a folder with Japanese, `[ ]`, a space and
`%`, plus a path over 260 characters; the end-to-end test runs jxl-photo's
encoder (beside this repo) on a tagged export.
