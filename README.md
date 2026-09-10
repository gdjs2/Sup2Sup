# Sup2Sup

**Crop PGS subtitle canvases, find clipped cues, and move them into the picture.**

Cropping a movie's black bars leaves its bitmap subtitles on the old canvas. Sup2Sup adjusts PGS tracks imported from a video container or external `.sup` files to match the retained picture, with a desktop preview for reviewing individual cues and a CLI for batch work.

For example, a `1920 × 1080` subtitle canvas can become `1920 × 804`. A 1080p PGS track can also be mapped onto cropped 4K video at 2× scale.

## Features

- Detect cues that are partially clipped or entirely outside the crop.
- Fit problem cues automatically, with an optional safe margin.
- Drag, nudge, or move multiple cues together, with undo/redo.
- Preview against video with audio, track/output selection, click-to-seek, and an original-canvas comparison.
- Load, check, and fix cues in background tasks with progress and cancellation.
- Save projects with optional video and multiple subtitle tracks; switch tracks during playback.
- Import all PGS tracks from a video using PyAV, apply a shared crop, and fit/export all tracks.
- Export an individual SUP with a preservation report, or all tracks to a directory.

The exporter changes **presentation and window geometry (PCS/WDS)**. It preserves bitmap packets (ODS), palettes (PDS), packet order, timestamps, and forced flags. An unchanged round trip produces identical bytes. Export checks the result and refuses unresolved placements that would lose subtitle pixels.

## Quick start

