"""
cosmo.cli.main

Single entrypoint for the COSMO tool:
  cosmo convert ...
  cosmo calibrate ...
  cosmo gui

This wraps the existing thin command modules:
- cosmo.cli.convert
- cosmo.cli.calibrate
- cosmo.cli.gui
"""

from __future__ import annotations

import argparse
import sys
from importlib import metadata


def _get_version() -> str:
    # Prefer installed package metadata, fallback to local __version.py
    try:
        return metadata.version("cosmo")
    except Exception:
        try:
            from cosmo.__version import __version__  # type: ignore
            return str(__version__)
        except Exception:
            return "0.0.0"


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="cosmo",
        description="COSMO conversion tools (OpenLABEL → OSI/MCAP + Omega-Prime CSV) with GUI",
    )
    ap.add_argument(
        "--version",
        action="store_true",
        help="Print version and exit",
    )

    sub = ap.add_subparsers(dest="command")

    # We intentionally do not duplicate all flags here;
    # each subcommand owns its argument parsing.
    p_convert = sub.add_parser("convert", help="Convert OpenLABEL to Omega-Prime CSV and optionally MCAP")
    p_convert.set_defaults(_dispatch="convert")

    p_cal = sub.add_parser("calibrate", help="Compute calibration (pixel→ground homography) and write Calibration JSON")
    p_cal.set_defaults(_dispatch="calibrate")

    p_gui = sub.add_parser("gui", help="Launch the COSMO GUI")
    p_gui.set_defaults(_dispatch="gui")

    return ap


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv

    ap = build_parser()
    # Parse only the top-level args; subcommand gets the rest
    ns, rest = ap.parse_known_args(argv)

    if ns.version:
        print(_get_version())
        return 0

    dispatch = getattr(ns, "_dispatch", None)
    if dispatch == "convert":
        from cosmo.cli.convert import main as convert_main
        return int(convert_main(rest))
    if dispatch == "calibrate":
        from cosmo.cli.calibrate import main as calibrate_main
        return int(calibrate_main(rest))
    if dispatch == "gui":
        from cosmo.cli.gui import main as gui_main
        return int(gui_main(rest))

    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())