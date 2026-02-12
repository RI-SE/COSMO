# tests/test_calibrate_cli_validation.py
from __future__ import annotations

from pathlib import Path

from tests.cli_helpers import run_cosmo


def test_calibrate_rejects_mixed_input_styles(tmp_path: Path):
    pp = tmp_path/"pixel_pairs.csv"
    vm = tmp_path/"visual_markers.csv"
    odr = tmp_path/"map.xodr"
    pp.write_text("point_name,u,v\np1,0,0\np2,1,0\np3,0,1\np4,1,1\n", encoding="utf-8")
    vm.write_text("point_name,E,N\np1,0,0\np2,1,0\np3,0,1\np4,1,1\n", encoding="utf-8")
    odr.write_text("<OpenDRIVE></OpenDRIVE>", encoding="utf-8")

    # Mix: --inputs + also pass --pixel-pairs (should error)
    res = run_cosmo(
        ["calibrate", "--inputs", str(pp), str(vm), str(odr), "--pixel-pairs", str(pp)],
        cwd=tmp_path,
    )
    assert res.returncode != 0
    assert "Mixed input styles detected" in (res.stderr + res.stdout)  # error text comes from argparse error [2](blob:https://www.microsoft365.com/c655cd7d-9ac4-4928-84e7-b9a8f04405bd)
