import argparse
import sys
from typing import List, Optional


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="COSMO: compute legacy Calibration.json (pixel→ground homography)"
    )
    ap.add_argument("--pixel-pairs", required=True, help="CSV with point_name,u,v")
    ap.add_argument("--visual-markers", required=True, help="CSV with ground coords (lat/lon/alt or E/N)")
    ap.add_argument("--opendrive", required=True, help="OpenDRIVE .xodr/.txt containing <geoReference>")

    ap.add_argument("--image", required=False, help="Optional image for overlay check")
    ap.add_argument("--openlabel", required=False, help="Optional OpenLABEL JSON for validation")
    ap.add_argument("--out-prefix", default="calib", help="Output prefix (default: calib)")

    ap.add_argument("--fps", type=float, default=30.0)
    ap.add_argument("--image-width", type=int, default=3840)
    ap.add_argument("--image-height", type=int, default=2160)
    ap.add_argument("--ransac-thresh-m", type=float, default=0.50)
    return ap


def main(argv: Optional[List[str]] = None) -> None:
    args = build_parser().parse_args(argv)

    # Phase-1 (no refactor required yet): call script main() with argv patching.
    from scripts import compute_calibration  # [3](https://stackoverflow.com/questions/77744891/license-not-specified-even-though-there-is-a-license-md-in-the-readme)

    old_argv = sys.argv[:]
    try:
        sys.argv = [
            "compute_calibration.py",
            "--pixel-pairs", args.pixel_pairs,
            "--visual-markers", args.visual_markers,
            "--opendrive", args.opendrive,
            "--fps", str(args.fps),
            "--image-width", str(args.image_width),
            "--image-height", str(args.image_height),
            "--ransac-thresh-m", str(args.ransac_thresh_m),
            "--out-prefix", str(args.out_prefix),
        ]
        if args.image:
            sys.argv += ["--image", args.image]
        if args.openlabel:
            sys.argv += ["--openlabel", args.openlabel]

        compute_calibration.main()
    finally:
        sys.argv = old_argv