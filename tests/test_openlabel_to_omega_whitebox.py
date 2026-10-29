from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
import pytest

from cosmo.converters.openlabel_to_omega import (  #
    convert_openlabel_to_omega,
    load_alignment,
    parse_openlabel,
)


def test_parse_openlabel_supports_rbbox_list_of_dicts():
    ol = {
        "openlabel": {
            "objects": {"1": {"name": "obj1", "type": "car", "subtype": "car", "role": "moving"}},
            "frames": {
                "0": {"objects": {"1": {"object_data": {"rbbox": [{"name": "shape", "val": [1, 2, 3, 4, 0.1]}]}}}}
            },
        }
    }
    objects_meta, frames = parse_openlabel(ol)
    assert "1" in objects_meta
    assert "0" in frames
    assert "1" in frames["0"]["objects"]
    assert frames["0"]["objects"]["1"]["rbbox"][:2] == [1.0, 2.0]


def _frame_with_vec(vec):
    obj = {"object_data": {"rbbox": [{"name": "shape", "val": [1, 2, 3, 4, 0.1]}], "vec": vec}}
    return {"openlabel": {"frames": {"0": {"objects": {"1": obj}}}}}


def test_parse_openlabel_reads_savant_confidence_newest_first():
    # SAVANT layout: vec is a list of {name, val}; histories are newest first
    vec = [{"name": "annotator", "val": ["markit_housekeeping(rot)", "markit_yolo"]},
           {"name": "confidence", "val": [0.8888, 0.42]}]
    _, frames = parse_openlabel(_frame_with_vec(vec))
    assert frames["0"]["objects"]["1"]["confidence"] == 0.8888


def test_parse_openlabel_missing_confidence_is_unknown():
    _, frames = parse_openlabel(_frame_with_vec([{"name": "annotator", "val": ["markit_yolo"]}]))
    assert frames["0"]["objects"]["1"]["confidence"] is None


def test_load_alignment_prefers_georef_over_calibration(tmp_path: Path):
    # calibration with fps=10
    calib_path = tmp_path / "Calibration.json"
    calib_path.write_text(json.dumps({"fps": 10, "homography": [[1,0,0],[0,1,0],[0,0,1]]}), encoding="utf-8")

    # georef with fps=20 and transformation_matrix
    georef_path = tmp_path / "my_georef_data.json"
    georef_path.write_text(json.dumps({
        "transform_method": "homography",
        "fps": 20,
        "transformation_matrix": [[2,0,0],[0,2,0],[0,0,1]],
    }), encoding="utf-8")

    fps, H, dims = load_alignment(str(calib_path), str(georef_path), fps_arg=None)
    assert fps == 20.0
    assert H is not None
    assert np.allclose(H, np.array([[2,0,0],[0,2,0],[0,0,1]], dtype=float))


def test_load_alignment_fps_precedence(tmp_path: Path):
    calib_path = tmp_path / "Calibration.json"
    calib_path.write_text(json.dumps({"fps": 10}), encoding="utf-8")
    georef_path = tmp_path / "my_georef_data.json"
    georef_path.write_text(json.dumps({"fps": None, "transformation_matrix": np.eye(3).tolist()}),
                           encoding="utf-8")

    # null georef fps falls through to the calibration; an explicit fps_arg overrides both
    assert load_alignment(str(calib_path), str(georef_path), fps_arg=None)[0] == 10.0
    assert load_alignment(str(calib_path), str(georef_path), fps_arg=25.0)[0] == 25.0
    assert load_alignment(None, None, fps_arg=None)[0] == 30.0


def test_convert_openlabel_to_omega_writes_csv_only(tmp_path: Path):
    # OpenLABEL minimal
    openlabel = {
        "openlabel": {
            "objects": {"1": {"name": "obj1", "type": "car", "subtype": "car", "role": "moving"}},
            "frames": {
                "0": {"objects": {"1": {"object_data": {"rbbox": [{"name": "shape", "val": [10, 20, 4, 5, 0.0]}]}}}},
                "1": {"objects": {"1": {"object_data": {"rbbox": [{"name": "shape", "val": [11, 20, 4, 5, 0.0]}]}}}},
            },
        }
    }
    ol_path = tmp_path / "openlabel.json"
    ol_path.write_text(json.dumps(openlabel), encoding="utf-8")

    calib_path = tmp_path / "Calibration.json"
    calib_path.write_text(json.dumps({
        "fps": 10.0,
        "image_width": 100,
        "image_height": 100,
        "homography": [[0.1,0,0],[0,0.1,0],[0,0,1]],
        "default_dimensions_m": {"car": {"length": 4.5, "width": 1.8, "height": 1.5}},
    }), encoding="utf-8")

    out_prefix = tmp_path / "out" / "run1"
    out_prefix.parent.mkdir(parents=True, exist_ok=True)

    convert_openlabel_to_omega(
        openlabel_path=str(ol_path),
        odr_path=None,
        out_prefix=str(out_prefix),
        calibration_path=str(calib_path),
        georef_data_path=None,
        fps_arg=None,
        write_csv=True,
        write_mcap=False,  # ensure no betterosi dependency
        swap_xy=False,
        flip_x=False,
        flip_y=False,
        xy_offset=(0.0, 0.0),
        yaw_offset_rad=0.0,
        log_fn=None,
    )

    csv_path = Path(str(out_prefix) + ".csv")
    assert csv_path.is_file()

    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        # Columns defined in module
        for col in ("total_nanos", "idx", "x", "y", "yaw", "type_name", "subtype_name", "role_name"):
            assert col in (reader.fieldnames or [])
        rows = list(reader)
        assert len(rows) >= 2
        # Ensure time is non-decreasing (rows are sorted by total_nanos)
        times = [int(r["total_nanos"]) for r in rows]
        assert times == sorted(times)


def _write_georef(tmp_path: Path, method: str, matrix):
    path = tmp_path / "georef.json"
    path.write_text(json.dumps({"transform_method": method, "transformation_matrix": matrix}))
    return str(path)


def test_load_alignment_reads_legacy_unknown_projective_georef_as_homography(tmp_path: Path):
    # ORBIT <= 0.8.0 labelled GUI homography exports "unknown"; the perspective row identifies them
    matrix = [[0.05, 0.0, 1.0], [0.0, 0.05, 2.0], [1e-5, 2e-4, 1.0]]
    with pytest.warns(UserWarning, match="read as homography"):
        _, H, _ = load_alignment(None, _write_georef(tmp_path, "unknown", matrix), None)
    np.testing.assert_allclose(H, matrix)


def test_load_alignment_rejects_unknown_affine_looking_georef(tmp_path: Path):
    matrix = [[0.05, 0.0, 1.0], [0.0, 0.05, 2.0], [0.0, 0.0, 1.0]]
    with pytest.raises(ValueError, match="Unsupported ORBIT georef transform_method"):
        load_alignment(None, _write_georef(tmp_path, "unknown", matrix), None)
