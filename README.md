# photo-origin-tag — record what each export was made from

A 16-bit TIFF exported from Capture One looks the same whether it was developed from a RAW, from a camera JPEG or from an 8-bit scan. Once the TIFF becomes the photo's archive master (a JPEG XL made by [jxl-photo](https://github.com/rsilvabr/jxl-photo), say) and the original is archived or gone, nothing in the file says which it was. photo-origin-tag looks at the originals beside each export and writes the answer into the export itself, as a comment Windows Explorer shows:

```
Comments: Source: RAW
Comments: Source: JPEG, Adobe RGB (1998)
Comments: Source: TIFF 8-bit, sRGB (EXIF, no ICC)
```

Nothing else in the file changes: the image bytes, the colour profile and every other tag are compared before and after each write. It never guesses: an export it cannot decide is left alone and listed for you. The default run is a simulation. Built for, and run on, an archive of 8 774 Capture One exports (2024–2026) of Nikon, Fujifilm and Olympus RAWs, camera and phone JPEGs, and NX Studio and OM Workspace TIFFs.

---

## Contents

- [Why record the origin?](#why-record-the-origin) · [Does it fit your archive?](#does-it-fit-your-archive)
- [What it writes](#what-it-writes)
- [How it decides](#how-it-decides) · [Capture One colour labels](#capture-one-colour-labels)
- [Requirements & installation](#requirements--installation)
- [Quick start](#quick-start)
- [Reading the report](#reading-the-report) · [overrides.csv](#overridescsv)
- [Safety guarantees](#safety-guarantees)
- [JPEG-only exports (`_EXPORT\JPG_only`)](#jpeg-only-exports-_exportjpg_only)
- [Every option](#every-option) · [Settings](#settings) · [Exit codes](#exit-codes) · [Logs](#logs)
- [Speed](#speed) · [Running it on a schedule](#running-it-on-a-schedule)
- [The other script: `pastas_exportar_tiff.py`](#the-other-script-pastas_exportar_tiffpy)
- [Tests](#tests) · [Changelog](#changelog)
- [Related projects](#related-projects) · [Disclaimer](#disclaimer) · [License](#license)

---

## Why record the origin?

- **The quality ceiling.** A master developed from a RAW and one developed from a JPEG are both 16-bit TIFFs, but the second carries the JPEG's 8 bits and its compression. Months later, from the master alone, you can no longer tell them apart.
- **The original's colour space.** A JPEG in Adobe RGB and one in sRGB end up in the same ProPhoto export. The comment keeps the name of the original's profile (never the profile itself).
- **Tools downstream can use it.** jxl-photo reads the origin it finds in the TIFF and carries it into the master. Its recompressor then knows that a JPEG-born master already went through one lossy step (`--on-regeneration`). Without jxl-photo, the comment alone is still useful: it travels with the file and shows in Explorer's "Comments" column and in any EXIF viewer.

## Does it fit your archive?

It was made for one workflow and decides from the folder layout, so check this first. It fits when:

- **the exports live in a folder named `_EXPORT`** (any case; the name is a [setting](#settings)), with subfolders inside it if you like (`_EXPORT\TIFF16`, `_EXPORT\16bit\ProPhoto`);
- **that `_EXPORT` folder sits directly in the folder that holds the originals**:

  ```
  D:\Photos\2025\250412_Park\              the originals
  ├── DSC_0001.NEF
  ├── DSC_0001.JPG                          a RAW+JPEG pair from the camera
  ├── IMG_0002.JPG                          a phone photo
  ├── SCAN_0003.tif                         an 8-bit scan
  └── _EXPORT\TIFF16\
      ├── DSC_0001_ProPhoto_v1.tif          → Source: RAW (or JPEG: see below)
      ├── IMG_0002_ProPhoto_v1.tif          → Source: JPEG, <its profile>
      └── SCAN_0003_ProPhoto_v1.tif         → Source: TIFF 8-bit, <its profile>
  ```

- **an export's name starts with its original's name, followed by `_` or nothing**: `DSC_0001.NEF` → `DSC_0001_ProPhoto_v1.tif` or `DSC_0001.tif`. The longest matching name wins, so `DSC_0001_NXstudio.tif` (a TIFF developed beside the RAW) and its export `DSC_0001_NXstudio_ProPhoto_v1.tif` are matched to each other, not to the NEF;
- **the exports are TIFFs**, plus, if you have them, JPEG exports in `_EXPORT\JPG_only` ([below](#jpeg-only-exports-_exportjpg_only)).

It falls short when:

- **exports are renamed** (`Park_web_01.tif`) or use another separator (`DSC_0001-2.tif`, `DSC_0001 web.tif`): no original is found, and the export is listed for review;
- **exports are not inside the originals' folder** (one `Pictures\Exports\` for everything);
- **a RAW and a JPEG of the same photo sit side by side** and the RAW is not a Nikon NEF: the file cannot say which one was developed, so the tool needs a [Capture One colour label](#capture-one-colour-labels). This is the one place the tool depends on a habit of yours;
- **the exports are JPEGs, PNGs or HEICs** outside `JPG_only`: only TIFFs are read.

It was tested with Capture One exports only. Each rule is a fact the tool reads from your files except the label rule, so try a simulation on one folder before deciding: it writes nothing and lists every export it could not decide.

## What it writes

| Origin | EXIF `XPComment` (Explorer: "Comments") | XMP `dc:Relation` |
|---|---|---|
| RAW | `Source: RAW` | nothing |
| 16-bit TIFF | `Source: TIFF 16-bit, <profile>` | nothing |
| 8-bit TIFF | `Source: TIFF 8-bit, <profile>` | `jxlphoto-origin:tiff8` |
| JPEG | `Source: JPEG, <profile>` | `jxlphoto-origin:jpeg` |

**`<profile>` is the colour profile of the original**, not of the export (the export's profile is the one Capture One chose):

- the name stored in its ICC profile: `Adobe RGB (1998)`, `Nikon Adobe RGB 4.0.0.3001`, `sRGB EOTF with DCI-P3 Color Gamut`;
- without an ICC profile, its EXIF colour space: `sRGB (EXIF, no ICC)` or `Adobe RGB (EXIF, no ICC)`. Many cameras mark an Adobe RGB JPEG this way, without embedding the profile;
- `no color profile` when it has neither (WhatsApp images, some scans);
- nothing after the type when the original could not be read. The report says so, and a profile recorded by an earlier run is kept.

A RAW has no profile of its own, so `Source: RAW` has none.

**The `jxlphoto-origin` token is for jxl-photo.** jxl-photo already writes `tiff16` for a 16-bit TIFF by itself, and has no `raw` value, so the token is written only where it would say something else (an 8-bit TIFF or a JPEG). If you do not use jxl-photo, it is a harmless XMP entry.

**The file's date changes, on purpose**: the file did change, so a sync or a backup picks it up.

## How it decides

For each export, the tool takes the file(s) beside `_EXPORT` with the longest name that the export's name starts with (sidecars such as `.xmp` do not count), then:

| Beside the export | Origin |
|---|---|
| only a RAW | RAW |
| only a JPEG | JPEG |
| only a TIFF | the TIFF's bit depth: `TIFF 8-bit` or `TIFF 16-bit` |
| a Nikon NEF + a JPEG | RAW when the export carries the NEF's trace (below); otherwise JPEG with a Green label, review without one |
| another RAW + a JPEG (Fujifilm RAF, ...) | the colour label: Blue = RAW, Green = JPEG |
| a RAW + a TIFF (Olympus ORF + OM Workspace TIFF, ...) | the colour label: Blue = RAW, Green = the TIFF's bit depth |
| a RAW + a JPEG + a TIFF | review; with `--trio-blue-raw`, a Blue label = RAW |
| nothing, or anything else | review |
| a line of `overrides.csv` | that line, always |

**The NEF's trace.** When Capture One develops a Nikon NEF, the export carries tags that only the NEF had (`XMP-crd`, `ExifIFD:SubjectDistance`). Checked against a year of labelled exports, the trace agreed with the labels in every case. Its absence proves nothing, though: some Capture One recipes strip it. So a NEF+JPEG pair without the trace counts as JPEG only with a Green label. And NX Studio copies the trace into the JPEGs it makes from a NEF: when the JPEG beside the export carries it too, the trace proves nothing and the label decides.

**A label that contradicts the files sends the export to review**: Blue with only a JPEG beside, Green with only a RAW, Green on a NEF export that carries the trace, and so on. It is never the label that wins over a fact.

### Capture One colour labels

Labels are needed only for the pairs above (a RAW and a JPEG or TIFF of the same photo). The tool reads the label from the export itself (`xmp:Label`), where Capture One writes it:

- **Blue**: developed from the RAW;
- **Green**: developed from the JPEG or the TIFF.

Where the table asks for a label, any other label (or none) sends the export to review. If you shoot RAW only or JPEG only, you never need a label. The two names are [settings](#settings).

## Requirements & installation

- **Python 3.12 or newer** (3.12 and 3.13 are the ones in use).
- **[ExifTool](https://exiftool.org/) 13.x** on the PATH (it reads and writes every tag).
- **`tifffile`** for `--apply` (`python -m pip install tifffile`): it reads the image bytes for the before/after check. A simulation runs without it.
- **Windows.** It is where the tool was built and run; paths over 260 characters, Japanese names, `[ ]` and `%` in folder names all work.

```console
git clone https://github.com/rsilvabr/photo-origin-tag.git
cd photo-origin-tag
python photo_origin_tag.py --help
```

To run the tests: `python -m pip install pytest numpy Pillow tifffile`, then [Tests](#tests).

## Quick start

**1. Simulate** on one folder. It reads, decides and writes a report; nothing is changed:

```console
python photo_origin_tag.py "D:\Photos\2025\250412_Park"
```

```
photo-origin-tag 0.7.0 | exiftool 13.59 | SIMULATION | overrides: 0 (...\overrides.csv)
  finding exports ...
  4 export(s) below _EXPORT
  reading exports: 4/4  0:00 elapsed, ~0:00 left
  reading original TIFFs: 1/1  0:00 elapsed, ~0:00 left
  reading original JPEGs: 1/1  0:00 elapsed, ~0:00 left

Summary (action, origin): count
  review   -       1
  write    jpeg    1
  write    raw     1
  write    tiff8   1

Colour profile of the originals (origin, profile): count
  jpeg    no color profile  1
  tiff8   no color profile  1

1 file(s) need attention (all in the report):
  review  no original found beside the export | D:\Photos\2025\250412_Park\_EXPORT\TIFF16\Park_web_01.tif

Report: ...\Logs\20261112_083140_report.csv
Simulation: nothing was written. Run again with --apply.
```

**2. Read the report** (a CSV, next section) and settle every `review` you care about: give the photo a colour label in Capture One and export it again, or add a line to [`overrides.csv`](#overridescsv).

**3. Write:**

```console
python photo_origin_tag.py "D:\Photos\2025\250412_Park" --apply
```

The summary now says `written` instead of `write`. Run it on as many roots as you like (`python photo_origin_tag.py D:\Photos\2024 D:\Photos\2025 --apply`), or on a whole drive (`D:\`): the recycle bin and `System Volume Information` are never entered. A second run changes nothing: an export that already carries the right tags is `ok`, and only its header is read.

**4. Then** let your masters and copies be made again from the tagged exports (in jxl-photo: its sync re-encodes an export whose date changed). The tool never edits a master: tag the exports, rebuild what is made from them.

## Reading the report

`Logs\<date>_<time>_report.csv`, one row per export, with the columns `action`, `origin`, `decided_by` (which rule), `reason`, `label`, `model` (the camera), `original` (the file(s) it was matched to), `profile` and `path`.

| Action | Meaning |
|---|---|
| `write` / `written` | will be tagged / was tagged |
| `ok` | already carries these tags; nothing to do |
| `review` | the rules cannot decide; left alone (`reason` says why) |
| `refuse` | it already carries something the tool will not overwrite (below) |
| `skip` | it carries jxl-photo markers: a decoded master, not an export |
| `error` | the write or its check failed; the file stayed as it was |

**What is refused, never overwritten:**

- an export already tagged with another origin, by an earlier run or by hand: only an override replaces it;
- a comment that is not exactly one of this tool's, even one that starts with `Source: `: never, override or not. Your own notes in "Comments" are safe;
- a file that changed between the read and the write, or is locked by another program.

The same source type with another profile (the profile was read from the original, it is a fact, not a rule) is rewritten without an override.

## overrides.csv

A file next to the script (or `--overrides <file>`), UTF-8, comma-separated, with a header row. One line per file or folder the rules cannot decide, or decide wrong; a folder line covers everything below it, and the most specific line wins. Lines starting with `#` are ignored.

```csv
path,origin,note
D:\Photos\2025\250412_Park\_EXPORT\TIFF16\Park_web_01.tif,jpeg,renamed export of IMG_0002
D:\Photos\2019\190801_Trip,raw,everything here was shot RAW only
```

`origin` is `raw`, `jpeg`, `tiff8` or `tiff16`. The same path on two lines is accepted only with the same origin; an invalid file stops the run before it starts (exit 2). A line that matched nothing is reported in the summary.

## Safety guarantees

- **Simulation by default.** Nothing is written without `--apply`.
- **Never guesses.** An export with no original, an unexpected mix of originals, a missing label where one is needed or a label that contradicts the files is left alone and listed.
- **Every write is checked before it counts.** ExifTool writes a temporary copy next to the file (`<name>~origin-tag-tmp-<pid>.tif`). The copy is compared with the original: the image bytes (hashed without decompressing), the ICC profile byte for byte, and every other tag. Only the two tags written may differ, plus the bookkeeping ExifTool updates when it rewrites a file (data offsets, `XMPToolkit`). The tool then checks the original has not changed since it was read, and only then replaces it (`os.replace`, atomic). If anything differs, the copy is deleted, the original stays as it was, and the file is an `error`.
- **A temporary copy left by a killed run is removed** on the next run, and only when its original still exists.
- **One `--apply` at a time.** A second one (a scheduled run starting while you run one by hand) stops before doing anything, exit 2: two runs would compete for the disk and remove each other's temporary copies. The lock is held by the running process, so a killed run leaves none behind.
- **No mass rewrite without you.** With `--max-rewrites N` (meant for unattended runs), `--apply` writes nothing when more than N writes would change tags an earlier run wrote, exit 4. New exports never count. See [Settings](#settings) for what can cause one.
- **Hostile paths.** Every file test of the suite runs in a folder with Japanese, `[ ]`, a space and `%` in its name; one test uses a path over 260 characters. (A path with `%` takes another route: Python makes the copy and ExifTool edits it in place, because ExifTool's `-o` reads `%d`, `%f`, `%e` in an output name as codes.)

## JPEG-only exports (`_EXPORT\JPG_only`)

Some photos can no longer be exported as a TIFF (the edit was lost; only the old JPEG export is left). If you keep those in `_EXPORT\JPG_only` (loose or in subfolders), the same run tags them, with the comment only: no XMP, no `jxlphoto-origin`.

- Every byte outside the JPEG's Exif segment (the image, XMP, ICC, anything after the image) is checked unchanged before the copy replaces it. A JPEG with no Exif at all gets a new one, with the four tags the format requires (`XResolution`, `YResolution`, `ResolutionUnit`, `YCbCrPositioning`); in a JPEG that already has Exif, a change to any of them is an error.
- **Tag them before you archive them.** A JPEG that already has a lossless JXL archive in `_EXPORT\JXL_jpeg` (jxl-photo's "JPG ONLY" archive) is refused: tagging it would make it differ from what that archive gives back. The summary says how many `JPG_only` JPEGs are still in review.
- A NEF+JPEG pair follows the same rule as a TIFF export: without the NEF's trace, a Green label is needed (web recipes often strip the trace, and sometimes all the EXIF).

## Every option

```
python photo_origin_tag.py ROOT [ROOT ...] [options]
```

| Option | What it does |
|---|---|
| `ROOT` | folders (or single files) to scan, at any depth, up to a whole drive; relative paths are made absolute |
| `--apply` | write; without it the run is a simulation |
| `--overrides <csv>` | another overrides file (default: `overrides.csv` next to the script); a file named here that does not exist stops the run (exit 2) |
| `--report <csv>` | write the report there instead of `Logs\`; if it cannot be written there (open in Excel), it goes to `Logs\` with a warning |
| `--workers <n>` | files written at once (default 1; see [Speed](#speed)) |
| `--trio-blue-raw` | a RAW + JPEG + TIFF trio beside the export with a Blue label counts as RAW (a NEF trio also needs the trace); any other label stays review, since Green cannot tell the JPEG from the TIFF |
| `--max-rewrites <n>` | with `--apply`, write nothing (exit 4) when more than n writes would change tags an earlier run wrote; new exports never count. The report's `reason` says what each one replaces. For unattended runs |

## Settings

At the top of `photo_origin_tag.py`, under `SETTINGS (edit here)`. The ones you are most likely to change:

| Setting | Default | What it is |
|---|---|---|
| `EXPORT_FOLDER` | `_EXPORT` | the export folder's name (any case) |
| `EXPORT_EXTS` | `.tif`, `.tiff` | which exports are tagged |
| `SKIP_DIRS` | `$RECYCLE.BIN`, `System Volume Information` | folders never entered, at any depth |
| `JPG_ONLY_FOLDER` / `JPG_ARCHIVE_FOLDER` | `JPG_only` / `JXL_jpeg` | the JPEG-only exports and their archives |
| `RAW_EXTS` | 22 RAW extensions | what counts as a RAW beside the export |
| `LABEL_RAW` / `LABEL_NOT_RAW` | `Blue` / `Green` | the Capture One labels |
| `TRIO_BLUE_IS_RAW` | `False` | the `--trio-blue-raw` rule, on for every run |
| `COMMENT_PREFIX` / `COMMENTS` | `Source: ` ... | the comment's wording |
| `PROFILE_IN_COMMENT` | `True` | add the original's profile to the comment |
| `EXIFTOOL` | `exiftool` | the ExifTool command, or its full path |
| `MAX_REWRITES` | `None` | the `--max-rewrites` limit for every run (`None`: no limit) |
| `LOG_DIR` | `Logs` | next to the script; the environment variable `PHOTO_ORIGIN_TAG_LOG_DIR` overrides it |

Changing the comment's wording (`COMMENTS`, `PROFILE_SEP`) after a first run makes the earlier comments "someone else's text" (refused). Choose it before you apply.

Changing a profile's wording (`PROFILE_EXIF_SRGB`, `PROFILE_EXIF_ADOBE`, `PROFILE_NONE`, `PROFILE_UNNAMED`, `PROFILE_MAX_LEN`) is the opposite: the profile is a fact read from the original, so every comment that carries the old wording is rewritten on the next `--apply`, and everything made from those exports (jxl-photo's masters and copies) is made again. The same happens if a new ExifTool reads a profile's name differently. `--max-rewrites` stops such a run before it writes.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | everything fine (also a simulation, even with files in review) |
| 1 | some file errored and stayed as it was (the report says which) |
| 2 | nothing ran: a root that does not exist, an invalid or missing `--overrides` file, a bad `--workers` or `--max-rewrites`, `tifffile` missing for `--apply`, ExifTool missing or not answering, another `--apply` running |
| 3 | the run was aborted (Ctrl+C or an unexpected error); the report is still written, and the files not reached stay `write`, reason `not attempted` |
| 4 | `--apply` wrote nothing: more writes would change tags an earlier run wrote than `--max-rewrites` allows (the report lists them all, reason `not written`) |

## Logs

In `Logs\` next to the script: the report CSV of each run (`<date>_<time>_report.csv`) and, for `--apply`, a per-file log of what was written (`<date>_<time>_written.log`). A report that cannot be written where `--report` says lands here instead. `photo_origin_tag.lock` is the lock of the running `--apply`; the file stays and only names its last holder.

When the output goes to a file instead of a console, the progress is a plain line a minute instead of one line rewritten in place.

## Speed

One ExifTool stays open for the whole run (`-stay_open`); it is never started per file. Writing rewrites each TIFF once, so the run is bound by the disk: 40 real exports (6.7 GB) on one HDD took 66 s, about 205 MB/s of reads plus writes, the drive's limit. Two workers were slower on that disk (74 s), so the default is one; try `--workers 2` on an SSD. A run where everything is already tagged only reads headers: for the 8 774 exports of that archive (one HDD), from 2½ minutes with a warm disk cache to 11 with a cold one, plus about 40 s to walk a whole drive of 1.4 million files (4 s when the walk is cached).

## Running it on a schedule

A run with `--apply` before the step that builds your masters keeps new exports tagged. Some advice from running it that way:

- **Give it the same roots as the step that builds the masters.** If that step reads the whole drive, pass the drive (`G:\`), not a list of year folders: a list has to be edited every January, and a year folder that does not exist yet stops the run (exit 2).
- **Pass `--max-rewrites`** (50, say): a normal run only tags new exports, which never count, so the limit only trips when something would rewrite tags of earlier runs (see [Settings](#settings)). Then nothing is written, exit 4, and you run it by hand after a look at the report and a backup.
- **Stop the chain at exit code 2 or above** (the tool did not run, was aborted, or refused a mass rewrite), so no master is made from an untagged export that day. Exit code 1 means some files errored and stayed as they were; the rest was tagged.
- **Send its output to a log file** (`>> tagger.log 2>&1`): the reason for a 2 or 3 is in it, and the summary of every run.

## The other script: `pastas_exportar_tiff.py`

The step before the tags, written for the same archive: which camera folders still have JPEG exports without the TIFF export a master needs. It scans the year folders, matches exported JPEGs to TIFFs by photo (the name without profile, version or camera), and opens a checklist in the browser (`127.0.0.1` only), where a click opens the folder in Explorer and the ticks persist between scans. Read-only on the disk; standard library only.

Its messages and categories are in Portuguese, and its root and first year (`ROOT`, `YEARS_FROM`) are settings at its top, set to that archive: it scans every year folder (`ROOT\<four digits>`) from `YEARS_FROM` on, so a new year is in as soon as its folder exists. Adapt them before use.

```console
python pastas_exportar_tiff.py            # scan + checklist page (Ctrl+C ends it)
python pastas_exportar_tiff.py --no-web   # scan + the .md list only
```

## Tests

```console
python -m pytest tests -q
```

170 tests, with the real ExifTool (they are skipped without it). Every file test runs in a folder with Japanese, `[ ]`, a space and `%` in its name; the main fixtures run a second time without the `%`, because the two take different write routes. Every rule of [How it decides](#how-it-decides), every refusal, the re-run, the rollback when a check fails, compressed and uncompressed TIFFs. One end-to-end test runs jxl-photo's encoder on a tagged export when a jxl-photo checkout sits beside this repo (skipped otherwise).

## Changelog

| Version | Date | What it added |
|---|---|---|
| **v0.7.0** | 2026-11-12 | Fixes from a review: a whole drive as the root (recycle bin skipped, names filtered before any path is made); exit 2 without a working ExifTool, for a missing `--overrides` file and while another `--apply` runs (lock); a report that cannot be written goes to `Logs\` instead of aborting the run; `--max-rewrites` (exit 4); the reason names the warning that kept a profile out; plain progress lines in a log; the checklist scans every year folder from `YEARS_FROM` on, and serves its page only to this machine |
| v0.6.0 | 2026-11-11 | `--trio-blue-raw`: a Blue RAW+JPEG+TIFF trio can count as RAW |
| v0.5.0 | 2026-11-11 | Fixes from an audit: paths with `%`; a NEF+JPEG pair without the trace needs a Green label; NX Studio JPEGs carrying the trace; exit codes 0–3 |
| v0.4.0 | 2026-11-09 | The JPEG exports in `_EXPORT\JPG_only` get the comment |
| v0.3.0 | 2026-11-08 | The original's colour profile in the comment |
| v0.2.0 | 2026-11-07 | One ExifTool for the whole run, `--workers` |
| v0.1.0 | 2026-11-07 | First version: TIFF exports tagged with their origin |

## Related projects

- [jxl-photo](https://github.com/rsilvabr/jxl-photo) turns the 16-bit TIFF exports into JPEG XL masters. It carries the `jxlphoto-origin` token and the comment into the master by itself, with no setting.
- [photo-backup](https://github.com/rsilvabr/photo-backup) backs up the exported photos with their originals and edits.

## Disclaimer

This tool was made for my personal workflow. Use at your own risk — I am not responsible for any issues you may encounter. If you find a bug, feel free to report it.

Always start with a simulation, on a copy or a small folder, before tagging an archive you care about.

## License

MIT License — feel free to use, modify, and distribute. See [LICENSE](LICENSE).

## Acknowledgments

- [Claude](https://www.anthropic.com/claude) (Anthropic) and [DeepSeek](https://www.deepseek.com), among other AI tools, for code assistance, reviews and technical discussion
