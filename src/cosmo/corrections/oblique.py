"""Oblique drone bbox correction: analytical and 3D-fitting paths."""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

from .drone_camera import DroneCamera

log = logging.getLogger(__name__)

# Default vehicle heights for height-induced projection bias
DEFAULT_VEHICLE_HEIGHTS: dict[str, float] = {
    "car": 1.5,
    "truck": 3.5,
    "bus": 3.2,
    "van": 2.2,
    "motorcycle": 1.1,
    "bicycle": 1.1,
    "pedestrian": 1.7,
    "other": 1.5,
}

# Minimum plausible ground-plane dimensions
_MIN_LENGTH = 0.5
_MIN_WIDTH = 0.3


@dataclass
class CorrectionResult:
    x: float
    y: float
    z: float
    length: float
    width: float
    height: float
    method: str  # "analytical" | "3d"


def _apply_homography(H: np.ndarray, u: float, v: float) -> tuple[float, float]:
    q = H @ np.array([u, v, 1.0], dtype=np.float64)
    if abs(q[2]) < 1e-12:
        return float("nan"), float("nan")
    return float(q[0] / q[2]), float(q[1] / q[2])


def _box3d_corners(cx: float, cy: float, L: float, W: float, H_veh: float, heading: float) -> np.ndarray:
    """Return the 8 world-space corners of a 3D box (base on ground plane)."""
    cos_h, sin_h = np.cos(heading), np.sin(heading)
    corners = []
    for sl in (-1.0, 1.0):
        for sw in (-1.0, 1.0):
            for sz in (0.0, 1.0):
                wx = cx + sl * (L / 2) * cos_h - sw * (W / 2) * sin_h
                wy = cy + sl * (L / 2) * sin_h + sw * (W / 2) * cos_h
                wz = sz * H_veh
                corners.append([wx, wy, wz])
    return np.array(corners, dtype=np.float64)


def _project_box(
    cx3d: float, cy3d: float, L: float, W: float, h_veh: float,
    heading_rad: float, K: np.ndarray, R: np.ndarray, cam_pos: np.ndarray,
) -> np.ndarray | None:
    """Project 3D box corners to image; return (N, 2) pixel points or None if degenerate."""
    corners_w = _box3d_corners(cx3d, cy3d, L, W, h_veh, heading_rad)
    pts_cam = (R @ (corners_w.T - cam_pos[:, None])).T
    visible = pts_cam[:, 2] > 0.1
    if visible.sum() < 3:
        return None
    pts_h = (K @ pts_cam[visible].T).T
    return pts_h[:, :2] / pts_h[:, 2:3]


def _projected_bbox(pts_img: np.ndarray, yaw_img: float) -> tuple[float, float, float, float]:
    """Center + (width, height) of the enclosing rect aligned with yaw_img."""
    cos_y, sin_y = np.cos(-yaw_img), np.sin(-yaw_img)
    rx = pts_img[:, 0] * cos_y - pts_img[:, 1] * sin_y
    ry = pts_img[:, 0] * sin_y + pts_img[:, 1] * cos_y
    return pts_img[:, 0].mean(), pts_img[:, 1].mean(), rx.max() - rx.min(), ry.max() - ry.min()


def _fit_loss(
    params: np.ndarray,
    x0: float, y0: float,
    camera: DroneCamera, H: np.ndarray,
    heading_rad: float, h_veh: float,
    obs: tuple[float, float, float, float, float],
) -> float:
    dX, dY, L, W = params
    obs_cx, obs_cy, obs_w, obs_h, obs_yaw = obs
    K = camera.K
    R = camera.rotation_matrix()
    nadir = np.array(_apply_homography(H, camera.image_width / 2, camera.image_height / 2))
    cam_pos = camera.camera_world_pos(nadir)
    pts = _project_box(x0 + dX, y0 + dY, L, W, h_veh, heading_rad, K, R, cam_pos)
    if pts is None:
        return 1e6
    cx_p, cy_p, w_p, h_p = _projected_bbox(pts, obs_yaw)
    return 2.0 * ((cx_p - obs_cx) ** 2 + (cy_p - obs_cy) ** 2) + (w_p - obs_w) ** 2 + (h_p - obs_h) ** 2


