# tests/cli_helpers.py
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path


def run_cosmo(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    """
    Prefer running the installed console script `cosmo`.
    Fallback to module execution if not on PATH.
    """
    env = dict(os.environ)
    env.setdefault("MPLBACKEND", "Agg")

    exe = shutil.which("cosmo")
    if exe:
        cmd = [exe, *args]
    else:
        # Still uses the real entrypoint (not run_cosmo.py)
        cmd = ["python", "-m", "cosmo.cli.main", *args]

    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, env=env)


def extract_json_from_stdout(stdout: str) -> dict:
    start = stdout.find("{")
    end = stdout.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise AssertionError(f"Could not find JSON in stdout.\nSTDOUT:\n{stdout}")
    return json.loads(stdout[start : end + 1])
