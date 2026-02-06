"""
cosmo.cli.calibrate

CLI entrypoint for: cosmo calibrate ...
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from cosmo.app.calibrate_app import CalibrateConfig, run_calibrate


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="cosmo calibrate",
        description="Compute Calibration.json (pixel->ground homography) into a per-run folder.",
    )
    ap.add_argument("--pixel-pairs", required=True, help="CSV with point_name,u,v")
    ap.add_argument("--visual-markers", required=True, help="CSV with point_name plus lat/lon/alt OR E/N")
    ap.add_argument("--opendrive", required=True, help="OpenDRIVE file (used for <geoReference> if lat/lon is used)")

    ap.add_argument("--image", required=False, help="Optional image for overlay plot")
    ap.add_argument("--openlabel", required=False, help="Optional OpenLABEL for validation")

    ap.add_argument("--fps", type=float, default=30.0)
    ap.add_argument("--image-width", type=int, default=3840)
    ap.add_argument("--image-height", type=int, default=2160)
    ap.add_argument("--ransac-thresh-m", type=float, default=0.50)

    ap.add_argument("--out", dest="out_dir", required=False, help="Base output directory or explicit run directory")
    ap.add_argument("--run-name", required=False, help="Optional override for run folder name")

    ap.add_argument("--json", action="store_true", help="Print result as JSON")
    return ap


def main(argv=None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)

    cfg = CalibrateConfig(
        pixel_pairs=args.pixel_pairs,
        visual_markers=args.visual_markers,
        opendrive=args.opendrive,
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

    result = run_calibrate(cfg, log_fn=_log)

    if args.json:
        print(json.dumps(asdict(result), indent=2))
    else:
        print(f"\nRun folder:  {result.run_dir}")
        print(f"Outputs:     {result.outputs_dir}")
        print(f"Calibration: {result.calibration_json_path}")
        if result.summary_json_path:
            print(f"Summary:     {result.summary_json_path}")
        if result.residuals_png_path:
            print(f"Residuals:   {result.residuals_png_path}")
        if result.overlay_png_path:
            print(f"Overlay:     {result.overlay_png_path}")
        if result.notes:
            print("Notes:")
            for n in result.notes:
                print(f"  - {n}")
    return 0
    