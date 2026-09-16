"""Command-line inspection and batch crop; Qt is imported only by the gui command."""

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from .edit.geometry import Crop, EditError
from .edit.project import Project
from .edit.session import Session
from .edit.timeline import format_pts
from .files import same_path, write_bytes
from .media import probe_video
from .pgs.parser import read_sup
from .pgs.writer import export_sup
from .video import centered_crop, subtitle_crop_from_video, suggest_video_canvas


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PGS subtitle crop and placement editor")
    sub = parser.add_subparsers(dest="command", required=True)
    gui = sub.add_parser("gui", help="Open the desktop editor (requires the gui extra)")
    gui.add_argument("input", nargs="?", type=Path)
    for name, help_text in (
        ("inspect", "Scan cues and crop intersections"),
        ("crop", "Export a cropped SUP"),
    ):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("input", type=Path)
        command.add_argument(
            "--crop",
            nargs=4,
            metavar=("LEFT", "TOP", "RIGHT", "BOTTOM"),
            type=int,
            default=(0, 0, 0, 0),
            help="Subtitle pixels to remove, or video pixels with --video-source",
        )
        command.add_argument(
            "--video-source",
            nargs=2,
            metavar=("WIDTH", "HEIGHT"),
            type=int,
            help="Original uncropped video size; converts --crop from video to subtitle pixels",
        )
        command.add_argument("--project", type=Path, help="Use saved project edits and crop")
        if name == "inspect":
            command.add_argument("--json", action="store_true", help="Print machine-readable scan")
        else:
            command.add_argument("output", type=Path)
            command.add_argument("--fit", choices=("warn", "inside", "margin"), default="warn")
            command.add_argument("--margin", type=int, default=20)
            command.add_argument(
                "--move",
                nargs=3,
                type=int,
                action="append",
                default=[],
                metavar=("CUE", "DX", "DY"),
                help="Relative move; cue numbers start at 1",
            )
            command.add_argument("--report", type=Path, help="Optional JSON export report")
            command.add_argument("--overwrite", action="store_true")
    create = sub.add_parser("project-create", help="Create a project from video and/or SUP tracks")
    create.add_argument("output", type=Path)
    create.add_argument("--video", type=Path)
    create.add_argument("--subtitle", type=Path, action="append", default=[])
    crop_options = create.add_mutually_exclusive_group()
    crop_options.add_argument(
        "--crop", nargs=4, type=int, metavar=("LEFT", "TOP", "RIGHT", "BOTTOM")
    )
    crop_options.add_argument(
        "--detect-crop",
        action="store_true",
        help="Suggest centered crop from video dimensions, without frame analysis",
    )
    create.add_argument(
        "--canvas",
        nargs=2,
        type=int,
        metavar=("WIDTH", "HEIGHT"),
        help="Reference canvas for --crop; defaults to the first subtitle canvas",
    )
    inspect = sub.add_parser("project-inspect", help="List tracks and problems in a saved project")
    inspect.add_argument("input", type=Path)
    export = sub.add_parser("project-export", help="Validate and export every project track")
    export.add_argument("input", type=Path)
    export.add_argument("directory", type=Path)
    for command in (create, export):
        command.add_argument("--fit", action="store_true", help="Fit problem cues in every track")
        command.add_argument("--margin", type=int, default=0, help="Safe margin in subtitle pixels")
        command.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "gui":
        from .gui.app import main as gui_main

        return gui_main([str(args.input)] if args.input else [])
    try:
        if args.command.startswith("project-"):
            return _project_command(args)
        if args.project:
            project = Project.load(args.project)
            matches = [
                t.session
                for t in project.tracks
                if not t.embedded and same_path(args.input, t.session.source)
            ]
            if len(matches) != 1:
                raise EditError("Input SUP must match exactly one external project track")
            session = matches[0]
            if any(args.crop) or args.video_source is not None:
                raise EditError("Use either --project or --crop/--video-source")
        else:
            session = Session(read_sup(args.input), args.input)
            crop = Crop(*args.crop)
            if args.video_source is not None:
                crop = subtitle_crop_from_video(
                    session.document.width, session.document.height, *args.video_source, crop
                )
            session.set_crop(crop)
        if args.command == "inspect":
            findings = session.findings()
            region = session.crop.rectangle(session.document.width, session.document.height)
            data = {
                "source_size": [session.document.width, session.document.height],
                "output_size": [region.width, region.height],
                "cues": len(findings),
                "safe": sum(f.status == "safe" for f in findings),
                "clipped": sum(f.status == "clipped" for f in findings),
                "outside": sum(f.status == "outside" for f in findings),
                "fullscreen_cropped": sum(f.fullscreen_cropped for f in findings),
                "blocking": sum(f.blocks_export for f in findings),
                "warnings": session.document.warnings,
                "findings": [
                    dict(
                        cue=f.cue_index + 1,
                        status=f.status,
                        fullscreen_cropped=f.fullscreen_cropped,
                        blocks_export=f.blocks_export,
                        start=format_pts(session.document.cues[f.cue_index].start_pts),
                        overflow=f.overflow,
                        bounds=asdict(f.bounds),
                    )
                    for f in findings
                ],
            }
            if args.json:
                print(json.dumps(data, indent=2))
            else:
                print(
                    f"{session.document.width}x{session.document.height} -> "
                    f"{region.width}x{region.height}: {len(findings)} cues; "
                    f"{data['safe']} safe, {data['clipped']} clipped, {data['outside']} outside, "
                    f"{data['fullscreen_cropped']} full-screen crops to review"
                )
                for finding in findings:
                    if finding.problem:
                        print(f"  #{finding.cue_index + 1}: {finding.description}")
                for warning in session.document.warnings:
                    print(f"  Warning: {warning}")
            return 0
        protected = [args.input] + (project.protected_paths() if args.project else [])
        for output in (args.output, args.report):
            if output and any(same_path(output, path) for path in protected):
                raise EditError("Output/report cannot overwrite the input SUP or project")
            if output and output.exists() and not args.overwrite:
                raise FileExistsError(f"{output} exists; choose a new name or use --overwrite")
        if args.report and same_path(args.report, args.output):
            raise EditError("Report and SUP output must use different paths")
        if args.fit != "warn":
            indices = [f.cue_index for f in session.findings() if f.problem]
            errors = session.auto_fit(indices, args.margin if args.fit == "margin" else 0)
            if errors:
                raise EditError("\n".join(errors))
        for cue, dx, dy in args.move:
            session.move([cue - 1], dx, dy)
        data, report = export_sup(session.document, session.crop, session.transforms)
        write_bytes(args.output, data, overwrite=args.overwrite)
        report_json = json.dumps(asdict(report), indent=2) + "\n"
        if args.report:
            try:
                write_bytes(args.report, report_json.encode(), overwrite=args.overwrite)
            except OSError as exc:
                print(
                    f"SUP was exported to {args.output}, but the report could not be saved: {exc}",
                    file=sys.stderr,
                )
                return 1
        print(report_json, end="")
        return 0
    except (OSError, ValueError) as exc:
        print(f"sup2sup: {exc}", file=sys.stderr)
        return 1


