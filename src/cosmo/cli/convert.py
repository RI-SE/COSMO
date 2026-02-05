import argparse
import math
from typing import List, Optional


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="COSMO: OpenLABEL → Omega-Prime CSV (+ optional MCAP/OSI)"
    )
    ap.add_argument("--openlabel", required=True, help="Path to OpenLABEL JSON")
    ap.add_argument("--odr", required=False, help="Path to OpenDRIVE (.xodr/.xml/.txt)")
    ap.add_argument("--out-prefix", required=True, help="Output file prefix (no extension)")

    ap.add_argument(
        "--georef-data",
        required=False,
        help="Path to ORBIT georef_data.json (uses transformation_matrix pixel→ground)",
    )
    ap.add_argument(
        "--calibration",
        required=False,
        help="Legacy calibration.json (fps/default dims or homography fallback)",
    )
    ap.add_argument("--fps", type=float, required=False, help="Override FPS")

    # Optional alignment tweaks
    ap.add_argument("--swap-xy", action="store_true", help="Swap projected X and Y")
    ap.add_argument("--flip-x", action="store_true", help="Flip X → -X")
    ap.add_argument("--flip-y", action="store_true", help="Flip Y → -Y")
    ap.add_argument(
        "--xy-offset",
        nargs=2,
        type=float,
        metavar=("DX", "DY"),
        default=(0.0, 0.0),
        help="Translate projected XY by (DX,DY) meters",
    )
    ap.add_argument(
        "--yaw-offset-deg",
        type=float,
        default=0.0,
        help="Rotate projected XY CCW by this many degrees (applied after swap/flip)",
    )

    ap.add_argument("--no-csv", action="store_true", help="Skip CSV writing")
    ap.add_argument("--no-mcap", action="store_true", help="Skip MCAP writing")

    return ap


def main(argv: Optional[List[str]] = None) -> None:
    args = build_parser().parse_args(argv)

    # Phase-1 (no refactor required yet):
    # Import the existing converter function directly.
    from scripts.convert_openlabel_to_omega import convert_openlabel_to_omega  # [2](https://huggingface.co/DavidAU/Qwen3-48B-A4B-Savant-Commander-Distill-12X-Closed-Open-Heretic-Uncensored-GGUF/blob/main/README.md)

    convert_openlabel_to_omega(
        openlabel_path=args.openlabel,
        odr_path=args.odr,
        out_prefix=args.out_prefix,
        calibration_path=args.calibration,
        georef_data_path=args.georef_data,
        fps_arg=args.fps,
        write_csv=(not args.no_csv),
        write_mcap=(not args.no_mcap),
        swap_xy=args.swap_xy,
        flip_x=args.flip_x,
        flip_y=args.flip_y,
        xy_offset=(float(args.xy_offset[0]), float(args.xy_offset[1])),
        yaw_offset_rad=math.radians(float(args.yaw_offset_deg)),
    )