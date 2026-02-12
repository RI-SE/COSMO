from __future__ import annotations

import json
from pathlib import Path

import pytest

from cosmo.converters.openlabel_to_omega import (
    convert_openlabel_to_omega,  # [1](https://risecloud-my.sharepoint.com/personal/anders_thorsen_ri_se/Documents/Microsoft%20Copilot%20Chat%20Files/openlabel_to_omega.py)
)


def _has_betterosi() -> bool:
    try:
        import betterosi  # noqa: F401
        return True
    except Exception:
        return False


def _try_read_mcap_topics(mcap_path: Path) -> set[str]:
    """
    Best-effort topic inspection. If the `mcap` package is installed, we read channels and return topics.
    Otherwise return empty set (still allows file existence checks).
    """
    try:
        from mcap.reader import make_reader  # type: ignore
    except Exception:
        return set()

    topics: set[str] = set()
    with mcap_path.open("rb") as f:
        reader = make_reader(f)
        for schema, channel, message in reader.iter_messages():
            topics.add(channel.topic)
    return topics


@pytest.mark.integration
@pytest.mark.skipif(not _has_betterosi(), reason="betterosi not installed; MCAP integration test skipped")  # [1](https://risecloud-my.sharepoint.com/personal/anders_thorsen_ri_se/Documents/Microsoft%20Copilot%20Chat%20Files/openlabel_to_omega.py)
def test_converter_writes_mcap_with_ground_truth_and_map(tmp_path: Path):
    # Minimal OpenLABEL with two frames (ensures multiple GT messages)
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

    # Calibration with simple homography (pixel->meters)
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

    # Provide OpenDRIVE so map embedding path is exercised (ground_truth_map topic) [1](https://risecloud-my.sharepoint.com/personal/anders_thorsen_ri_se/Documents/Microsoft%20Copilot%20Chat%20Files/openlabel_to_omega.py)
    odr_path = tmp_path / "map.xodr"
    odr_path.write_text("<OpenDRIVE></OpenDRIVE>", encoding="utf-8")

    out_prefix = tmp_path / "out" / "run1"
    out_prefix.parent.mkdir(parents=True, exist_ok=True)

    convert_openlabel_to_omega(
        openlabel_path=str(ol_path),
        odr_path=str(odr_path),
        out_prefix=str(out_prefix),
        calibration_path=str(calib_path),
        georef_data_path=None,
        fps_arg=None,
        write_csv=False,   # focus on MCAP in this integration test
        write_mcap=True,   # requires betterosi [1](https://risecloud-my.sharepoint.com/personal/anders_thorsen_ri_se/Documents/Microsoft%20Copilot%20Chat%20Files/openlabel_to_omega.py)
        swap_xy=False,
        flip_x=False,
        flip_y=False,
        xy_offset=(0.0, 0.0),
        yaw_offset_rad=0.0,
        log_fn=None,
    )

    mcap_path = Path(str(out_prefix) + ".mcap")
    assert mcap_path.is_file(), f"MCAP not created: {mcap_path}"
    assert mcap_path.stat().st_size > 0, "MCAP file is empty"

    # Optional deeper validation if the `mcap` reader is installed:
    topics = _try_read_mcap_topics(mcap_path)
    if topics:
        # Converter writes these topics without leading '/' [1](https://risecloud-my.sharepoint.com/personal/anders_thorsen_ri_se/Documents/Microsoft%20Copilot%20Chat%20Files/openlabel_to_omega.py)
        assert "ground_truth" in topics
        assert "ground_truth_map" in topics
