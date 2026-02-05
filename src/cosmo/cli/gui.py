def main() -> None:
    # Lazy import so CLI tooling doesn't require Qt unless you run the GUI.
    from cosmo.gui import main as gui_main
    gui_main()