from __future__ import annotations

import json
from pathlib import Path

import pytest

from cosmo.app.convert_app import (  #
    ConvertConfig,
    run_convert,
)


def _has_betterosi() -> bool:
    try:
        import betterosi  # noqa: F401
        return True
    except Exception:
        return False


@pytest.mark.integration
@pytest.mark.skipif(not _has_betterosi(), reason="betterosi not installed; MCAP integration test skipped")  #
def test_run_convert_app_writes_mcap_and_sets_result_path(tmp_path: Path):
    # Minimal OpenLABEL
    openlabel = {
        "openlabel": {
            "objects": {"1": {"name": "obj1", "type": "car", "subtype": "car", "role": "moving"}},
            "frames": {"0": {"objects": {"1": {"object_data": {"rbbox": [{"name": "shape", "val": [10, 20, 4, 5, 0.0]}]}}}}},
        }
    }
    ol_path = tmp_path / "openlabel.json"
    ol_path.write_text(json.dumps(openlabel), encoding="utf-8")

    # Calibration
    calib_path = tmp_path / "Calibration.json"
    calib_path.write_text(json.dumps({"fps": 10.0, "homography": [[1, 0, 0], [0, 1, 0], [0, 0, 1]]}), encoding="utf-8")

    # OpenDRIVE embedded flag in convert_app summary is based on cfg.opendrive
    odr_path = tmp_path / "map.xodr"
    odr_path.write_text("<OpenDRIVE></OpenDRIVE>", encoding="utf-8")

    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()

    cfg = ConvertConfig(
        openlabel=str(ol_path),
        opendrive=str(odr_path),
        calibration=str(calib_path),
        write_csv=False,
        write_mcap=True,
        out_dir=str(runs_dir),
        run_name="int_mcap",
    )  # config matches app layer

    result = run_convert(cfg, log_fn=None)

    assert Path(result.run_dir).exists()
    assert Path(result.outputs_dir).exists()

    # convert_app only sets mcap_path if file exists
    assert result.mcap_path is not None
    assert Path(result.mcap_path).is_file()
    assert Path(result.mcap_path).stat().st_size > 0

    # CSV disabled => should remain None
    assert result.csv_path is None
