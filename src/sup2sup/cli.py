"""Command-line inspection and batch crop; Qt is imported only by the gui command."""

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

from .edit.geometry import Crop, EditError
from .edit.session import Session
from .edit.timeline import format_pts
from .files import same_path, write_bytes
from .pgs.parser import read_sup
from .pgs.writer import export_sup
from .video import subtitle_crop_from_video


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Lossless PGS subtitle crop and placement editor")
    sub = parser.add_subparsers(dest="command", required=True)
    gui = sub.add_parser("gui", help="Open the desktop editor (requires the gui extra)")
    gui.add_argument("input", nargs="?", type=Path)
    for name, help_text in (("inspect", "Scan cues and crop intersections"),
                            ("crop", "Export a cropped SUP")):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("input", type=Path)
        command.add_argument("--crop", nargs=4, metavar=("LEFT", "TOP", "RIGHT", "BOTTOM"),
                             type=int, default=(0, 0, 0, 0),
                             help="Subtitle pixels to remove, or video pixels with --video-source")
        command.add_argument("--video-source", nargs=2, metavar=("WIDTH", "HEIGHT"), type=int,
                             help="Original uncropped video size; converts --crop from video to subtitle pixels")
        command.add_argument("--project", type=Path, help="Use saved project edits and crop")
        if name == "inspect":
            command.add_argument("--json", action="store_true", help="Print machine-readable scan")
        else:
            command.add_argument("output", type=Path)
            command.add_argument("--fit", choices=("warn", "inside", "margin"), default="warn")
            command.add_argument("--margin", type=int, default=20)
            command.add_argument("--move", nargs=3, type=int, action="append", default=[],
                                 metavar=("CUE", "DX", "DY"), help="Relative move; cue numbers start at 1")
            command.add_argument("--report", type=Path, help="Optional JSON export report")
            command.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "gui":
        from .gui.app import main as gui_main
        return gui_main([str(args.input)] if args.input else [])
    try:
        if args.project:
            session = Session.load(args.project)
            if not same_path(args.input, session.source):
                raise EditError("Input SUP does not match the project source")
            if any(args.crop) or args.video_source is not None:
                raise EditError("Use either --project or --crop/--video-source")
        else:
            session = Session(read_sup(args.input), args.input)
            crop = Crop(*args.crop)
            if args.video_source is not None:
                crop = subtitle_crop_from_video(session.document.width, session.document.height,
                                                *args.video_source, crop)
            session.set_crop(crop)
        if args.command == "inspect":
            findings = session.findings()
            region = session.crop.rectangle(session.document.width, session.document.height)
            data = {
                "source_size": [session.document.width, session.document.height],
                "output_size": [region.width, region.height], "cues": len(findings),
                "safe": sum(f.status == "safe" for f in findings),
                "clipped": sum(f.status == "clipped" for f in findings),
                "outside": sum(f.status == "outside" for f in findings),
                "warnings": session.document.warnings,
                "findings": [dict(cue=f.cue_index + 1, status=f.status,
                                  start=format_pts(session.document.cues[f.cue_index].start_pts),
                                  overflow=f.overflow, bounds=asdict(f.bounds)) for f in findings],
            }
            if args.json:
                print(json.dumps(data, indent=2))
            else:
                print(f"{session.document.width}x{session.document.height} -> "
                      f"{region.width}x{region.height}: {len(findings)} cues; "
                      f"{data['safe']} safe, {data['clipped']} clipped, {data['outside']} outside")
                for finding in findings:
                    if finding.problem:
                        print(f"  #{finding.cue_index + 1}: {finding.description}")
                for warning in session.document.warnings:
                    print(f"  Warning: {warning}")
            return 0
        protected = [args.input] + ([args.project] if args.project else [])
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
                print(f"SUP was exported to {args.output}, but the report could not be saved: {exc}",
                      file=sys.stderr)
                return 1
        print(report_json, end="")
        return 0
    except (OSError, ValueError) as exc:
        print(f"sup2sup: {exc}", file=sys.stderr)
        return 1
