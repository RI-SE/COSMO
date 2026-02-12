# tests/cli_helpers.py
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def run_cosmo(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    """
    Run COSMO CLI.

    - On Windows: use `python -m cosmo.cli.main ...` to avoid .cmd/.exe shim issues.
    - On non-Windows: prefer the real console script `cosmo` (from [project.scripts]),
      falling back to module execution if needed.
    """
    env = dict(os.environ)
    env.setdefault("MPLBACKEND", "Agg")

    if os.name == "nt":
        cmd = [sys.executable, "-m", "cosmo.cli.main", *args]  # real entrypoint logic [3](https://risecloud-my.sharepoint.com/personal/anders_thorsen_ri_se/Documents/Microsoft%20Copilot%20Chat%20Files/main.py)
    else:
        exe = shutil.which("cosmo")
        cmd = [exe, *args] if exe else [sys.executable, "-m", "cosmo.cli.main", *args]  # same entrypoint [3](https://risecloud-my.sharepoint.com/personal/anders_thorsen_ri_se/Documents/Microsoft%20Copilot%20Chat%20Files/main.py)

    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, env=env)


def extract_json_from_stdout(stdout: str) -> dict:
    """Extract a JSON object from mixed stdout (best effort)."""
    start = stdout.find("{")
    end = stdout.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise AssertionError(f"Could not find JSON in stdout.\nSTDOUT:\n{stdout}")
    return json.loads(stdout[start : end + 1])
