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

## Latest validation

**84 tests passed, with no skips**, on Windows with Python 3.14.6, PySide6 6.11.2, and FFmpeg 8.1.2. Compilation succeeded. This records one tested environment rather than a guarantee of compatibility across platforms and codecs.

## Coverage

| Area | Checks |
| --- | --- |
| SUP parsing and rendering | Packet framing, RLE variants and malformed data, fragmented/reused objects, palette updates, forced/cropped objects, timestamp wrap, cue clearing |
| Geometry and export | HD/UHD canvases, multi-object fitting, window reuse and clear events, unchanged round trips, preservation of ODS/PDS and timestamps, refusal of unresolved placements |
| Projects and CLI | Crop/offset persistence, source hashes, undo/redo, input/output protection, reports, video-pixel crop conversion |
| Responsiveness | 5,000-cue loading, progress delivery, GUI timers during background checks, cancellation without partial edits, filtered selection, recovery after failed loading |
| Preview | Palette equivalence with the core renderer, 1080p-to-4K mapping, asymmetric crops, whole-pixel validation, actual scene rendering, video-only fallback and overlay restoration |
| Seeking | Absolute clicks, dragging, precise handle clicks without movement, endpoints, keyboard navigation, inverted/right-to-left layouts, empty/disabled controls, no feedback from playback updates |
| Audio | Volume/mute synchronization, device selection/fallback, track switching, decoding, pause/resume, and seeking with track selection preserved |

Three FFmpeg integration tests independently decode exported SUP data, compare rendered pixels, and check the original clearing time. The 4K test converts video crop margins into subtitle pixels, fits and exports the cue, then explicitly scales the subtitle canvas onto a `3840 × 1608` frame.

The GUI tests render synthetic video through Qt's real video sink. The audio integration test generates a video with two AAC tracks and checks decoded buffers. Playback-slider tests send real Qt mouse and keyboard events.

## Manual checks and limits

Supplementary local testing exercised HEVC Main 10/Dolby Vision profile 8 playback, matching a cropped 4K frame to a 1080p subtitle canvas, seeking while paused, and resuming playback. These checks are separate from the repeatable suite; the media and diagnostic captures are not distributed.

Physical speaker output, HDR/Dolby Vision color accuracy, and compatibility with every movie encode have not been verified. Platform-specific codec support, hardware decoding, and seek precision depend on Qt Multimedia and the host system.

## Test data hygiene

Keep local captures, extracted subtitles, debug scripts, and logs under `.test-artifacts/`. That directory is ignored by Git. Saved projects can include source paths, and reports include source hashes; review both before sharing. New regression fixtures should be synthetic and reproducible from code, like [tests/fixtures.py](tests/fixtures.py).
