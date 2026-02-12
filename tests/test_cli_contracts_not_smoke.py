from __future__ import annotations

import json
from pathlib import Path

from tests.cli_helpers import run_cosmo


def test_calibrate_rejects_mixed_input_styles(tmp_path: Path):
    pp = tmp_path / "pixel_pairs.csv"
    vm = tmp_path / "visual_markers.csv"
    odr = tmp_path / "map.xodr"
    pp.write_text("point_name,u,v\np1,0,0\np2,1,0\np3,0,1\np4,1,1\n", encoding="utf-8")
    vm.write_text("point_name,E,N\np1,0,0\np2,1,0\np3,0,1\np4,1,1\n", encoding="utf-8")
    odr.write_text("<OpenDRIVE></OpenDRIVE>", encoding="utf-8")

    # Mix two styles intentionally -> should error (logic is in calibrate.py) [2](blob:https://www.microsoft365.com/c655cd7d-9ac4-4928-84e7-b9a8f04405bd)
    res = run_cosmo(
        ["calibrate", "--inputs", str(pp), str(vm), str(odr), "--pixel-pairs", str(pp)],
        cwd=tmp_path,
    )
    assert res.returncode != 0
    assert "Mixed input styles" in (res.stderr + res.stdout)


def test_convert_rejects_positional_and_flag_input(tmp_path: Path):
    ol = tmp_path / "openlabel.json"
    ol.write_text(json.dumps({"openlabel": {"objects": {}, "frames": {}}}), encoding="utf-8")

    # convert.py rejects positional + --input together [1](blob:https://www.microsoft365.com/3377b2c7-ce90-4a5f-bcf4-96fccf281101)
    res = run_cosmo(["convert", str(ol), "--input", str(ol), "--no-mcap"], cwd=tmp_path)
    assert res.returncode != 0
    assert "Provide either a positional input OR --input/--openlabel" in (res.stderr + res.stdout)
