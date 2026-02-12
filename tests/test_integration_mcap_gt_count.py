# tests/test_integration_mcap_gt_count.py
"""Integration test: validate GroundTruth message count and MCAP timing semantics.

This test requires:
- `betterosi` (to write MCAP)
- `mcap` Python reader (to inspect messages)

It generates a tiny OpenLABEL with a known number of frames, runs COSMO conversion
via the app layer (run_convert), then inspects the resulting MCAP:

Checks:
1) `ground_truth` message count == number of frames.
2) `ground_truth_map` message count == 1 (map written once).
3) `ground_truth` message log_time is strictly increasing.

Marked with `@pytest.mark.integration` so it can be run separately.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cosmo.app.convert_app import (  # app layer [2](https://risecloud-my.sharepoint.com/personal/anders_thorsen_ri_se/Documents/Microsoft%20Copilot%20Chat%20Files/compute.py)
    ConvertConfig,
    run_convert,
)
from tests.mcap_helpers import assert_mcap_nonempty, has_mcap_reader


def _has_betterosi() -> bool:
    try:
        import betterosi  # noqa: F401
        return True
    except Exception:
        return False


def _collect_topic_log_times(mcap_path: Path, topic: str) -> list[int]:
    """Collect log_time values for messages on a given topic using the `mcap` reader."""
    from mcap.reader import make_reader  # type: ignore

    times: list[int] = []
    with mcap_path.open("rb") as f:
        reader = make_reader(f)
        # iter_messages yields (schema, channel, message)
        for _schema, channel, msg in reader.iter_messages():
            if channel.topic == topic:
                # `msg` typically has `log_time`; fall back to attribute lookup defensively.
                t = getattr(msg, "log_time", None)
                if t is None:
                    raise AssertionError("MCAP message missing log_time; cannot validate timing.")
                times.append(int(t))
    return times


def _count_topic_messages(mcap_path: Path, topic: str) -> int:
    """Count messages on a given topic."""
    return len(_collect_topic_log_times(mcap_path, topic))


@pytest.mark.integration
@pytest.mark.skipif(not _has_betterosi(), reason="betterosi not installed; cannot write MCAP")  # converter depends on it [1](https://risecloud-my.sharepoint.com/personal/anders_thorsen_ri_se/Documents/Microsoft%20Copilot%20Chat%20Files/openlabel_to_omega.py)
@pytest.mark.skipif(not has_mcap_reader(), reason="mcap reader not installed; cannot inspect MCAP")
def test_mcap_gt_count_map_once_and_strictly_increasing_log_time(tmp_path: Path):
    # Create OpenLABEL with a known number of frames.
    frame_ids = ["0", "1", "2"]  # 3 frames => expect 3 ground_truth messages

    openlabel = {
        "openlabel": {
            "objects": {"1": {"name": "obj1", "type": "car", "subtype": "car", "role": "moving"}},
            "frames": {
                fid: {
                    "objects": {
                        "1": {
                            "object_data": {
                                "rbbox": [{"name": "shape", "val": [10 + int(fid), 20.0, 4.0, 5.0, 0.0]}]
                            }
                        }
                    }
                }
                for fid in frame_ids
            },
        }
    }

    ol_path = tmp_path / "openlabel.json"
    ol_path.write_text(json.dumps(openlabel), encoding="utf-8")

    # Calibration homography (simple scale) + fps=10 => dt=0.1s => total_nanos increments by 1e8
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

    # OpenDRIVE included so map is embedded as `ground_truth_map` (written once with log_time=0) [1](https://risecloud-my.sharepoint.com/personal/anders_thorsen_ri_se/Documents/Microsoft%20Copilot%20Chat%20Files/openlabel_to_omega.py)
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
        run_name="mcap_gt_count",
    )  # app layer writes into per-run outputs/ folder [2](https://risecloud-my.sharepoint.com/personal/anders_thorsen_ri_se/Documents/Microsoft%20Copilot%20Chat%20Files/compute.py)

    result = run_convert(cfg, log_fn=None)
    assert result.mcap_path, "Expected mcap_path to be set when MCAP is written"  # app layer sets only if file exists [2](https://risecloud-my.sharepoint.com/personal/anders_thorsen_ri_se/Documents/Microsoft%20Copilot%20Chat%20Files/compute.py)

    mcap_path = assert_mcap_nonempty(result.mcap_path)

    # 1) GroundTruth message count == frame count
    gt_times = _collect_topic_log_times(mcap_path, "ground_truth")
    assert len(gt_times) == len(frame_ids), f"Expected {len(frame_ids)} ground_truth messages, got {len(gt_times)}"

    # 2) Map message written exactly once at log_time=0 (converter writes once) [1](https://risecloud-my.sharepoint.com/personal/anders_thorsen_ri_se/Documents/Microsoft%20Copilot%20Chat%20Files/openlabel_to_omega.py)
    map_times = _collect_topic_log_times(mcap_path, "ground_truth_map")
    assert len(map_times) == 1, f"Expected exactly 1 ground_truth_map message, got {len(map_times)}"
    assert map_times[0] == 0, f"Expected ground_truth_map log_time=0, got {map_times[0]}"

    # 3) GroundTruth log_time strictly increasing (converter uses log_time=total_nanos) [1](https://risecloud-my.sharepoint.com/personal/anders_thorsen_ri_se/Documents/Microsoft%20Copilot%20Chat%20Files/openlabel_to_omega.py)
    assert gt_times == sorted(gt_times), f"ground_truth log_time not non-decreasing: {gt_times}"
    assert all(b > a for a, b in zip(gt_times, gt_times[1:])), f"ground_truth log_time not strictly increasing: {gt_times}"