def _project_command(args):
    if args.command == "project-create":
        project = Project()
        if args.video:
            info = project.import_video(args.video)
            if info.skipped_subtitles:
                print(f"Skipped {info.skipped_subtitles} non-PGS subtitle tracks", file=sys.stderr)
        for path in args.subtitle:
            project.add_sup(path)
        if args.detect_crop:
            if not args.video:
                raise EditError("--detect-crop requires --video")
            info = probe_video(args.video)
            canvas = (
                tuple(args.canvas)
                if args.canvas
                else suggest_video_canvas(1920, 1080, info.width, info.height)
            )
            project.set_crop(canvas, centered_crop(*canvas, info.width, info.height))
        elif args.crop is not None:
            if not args.canvas and not project.tracks:
                raise EditError("--crop requires --canvas when no subtitle is imported")
            doc = project.tracks[0].session.document if project.tracks else None
            canvas = tuple(args.canvas) if args.canvas else (doc.width, doc.height)
            project.set_crop(canvas, Crop(*args.crop))
        elif args.canvas:
            raise EditError("--canvas requires --crop or --detect-crop")
    else:
        project = Project.load(args.input)
    if getattr(args, "fit", False):
        failures = project.fit_all(args.margin)
        if failures:
            raise EditError("\n".join(failures))
    if args.command == "project-export":
        print(json.dumps(project.export_all(args.directory, overwrite=args.overwrite), indent=2))
    else:
        if args.command == "project-create":
            project.save(args.output, overwrite=args.overwrite)
        tracks = []
        for index, track in enumerate(project.tracks, 1):
            findings = track.session.findings()
            region = track.session.crop.rectangle(
                track.session.document.width, track.session.document.height
            )
            tracks.append(
                dict(
                    track=index,
                    name=track.name,
                    cues=len(findings),
                    problems=sum(f.problem for f in findings),
                    fullscreen_cropped=sum(f.fullscreen_cropped for f in findings),
                    blocking=sum(f.blocks_export for f in findings),
                    output_size=[region.width, region.height],
                    embedded=track.embedded,
                    stream_index=track.stream_index,
                )
            )
        print(
            json.dumps(
                dict(video=str(project.video) if project.video else None, tracks=tracks), indent=2
            )
        )
    return 0
