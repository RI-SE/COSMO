"""Uncertainty propagation, smoothing, companion OpenLABEL and SensorData (scenario-uncertainty SPEC P5)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from cosmo.converters.kinematics import rts_smooth, smooth_tracks
from cosmo.converters.openlabel_to_omega import convert_openlabel_to_omega
from cosmo.converters.quality import (
    Propagation,
    box_quality,
    default_sigma_px,
    post_transform_matrix,
)

H_SCALE = np.array([[0.1, 0, 0], [0, 0.1, 0], [0, 0, 1.0]])  # 0.1 m per px


def _sample(cx=100.0, cy=100.0, w=40.0, h=10.0):
    return {"oid": "1", "rbbox": [cx, cy, w, h, 0.0], "h_veh": 1.5, "heading_world": 0.0, "yaw": 0.0}


def test_box_quality_reads_entries_and_infers_legacy_origin():
    vec = [{"name": "annotator", "val": ["markit_housekeeping(gap)", "markit_yolo"]},
           {"name": "sigma_px", "val": [1, 2, 3, 4, 0.1]}, {"name": "gap_distance", "val": [3]}]
    quality = box_quality(vec)
    assert quality["origin"] == "interpolated" and quality["gap_distance"] == 3
    assert quality["sigma_px"] == [1.0, 2.0, 3.0, 4.0, 0.1] and quality["p_exist"] is None
    assert box_quality([{"name": "origin", "val": ["reviewed"]}])["origin"] == "reviewed"


def test_default_sigma_follows_spec_p7():
    # sqrt(40*10) = 20 px
    assert default_sigma_px(40, 10, "detected", None)[:2] == pytest.approx([0.4, 0.4])
    assert default_sigma_px(40, 10, "manual", None)[0] == pytest.approx(0.2)
    assert default_sigma_px(40, 10, "interpolated", 5)[0] == pytest.approx(0.6)
    assert default_sigma_px(40, 10, "tracked", None)[0] == pytest.approx(1.0)  # unknown gap = 15


def test_post_transform_swaps_and_flips_covariance():
    T = post_transform_matrix(swap_xy=True, flip_x=True)
    cov = T @ np.diag([1.0, 4.0]) @ T.T
    assert np.allclose(cov, np.diag([4.0, 1.0]))


def test_pixel_and_frame_terms(tmp_path):
    video = {"stabilization": {"per_frame": {"reproj_err_px": [2.0, None], "filled": [False, True]}}}
    propagation = Propagation(H_SCALE, None, {}, video)
    quality = {"sigma_px": [2.0, 2.0, 1.0, 1.0, 0.05], "origin": "detected", "gap_distance": None, "p_exist": 0.9}

    frame0 = propagation.sample(_sample(), quality, 0, "car")
    # pixel: (0.1*2)² = 0.04; frame: 2 px RMS 2D -> 2 px²/axis -> 0.02 m²
    assert np.allclose(frame0["cov_noise"], np.eye(2) * 0.06)
    assert np.allclose(frame0["cov_bias"], 0) and frame0["sigma_size"] == pytest.approx([0.1, 0.1])
    propagation.sample(_sample(), quality, 1, "car")  # filled frame: unknown
    assert propagation.run_quality()["defaults_used"] == ["frame", "georef"]


def test_smoother_reduces_noise_but_keeps_bias():
    rng = np.random.default_rng(0)
    t = np.arange(200) / 30.0
    truth = np.column_stack([10 * t, 2 * t])
    z = truth + rng.normal(0, 0.2, truth.shape)
    states, covs = rts_smooth(t, z, np.repeat(np.eye(2)[None] * 0.04, len(t), axis=0))
    assert np.sqrt(np.mean((states[20:-20, :2] - truth[20:-20]) ** 2)) < 0.1
    assert np.allclose(states[20:-20, 2], 10, atol=0.5) and covs[100, 0, 0] < 0.04

    samples = [{"idx": 1, "X": x, "Y": y, "vel": (0, 0, 0), "acc": (0, 0, 0), "cov_noise": np.eye(2) * 0.04}
               for x, y in z]
    smooth_tracks(zip(t, ([s] for s in samples)), replace=True)
    assert samples[100]["vel"][0] == pytest.approx(10, abs=0.5)


def _convert(tmp_path: Path, **kwargs):
    boxes = {str(f): {"objects": {"1": {"object_data": {
        "rbbox": [{"name": "shape", "val": [100 + 5 * f, 100, 40, 10, 0.0]}],
        "vec": [{"name": "annotator", "val": ["markit_yolo"]}, {"name": "p_exist", "val": [0.8]}]}}}}
        for f in range(10)}
    openlabel = {"openlabel": {"metadata": {"schema_version": "1.1"},
                               "objects": {"1": {"name": "car1", "type": "car"}}, "frames": boxes,
                               "tags": {"0": {"type": "WeatherRain", "tag_data": {}}}}}
    (tmp_path / "ol.json").write_text(json.dumps(openlabel))
    (tmp_path / "calib.json").write_text(json.dumps({"fps": 10.0, "homography": H_SCALE.tolist()}))
    report = {}
    convert_openlabel_to_omega(openlabel_path=str(tmp_path / "ol.json"), odr_path=None,
                               out_prefix=str(tmp_path / "out"), calibration_path=str(tmp_path / "calib.json"),
                               quality_report=report, **kwargs)
    return report


def test_companion_variants(tmp_path):
    report = _convert(tmp_path, write_mcap=False, companion_path=str(tmp_path / "out.companion.json"),
                      companion_frames=True, smooth_kinematics=True)
    doc = json.loads((tmp_path / "out.companion.json").read_text())["openlabel"]
    assert doc["metadata"]["quality"]["kinematics"]["method"] == "rts_constant_acceleration"
    assert "sigma_px" in doc["metadata"]["quality"]["defaults_used"]
    assert doc["tags"]["0"]["type"] == "WeatherRain"
    num = {e["name"]: e["val"] for e in doc["objects"]["1"]["object_data"]["num"]}
    assert num["n_frames"] == 10 and num["p_exist_mean"] == pytest.approx(0.8)
    vec = {e["name"]: e["val"] for e in doc["frames"]["5"]["objects"]["1"]["object_data"]["vec"]}
    assert vec["velocity"][0] == pytest.approx(5.0, abs=0.2)  # 5 px/frame * 0.1 m * 10 fps
    assert {"position_cov_rel", "velocity_cov", "sigma_size", "origin", "p_exist"} <= set(vec)
    assert report["run"]["share_interpolated"]["value"] == 0.0


def test_sensor_data_topic(tmp_path):
    betterosi = pytest.importorskip("betterosi")
    reader = pytest.importorskip("mcap.reader")
    _convert(tmp_path, write_csv=False, sensor_data=True)

    with open(tmp_path / "out.mcap", "rb") as f:
        messages = [m for _, channel, m in reader.make_reader(f).iter_messages() if channel.topic == "sensor_data"]
    assert len(messages) == 10
    detected = betterosi.SensorData().parse(messages[5].data).moving_object[0]
    assert detected.header.existence_probability == pytest.approx(0.8)
    assert detected.header.ground_truth_id[0].value == 1
    assert detected.header.measurement_state == betterosi.DetectedItemHeaderMeasurementState.MEASURED
    assert detected.base_rmse.position.x > 0


def test_precorrected_input_is_recognised():
    from cosmo.converters.openlabel_to_omega import is_precorrected

    box = {"object_data": {"rbbox": [{"name": "shape", "val": [1, 2, 3, 4, 0]}]}}
    corrected = {"object_data": {**box["object_data"],
                                 "cuboid": [{"name": "shape", "val": [0] * 9, "coordinate_system": "world"}]}}
    assert is_precorrected({"frames": {"0": {"objects": {}}, "1": {"objects": {"1": corrected}}}})
    assert not is_precorrected({"frames": {"0": {"objects": {"1": box}}}})


def test_precorrected_uses_height_model_without_moving_boxes():
    class Model:  # analytical corrector stand-in: shift grows with the assumed height
        @staticmethod
        def correction_height(label, h):
            return h

        def _correct_analytical(self, cx, cy, w, h, yaw, label, heading, h_veh):
            from types import SimpleNamespace
            return SimpleNamespace(x=0.1 * cx + 0.2 * h_veh, y=0.1 * cy, length=4.0 + 0.1 * h_veh, width=1.8)

    propagation = Propagation(H_SCALE, None, {}, None, Model(), precorrected=True)
    quality = {"sigma_px": [2.0, 2.0, 1.0, 1.0, 0.05], "origin": "detected", "gap_distance": None, "p_exist": None}
    out = propagation.sample(_sample(), quality, 0, "car")
    # height sigma 0.15 * 1.5 m; d(x)/dh = 0.2 -> var 0.045² in x
    assert out["cov_bias"][0, 0] == pytest.approx((0.2 * 0.225) ** 2)
    assert out["oblique"] == ["cosmo_correct", "unknown", "1.5"]


def test_camera_terms_add_bias(tmp_path):
    from types import SimpleNamespace

    class Model:
        @staticmethod
        def correction_height(label, h):
            return h

        def __init__(self, height=100.0):
            self.camera = SimpleNamespace(drone_height=height, gimbal_pitch_deg=-60.0, camera_azimuth_deg=0.0,
                                          stddev={"drone_height": 0.0})

        def with_camera(self, **changes):
            return Model(changes.get("drone_height", self.camera.drone_height))

        def _correct_analytical(self, cx, cy, w, h, yaw, label, heading, h_veh):
            return SimpleNamespace(x=0.1 * cx + 0.01 * self.camera.drone_height, y=0.1 * cy, length=4.0, width=1.8)

    quality = {"sigma_px": [2.0, 2.0, 1.0, 1.0, 0.05], "origin": "detected", "gap_distance": None, "p_exist": None}
    plain = Propagation(H_SCALE, None, {}, None, Model()).sample(_sample(), quality, 0, "car")
    with_camera = Propagation(H_SCALE, None, {}, None, Model(), camera_uncertainty=True)
    out = with_camera.sample(_sample(), quality, 0, "car")
    # dx/dH = 0.01 with sigma 1.0 m (assumed): + 1e-4 m² in x
    assert out["cov_bias"][0, 0] - plain["cov_bias"][0, 0] == pytest.approx(1e-4)
    assert "camera" in with_camera.run_quality()["defaults_used"]
