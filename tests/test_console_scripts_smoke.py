from __future__ import annotations

import os
import shutil
import subprocess
import sys

import pytest


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env.setdefault("MPLBACKEND", "Agg")
    return subprocess.run(cmd, capture_output=True, text=True, env=env)


@pytest.mark.smoke
def test_console_scripts_exist_and_help_works():
    """
    Validate console scripts from [project.scripts] exist, and help/version work.

    On Windows we avoid executing the generated .EXE shims (can be blocked by policy)
    and instead validate help via `python -m ...`.
    """
    cosmo = shutil.which("cosmo")
    cosmo_convert = shutil.which("cosmo-convert")
    cosmo_calibrate = shutil.which("cosmo-calibrate")
    cosmo_gui = shutil.which("cosmo-gui")

    assert cosmo, "Expected 'cosmo' on PATH after install."
    assert cosmo_convert, "Expected 'cosmo-convert' on PATH after install."
    assert cosmo_calibrate, "Expected 'cosmo-calibrate' on PATH after install."
    assert cosmo_gui, "Expected 'cosmo-gui' on PATH after install."

    # Always safe: module entrypoint tests real main.py behavior. [3](https://risecloud-my.sharepoint.com/personal/anders_thorsen_ri_se/Documents/Microsoft%20Copilot%20Chat%20Files/main.py)
    res = _run([sys.executable, "-m", "cosmo.cli.main", "--version"])
    assert res.returncode == 0, f"cosmo --version failed.\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"
    assert res.stdout.strip(), "cosmo --version produced no output."

    # Dispatch help via main.py (convert/calibrate are subcommands). [3](https://risecloud-my.sharepoint.com/personal/anders_thorsen_ri_se/Documents/Microsoft%20Copilot%20Chat%20Files/main.py)
    res = _run([sys.executable, "-m", "cosmo.cli.main", "convert", "--help"])
    assert res.returncode == 0, f"cosmo convert --help failed.\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"

    res = _run([sys.executable, "-m", "cosmo.cli.main", "calibrate", "--help"])
    assert res.returncode == 0, f"cosmo calibrate --help failed.\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"

    # Direct entrypoints: run via module on Windows to avoid blocked .EXE shims. [1](https://risecloud-my.sharepoint.com/personal/anders_thorsen_ri_se/Documents/Microsoft%20Copilot%20Chat%20Files/main.py)[2](https://risecloud-my.sharepoint.com/personal/anders_thorsen_ri_se/Documents/Microsoft%20Copilot%20Chat%20Files/main.py)
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

    # Do NOT execute cosmo-gui in tests: it launches GUI immediately and can fail headless. [3](https://risecloud-my.sharepoint.com/personal/anders_thorsen_ri_se/Documents/Microsoft%20Copilot%20Chat%20Files/main.py)[3](https://risecloud-my.sharepoint.com/personal/anders_thorsen_ri_se/Documents/Microsoft%20Copilot%20Chat%20Files/main.py)
