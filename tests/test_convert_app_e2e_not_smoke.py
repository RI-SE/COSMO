# tests/test_convert_app_e2e_not_smoke.py
from __future__ import annotations

import csv
import json
from pathlib import Path

from cosmo.app.convert_app import (  # [1](https://risecloud-my.sharepoint.com/personal/anders_thorsen_ri_se/Documents/Microsoft%20Copilot%20Chat%20Files/compute.py)
    ConvertConfig,
    run_convert,
)


def test_run_convert_end_to_end_csv(tmp_path: Path):
    ol = {
        "openlabel": {
            "objects": {"1": {"name": "obj1", "type": "car", "subtype": "car", "role": "moving"}},
            "frames": {"0": {"objects": {"1": {"object_data": {"rbbox": [{"name": "shape", "val": [10, 20, 4, 5, 0.0]}]}}}}},
        }
    }
    ol_path = tmp_path / "openlabel.json"
    ol_path.write_text(json.dumps(ol), encoding="utf-8")

    calib_path = tmp_path / "Calibration.json"
    calib_path.write_text(json.dumps({"fps": 10.0, "homography": [[1,0,0],[0,1,0],[0,0,1]]}), encoding="utf-8")

    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()

    cfg = ConvertConfig(
        openlabel=str(ol_path),
        calibration=str(calib_path),
        write_csv=True,
        write_mcap=False,
        out_dir=str(runs_dir),
        run_name="e2e",
    )
    result = run_convert(cfg, log_fn=None)

    assert result.csv_path is not None
    csv_path = Path(result.csv_path)
    assert csv_path.is_file()

    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        assert reader.fieldnames is not None
        assert "total_nanos" in reader.fieldnames
