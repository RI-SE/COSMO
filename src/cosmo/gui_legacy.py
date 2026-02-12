"""
Legacy GUI shim.

This module exists for backwards compatibility with earlier layouts where the GUI
lived in src/cosmo/gui.py.

The actual GUI implementation now lives in:
  cosmo.gui.main_window

Preferred usage:
  - CLI:  cosmo gui
  - API:  from cosmo.gui.main_window import main
"""

from __future__ import annotations


def main() -> None:
    """Launch the COSMO GUI."""
    from cosmo.gui.main_window import main as _main
    _main()


if __name__ == "__main__":
    main()
