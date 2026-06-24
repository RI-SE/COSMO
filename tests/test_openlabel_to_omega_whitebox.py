from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np

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