class BboxCorrector:
    """Correct oblique-drone bboxes for height-induced position bias and dimension inflation."""

    def __init__(self, camera: DroneCamera, H: np.ndarray, mode: str = "analytical"):
        self.camera = camera
        self.H = H
        if mode == "3d":
            try:
                import scipy.optimize  # noqa: F401
                self.mode = "3d"
            except ImportError:
                log.warning("scipy not installed; falling back to analytical correction")
                self.mode = "analytical"
        else:
            self.mode = "analytical"
        # Cache camera nadir and position (constant across frames)
        self._nadir_xy = np.array(_apply_homography(H, camera.image_width / 2, camera.image_height / 2))
        self._cam_pos = camera.camera_world_pos(self._nadir_xy)

    def correct(
        self,
        cx: float, cy: float,
        w_px: float, h_px: float,
        yaw_img: float,
        label_type: str,
        heading_rad: float,
    ) -> CorrectionResult:
        analytical = self._correct_analytical(cx, cy, w_px, h_px, yaw_img, label_type, heading_rad)
        if self.mode == "3d":
            return self._correct_3d(cx, cy, w_px, h_px, yaw_img, label_type, heading_rad, analytical)
        return analytical

    def _correct_analytical(
        self,
        cx: float, cy: float,
        w_px: float, h_px: float,
        yaw_img: float,
        label_type: str,
        heading_rad: float,
    ) -> CorrectionResult:
        H = self.H
        # 1. Map rbbox corners to ground
        cos_a, sin_a = np.cos(yaw_img), np.sin(yaw_img)
        hw, hh = w_px / 2, h_px / 2
        corners_px = [
            (cx + dx * cos_a - dy * sin_a, cy + dx * sin_a + dy * cos_a)
            for dx, dy in ((-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh))
        ]
        corners_world = [_apply_homography(H, u, v) for u, v in corners_px]

        # 2. Raw vehicle center via H
        X_raw, Y_raw = _apply_homography(H, cx, cy)

        # 3. Project corners to vehicle axis frame to get observed dims
        cos_h, sin_h = np.cos(-heading_rad), np.sin(-heading_rad)
        veh_xs = [x * cos_h - y * sin_h for x, y in corners_world]
        veh_ys = [x * sin_h + y * cos_h for x, y in corners_world]
        L_obs = max(veh_xs) - min(veh_xs)
        W_obs = max(veh_ys) - min(veh_ys)

        # 4. Elevation angle from nadir to vehicle (at the camera's horizontal distance)
        dx = X_raw - self._cam_pos[0]
        dy = Y_raw - self._cam_pos[1]
        R_h = np.hypot(dx, dy)
        theta = np.arctan2(R_h, self.camera.drone_height)  # angle from nadir

        # 5. Angle of camera-to-vehicle direction relative to vehicle heading
        cam_to_veh_vx = dx * cos_h - dy * sin_h   # in vehicle frame
        cam_to_veh_vy = dx * sin_h + dy * cos_h
        alpha = np.arctan2(cam_to_veh_vy, cam_to_veh_vx)

        # 6. Height-induced projection inflation
        h_veh = DEFAULT_VEHICLE_HEIGHTS.get(label_type.lower(), DEFAULT_VEHICLE_HEIGHTS["other"])
        correction = h_veh * np.tan(theta)
        L_corr = max(L_obs - correction * abs(np.cos(alpha)), _MIN_LENGTH)
        W_corr = max(W_obs - correction * abs(np.sin(alpha)), _MIN_WIDTH)

        # 7. Center shift: roof center is displaced toward camera by h_veh/2 / H_drone
        scale = (h_veh / 2) / self.camera.drone_height
        X_corr = X_raw - dx * scale
        Y_corr = Y_raw - dy * scale

        return CorrectionResult(
            x=X_corr, y=Y_corr, z=0.0,
            length=L_corr, width=W_corr, height=h_veh,
            method="analytical",
        )

    def _correct_3d(
        self,
        cx: float, cy: float,
        w_px: float, h_px: float,
        yaw_img: float,
        label_type: str,
        heading_rad: float,
        initial: CorrectionResult,
    ) -> CorrectionResult:
        from scipy.optimize import minimize  # type: ignore

        h_veh = DEFAULT_VEHICLE_HEIGHTS.get(label_type.lower(), DEFAULT_VEHICLE_HEIGHTS["other"])
        x0, y0 = initial.x, initial.y
        L0, W0 = initial.length, initial.width
        obs = (cx, cy, w_px, h_px, yaw_img)

        # Bounds: dX/dY within ±5m, L/W within [0.5×, 2.5×] of analytical result
        bounds = [(-5.0, 5.0), (-5.0, 5.0), (max(0.3, L0 * 0.5), L0 * 2.5), (max(0.2, W0 * 0.5), W0 * 2.0)]

        result = minimize(
            _fit_loss,
            x0=np.array([0.0, 0.0, L0, W0]),
            args=(x0, y0, self.camera, self.H, heading_rad, h_veh, obs),
            method="L-BFGS-B",
            bounds=bounds,
            options={"maxiter": 100, "ftol": 1e-6},
        )

        if result.success or result.fun < 1e4:
            dX, dY, L_fit, W_fit = result.x
            return CorrectionResult(
                x=x0 + dX, y=y0 + dY, z=0.0,
                length=float(L_fit), width=float(W_fit), height=h_veh,
                method="3d",
            )

        log.debug("3D fitting did not converge (fun=%.1f); using analytical result", result.fun)
        return CorrectionResult(x=initial.x, y=initial.y, z=0.0,
                                length=initial.length, width=initial.width, height=h_veh,
                                method="analytical")
