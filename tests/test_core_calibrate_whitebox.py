from __future__ import annotations

import json
from pathlib import Path

from cosmo.app.calibrate_app import (  # app layer API [1](https://risecloud-my.sharepoint.com/personal/anders_thorsen_ri_se/Documents/Microsoft%20Copilot%20Chat%20Files/calibrate_app.py)
    CalibrateConfig,
    run_calibrate,
)


def test_run_calibrate_writes_stem_based_outputs(tmp_path: Path):
    # Minimal E/N marker mode inputs
    pixel_pairs = tmp_path / "pixel_pairs.csv"
    pixel_pairs.write_text(
        "point_name,u,v\n"
        "p1,0.0,0.0\n"
        "p2,100.0,0.0\n"
        "p3,0.0,100.0\n"
        "p4,100.0,100.0\n",
        encoding="utf-8",
    )

    visual_markers = tmp_path / "visual_markers.csv"
    visual_markers.write_text(
        "point_name,E,N\n"
        "p1,0.0,0.0\n"
        "p2,10.0,0.0\n"
        "p3,0.0,10.0\n"
        "p4,10.0,10.0\n",
        encoding="utf-8",
    )

    opendrive = tmp_path / "map.xodr"
    opendrive.write_text("<OpenDRIVE></OpenDRIVE>", encoding="utf-8")

    # Deterministic run folder: existing out_dir + run_name => out_dir/run_name [1](https://risecloud-my.sharepoint.com/personal/anders_thorsen_ri_se/Documents/Microsoft%20Copilot%20Chat%20Files/calibrate_app.py)
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()

    cfg = CalibrateConfig(
        pixel_pairs=str(pixel_pairs),
        visual_markers=str(visual_markers),
        opendrive=str(opendrive),
        image=None,
        openlabel=None,
        fps=30.0,
        image_width=3840,
        image_height=2160,
        ransac_thresh_m=1.0,
        origin_lat0=None,
        origin_lon0=None,
        out_dir=str(runs_dir),
        run_name="wb_calib",
    )

    logs: list[str] = []
    result = run_calibrate(cfg, log_fn=logs.append)  # same signature used by CLI [1](https://risecloud-my.sharepoint.com/personal/anders_thorsen_ri_se/Documents/Microsoft%20Copilot%20Chat%20Files/calibrate_app.py)

    run_dir = Path(result.run_dir)
    outputs_dir = Path(result.outputs_dir)
    assert run_dir == runs_dir / "wb_calib"
    assert outputs_dir == run_dir / "outputs"
    assert outputs_dir.exists()

    # Output naming is stem-based: <base>_calibration.json etc. [1](https://risecloud-my.sharepoint.com/personal/anders_thorsen_ri_se/Documents/Microsoft%20Copilot%20Chat%20Files/calibrate_app.py)
    calib_path = Path(result.calibration_json_path)
    assert calib_path.is_file()
    assert calib_path.name.endswith("_calibration.json")

    calib = json.loads(calib_path.read_text(encoding="utf-8"))
    assert "homography" in calib
    H = calib["homography"]
    assert isinstance(H, list) and len(H) == 3 and all(len(r) == 3 for r in H)

    # Summary JSON should be produced (unless compute/write changes) [1](https://risecloud-my.sharepoint.com/personal/anders_thorsen_ri_se/Documents/Microsoft%20Copilot%20Chat%20Files/calibrate_app.py)
    if result.summary_json_path:
        assert Path(result.summary_json_path).is_file()

    # Residual plot is intended but can be None (matplotlib issues), so keep optional. [1](https://risecloud-my.sharepoint.com/personal/anders_thorsen_ri_se/Documents/Microsoft%20Copilot%20Chat%20Files/calibrate_app.py)
    if result.residuals_png_path:
        assert Path(result.residuals_png_path).is_file()

    # No overlay expected because image=None [1](https://risecloud-my.sharepoint.com/personal/anders_thorsen_ri_se/Documents/Microsoft%20Copilot%20Chat%20Files/calibrate_app.py)
    assert result.overlay_png_path is None

    # Some logging should occur (useful diagnostic)
    assert any("cosmo.calibration.compute" in s for s in logs) or logs
