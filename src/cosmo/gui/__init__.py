"""
COSMO GUI package.

The main GUI entrypoint is:
  cosmo.gui.main_window.main

For convenience, we re-export it here so you can also do:
  from cosmo.gui import main
"""

from __future__ import annotations

from .main_window import main

__all__ = ["main"]
