# tests/test_convert_alignment_flags.py
from __future__ import annotations

import json
from pathlib import Path

from tests.cli_helpers import run_cosmo


def test_convert_accepts_alignment_flags(tmp_path: Path):
    ol = tmp_path/"openlabel.json"
    ol.write_text(json.dumps({
        "openlabel": {"objects": {"1": {"name":"o1","type":"car","subtype":"car","role":"moving"}},
                      "frames": {"0": {"objects": {"1": {"object_data": {"rbbox": [{"name":"shape","val":[10,20,3,4,0.1]}]}}}}}}
    }), encoding="utf-8")

    calib = tmp_path/"Calibration.json"
    calib.write_text(json.dumps({
        "fps": 30.0, "image_width": 100, "image_height": 100,
        "homography": [[0.1,0,0],[0,0.1,0],[0,0,1]],
    }), encoding="utf-8")

    out_dir = tmp_path/"runs"
    out_dir.mkdir()

    res = run_cosmo(
        ["convert", "--input", str(ol), "--calibration", str(calib),
         "--output", str(out_dir), "--run-name", "align",
         "--no-mcap", "--swap-xy", "--flip-x", "--flip-y", "--xy-offset", "1.0", "-2.0", "--yaw-offset-deg", "90"],
        cwd=tmp_path,
    )
    assert res.returncode == 0  # should be accepted and run successfully
