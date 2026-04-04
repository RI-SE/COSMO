"""
cosmo.cli.main

Single entrypoint for the COSMO tool:

  cosmo                # defaults to GUI (with help fallback if GUI can't start)
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
import os
import sys
import traceback
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
    p_convert = sub.add_parser("convert", help="Convert OpenLABEL to Omega-Prime CSV and optionally MCAP", add_help=False)
    p_convert.set_defaults(_dispatch="convert")

    p_correct = sub.add_parser("correct", help="Correct oblique-drone bboxes in an OpenLABEL file", add_help=False)
    p_correct.set_defaults(_dispatch="correct")

    p_cal = sub.add_parser("calibrate", help="Compute calibration (pixel→ground homography) and write Calibration JSON", add_help=False)
    p_cal.set_defaults(_dispatch="calibrate")

    p_gui = sub.add_parser("gui", help="Launch the COSMO GUI")
    p_gui.set_defaults(_dispatch="gui")

    return ap


def _has_display() -> bool:
    """
    Best-effort detection of whether a GUI display is available.
    - On Windows: assume a desktop session exists.
    - On Linux: check for X11/Wayland variables (incl. WSLg).
    If uncertain, assume "yes" and let the GUI launch attempt decide.
    """
    if os.name == "nt":
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _debug_enabled() -> bool:
    """Enable detailed tracebacks if COSMO_DEBUG=1/true/yes."""
    v = os.environ.get("COSMO_DEBUG", "")
    return v.strip().lower() in {"1", "true", "yes", "y", "on"}


def _is_missing_qt_binding(exc: BaseException) -> str | None:
    """
    Return the missing Qt binding name if it looks like a missing binding error.
    Works for ModuleNotFoundError and ImportError-style messages.
    """
    name = getattr(exc, "name", None)
    if isinstance(name, str) and name in {"PyQt5", "PyQt6", "PySide6"}:
        return name

    msg = str(exc)
    for candidate in ("PyQt5", "PyQt6", "PySide6"):
        if f"No module named '{candidate}'" in msg or f"No module named {candidate}" in msg:
            return candidate
    return None


def _looks_like_qt_platform_plugin_issue(msg: str) -> bool:
    m = msg.lower()
    return (
        "qt platform plugin" in m
        or "could not load the qt platform plugin" in m
        or "this application failed to start because no qt platform plugin" in m
        or "available platform plugins are" in m
    )


def _print_gui_failure_help(ap: argparse.ArgumentParser, exc: BaseException) -> int:
    if _debug_enabled():
        print("COSMO_DEBUG enabled: printing full traceback.\n", file=sys.stderr)
        traceback.print_exc()
        print("", file=sys.stderr)

    msg = str(exc)
    missing = _is_missing_qt_binding(exc)

    print("COSMO GUI could not be started.", file=sys.stderr)

    # Missing Qt binding
    if missing:
        print(f"Reason: Missing Qt binding '{missing}'.", file=sys.stderr)
        print("Fix (recommended): install Qt via conda-forge in your active environment:", file=sys.stderr)
        print("  conda install -c conda-forge pyqt", file=sys.stderr)
        print("Alternative: install optional GUI deps if pip is allowed:", file=sys.stderr)
        print("  python -m pip install '.[gui]'", file=sys.stderr)
        print("", file=sys.stderr)
        print("Meanwhile you can run CLI commands:", file=sys.stderr)
        print("  cosmo convert ...\n  cosmo calibrate ...", file=sys.stderr)
        ap.print_help()
        return 1

    # Headless / no display
    if os.name != "nt" and not _has_display():
        print("Reason: No GUI display detected (headless session).", file=sys.stderr)
        print("Fix: run under a desktop session/WSLg or set up X11/Wayland forwarding.", file=sys.stderr)
        print("", file=sys.stderr)
        print("Meanwhile you can run CLI commands:", file=sys.stderr)
        print("  cosmo convert ...\n  cosmo calibrate ...\n  cosmo gui", file=sys.stderr)
        ap.print_help()
        return 2

    # Qt platform plugin issue (Linux/WSL typical)
    if _looks_like_qt_platform_plugin_issue(msg):
        print("Reason: Qt platform plugin failed to initialize.", file=sys.stderr)
        print("This often happens on Linux/WSL when 'xcb'/'wayland' runtime pieces are missing", file=sys.stderr)
        print("or when no display server is available.", file=sys.stderr)
        print("", file=sys.stderr)
        print("Things to try:", file=sys.stderr)
        print("  - If in WSL: ensure WSLg is enabled and a GUI session is available.", file=sys.stderr)
        print("  - Ensure DISPLAY or WAYLAND_DISPLAY is set.", file=sys.stderr)
        print("  - Prefer conda-forge Qt packages:  conda install -c conda-forge pyqt", file=sys.stderr)
        print("", file=sys.stderr)
        print("Error detail:", file=sys.stderr)
        print(f"  {msg}", file=sys.stderr)
        ap.print_help()
        return 1

    # Generic fallback
    print("Reason: An exception occurred while launching the GUI.", file=sys.stderr)
    print("Tip: set COSMO_DEBUG=1 to see a full traceback for debugging.", file=sys.stderr)
    print("Error detail:", file=sys.stderr)
    print(f"  {msg}", file=sys.stderr)
    print("", file=sys.stderr)
    print("You can still use CLI commands:", file=sys.stderr)
    print("  cosmo convert ...\n  cosmo calibrate ...\n  cosmo gui", file=sys.stderr)
    ap.print_help()
    return 1


def _run_gui(ap: argparse.ArgumentParser, rest: list[str]) -> int:
    try:
        from cosmo.cli.gui import main as gui_main
        return int(gui_main(rest))
    except Exception as e:
        return _print_gui_failure_help(ap, e)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    ap = build_parser()

    # Default behavior: `cosmo` (no args) launches GUI.
    # Fallback behavior: if GUI can't start, print help + hints instead of a traceback.
    if len(argv) == 0:
        if os.name != "nt" and not _has_display():
            print("No GUI display detected (headless session).", file=sys.stderr)
            print("Hint: run one of:", file=sys.stderr)
            print("  cosmo convert ...\n  cosmo calibrate ...\n  cosmo gui", file=sys.stderr)
            ap.print_help()
            return 2
        return _run_gui(ap, [])

    ns, rest = ap.parse_known_args(argv)

    if ns.version:
        print(_get_version())
        return 0

    dispatch = getattr(ns, "_dispatch", None)

    if dispatch == "convert":
        from cosmo.cli.convert import main as convert_main
        return int(convert_main(rest))

    if dispatch == "correct":
        from cosmo.cli.correct import main as correct_main
        return int(correct_main(rest))

    if dispatch == "calibrate":
        from cosmo.cli.calibrate import main as calibrate_main
        return int(calibrate_main(rest))

    if dispatch == "gui":
        return _run_gui(ap, rest)

    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
