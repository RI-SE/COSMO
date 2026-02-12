# src/cosmo/cli/gui.py
from __future__ import annotations


def main(argv=None) -> int:
    # GUI ignores argv; kept for symmetry with other CLI modules
    from cosmo.gui.main_window import main as gui_main
    gui_main()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
