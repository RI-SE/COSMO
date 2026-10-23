"""Oblique correction against a synthetic pinhole camera with a known 3D car."""

from __future__ import annotations

import math

import numpy as np
import pytest

from cosmo.corrections import BboxCorrector
from cosmo.corrections.drone_camera import DroneCamera

HEIGHT, ELEVATION, AZIMUTH = 100.0, 30.0, 0.0  # camera 30° from nadir, looking north
CAR = (4.5, 1.8, 1.5)


def _camera(azimuth=AZIMUTH):
    return DroneCamera(drone_height=HEIGHT, gimbal_pitch_deg=ELEVATION - 90.0, camera_azimuth_deg=azimuth,
                       image_width=3840, image_height=2160, hfov_deg=73.0)


def _homography():
    """pixel -> ground for the true camera, centred on the image-centre ground point (0, 0)."""
    cam = _camera()
    C = np.array([0.0, -HEIGHT * math.tan(math.radians(ELEVATION)), HEIGHT])
    R = cam.rotation_matrix()
    H_inv = cam.K @ np.column_stack([R[:, 0], R[:, 1], -R @ C])
    return np.linalg.inv(H_inv / H_inv[2, 2]), cam.K, R, C


def _annotated_box(x, y, heading):
    """Image rbbox (cx, cy, w, h, yaw) tightly around the projected car silhouette (roof included)."""
    _, K, R, C = _homography()
    L, W, Hv = CAR
    c, s = math.cos(heading), math.sin(heading)
    corners = [(x + dx * c - dy * s, y + dx * s + dy * c, z)
               for dx in (-L / 2, L / 2) for dy in (-W / 2, W / 2) for z in (0.0, Hv)]
    pix = np.array([(K @ (R @ (np.array(p) - C)))[:2] / (K @ (R @ (np.array(p) - C)))[2] for p in corners])
    ground = [p[:2] for p in corners if p[2] == 0.0]
    H = _homography()[0]
    H_inv = np.linalg.inv(H)
    a = H_inv @ [*ground[0], 1]
    b = H_inv @ [*ground[2], 1]  # along the length on the ground
    yaw = math.atan2(b[1] / b[2] - a[1] / a[2], b[0] / b[2] - a[0] / a[2])
    u = np.array([math.cos(yaw), math.sin(yaw)])
    v = np.array([-u[1], u[0]])
    pu, pv = pix @ u, pix @ v
    centre = u * (pu.max() + pu.min()) / 2 + v * (pv.max() + pv.min()) / 2
    return [centre[0], centre[1], pu.max() - pu.min(), pv.max() - pv.min(), yaw]


@pytest.mark.parametrize("x, y, heading", [(15.0, 20.0, 0.0), (-20.0, 10.0, math.pi / 2), (5.0, -15.0, 0.7)])
def test_analytical_correction_recovers_car_size(x, y, heading):
    H = _homography()[0]
    # The flight-record azimuth is 180° off (the bug that summed drone and absolute gimbal yaw)
    corrector = BboxCorrector(_camera(azimuth=AZIMUTH + 180.0), H)
    assert corrector.camera.camera_azimuth_deg == pytest.approx(AZIMUTH, abs=0.5)
    assert "differs" in corrector.azimuth_warning() and BboxCorrector(_camera(), H).azimuth_warning() is None

    cx, cy, w, h, yaw = _annotated_box(x, y, heading)
    world_heading = heading  # what COSMO derives from the box orientation through H
    result = corrector.correct(cx, cy, w, h, yaw, "car", world_heading, CAR[2])
    raw = BboxCorrector(_camera(), H)._correct_analytical(cx, cy, w, h, yaw, "car", world_heading, 0.0)
    # Correction moves towards the true footprint: smaller than uncorrected, close to the real car. The
    # remaining size error is the image rectangle's own overshoot under perspective (up to ~0.5 m for a car
    # 70 m out at the image side), which only the 3d fit models.
    assert result.length < raw.length and result.width < raw.width
    assert result.length == pytest.approx(CAR[0], abs=0.5) and result.width == pytest.approx(CAR[1], abs=0.5)
    assert math.hypot(result.x - x, result.y - y) < 0.3


def test_height_estimate_recovers_vehicle_height():
    from cosmo.corrections.height_estimate import estimate_height

    corrector = BboxCorrector(_camera(), _homography()[0])
    # A car driving north along x = 5 m, 40 frames from 60 m before to 40 m past the image centre
    observations = [(_annotated_box(5.0, y, math.pi / 2), math.pi / 2) for y in np.linspace(-40, 40, 40)]
    h_eff, sigma = estimate_height(corrector, observations)
    assert h_eff == pytest.approx(CAR[2], abs=0.3) and sigma >= 0.1
    assert estimate_height(corrector, observations[:5]) is None  # too few frames


def test_3d_fit_records_residual_and_entry():
    from cosmo.converters.quality import box_quality

    H = _homography()[0]
    corrector = BboxCorrector(_camera(), H, mode="3d")
    box = _annotated_box(15.0, 20.0, 0.0)
    result = corrector.correct(*box, "car", 0.0, CAR[2])
    assert result.method == "3d" and result.fit_residual is not None and result.fit_residual < 1.0
    assert corrector._correct_analytical(*box, "car", 0.0, CAR[2]).fit_residual is None

    raw = H @ [box[0], box[1], 1.0]
    entry = result.oblique_entry((raw[0] / raw[2], raw[1] / raw[2]))
    assert entry[0] == "3d" and float(entry[1]) > 0 and entry[2] == "1.5" and float(entry[3]) < 1.0
    assert box_quality([{"name": "oblique", "val": entry}])["oblique"] == entry
