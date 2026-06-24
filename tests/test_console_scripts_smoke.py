from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env.setdefault("MPLBACKEND", "Agg")
    return subprocess.run(cmd, capture_output=True, text=True, env=env)


def _find_script(name: str) -> str | None:
    """Find a console script on PATH or, as a fallback, next to sys.executable (venv bin dir)."""
    found = shutil.which(name)
    if found:
        return found
    # When running via `.venv/bin/python -m pytest` without activating the venv,
    # scripts live alongside the interpreter but PATH is not updated.
    candidate = Path(sys.executable).parent / name
    if candidate.is_file():
        return str(candidate)
    # Windows: scripts have .exe suffix
    candidate_exe = candidate.with_suffix(".exe")
    if candidate_exe.is_file():
        return str(candidate_exe)
    return None


@pytest.mark.smoke
def test_console_scripts_exist_and_help_works():
    """
    Validate console scripts from [project.scripts] exist, and help/version work.

    On Windows we avoid executing the generated .EXE shims (can be blocked by policy)
    and instead validate help via `python -m ...`.
    """
    cosmo = _find_script("cosmo")
    cosmo_convert = _find_script("cosmo-convert")
    cosmo_calibrate = _find_script("cosmo-calibrate")
    cosmo_gui = _find_script("cosmo-gui")

    assert cosmo, "Expected 'cosmo' on PATH or in venv bin dir after install."
    assert cosmo_convert, "Expected 'cosmo-convert' on PATH or in venv bin dir after install."
    assert cosmo_calibrate, "Expected 'cosmo-calibrate' on PATH or in venv bin dir after install."
    assert cosmo_gui, "Expected 'cosmo-gui' on PATH or in venv bin dir after install."

    # Always safe: module entrypoint tests real main.py behavior.
    res = _run([sys.executable, "-m", "cosmo.cli.main", "--version"])
    assert res.returncode == 0, f"cosmo --version failed.\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"
    assert res.stdout.strip(), "cosmo --version produced no output."

    # Dispatch help via main.py (convert/calibrate are subcommands).
    res = _run([sys.executable, "-m", "cosmo.cli.main", "convert", "--help"])
    assert res.returncode == 0, f"cosmo convert --help failed.\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"

    res = _run([sys.executable, "-m", "cosmo.cli.main", "calibrate", "--help"])
    assert res.returncode == 0, f"cosmo calibrate --help failed.\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"

    # Direct entrypoints: run via module on Windows to avoid blocked .EXE shims.
    if os.name == "nt":
        res = _run([sys.executable, "-m", "cosmo.cli.convert", "--help"])
        assert res.returncode == 0, f"python -m cosmo.cli.convert --help failed.\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"

        res = _run([sys.executable, "-m", "cosmo.cli.calibrate", "--help"])
        assert res.returncode == 0, f"python -m cosmo.cli.calibrate --help failed.\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"
    else:
        res = _run([cosmo_convert, "--help"])
        assert res.returncode == 0, f"cosmo-convert --help failed.\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"

        res = _run([cosmo_calibrate, "--help"])
        assert res.returncode == 0, f"cosmo-calibrate --help failed.\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"

    # Do NOT execute cosmo-gui in tests: it launches GUI immediately and can fail headless.
