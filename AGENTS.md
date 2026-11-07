# photo-origin-tag — agent notes

Owner's tool, private. Tags Capture One TIFF exports with their origin before
jxl-photo encodes them. Read SPEC.md (decisions) and README.md (use).

## Decisions taken (07/11/2026, by the owner)
- External tool, NOT inside jxl-photo: the rules are a heuristic tied to the
  owner's workflow, and jxl-photo's rule is "an unknown origin is never
  guessed". jxl-photo needs no change: its encoder inherits a
  `jxlphoto-origin` already in the TIFF (`_origin_for_output`, test `test_r2a`)
  and copies the EXIF `XPComment` into the master's Exif box.
- Write ONLY the TIFF exports, then rebuild the masters and the copies. Never
  edit existing masters.
- Comment text in English: `Source: RAW` / `JPEG` / `TIFF 8-bit` / `TIFF 16-bit`.
- `jxlphoto-origin` only for `jpeg` and `tiff8` (the encoder writes `tiff16`
  itself; jxl-photo has no `raw` value). Never add another `jxlphoto-*` token:
  the namespace belongs to jxl-photo.
- C1 labels: Blue = from RAW, Green = from JPEG/TIFF, Pink = rejected (still
  RAW when the machine says so).
- Do NOT use the origin to pick a bigger distance for JPEG-born photos (owner:
  small saving, not worth it for now).
- jxl-photo's recompressor counts `jxlphoto-origin:jpeg` as a hidden lossy
  generation (`--on-regeneration`). SRGB already runs with `convert`; JPG
  MOBILE is the transcoder (no such guard).

## Rules for changes
- Settings live at the top of `photo_origin_tag.py`; never repeat them as
  literals.
- exiftool only through `run_exiftool` (UTF-8 argfile, WindowsLongPath).
- Every file test uses a hostile path (Japanese + `[ ]` + space + `%`).
- A write goes through `write_item`: exiftool `-o` temp copy in the same
  folder → image bytes + ICC + every other tag checked → unchanged-since check
  → `os.replace`. Anything that differs is an error; the original stays.
- Never edit `G:` in tests. Test on copies (scratch folder or `E:\TESTE`).
- Commits: the owner's authorship only, no Co-Authored-By / session trailers.
- On the owner's machine `py` is 3.13; `python`/`py -3.12` has pytest.

## Speed (v0.2.0, measured 07/11/2026)
- One persistent exiftool per worker (`ExifToolProcess`, `-stay_open`, UTF-8
  args on stdin, `-echo4` marker for stderr). 40 real exports, 6.7 GB, HDD D:,
  unbuffered copies (robocopy /J) + write-cache flush in the timing: v0.1.0
  87 s, v0.2.0 66 s; `--workers 2` 74 s; reading 25 files ahead (tried and
  removed) 66 s = no gain. The run moves ~205 MB/s = the HDD's limit. Do not
  add staging/batching/parallelism for one HDD without a new measurement.

## State (07/11/2026)
- v0.2.0 (stay_open exiftool, --workers), 29 tests. v0.1.0 committed (tag v0.1.0). Simulation on G:\2024-2026: 7154 exports → jpeg 1971,
  tiff8 7, tiff16 965, raw 4208, review 3 (JPG+TIF with the same name, listed
  in overrides.csv as comments). Applied to 20 real copies (every rule/year):
  pixels, ICC and Deflate unchanged; only XPComment, dc:Relation,
  XMPToolkit and strip offsets differ; jxl-photo's master inherits both tags.
- NOT yet applied to G:. The owner runs `presets_LOCAL\JXL 9 - ORIGEM +
  MASTERS + COPIAS.bat` after a backup (~1 TB of exports to rewrite, ~3 h,
  then the master/copy re-encodes).
