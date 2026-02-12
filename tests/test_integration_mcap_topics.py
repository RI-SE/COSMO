# tests/test_integration_mcap_topics.py
"""Integration test: validate MCAP topics written by COSMO conversion.

This test is intentionally marked as `integration` because it requires optional
MCAP/OSI tooling (`betterosi`) and (for topic inspection) the `mcap` reader.

It exercises the in-package converter via the app layer:
- `cosmo.app.convert_app.run_convert` creates a per-run folder and calls
  `cosmo.converters.openlabel_to_omega.convert_openlabel_to_omega`.

The converter writes (when MCAP is enabled and `betterosi` is installed):
- topic `ground_truth` (OSI GroundTruth messages)
- topic `ground_truth_map` (OpenDRIVE map), when an OpenDRIVE path is provided.

If the `mcap` reader is not installed, the test is skipped (because we cannot
inspect topics). File existence is covered by other integration tests.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cosmo.app.convert_app import ConvertConfig, run_convert
from tests.mcap_helpers import assert_mcap_has_topics, assert_mcap_nonempty, has_mcap_reader


def _has_betterosi() -> bool:
    try:
        import betterosi  # noqa: F401
        return True
    except Exception:
        return False


@pytest.mark.integration
@pytest.mark.skipif(not _has_betterosi(), reason="betterosi not installed; cannot write MCAP")
@pytest.mark.skipif(not has_mcap_reader(), reason="mcap reader not installed; cannot inspect topics")
def test_mcap_contains_ground_truth_and_map_topics(tmp_path: Path):
    # Minimal OpenLABEL: 2 frames to ensure at least two GT messages.
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

    # Minimal calibration homography
    calib_path = tmp_path / "Calibration.json"
    calib_path.write_text(
        json.dumps(
            {
                "fps": 10.0,
                "image_width": 100,
                "image_height": 100,
                "homography": [[0.1, 0.0, 0.0], [0.0, 0.1, 0.0], [0.0, 0.0, 1.0]],
                "default_dimensions_m": {"car": {"length": 4.5, "width": 1.8, "height": 1.5}},
            }
        ),
        encoding="utf-8",
    )

    # Provide an OpenDRIVE file so the converter embeds the map into MCAP.
    odr_path = tmp_path / "map.xodr"
    odr_path.write_text("<OpenDRIVE></OpenDRIVE>", encoding="utf-8")

    runs_dir = tmp_path / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)

    cfg = ConvertConfig(
        openlabel=str(ol_path),
        opendrive=str(odr_path),
        calibration=str(calib_path),
        write_csv=False,
        write_mcap=True,
        out_dir=str(runs_dir),
        run_name="mcap_topics",
    )

    result = run_convert(cfg, log_fn=None)

    assert result.mcap_path, "Expected mcap_path to be set when MCAP is written"
    mcap_path = assert_mcap_nonempty(result.mcap_path)

    # Assert both topics exist.
    assert_mcap_has_topics(mcap_path, {"ground_truth", "ground_truth_map"})

