# Testing

The suite uses Python's standard-library `unittest` and creates synthetic SUP/media fixtures. No private media, credentials, or machine-specific paths are required.

## Run

From the repository root:

```sh
uv run --locked --extra gui python -m unittest discover -s tests -v
uv run --locked --extra gui python -m compileall -q src tests
```

For core-only testing, omit `--extra gui`. GUI tests skip when PySide6 is absent. FFmpeg integration tests skip when the executable is missing, and tests requiring an audio output device can skip on headless systems. The offscreen Qt tests mute generated audio during playback checks.

To use an existing environment without synchronizing dependencies:

```sh
uv run --no-sync python -m unittest discover -s tests -v
uv run --no-sync python -m compileall -q src tests
```

## Current validation

**157 tests passed, with no skips**, on Windows with Python 3.14.6, PySide6 6.11.2, PyAV 17.1.0, and FFmpeg 8.1.2. GUI tests used Qt's offscreen platform. Compilation succeeded. Ruff was not installed in this environment, so lint was not rerun.

## Prior baseline validation

**133 tests passed, with no skips**, on Linux with Python 3.14.0, PySide6 6.11.2, and PyAV 17.1.0. Compilation and lint of the files changed in that revision passed; repository-wide lint retained pre-existing findings elsewhere.

**84 tests passed, with no skips**, on Windows with Python 3.14.6, PySide6 6.11.2, and FFmpeg 8.1.2. Compilation succeeded. This records one tested environment rather than a guarantee of compatibility across platforms and codecs.

## Coverage

| Area | Checks |
| --- | --- |
| SUP parsing and rendering | Packet framing, RLE variants and malformed data, fragmented/reused objects, palette updates, forced/cropped objects, timestamp wrap, cue clearing |
| Bitmap memory | Import/check/export of 527 MiB of expanded images held as under 2 MiB of RLE data, allocation-free RLE validation, bounded preview-cache eviction and sharing across tracks, malformed unused objects, and cancellation |
| Geometry and export | HD/UHD canvases, multi-object fitting, window reuse and clear events, unchanged round trips, preservation of ODS/PDS and timestamps, refusal of unresolved placements |
| Full-screen cues | Exact RLE pixel crops without image expansion, asymmetric margins and manual offsets, HD/4K, fragmentation, reused images during fades, version wrap/epoch reset, duplicate object references and source windows, review flags/filter/undo, mixed-cue validation, CLI and batch export, cancellation |
| Projects and CLI | Multi-track persistence, embedded PGS, legacy migration, shared crop inheritance, isolated edits, source hashes, batch preflight, collision-safe names, input protection, CLI create/inspect/export |
| Container import | PyAV-generated multi-PGS video with empty PATH, metadata/language, late cue and clearing timestamps, unchanged encoded payloads, cancellation, and video without subtitles; exactly one demux scan for multiple tracks, parsing only after extraction completes, temporary-file cleanup, and unchanged project state if a later track fails |
| Responsiveness | 5,000-cue loading, progress delivery, GUI timers during background checks, cancellation without partial edits, filtered selection, recovery after failed loading |
| Preview | Palette equivalence with the core renderer, 1080p-to-4K mapping, asymmetric crops, whole-pixel validation, actual scene rendering, video-only fallback and overlay restoration |
| Seeking | Absolute clicks, dragging, precise handle clicks without movement, endpoints, keyboard navigation, inverted/right-to-left layouts, empty/disabled controls, no feedback from playback updates |
| Audio | Volume/mute synchronization, device selection/fallback, track switching, decoding, pause/resume, and seeking with track selection preserved |

Five FFmpeg integration tests independently decode exported SUP data, compare rendered pixels, and check the original clearing time. The 4K test converts video crop margins into subtitle pixels, fits and exports the cue, then explicitly scales the subtitle canvas onto a `3840 × 1608` frame. Full-screen tests verify intentional clipping of visible pixels and replacement of a reused bitmap during a palette update after moving one cue.

The large-track memory regression generates 360 distinct bitmap versions, including version-number wrap. It verifies that loading, cue checks, geometry export, and export read-back never call the pixel decoder, while preview access still returns the correct first and last images. Separate checks ensure malformed RLE is rejected during import even for objects never previewed, and that evicted images can be decoded again correctly. These synthetic tests exercise the former 512 MiB decoded-image failure; the particular subtitle file that prompted the fix was not supplied.

Full-screen crop regressions compare exported palette indices against independently sliced source pixels. They cover unchanged round trips, nonblocking review status, ordinary objects that still block export, cancellation while cropping a bitmap, and output exceeding one 64 KiB ODS packet. The GUI check verifies the amber status, tooltip, review filter, fitting without removing the flag, and undo. These fixtures are synthetic; a real full-screen subtitle track was not supplied for this feature.

Project GUI tests also check crop and filter persistence across track changes, shared crop undo/redo, batch fitting, save/reopen, and crop detection before subtitle import. Export tests check the combined menu, active-track default selection, cancellation, selected-only validation/output, stable names across subsets, and protection of unselected input files. Import tests exercise the combined menu and selector placement, cancelling the picker, choosing a video and subtitle subset, restoring the selected video stream during real Qt playback, and retaining track counters during nested parser progress. Extraction progress tests verify a monotonic container-wide byte position (including video packets before the first subtitle), completion at EOF, and sequential per-track counters only during parsing.

The GUI tests render synthetic video through Qt's real video sink. The audio integration test generates a video with two AAC tracks and checks decoded buffers. Playback-slider tests send real Qt mouse and keyboard events.

Export progress tests check intermediate cue counts, nested read-back verification, checksum accuracy, selected-track numbering, and cleanup if verification is interrupted before publication. Both GUI export paths are exercised with deliberately slow validation: progress must appear before the first track finishes, while the GUI timer continues running. Unchanged exports remain byte-identical and unresolved cue placements still block export.

## Manual checks and limits

Supplementary local testing exercised HEVC Main 10/Dolby Vision profile 8 playback, matching a cropped 4K frame to a 1080p subtitle canvas, seeking while paused, and resuming playback. These checks are separate from the repeatable suite; the media and diagnostic captures are not distributed.

Physical speaker output, HDR/Dolby Vision color accuracy, and compatibility with every movie encode have not been verified. Platform-specific codec support, hardware decoding, and seek precision depend on Qt Multimedia and the host system.

## Test data hygiene

Keep local captures, extracted subtitles, debug scripts, and logs under `.test-artifacts/`. That directory is ignored by Git. Saved projects can include source paths, and reports include source hashes; review both before sharing. New regression fixtures should be synthetic and reproducible from code, like [tests/fixtures.py](tests/fixtures.py).
