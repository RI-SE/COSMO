from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from cosmo.calibration.compute import (  #
    compute_calibration,
    write_calibration_outputs,
)


def _write_min_en_inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
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
    odr = tmp_path / "map.xodr"
    odr.write_text("<OpenDRIVE></OpenDRIVE>", encoding="utf-8")
    return pixel_pairs, visual_markers, odr


def test_compute_calibration_en_mode_returns_homography(tmp_path: Path):
    pixel_pairs, visual_markers, odr = _write_min_en_inputs(tmp_path)

    comp = compute_calibration(
        pixel_pairs_csv=str(pixel_pairs),
        visual_markers_csv=str(visual_markers),
        opendrive_path=str(odr),
        ransac_thresh_m=1.0,
        log_fn=None,
    )

    assert comp.H.shape == (3, 3)
    assert np.isfinite(comp.H).all()
    assert len(comp.inlier_idx) >= 4
    assert comp.pix_points.shape[1] == 2
    assert comp.world_points.shape[1] == 2


def test_write_calibration_outputs_writes_expected_json_schema(tmp_path: Path):
    pixel_pairs, visual_markers, odr = _write_min_en_inputs(tmp_path)

    comp = compute_calibration(
        pixel_pairs_csv=str(pixel_pairs),
        visual_markers_csv=str(visual_markers),
        opendrive_path=str(odr),
        ransac_thresh_m=1.0,
    )

    calib_path = tmp_path / "out" / "test_calibration.json"
    summary_path = tmp_path / "out" / "test_summary.json"
    resid_png = tmp_path / "out" / "resid.png"

    outs = write_calibration_outputs(
        comp,
        calibration_json_path=str(calib_path),
        summary_json_path=str(summary_path),
        fps=30.0,
        image_width=3840,
        image_height=2160,
        residuals_png_path=str(resid_png),  # optional; may become None on plot errors
        overlay_png_path=None,
        image_path=None,
        openlabel_path=None,
    )

    assert Path(outs.calibration_json_path).is_file()
    assert Path(outs.summary_json_path).is_file()

    calib = json.loads(Path(outs.calibration_json_path).read_text(encoding="utf-8"))
    # Required keys written by write_calibration_outputs
    for k in ("fps", "image_width", "image_height", "homography", "intrinsics", "extrinsics", "default_dimensions_m"):
        assert k in calib
    H = calib["homography"]
    assert isinstance(H, list) and len(H) == 3 and all(len(r) == 3 for r in H)

    summary = json.loads(Path(outs.summary_json_path).read_text(encoding="utf-8"))
    for k in ("rmse_m", "inliers_count", "pairs_used", "pixel_points", "world_points_ENU_m", "homography"):
        assert k in summary

    # Residual plot can be omitted if matplotlib errors; if present, ensure file exists
    if outs.residuals_png_path is not None:
        assert Path(outs.residuals_png_path).is_file()


def test_compute_calibration_rejects_bad_pixel_pairs_schema(tmp_path: Path):
    pixel_pairs = tmp_path / "pixel_pairs.csv"
    pixel_pairs.write_text("wrong,u,v\np1,0,0\n", encoding="utf-8")

    visual_markers = tmp_path / "visual_markers.csv"
    visual_markers.write_text("point_name,E,N\np1,0,0\np2,1,0\np3,0,1\np4,1,1\n", encoding="utf-8")

    odr = tmp_path / "map.xodr"
    odr.write_text("<OpenDRIVE></OpenDRIVE>", encoding="utf-8")

    with pytest.raises(RuntimeError, match="pixel_pairs CSV must have columns"):
        compute_calibration(str(pixel_pairs), str(visual_markers), str(odr))
