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
import sys
from dataclasses import asdict
from datetime import datetime, timezone
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
    ap.add_argument(
        "--output-prefix",
        dest="output_prefix",
        required=False,
        metavar="PREFIX",
        help="Write outputs directly to <PREFIX>.csv and <PREFIX>.mcap (bypasses run-folder structure)",
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

    # Oblique correction
    ob = ap.add_argument_group("oblique correction")
    ob.add_argument("--flight-record", metavar="PATH",
                    help="Path to FlightRecord_*.video_stats.json for oblique correction")
    ob.add_argument("--flight-record-sequence", type=int, default=0, metavar="N",
                    help="Sequence index within the flight record (default: 0)")
    ob.add_argument("--bbox-correction", choices=["none", "analytical", "3d"], default="none",
                    help="Bbox correction mode (default: none)")
    ob.add_argument("--camera-model", default="mavic3pro-standard",
                    help="Camera model key for HFOV/resolution lookup (default: mavic3pro-standard)")
    ob.add_argument("--hfov-deg", type=float, default=None, metavar="FLOAT",
                    help="Override horizontal FOV in degrees")
    ob.add_argument("--use-gps-cam-pos", action="store_true",
                    help=(
                        "Use GPS drone position as camera position instead of H-derived (default). "
                        "H-derived is geometrically consistent with the calibrated homography."
                    ))

    # Size stabilization
    ap.add_argument("--stabilize-size", action="store_true",
                    help="Use per-object average dimensions instead of per-frame dimensions.")

    ap.add_argument("--country-code", type=int, default=None,
                    help="ISO 3166-1 numeric country code (e.g. 752=Sweden); "
                         "auto-derived from georef lat/lon if omitted.")

    # Run folder naming
    ap.add_argument("--run-name", required=False, help="Optional override for run folder name")

    # Output formatting
    ap.add_argument("--json", action="store_true", help="Print result as JSON")

    # Provenance
    prov = ap.add_argument_group("provenance")
    prov.add_argument("--prov-out", metavar="PATH", help="Write W3C-PROV provenance to this file (omit to skip)")
    prov.add_argument("--prov-in", metavar="PATH", help="Continue an existing upstream provenance chain (optional)")
    prov.add_argument("--opendrive-prov", metavar="PATH",
                      help="Provenance file for --opendrive/--odr input (will be inlined into output DPR)")
    prov.add_argument("--georef-prov", metavar="PATH",
                      help="Provenance file for --georef-data input (will be inlined into output DPR)")
    prov.add_argument("--flight-record-prov", metavar="PATH",
                      help="Provenance file for --flight-record input (will be inlined into output DPR)")

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


def _record_provenance_convert(args, openlabel: str, result, start_time: datetime, end_time: datetime) -> None:
    """Record a dataprov provenance step for cosmo-convert."""
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
            entity_id=f"convert:{Path(openlabel).stem}",
            initial_source=openlabel,
            description=f"Conversion of {Path(openlabel).name} to Omega-Prime",
        )

    inputs = [openlabel]
    input_formats = ["json"]
    # Parallel list: None = no separate prov file; path = inline this chain into output DPR.
    # Index 0 (OpenLABEL): its provenance IS prov_in (the chain itself), no separate ref needed.
    input_prov_files: list[str | None] = [None]

    for path, fmt, prov_path in [
        (args.opendrive, "xodr", args.opendrive_prov if args.opendrive_prov and Path(args.opendrive_prov).exists() else None),
        (args.georef_data, "json", args.georef_prov if args.georef_prov and Path(args.georef_prov).exists() else None),
        (args.calibration, "json", None),
        (args.flight_record, "json", args.flight_record_prov if args.flight_record_prov and Path(args.flight_record_prov).exists() else None),
    ]:
        if path:
            inputs.append(path)
            input_formats.append(fmt)
            input_prov_files.append(prov_path)

    outputs: list[str] = []
    output_formats: list[str] = []
    if result.csv_path:
        outputs.append(str(result.csv_path))
        output_formats.append("csv")
    if result.mcap_path:
        outputs.append(str(result.mcap_path))
        output_formats.append("mcap")

    has_secondary_prov = any(p is not None for p in input_prov_files[1:])

    chain.add(
        tool_name="cosmo-convert",
        tool_version=tool_version,
        operation="convert",
        inputs=inputs,
        input_formats=input_formats,
        outputs=outputs,
        output_formats=output_formats,
        arguments=" ".join(sys.argv),
        started_at=start_time.isoformat().replace("+00:00", "Z"),
        ended_at=end_time.isoformat().replace("+00:00", "Z"),
        input_provenance_files=input_prov_files if (prov_in or has_secondary_prov) else None,
        capture_agent=True,
        capture_environment=True,
    )
    chain.save(args.prov_out, input_prov="inline" if has_secondary_prov else "reference")
    print(f"Provenance: {args.prov_out}")


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
        flight_record=args.flight_record,
        flight_record_sequence=args.flight_record_sequence,
        bbox_correction=args.bbox_correction,
        camera_model=args.camera_model,
        hfov_deg=args.hfov_deg,
        use_gps_cam_pos=args.use_gps_cam_pos,
        out_dir=args.out_dir,
        run_name=args.run_name,
        output_prefix=getattr(args, "output_prefix", None),
        stabilize_size=args.stabilize_size,
        country_code=args.country_code,
    )

    def _log(line: str) -> None:
        print(line, flush=True)

    start_time = datetime.now(timezone.utc)
    result = run_convert(cfg, log_fn=_log)
    end_time = datetime.now(timezone.utc)

    if args.json:
        print(json.dumps(asdict(result), indent=2))
    else:
        if not cfg.output_prefix:
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

    if args.prov_out:
        _record_provenance_convert(args, openlabel, result, start_time, end_time)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
