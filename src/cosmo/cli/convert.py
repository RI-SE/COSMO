"""
cosmo.cli.convert

CLI entrypoint for: cosmo convert ...
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from typing import Tuple

from cosmo.app.convert_app import ConvertConfig, run_convert


def _parse_xy_offset(values) -> Tuple[float, float]:
    if values is None:
        return (0.0, 0.0)
    if len(values) != 2:
        raise argparse.ArgumentTypeError("--xy-offset requires two numbers: DX DY")
    return (float(values[0]), float(values[1]))


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="cosmo convert",
        description="Convert OpenLABEL to Omega-Prime CSV and optionally OSI/MCAP (per-run output folders).",
    )
    ap.add_argument("--openlabel", required=True, help="Path to OpenLABEL JSON")
    ap.add_argument("--opendrive", "--odr", dest="opendrive", required=False, help="Path to OpenDRIVE .xodr/.xml/.txt")
    ap.add_argument("--georef-data", dest="georef_data", required=False, help="Path to ORBIT *_georef_data.json")
    ap.add_argument("--calibration", required=False, help="Path to legacy calibration JSON (optional)")

    ap.add_argument("--fps", type=float, required=False, help="FPS override (optional)")

    ap.add_argument("--no-csv", action="store_true", help="Do not write CSV")
    ap.add_argument("--no-mcap", action="store_true", help="Do not write MCAP (OSI GroundTruth)")

    ap.add_argument("--swap-xy", action="store_true", help="Swap X and Y after projection")
    ap.add_argument("--flip-x", action="store_true", help="Flip X -> -X after projection")
    ap.add_argument("--flip-y", action="store_true", help="Flip Y -> -Y after projection")
    ap.add_argument("--xy-offset", nargs=2, metavar=("DX", "DY"), help="Translate XY by DX DY meters after projection")
    ap.add_argument("--yaw-offset-deg", type=float, default=0.0, help="Yaw offset (deg CCW)")

    ap.add_argument("--out", dest="out_dir", required=False, help="Base output directory or explicit run directory")
    ap.add_argument("--run-name", required=False, help="Optional override for run folder name")

    ap.add_argument("--json", action="store_true", help="Print result as JSON")
    return ap


def main(argv=None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)

    cfg = ConvertConfig(
        openlabel=args.openlabel,
        opendrive=args.opendrive,
        georef_data=args.georef_data,
        calibration=args.calibration,
        fps=args.fps,
        write_csv=not args.no_csv,
        write_mcap=not args.no_mcap,
        swap_xy=args.swap_xy,
        flip_x=args.flip_x,
        flip_y=args.flip_y,
        xy_offset=_parse_xy_offset(args.xy_offset),
        yaw_offset_deg=float(args.yaw_offset_deg or 0.0),
        out_dir=args.out_dir,
        run_name=args.run_name,
    )

    def _log(line: str) -> None:
        print(line, flush=True)

    result = run_convert(cfg, log_fn=_log)
    if args.json:
        print(json.dumps(asdict(result), indent=2))
    else:
        print(f"\nRun folder: {result.run_dir}")
        print(f"Outputs:   {result.outputs_dir}")
        if result.csv_path:
            print(f"CSV:       {result.csv_path}")
        if result.mcap_path:
            print(f"MCAP:      {result.mcap_path}")
        if result.notes:
            print("Notes:")
            for n in result.notes:
                print(f"  - {n}")
    return 0