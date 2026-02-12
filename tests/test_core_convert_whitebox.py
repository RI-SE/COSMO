from __future__ import annotations

import csv
import json
from pathlib import Path

from cosmo.app.convert_app import (  # app layer API [2](https://risecloud-my.sharepoint.com/personal/anders_thorsen_ri_se/Documents/Microsoft%20Copilot%20Chat%20Files/convert_app.py)
    ConvertConfig,
    run_convert,
)


def test_run_convert_writes_csv_and_sets_csv_path(tmp_path: Path):
    # Minimal OpenLABEL with one rbbox
    openlabel = {
        "openlabel": {
            "objects": {"1": {"name": "obj1", "type": "car", "subtype": "car", "role": "moving"}},
            "frames": {
                "0": {
                    "objects": {
                        "1": {
                            "object_data": {
                                "rbbox": [{"name": "shape", "val": [100.0, 200.0, 50.0, 60.0, 0.0]}]
                            }
                        }
                    }
                }
            },
        }
    }
    openlabel_path = tmp_path / "openlabel.json"
    openlabel_path.write_text(json.dumps(openlabel), encoding="utf-8")

    # Minimal calibration file path (convert_app passes this to converter if provided) [2](https://risecloud-my.sharepoint.com/personal/anders_thorsen_ri_se/Documents/Microsoft%20Copilot%20Chat%20Files/convert_app.py)
    calib = {
        "fps": 30.0,
        "image_width": 3840,
        "image_height": 2160,
        "homography": [[0.01, 0.0, 0.0], [0.0, 0.01, 0.0], [0.0, 0.0, 1.0]],
    }
    calib_path = tmp_path / "Calibration.json"
    calib_path.write_text(json.dumps(calib), encoding="utf-8")

    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()

    cfg = ConvertConfig(
        openlabel=str(openlabel_path),
        opendrive=None,
        georef_data=None,
        calibration=str(calib_path),
        fps=None,
        write_csv=True,
        write_mcap=False,  # keep core tests independent of optional MCAP deps [2](https://risecloud-my.sharepoint.com/personal/anders_thorsen_ri_se/Documents/Microsoft%20Copilot%20Chat%20Files/convert_app.py)
        swap_xy=False,
        flip_x=False,
        flip_y=False,
        xy_offset=(0.0, 0.0),
        yaw_offset_deg=0.0,
        out_dir=str(runs_dir),
        run_name="wb_convert",
    )

    logs: list[str] = []
    result = run_convert(cfg, log_fn=logs.append)  # same signature used by CLI [2](https://risecloud-my.sharepoint.com/personal/anders_thorsen_ri_se/Documents/Microsoft%20Copilot%20Chat%20Files/convert_app.py)

    run_dir = Path(result.run_dir)
    outputs_dir = Path(result.outputs_dir)
    assert run_dir == runs_dir / "wb_convert"
    assert outputs_dir == run_dir / "outputs"
    assert outputs_dir.exists()

    # csv_path is only set if the file exists (summary filters non-existent files) [2](https://risecloud-my.sharepoint.com/personal/anders_thorsen_ri_se/Documents/Microsoft%20Copilot%20Chat%20Files/convert_app.py)
    assert result.csv_path is not None
    csv_path = Path(result.csv_path)
    assert csv_path.is_file()
    assert csv_path.name.endswith(".csv")

    # Minimal schema checks (avoid coupling to full schema evolution)
    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        assert reader.fieldnames is not None
        for col in ("total_nanos", "idx", "x", "y", "yaw"):
            assert col in reader.fieldnames

        rows = list(reader)
        assert len(rows) >= 1

    # MCAP disabled => should be None
    assert result.mcap_path is None


def test_run_convert_no_csv_results_in_none_csv_path(tmp_path: Path):
    openlabel_path = tmp_path / "openlabel.json"
    openlabel_path.write_text(json.dumps({"openlabel": {"objects": {}, "frames": {}}}), encoding="utf-8")

    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()

    cfg = ConvertConfig(
        openlabel=str(openlabel_path),
        write_csv=False,
        write_mcap=False,
        out_dir=str(runs_dir),
        run_name="wb_no_csv",
    )  # relies on defaults for other fields [2](https://risecloud-my.sharepoint.com/personal/anders_thorsen_ri_se/Documents/Microsoft%20Copilot%20Chat%20Files/convert_app.py)

    result = run_convert(cfg, log_fn=lambda _: None)
    assert result.csv_path is None
    assert result.mcap_path is None
