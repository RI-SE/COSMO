"""
cosmo.cli.convert

CLI entrypoint for:
  cosmo convert ...

Supports both explicit flags and standard --input/--output patterns:
  cosmo convert --input input.json --output out_dir
  cosmo convert input.json -o out_dir
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Tuple

from cosmo.app.convert_app import ConvertConfig, run_convert


def _existing_file(p: str) -> str:
    """Argparse type: ensure a path exists and is a file."""
    path = Path(p)
    if not path.exists():
        raise argparse.ArgumentTypeError(f"File not found: {p}")
    if not path.is_file():
        raise argparse.ArgumentTypeError(f"Not a file: {p}")
    return str(path)


def _parse_xy_offset(values) -> Tuple[float, float]:
    if values is None:
        return (0.0, 0.0)
    if len(values) != 2:
        raise argparse.ArgumentTypeError("--xy-offset requires two numbers: DX DY")
    return (float(values[0]), float(values[1]))


def build_parser() -> argparse.ArgumentParser:
    epilog = """
Examples:
  cosmo convert --input scenario.json
  cosmo convert scenario.json --output runs/
  cosmo convert --input scenario.json --odr map.xodr --mcap --csv
  cosmo convert --input scenario.json --no-mcap
  cosmo convert --input scenario.json --xy-offset 1.2 -0.5 --yaw-offset-deg 90
"""
    ap = argparse.ArgumentParser(
        prog="cosmo convert",
        description="Convert OpenLABEL to Omega-Prime CSV and optionally OSI/MCAP (per-run output folders).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=epilog.strip(),
    )

    # INPUT / OUTPUT pattern
    ap.add_argument(
        "input",
        nargs="?",
        help="Path to OpenLABEL JSON (positional alternative to --input/--openlabel)",
    )
    ap.add_argument(
        "--input",
        "--openlabel",
        dest="openlabel",
        help="Path to OpenLABEL JSON",
    )
    ap.add_argument(
        "-o",
        "--output",
        "--out",
        dest="out_dir",
        required=False,
        help="Base output directory or explicit run directory",
    )

    # Related inputs
    ap.add_argument("--opendrive", "--odr", dest="opendrive", required=False, help="Path to OpenDRIVE .xodr/.xml/.txt")
    ap.add_argument("--georef-data", dest="georef_data", required=False, help="Path to ORBIT *_georef_data.json")
    ap.add_argument("--calibration", required=False, help="Path to legacy calibration JSON (optional)")
    ap.add_argument("--fps", type=float, required=False, help="FPS override (optional)")

    # Output toggles (standard style)
    ap.add_argument(
        "--csv",
        dest="write_csv",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Write Omega-Prime CSV (default: true). Use --no-csv to disable.",
    )
    ap.add_argument(
        "--mcap",
        dest="write_mcap",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Write MCAP (OSI GroundTruth) (default: true). Use --no-mcap to disable.",
    )

    # Coordinate transforms
    ap.add_argument("--swap-xy", action="store_true", help="Swap X and Y after projection")
    ap.add_argument("--flip-x", action="store_true", help="Flip X -> -X after projection")
    ap.add_argument("--flip-y", action="store_true", help="Flip Y -> -Y after projection")
    ap.add_argument("--xy-offset", nargs=2, metavar=("DX", "DY"), help="Translate XY by DX DY meters after projection")
    ap.add_argument("--yaw-offset-deg", type=float, default=0.0, help="Yaw offset (deg CCW)")
    ap.add_argument("--strip-xodr-namespace", action="store_true",
                    help="Strip XML namespace declarations from OpenDRIVE before embedding in MCAP "
                         "(workaround for omega-prime namespace-unaware XPath; disable once omega-prime adds namespace support)")

    # Run folder naming
    ap.add_argument("--run-name", required=False, help="Optional override for run folder name")

    # Output formatting
    ap.add_argument("--json", action="store_true", help="Print result as JSON")

    return ap


def _resolve_openlabel(args: argparse.Namespace, ap: argparse.ArgumentParser) -> str:
    """
    Determine OpenLABEL input from either:
      - positional "input"
      - --input/--openlabel
    """
    if args.input and args.openlabel:
        ap.error("Provide either a positional input OR --input/--openlabel, not both.")

    openlabel = args.openlabel or args.input
    if not openlabel:
        ap.error("Missing input. Provide OpenLABEL JSON as positional argument or via --input/--openlabel.")

    try:
        return _existing_file(openlabel)
    except argparse.ArgumentTypeError as e:
        ap.error(str(e))
        raise


def main(argv=None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)

    openlabel = _resolve_openlabel(args, ap)

    cfg = ConvertConfig(
        openlabel=openlabel,
        opendrive=args.opendrive,
        georef_data=args.georef_data,
        calibration=args.calibration,
        fps=args.fps,
        write_csv=bool(args.write_csv),
        write_mcap=bool(args.write_mcap),
        swap_xy=args.swap_xy,
        flip_x=args.flip_x,
        flip_y=args.flip_y,
        xy_offset=_parse_xy_offset(args.xy_offset),
        yaw_offset_deg=float(args.yaw_offset_deg or 0.0),
        strip_xodr_namespace=args.strip_xodr_namespace,
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
        print(f"Outputs: {result.outputs_dir}")
        if result.csv_path:
            print(f"CSV: {result.csv_path}")
        if result.mcap_path:
            print(f"MCAP: {result.mcap_path}")
        if result.notes:
            print("Notes:")
            for n in result.notes:
                print(f" - {n}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
