# tests/test_convert_outputs.py
from __future__ import annotations

import json
from pathlib import Path

from tests.cli_helpers import extract_json_from_stdout, run_cosmo


def test_convert_no_csv_reports_no_csv_path(tmp_path: Path):
    # Minimal OpenLABEL
    ol = tmp_path/"openlabel.json"
    ol.write_text(json.dumps({
        "openlabel": {"objects": {"1": {"name":"o1","type":"car","subtype":"car","role":"moving"}},
                      "frames": {"0": {"objects": {"1": {"object_data": {"rbbox": [{"name":"shape","val":[1,2,3,4,0]}]}}}}}}
    }), encoding="utf-8")

    # Calibration
    calib = tmp_path/"Calibration.json"
    calib.write_text(json.dumps({
        "fps": 30.0, "image_width": 100, "image_height": 100,
        "homography": [[1,0,0],[0,1,0],[0,0,1]],
    }), encoding="utf-8")

    out_dir = tmp_path/"runs"
    out_dir.mkdir()

    res = run_cosmo(
        ["convert", "--input", str(ol), "--calibration", str(calib),
         "--output", str(out_dir), "--run-name", "no_csv", "--no-csv", "--no-mcap", "--json"],
        cwd=tmp_path,
    )
    assert res.returncode == 0
    payload = extract_json_from_stdout(res.stdout)
    assert payload.get("csv_path") in (None, "", False)  # run_convert should reflect csv disabled [3](blob:https://www.microsoft365.com/3377b2c7-ce90-4a5f-bcf4-96fccf281101)
