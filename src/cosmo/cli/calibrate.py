"""
cosmo.cli.calibrate

CLI entrypoint for:
  cosmo calibrate ...

Supports:
  1) --inputs PIXEL_PAIRS VISUAL_MARKERS OPENDRIVE
  2) positional inputs: PIXEL_PAIRS VISUAL_MARKERS OPENDRIVE
  3) explicit flags: --pixel-pairs/--visual-markers/--opendrive
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from cosmo.app.calibrate_app import CalibrateConfig, run_calibrate


def _existing_file(p: str) -> str:
    """Argparse helper: ensure a path exists and is a file."""
    path = Path(p)
    if not path.exists():
        raise argparse.ArgumentTypeError(f"File not found: {p}")
    if not path.is_file():
        raise argparse.ArgumentTypeError(f"Not a file: {p}")
    return str(path)


def build_parser() -> argparse.ArgumentParser:
    epilog = """
Examples:
  cosmo calibrate --inputs pixel_pairs.csv visual_markers.csv map.xodr -o runs/
  cosmo calibrate pixel_pairs.csv visual_markers.csv map.xodr -o runs/
  cosmo calibrate --pixel-pairs pixel_pairs.csv --visual-markers visual_markers.csv --opendrive map.xodr
"""
    ap = argparse.ArgumentParser(
        prog="cosmo calibrate",
        description="Compute Calibration.json (pixel->ground homography) into a per-run folder.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=epilog.strip(),
    )

    # --inputs style
    ap.add_argument(
        "--inputs",
        nargs=3,
        metavar=("PIXEL_PAIRS", "VISUAL_MARKERS", "OPENDRIVE"),
        help="Three input files in order: pixel_pairs.csv visual_markers.csv opendrive.xodr",
    )

    # positional inputs
    ap.add_argument("pixel_pairs_pos", nargs="?", help="CSV with point_name,u,v (positional alternative)")
    ap.add_argument("visual_markers_pos", nargs="?", help="CSV with point_name plus lat/lon/alt OR E/N (positional alternative)")
    ap.add_argument("opendrive_pos", nargs="?", help="OpenDRIVE file (positional alternative)")

    # flagged inputs (backwards compatible with your original)
    ap.add_argument("--pixel-pairs", dest="pixel_pairs", help="CSV with point_name,u,v")
    ap.add_argument("--visual-markers", dest="visual_markers", help="CSV with point_name plus lat/lon/alt OR E/N")
    ap.add_argument("--opendrive", dest="opendrive", help="OpenDRIVE file (used for <geoReference> if lat/lon is used)")

    ap.add_argument("--image", required=False, help="Optional image for overlay plot")
    ap.add_argument("--openlabel", required=False, help="Optional OpenLABEL for validation")

    ap.add_argument("--fps", type=float, default=30.0)
    ap.add_argument("--image-width", type=int, default=3840)
    ap.add_argument("--image-height", type=int, default=2160)
    ap.add_argument("--ransac-thresh-m", type=float, default=0.50)

    # output pattern
    ap.add_argument("-o", "--output", "--out", dest="out_dir", required=False, help="Base output directory or explicit run directory")
    ap.add_argument("--run-name", required=False, help="Optional override for run folder name")

    ap.add_argument("--json", action="store_true", help="Print result as JSON")

    # Provenance
    prov = ap.add_argument_group("provenance")
    prov.add_argument("--prov-out", metavar="PATH", help="Write W3C-PROV provenance to this file (omit to skip)")
    prov.add_argument("--prov-in", metavar="PATH", help="Continue an existing upstream provenance chain (optional)")

    return ap


def _resolve_inputs(args: argparse.Namespace, ap: argparse.ArgumentParser) -> tuple[str, str, str]:
    used_inputs_style = args.inputs is not None
    used_positional_style = any([args.pixel_pairs_pos, args.visual_markers_pos, args.opendrive_pos])
    used_flag_style = any([args.pixel_pairs, args.visual_markers, args.opendrive])

    styles_used = sum([used_inputs_style, used_positional_style, used_flag_style])
    if styles_used > 1:
        ap.error(
            "Mixed input styles detected. Use only one of: "
            "--inputs (3 paths), positional (3 paths), or explicit flags "
            "(--pixel-pairs/--visual-markers/--opendrive)."
        )

    if used_inputs_style:
        pixel_pairs, visual_markers, opendrive = args.inputs
    elif used_positional_style:
        pixel_pairs, visual_markers, opendrive = args.pixel_pairs_pos, args.visual_markers_pos, args.opendrive_pos
    else:
        pixel_pairs, visual_markers, opendrive = args.pixel_pairs, args.visual_markers, args.opendrive

    missing = []
    if not pixel_pairs:
        missing.append("PIXEL_PAIRS (CSV)")
    if not visual_markers:
        missing.append("VISUAL_MARKERS (CSV)")
    if not opendrive:
        missing.append("OPENDRIVE (.xodr/.xml/.txt)")
    if missing:
        ap.error("Missing required inputs: " + ", ".join(missing))

    # Validate existence
    try:
        pixel_pairs = _existing_file(pixel_pairs)
        visual_markers = _existing_file(visual_markers)
        opendrive = _existing_file(opendrive)
    except argparse.ArgumentTypeError as e:
        ap.error(str(e))
        raise

    # Optional inputs
    if args.image:
        try:
            args.image = _existing_file(args.image)
        except argparse.ArgumentTypeError as e:
            ap.error(str(e))

    if args.openlabel:
        try:
            args.openlabel = _existing_file(args.openlabel)
        except argparse.ArgumentTypeError as e:
            ap.error(str(e))

    return pixel_pairs, visual_markers, opendrive


def _record_provenance_calibrate(
    args, pixel_pairs: str, visual_markers: str, opendrive: str,
    result, start_time: datetime, end_time: datetime,
) -> None:
    """Record a dataprov provenance step for cosmo-calibrate."""
    try:
        from importlib.metadata import version as _pkg_version
        tool_version = _pkg_version("cosmo")
    except Exception:
        tool_version = "unknown"

    from dataprov import ProvenanceChain

    prov_in = args.prov_in
    if prov_in and Path(prov_in).exists():
        chain = ProvenanceChain.load(prov_in)
    else:
        chain = ProvenanceChain.create(
            entity_id=f"calibrate:{Path(pixel_pairs).stem}",
            initial_source=pixel_pairs,
            description=f"Calibration from {Path(pixel_pairs).name}",
        )

    inputs = [pixel_pairs, visual_markers, opendrive]
    input_formats = ["csv", "csv", "xodr"]
    if args.image:
        inputs.append(args.image)
        input_formats.append("png")
    if args.openlabel:
        inputs.append(args.openlabel)
        input_formats.append("json")

    chain.add(
        tool_name="cosmo-calibrate",
        tool_version=tool_version,
        operation="calibrate",
        inputs=inputs,
        input_formats=input_formats,
        outputs=[str(result.calibration_json_path)],
        output_formats=["json"],
        arguments=" ".join(sys.argv),
        started_at=start_time.isoformat().replace("+00:00", "Z"),
        ended_at=end_time.isoformat().replace("+00:00", "Z"),
        input_provenance_files=[prov_in] if prov_in else None,
        capture_agent=True,
        capture_environment=True,
    )
    chain.save(args.prov_out)
    print(f"Provenance: {args.prov_out}")


def main(argv=None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)

    pixel_pairs, visual_markers, opendrive = _resolve_inputs(args, ap)

    cfg = CalibrateConfig(
        pixel_pairs=pixel_pairs,
        visual_markers=visual_markers,
        opendrive=opendrive,
        image=args.image,
        openlabel=args.openlabel,
        fps=float(args.fps),
        image_width=int(args.image_width),
        image_height=int(args.image_height),
        ransac_thresh_m=float(args.ransac_thresh_m),
        out_dir=args.out_dir,
        run_name=args.run_name,
    )

    def _log(line: str) -> None:
        print(line, flush=True)

    start_time = datetime.now(timezone.utc)
    result = run_calibrate(cfg, log_fn=_log)
    end_time = datetime.now(timezone.utc)

    if args.json:
        print(json.dumps(asdict(result), indent=2))
    else:
        print(f"\nRun folder: {result.run_dir}")
        print(f"Outputs: {result.outputs_dir}")
        print(f"Calibration: {result.calibration_json_path}")
        if result.summary_json_path:
            print(f"Summary: {result.summary_json_path}")
        if result.residuals_png_path:
            print(f"Residuals: {result.residuals_png_path}")
        if result.overlay_png_path:
            print(f"Overlay: {result.overlay_png_path}")
        if result.notes:
            print("Notes:")
            for n in result.notes:
                print(f" - {n}")

    if args.prov_out:
        _record_provenance_calibrate(args, pixel_pairs, visual_markers, opendrive, result, start_time, end_time)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
