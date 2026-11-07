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
| 16-bit TIFF | `Source: TIFF 16-bit` | nothing (same) |
| 8-bit TIFF | `Source: TIFF 8-bit` | `jxlphoto-origin:tiff8` |
| JPEG | `Source: JPEG` | `jxlphoto-origin:jpeg` |

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

The report (CSV) and the per-file log land in `Logs\`. A second run changes
nothing. `presets_LOCAL\JXL 9 - ORIGEM + MASTERS + COPIAS.bat` runs the
simulation, asks, tags, then SYNC PHOTOS, SRGB and JPG MOBILE.

Refusals (never overwritten): a different `jxlphoto-origin` or a `Source:`
comment the rules disagree with (an override replaces those), any other
`XPComment` text (never), a TIFF carrying jxl-photo markers (a decoded master,
not an export), a file that changed while it was being tagged.

## Tests

```
python -m pytest tests -q
```

Real exiftool, every file test in a folder with Japanese, `[ ]`, a space and
`%`, plus a path over 260 characters; the end-to-end test runs jxl-photo's
encoder (beside this repo) on a tagged export.
