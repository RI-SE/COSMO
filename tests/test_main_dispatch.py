# tests/test_main_dispatch.py
from __future__ import annotations

import os
import subprocess
import sys


def test_main_version_works_without_gui():
    # Use module entrypoint to avoid console-script issues on some Windows setups
    res = subprocess.run(
        [sys.executable, "-m", "cosmo.cli.main", "--version"],
        capture_output=True,
        text=True,
        env={**os.environ, "MPLBACKEND": "Agg"},
    )
    assert res.returncode == 0  # main.py returns 0 on --version [1](https://risecloud-my.sharepoint.com/personal/anders_thorsen_ri_se/Documents/Microsoft%20Copilot%20Chat%20Files/main.py)
    assert res.stdout.strip()  # prints version string [1](https://risecloud-my.sharepoint.com/personal/anders_thorsen_ri_se/Documents/Microsoft%20Copilot%20Chat%20Files/main.py)
