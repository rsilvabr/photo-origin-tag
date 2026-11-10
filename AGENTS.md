# photo-origin-tag — agent notes

Owner's tool, private. Tags Capture One TIFF exports with their origin before
jxl-photo encodes them. Read SPEC.md (decisions) and README.md (use).

## Decisions taken (07/11/2026, by the owner)
- External tool, NOT inside jxl-photo: the rules are a heuristic tied to the
  owner's workflow, and jxl-photo's rule is "an unknown origin is never
  guessed". jxl-photo needs no change: its encoder inherits a
  `jxlphoto-origin` already in the TIFF (`_origin_for_output`, test `test_r2a`)
  and copies the EXIF `XPComment` into the master's Exif box.
- Write the TIFF exports (and, since 09/11, the JPG_only JPEGs), then rebuild
  the masters and the copies. Never edit existing masters.
- Comment text in English: `Source: RAW` / `JPEG` / `TIFF 8-bit` / `TIFF 16-bit`.
- (08/11) The comment also names the ORIGINAL's colour profile for JPEG/TIFF
  originals (`Source: JPEG, Adobe RGB (1998)`): the ICC description, else the
  EXIF ColorSpace (`sRGB (EXIF, no ICC)` / `Adobe RGB (EXIF, no ICC)`), else
  `no color profile`, only the NAME (never the base64 ICC). Not for RAW (the
  export carries C1's profile). In XPComment only: jxl-photo never reads it.
  "No profile" is said only of a file exiftool read cleanly (`profile_label`);
  same source type + another profile = a fact, rewritten freely; another
  source type still needs an override.
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

- (08/11) `pastas_exportar_tiff.py` lives here too (moved from jxl-photo's
  gitignored `AI_tools/`): the "which exports still lack a TIFF" checklist.
  Standard library only, reads the disk only; output + checks in `Logs\`.
  `_EXPORT\JPG_only` = no TIFF possible, accepted (section 7).
- (09/11) JPEGs below `_EXPORT\JPG_only` get the XPComment only, never XMP or
  a `jxlphoto-*` token, and only BEFORE their archive exists in
  `_EXPORT\JXL_jpeg` (`jpg_archive_path`; tagging later would break the match
  with what the archive gives back). `write_item` checks every byte outside
  the Exif segment (`jpeg_fingerprint`). A missing RAW signal proves nothing
  in a JPEG export (the WEB recipe drops `XMP-crd`): a NEF+JPEG pair without
  it needs a Green label, else review.

## Decisions taken (11/11/2026, v0.5.0, after the Fable audit)
- A path with `%`: Python copies the file and exiftool edits the copy in place
  (`-overwrite_original`); without `%` it stays `-o`, the measured route of
  every real file. `%%` is no escape in exiftool 13.59, and `-o %d%f...%e`
  breaks on a folder like `a%c`. See `write_item`.
- A NEF+JPEG pair without the RAW signal counts as JPEG only with a Green
  label, in TIFF exports too (the 752 real cases all have it). No new colour.
- `XMP-crd` does not come only from the NEF: NX Studio's JPEG has it (14 real
  cases). If the JPEG beside carries the signal, the export's signal proves
  nothing and the label decides (`jpeg_has_signal`, `_jpegs_with_signal`).
- Exit codes: 0 ok, 1 a file errored, 2 usage/config, 3 aborted (the report is
  written anyway). `UsageError` is the way to exit 2.
- Roots become absolute with `os.path.abspath`, NOT `Path.resolve()` (it
  follows junctions and swaps a mapped letter for UNC; the overrides' `G:\...`
  would stop matching).
- A comment that is not exactly one of this script's is someone else's, even
  if it starts with `Source: `: refused always, override or not.
- The 132 JPEGs of `JPG_only` in review stay without a comment (they were all
  archived on 10/11).
- (v0.6.0) Plan item A11 is an OPTION, `--trio-blue-raw` (`TRIO_BLUE_IS_RAW`,
  off in the script): a RAW+JPEG+TIFF trio with a Blue label → RAW (a NEF trio
  also needs the signal); any other label → review. The owner's presets
  (scheduled task and `JXL 9`) pass it.
- Part C: the scheduled task (`jxl-photo_LOCAL\run_scheduled_presets.cmd`)
  runs the tagger before SYNC PHOTOS and stops the day at exit code >= 2;
  `JXL 9` stops after its step 2 at >= 2.

## Rules for changes
- Settings live at the top of `photo_origin_tag.py`; never repeat them as
  literals.
- exiftool only through `run_exiftool` (UTF-8 argfile, WindowsLongPath).
- Every file test uses a hostile path (Japanese + `[ ]` + space + `%`). The
  `library` and `base` fixtures run twice, on `HOSTILE` and on
  `HOSTILE_NO_PERCENT` (the same without the `%`): a path with `%` takes the
  copy route of `write_item`, one without it takes `-o`, and the second is
  the route of every real file.
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

## State (11/11/2026)
- v0.6.0: `--trio-blue-raw` (A11 as an option), 146 tests. Simulation on
  G:\2024-2026 with it (read only): 8774 exports, 18 `write raw` (all `DSCF`
  trios, Blue), 6 TIFFs + 132 `JPG_only` JPEGs in review, 0 `error`. The 18
  get written by the next scheduled run (Part C), not by this session.
- v0.5.0 (audit fixes, Parts A and B of
  `AI_tools/261111_Fable_plan_audit-fixes.md`): 136 tests (63 before). The
  audit, the plan and the coder's reports are in `AI_tools/`. Reviewed on
  11/11 with a simulation on G:\2024-2026 (read only): 8774 exports, 0 `write`,
  0 `error`; every one of the 8428 TIFF rows has the action and origin of the
  08/11 report, the 346 `JPG_only` JPEGs are 214 `ok` + 132 `review`. Nothing
  was written to G: by this work.
- Since 10/11 all 346 JPEGs of `JPG_only` have their archive in `JXL_jpeg`
  (214 with the comment, 132 in review and now refused as "already archived").

## State (08/11/2026)
- v0.3.0: the original's colour profile in the comment, 36 tests. Simulation
  on G:\2026 (1434 exports, classification unchanged): JPEG originals mostly
  WITHOUT ICC (EXIF sRGB 77, EXIF Adobe 26 from Nikon D810/Zf/Z7/Z8), Xiaomi
  `sRGB EOTF with DCI-P3 Color Gamut` 74, 4 with nothing (WhatsApp/scan);
  TIFF originals: Elle V2/V4 g22 150, Nikon Adobe RGB 7, ProPhoto RGB 1.
- v0.2.0 (stay_open exiftool, --workers), 29 tests. v0.1.0 committed (tag v0.1.0). Simulation on G:\2024-2026: 7154 exports → jpeg 1971,
  tiff8 7, tiff16 965, raw 4208, review 3 (JPG+TIF with the same name, listed
  in overrides.csv as comments). Applied to 20 real copies (every rule/year):
  pixels, ICC and Deflate unchanged; only XPComment, dc:Relation,
  XMPToolkit and strip offsets differ; jxl-photo's master inherits both tags.
- Applied to the TIFFs of G:\2024-2026 on 08-09/11 (exit 0). v0.4.0 (JPG_only
  JPEGs, 63 tests) applied to G:'s 9 JPG_only folders on 09/11, before any
  archive existed: 346 JPEGs → 214 tagged (raw 162, jpeg 50, tiff16 2), 132
  left for review; an independent hash found no byte changed outside Exif.

## Pending list
The owner keeps one list of everything still pending, across all repos, in
`../PENDENCIAS.md` (the folder above this repo, outside git, in Portuguese).
Read it when asked what is left to do. When work here finishes an item, leaves
something for later, or waits on a decision from the owner, update that file in
the same session: remove what is done, add what is new. It is private: never
copy its contents into tracked files, commits or release notes.
