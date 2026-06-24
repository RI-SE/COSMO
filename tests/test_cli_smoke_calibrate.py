from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.cli_helpers import extract_json_from_stdout, run_cosmo


@pytest.mark.smoke
def test_cli_calibrate_smoke(tmp_path: Path):
    """
    Smoke test for the real CLI:
      cosmo calibrate --inputs pixel_pairs.csv visual_markers.csv map.xodr --json

    Uses --inputs style and --json output exactly as supported by cosmo.cli.calibrate.
    """
    # Minimal input CSVs
    pixel_pairs_csv = tmp_path / "pixel_pairs.csv"
    pixel_pairs_csv.write_text(
        """point_name,u,v
p1,0.0,0.0
p2,100.0,0.0
p3,0.0,100.0
p4,100.0,100.0
""",
        encoding="utf-8",
    )

    visual_markers_csv = tmp_path / "visual_markers.csv"
    visual_markers_csv.write_text(
        """point_name,E,N
p1,0.0,0.0
p2,10.0,0.0
p3,0.0,10.0
p4,10.0,10.0
""",
        encoding="utf-8",
    )

    # Dummy OpenDRIVE file (required arg in CLI contract)
    odr_path = tmp_path / "map.xodr"
    odr_path.write_text("<OpenDRIVE></OpenDRIVE>", encoding="utf-8")

    out_dir = tmp_path / "runs"
    out_dir.mkdir(parents=True, exist_ok=True)

    res = run_cosmo(
        [
            "calibrate",
            "--inputs",
            str(pixel_pairs_csv),
            str(visual_markers_csv),
            str(odr_path),
            "--fps",
            "30",
            "--image-width",
            "3840",
            "--image-height",
            "2160",
            "--ransac-thresh-m",
            "1.0",
            "--output",
            str(out_dir),
            "--run-name",
            "smoke_calibrate",
            "--json",
        ],
        cwd=tmp_path,
    )

    assert res.returncode == 0, (
        "cosmo calibrate failed.\n"
        f"STDOUT:\n{res.stdout}\n"
        f"STDERR:\n{res.stderr}\n"
    )

    payload = extract_json_from_stdout(res.stdout)

    # CLI prints asdict(result) when --json is provided.
    run_dir = Path(payload["run_dir"])
    outputs_dir = Path(payload.get("outputs_dir") or payload["run_dir"])
    calib_path = Path(payload["calibration_json_path"])

    assert run_dir.exists(), f"run_dir does not exist: {run_dir}"
    assert outputs_dir.exists(), f"outputs_dir does not exist: {outputs_dir}"
    assert calib_path.is_file(), f"Calibration.json not found: {calib_path}"

    data = json.loads(calib_path.read_text(encoding="utf-8"))
    assert "homography" in data, "Calibration.json missing 'homography'"
    H = data["homography"]
    assert isinstance(H, list) and len(H) == 3 and all(len(row) == 3 for row in H), "Homography must be 3x3"
