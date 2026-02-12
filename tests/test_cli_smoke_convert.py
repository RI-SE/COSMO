from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.cli_helpers import extract_json_from_stdout, run_cosmo


@pytest.mark.smoke
def test_cli_convert_smoke_csv_only(tmp_path: Path):
    """
    Smoke test for the real CLI:
      cosmo convert --input openlabel.json --calibration Calibration.json --no-mcap --json

    Uses flags from cosmo.cli.convert and disables MCAP for CI stability. [1](blob:https://www.microsoft365.com/3377b2c7-ce90-4a5f-bcf4-96fccf281101)
    """
    # Minimal OpenLABEL input (one object, one frame, rbbox)
    openlabel = {
        "openlabel": {
            "objects": {
                "1": {"name": "obj1", "type": "car", "subtype": "car", "role": "moving"}
            },
            "frames": {
                "0": {
                    "objects": {
                        "1": {
                            "object_data": {
                                "rbbox": [
                                    {"name": "shape", "val": [100.0, 200.0, 50.0, 60.0, 0.0]}
                                ]
                            }
                        }
                    }
                }
            },
        }
    }
    in_json = tmp_path / "openlabel.json"
    in_json.write_text(json.dumps(openlabel), encoding="utf-8")

    # Minimal calibration homography (pixel->meter scale)
    calib = {
        "fps": 30.0,
        "image_width": 3840,
        "image_height": 2160,
        "homography": [[0.01, 0.0, 0.0], [0.0, 0.01, 0.0], [0.0, 0.0, 1.0]],
    }
    calib_path = tmp_path / "Calibration.json"
    calib_path.write_text(json.dumps(calib), encoding="utf-8")

    out_dir = tmp_path / "runs"
    out_dir.mkdir(parents=True, exist_ok=True)

    res = run_cosmo(
        [
            "convert",
            "--input",
            str(in_json),
            "--calibration",
            str(calib_path),
            "--output",
            str(out_dir),
            "--run-name",
            "smoke_convert",
            "--no-mcap",  # convert CLI uses BooleanOptionalAction; --no-mcap disables MCAP. [1](blob:https://www.microsoft365.com/3377b2c7-ce90-4a5f-bcf4-96fccf281101)
            "--json",
        ],
        cwd=tmp_path,
    )

    assert res.returncode == 0, (
        "cosmo convert failed.\n"
        f"STDOUT:\n{res.stdout}\n"
        f"STDERR:\n{res.stderr}\n"
    )

    payload = extract_json_from_stdout(res.stdout)

    run_dir = Path(payload["run_dir"])
    outputs_dir = Path(payload.get("outputs_dir") or payload["run_dir"])
    assert run_dir.exists(), f"run_dir does not exist: {run_dir}"
    assert outputs_dir.exists(), f"outputs_dir does not exist: {outputs_dir}"

    csv_path = payload.get("csv_path")
    assert csv_path, f"CLI did not report csv_path. Payload keys: {list(payload.keys())}"
    csv_path = Path(csv_path)
    assert csv_path.is_file(), f"Expected CSV not created: {csv_path}"

    lines = csv_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) >= 2, "CSV should contain header + at least one row"

    header = [c.strip() for c in lines[0].split(",")]
    for col in ("total_nanos", "idx", "x", "y", "yaw"):
        assert col in header, f"Missing expected column '{col}' in CSV header"