Requirements: [uv](https://docs.astral.sh/uv/getting-started/installation/) and Python 3.11–3.14. The repository's `.python-version` selects Python 3.14. Desktop playback has been tested on Windows; other platforms have not been verified.

From the cloned or extracted repository directory:

```sh
uv sync --locked --extra gui
uv run --locked --extra gui sup2sup gui
```

To open a subtitle file directly:

```sh
uv run --locked --extra gui sup2sup gui "movie.sup"
```

The GUI uses PySide6 and Qt Multimedia for video and audio. Container import uses the [PyAV Python library](https://pyav.org/docs/stable/overview/installation.html), installed automatically with Sup2Sup. The application does not invoke or require `ffmpeg` or `ffprobe` executables. PyAV wheels bundle the media libraries on supported platforms. A separate FFmpeg executable is used only by the optional independent decoder/audio integration tests.

## Desktop workflow

1. The GUI starts with an empty project. Use **New project** to start another, or **Open project** to resume a saved `.sup2sup.json` file.
2. Optionally **Import video + PGS tracks**. One video is attached for playback and every embedded PGS subtitle track is imported. All new PGS tracks are extracted together in one scan, then loaded one by one from temporary SUP files. Temporary files are removed after loading or cancellation. Non-PGS tracks (such as SRT, ASS, or VobSub) are skipped with a count. Reimporting the same video avoids duplicating its existing PGS tracks. Replacing/removing the video keeps imported subtitle tracks and edits.
3. **Import SUP files** adds one or more external tracks to the current project. Choose the active track with the **Subtitle track** selector; switching preserves video playback and position. Each track has its own crop, offsets, and undo/redo history.
4. Set margins in **subtitle pixels**, then choose **Apply crop to this track** or **Apply crop to all tracks**. The latter sets a shared project preset, converts margins exactly for each track's resolution, and applies to subsequent imports. Alternatively, use the centered output-size controls for the active track.
5. With video loaded, **Detect project crop…** suggests a centered crop against a standard canvas, including 1920 × 1080 and 3840 × 2160. This is optional and works before importing subtitles. Review the original size and margins before applying. Detection uses video dimensions, not black-bar analysis of decoded frames; a full-size letterboxed video still needs manual margins. **Match video crop…** adjusts only the active track.
6. Review **Problems only**, drag/nudge cues, or use **Fit problems in this track** / **Fit problems in all tracks**. A safe margin of `0` minimizes movement; `20` leaves 20 native subtitle pixels of space. Undo/redo operates on the active track, including its portion of a batch edit.
7. **Save project** keeps the video reference, track list, active track, shared crop preset, and every track's edits. **Export all tracks…** writes all tracks to a chosen directory. Every track must pass geometry validation first; unresolved cues block the batch. Existing output files are refused in the GUI; choose another directory. **Export SUP** remains available for the active track with an optional preservation report.

A new project may be an empty draft; exporting requires at least one subtitle track. External SUP sources are referenced by relative paths and verified using hashes. Extracted PGS data is embedded in the project file, so temporary extraction files and the original container are not needed to reopen those subtitles. Embedded data increases project-file size. Missing video does not prevent subtitle editing; import a replacement to restore playback. Old single-track projects still open and save in the new format.

Audio settings, preview delay, video mapping mode, and playback position remain temporary preview settings. Positive preview delay displays subtitles later without changing exported timestamps. Container extraction normalizes timestamps to the container's playback origin, retaining a late first cue's offset; container formats may not retain the source SUP's original decode timestamps. Subsequent editing/export preserves the imported SUP's timestamps and encoded bitmap/palette data.

Loading and batch edits run in the background and can be cancelled without applying partial changes. Export stages and validates every track before publishing any output. Output filenames derive from track names, with numeric suffixes for duplicates. A filesystem failure during publication can leave an incomplete batch; completed files remain available. The movie itself is never cropped or rewritten.

### Playback controls

Click the playback bar to jump, or drag and release to seek. Seeking preserves the playing/paused state. With the bar focused, arrow keys move 5 seconds, Page Up/Down move 30 seconds, and Home/End jump to the endpoints. The bar also navigates cues without a video.

Use **Mute/Unmute**, volume, **Audio**, and **Output** to control sound. Playback starts with sound enabled at 70% volume through the system default output. Select speakers or headphones explicitly if that output is unsuitable.

## 1080p subtitles on cropped 4K video

For a `1920 × 1080` PGS track paired with video cropped from `3840 × 2160` to `3840 × 1608`:

1. Open the SUP and cropped video. Click **Match video crop…**, or **Set subtitle alignment…** if alignment is pending.
2. Confirm the original video size is `3840 × 2160`. The dialog suggests a centered crop: left/right `0`, top/bottom `276` **video pixels**. Enter the actual margins if the crop is asymmetric.
3. Click **Apply subtitle crop**. The margins become top/bottom `138` **subtitle pixels**, and the cues are checked in the background.
4. Fit or move affected cues, then export. The SUP canvas is `1920 × 804`, displayed at 2× scale over the `3840 × 1608` video.

| Setting | Subtitle pixels | Video pixels at 2× |
| --- | ---: | ---: |
| Top/bottom crop in this example | 138 | 276 |
| Safe margin | 20 | 40 |
| Nudge step | 1 | 2 |
| Retained canvas | 1920 × 804 | 3840 × 1608 |

The exported track retains its native bitmap resolution. Your playback software must scale that subtitle canvas to the video. Native 4K PGS sources instead retain their 4K bitmap resolution.

Original video dimensions and centered margins are editable suggestions: the cropped size alone cannot identify which borders were removed. Conversion requires matching original aspect ratios and whole subtitle pixels. For example, `277` video pixels at 2× would require `138.5` subtitle pixels, so the dialog rejects that crop instead of rounding it.

All sidebar crop margins, fit margins, nudges, and offsets use **subtitle pixels**. Position conversion is:

```text
output_x = original_x + offset_x - crop_left
output_y = original_y + offset_y - crop_top
```

## Command line

For CLI-only use, install with `uv sync --locked`. After either installation, the commands below use `--no-sync` to keep the currently installed environment, including any GUI dependencies.

Inspect crop problems without writing an output:

```sh
uv run --no-sync sup2sup inspect movie.sup --crop 0 138 0 138
uv run --no-sync sup2sup inspect movie.sup --crop 0 138 0 138 --json
```

Fit problem cues and export with a preservation report:

```sh
uv run --no-sync sup2sup crop movie.sup movie.cropped.sup --crop 0 138 0 138 --fit margin --margin 20 --report movie.report.json
```

Move a specific cue, or apply a saved project:

```sh
uv run --no-sync sup2sup crop movie.sup movie.cropped.sup --crop 0 138 0 138 --move 152 0 -104
uv run --no-sync sup2sup crop movie.sup movie.cropped.sup --project movie.sup2sup.json
```

For a 1080p SUP accompanying 4K video, supply the **uncropped** video size to interpret `--crop` in video pixels:

```sh
uv run --no-sync sup2sup crop movie.sup movie.cropped.sup --video-source 3840 2160 --crop 0 276 0 276 --fit margin --margin 20
```

`--margin` and `--move` always use subtitle pixels. Saved projects already contain converted margins, so `--project` cannot be combined with `--video-source` or nonzero `--crop` margins.

Cue numbers start at **1** in the CLI/UI. Repeated `--move CUE DX DY` options accumulate after automatic fitting. The default `--fit warn` leaves placements unchanged and refuses export when the crop would lose an object. Existing outputs require `--overwrite`; the input SUP is protected.

### Multi-track projects

Create a project from a video's PGS tracks and/or external files:

```sh
uv run --no-sync sup2sup project-create movie.sup2sup.json --video movie.mkv --subtitle commentary.sup
uv run --no-sync sup2sup project-create subtitles.sup2sup.json --subtitle english.sup --subtitle japanese.sup --crop 0 138 0 138 --fit --margin 20
```

`--crop` uses the first subtitle's canvas unless `--canvas WIDTH HEIGHT` is supplied. It is converted exactly to each track's native resolution. `--detect-crop` with `--video` instead uses the same optional centered video-dimension suggestion as the GUI; `--canvas` can override the inferred original dimensions. These two crop options are mutually exclusive. No crop is applied by default.

Inspect every track, then export all saved edits (optionally fitting remaining problems):

```sh
uv run --no-sync sup2sup project-inspect movie.sup2sup.json
uv run --no-sync sup2sup project-export movie.sup2sup.json ./finished --fit --margin 20
```

These commands produce JSON summaries/reports. `project-export --fit` affects the exported files without changing the saved project. `--overwrite` explicitly permits replacing existing output files; source media and project files remain protected. Existing `inspect` and `crop` commands remain available, and `--project` can select an external source that appears exactly once in a multi-track project.

## Limitations

- Detection uses whole object rectangles, including transparent padding, and honors existing object crops. All objects in a cue move together.
- A cue or full encoded bitmap larger than the target canvas cannot be fitted without resampling. Reduce the crop or safe margin when appropriate.
- Each nonempty presentation display set is a cue. Fade/animation updates can create adjacent entries; select them together when applying a shared movement. OCR, text editing, bitmap resampling, and fade grouping are not implemented.
- Fragmented images must complete within their display set. Object, palette, and window reuse are supported, including both object/window slots. Malformed, incomplete, changing-canvas, or oversized streams are rejected.
- Existing window clipping can be previewed and copied unchanged, but blocks geometry export. Unknown segment types are retained for unchanged round trips and also block geometry export. Window rectangles are adjusted to cover moved objects, including clear events.
- Arbitrary cropped canvases are intended for file playback/remuxing. Blu-ray authoring, frame-exact scrubbing, and accurate HDR/Dolby Vision color reproduction are outside the current scope. Codec support and playback performance depend on Qt and the system.
- Input size and cumulative decoded bitmap allocations are each limited to 512 MiB. A final cue without a clearing presentation remains open-ended.

## Development and tests

Run the suite with the desktop dependencies:

```sh
uv run --locked --extra gui python -m unittest discover -s tests -v
```

GUI tests skip when PySide6 is absent. External decoder tests require FFmpeg on `PATH`; audio-device checks can skip on machines without an output device. Tests generate synthetic SUP/media fixtures and require no movie downloads. See [TESTING.md](TESTING.md) for coverage and validation details.

Optional lint tooling is available through the `dev` group:

```sh
uv sync --locked --extra gui --group dev
uv run --locked --extra gui --group dev ruff check src tests
```

| Path | Purpose |
| --- | --- |
| `src/sup2sup/pgs/` | SUP parser, RLE/palette renderer, geometry exporter |
| `src/sup2sup/edit/` | Crop/fit logic, timeline, project state, undo/redo |
| `src/sup2sup/gui/` | PySide6 desktop interface |
| `src/sup2sup/cli.py` | Inspection and batch export commands |
| `tests/` | Synthetic fixtures and regression tests |

### Sharing files and bug reports

Use small synthetic examples when reporting issues. Saved `.sup2sup.json` projects include source paths, hashes, and embedded subtitle data; logs and screenshots may reveal local paths or media titles. Review those details before sharing. Keep local captures and diagnostics under `.test-artifacts/`, which is ignored along with media files, generated projects/reports, caches, and local credentials.

Implementation references: [FFmpeg's PGS decoder](https://github.com/FFmpeg/FFmpeg/blob/master/libavcodec/pgssubdec.c), [Qt QGraphicsVideoItem](https://doc.qt.io/qtforpython-6/PySide6/QtMultimediaWidgets/QGraphicsVideoItem.html), and [Qt QMediaPlayer](https://doc.qt.io/qtforpython-6/PySide6/QtMultimedia/QMediaPlayer.html).

## License

Sup2Sup's source code and documentation are licensed under the [MIT License](LICENSE).

Third-party dependencies retain their own licenses. In particular, PySide6/Qt are covered by their [Qt for Python licensing terms](https://doc.qt.io/qtforpython-6/licenses.html); those terms also apply when distributing them with an application.
