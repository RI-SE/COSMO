from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path


def run_cosmo(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    """
    Run the real COSMO CLI installed from [project.scripts]:

        cosmo <args...>

    Fallback: if the console script isn't on PATH (rare in CI), run via module entrypoint:
        python -m cosmo.cli.main <args...>

    NOTE: Never call plain `cosmo` with no args in CI, because cosmo.cli.main defaults to GUI. [2](https://risecloud-my.sharepoint.com/personal/anders_thorsen_ri_se/Documents/Microsoft%20Copilot%20Chat%20Files/main.py)
    """
    env = dict(os.environ)
    env.setdefault("MPLBACKEND", "Agg")  # CI already sets this, but keep it deterministic. [1](blob:https://www.microsoft365.com/3377b2c7-ce90-4a5f-bcf4-96fccf281101)

    exe = shutil.which("cosmo")
    if exe:
        cmd = [exe, *args]
    else:
        # Still the real entrypoint, just executed as a module.
        cmd = [os.environ.get("PYTHON", "python"), "-m", "cosmo.cli.main", *args]  # dispatch logic in main.py [2](https://risecloud-my.sharepoint.com/personal/anders_thorsen_ri_se/Documents/Microsoft%20Copilot%20Chat%20Files/main.py)

    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, env=env)


def extract_json_from_stdout(stdout: str) -> dict:
    """Extract a JSON object from mixed stdout (best effort)."""
    start = stdout.find("{")
    end = stdout.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise AssertionError(f"Could not find JSON in stdout.\nSTDOUT:\n{stdout}")
    return json.loads(stdout[start : end + 1])
