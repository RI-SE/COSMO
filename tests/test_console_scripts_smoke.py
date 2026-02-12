from __future__ import annotations

import os
import shutil
import subprocess

import pytest


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env.setdefault("MPLBACKEND", "Agg")  # consistent with CI settings [2](blob:https://www.microsoft365.com/3377b2c7-ce90-4a5f-bcf4-96fccf281101)
    return subprocess.run(cmd, capture_output=True, text=True, env=env)


@pytest.mark.smoke
def test_console_scripts_exist_and_help_works():
    """
    Smoke test that console scripts from [project.scripts] are installed and usable.

    We test:
      - cosmo --version  (handled by cosmo.cli.main) [1](https://risecloud-my.sharepoint.com/personal/anders_thorsen_ri_se/Documents/Microsoft%20Copilot%20Chat%20Files/main.py)
      - cosmo convert --help / cosmo calibrate --help (subcommand dispatch in cosmo.cli.main) [1](https://risecloud-my.sharepoint.com/personal/anders_thorsen_ri_se/Documents/Microsoft%20Copilot%20Chat%20Files/main.py)
      - cosmo-convert --help (direct entry point to cosmo.cli.convert) [2](blob:https://www.microsoft365.com/3377b2c7-ce90-4a5f-bcf4-96fccf281101)
      - cosmo-calibrate --help (direct entry point to cosmo.cli.calibrate) [3](blob:https://www.microsoft365.com/c655cd7d-9ac4-4928-84e7-b9a8f04405bd)

    We *do not run* cosmo-gui because it launches the GUI immediately and CI is headless. [1](https://risecloud-my.sharepoint.com/personal/anders_thorsen_ri_se/Documents/Microsoft%20Copilot%20Chat%20Files/main.py)[1](https://risecloud-my.sharepoint.com/personal/anders_thorsen_ri_se/Documents/Microsoft%20Copilot%20Chat%20Files/main.py)
    """

    # Ensure the scripts exist on PATH (installed by pip via [project.scripts]).
    cosmo = shutil.which("cosmo")
    cosmo_convert = shutil.which("cosmo-convert")
    cosmo_calibrate = shutil.which("cosmo-calibrate")
    cosmo_gui = shutil.which("cosmo-gui")

    assert cosmo, "Expected 'cosmo' console script to be on PATH after install."
    assert cosmo_convert, "Expected 'cosmo-convert' console script to be on PATH after install."
    assert cosmo_calibrate, "Expected 'cosmo-calibrate' console script to be on PATH after install."
    assert cosmo_gui, "Expected 'cosmo-gui' console script to be on PATH after install."

    # 1) Top-level version (main.py handles --version and exits 0) [1](https://risecloud-my.sharepoint.com/personal/anders_thorsen_ri_se/Documents/Microsoft%20Copilot%20Chat%20Files/main.py)
    res = _run([cosmo, "--version"])
    assert res.returncode == 0, f"cosmo --version failed.\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"
    assert res.stdout.strip(), "cosmo --version produced no output."

    # 2) Subcommand dispatch via main.py (parse_known_args + forward rest) [1](https://risecloud-my.sharepoint.com/personal/anders_thorsen_ri_se/Documents/Microsoft%20Copilot%20Chat%20Files/main.py)
    res = _run([cosmo, "convert", "--help"])
    assert res.returncode == 0, f"cosmo convert --help failed.\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"

    res = _run([cosmo, "calibrate", "--help"])
    assert res.returncode == 0, f"cosmo calibrate --help failed.\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"

    # 3) Direct entry points to the thin modules (argparse-owned) [3](blob:https://www.microsoft365.com/c655cd7d-9ac4-4928-84e7-b9a8f04405bd)[2](blob:https://www.microsoft365.com/3377b2c7-ce90-4a5f-bcf4-96fccf281101)
    res = _run([cosmo_convert, "--help"])
    assert res.returncode == 0, f"cosmo-convert --help failed.\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"

    res = _run([cosmo_calibrate, "--help"])
    assert res.returncode == 0, f"cosmo-calibrate --help failed.\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"
