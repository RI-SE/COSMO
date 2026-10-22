"""Oblique drone bbox correction: analytical and 3D-fitting paths."""
from __future__ import annotations

import dataclasses
import logging
import re
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


AZIMUTH_WARN_DEG = 20.0


def _view_azimuth_deg(H: np.ndarray, u: float, v: float, step: float = 50.0) -> float:
    """Ground direction of image "up" at (u, v), degrees clockwise from north: where a tilted camera looks."""
    x0, y0 = _apply_homography(H, u, v)
    x1, y1 = _apply_homography(H, u, v - step)
    return float(np.degrees(np.arctan2(x1 - x0, y1 - y0)))


def _apply_homography(H: np.ndarray, u: float, v: float) -> tuple[float, float]:
    q = H @ np.array([u, v, 1.0], dtype=np.float64)
    if abs(q[2]) < 1e-12:
        return float("nan"), float("nan")
    return float(q[0] / q[2]), float(q[1] / q[2])


def _box3d_corners(cx: float, cy: float, L: float, W: float, H_veh: float, heading: float) -> np.ndarray:
    """Return the 8 world-space corners of a 3D box (base on ground plane)."""
    cos_h, sin_h = np.cos(heading), np.sin(heading)
    sl = np.array([-1., -1., -1., -1.,  1.,  1.,  1.,  1.])
    sw = np.array([-1., -1.,  1.,  1., -1., -1.,  1.,  1.])
    sz = np.array([ 0.,  1.,  0.,  1.,  0.,  1.,  0.,  1.])
    wx = cx + sl * (L / 2) * cos_h - sw * (W / 2) * sin_h
    wy = cy + sl * (L / 2) * sin_h + sw * (W / 2) * cos_h
    return np.column_stack([wx, wy, sz * H_veh])


def _cam_pos_from_gps(lat: float, lon: float, drone_height: float, proj_string: str) -> np.ndarray | None:
    """Camera world position from drone GPS + proj_string UTM zone."""
    m = re.search(r'\+zone=(\d+)', proj_string)
    if not m:
        return None
    from cosmo.gui.marker_converter import latlon_to_utm
    zone = int(m.group(1))
    e, n = latlon_to_utm(lat, lon, zone)
    return np.array([e, n, drone_height])


def _decompose_H_to_P(H: np.ndarray, K: np.ndarray,
                      nadir_world: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Decompose ground-plane homography H into a full 3×4 projection matrix.

    H maps pixel (u,v,1) → world UTM (X,Y,1).  Working in nadir-centred local
    coords avoids numerical ill-conditioning from large UTM offsets.

    Returns:
        P   — 3×4 matrix: P @ [X, Y, Z, 1] → homogeneous pixel (UTM world coords)
        cam — camera centre in world UTM coordinates (3,)
    """
    H_inv = np.linalg.inv(H)                        # world UTM → pixel
    nx, ny = float(nadir_world[0]), float(nadir_world[1])

    # Shift world origin to nadir so the translation column ≈ [0, 0, 1/λ].
    # H_local maps (X−nx, Y−ny, 1) → pixel.
    T_shift = np.array([[1, 0, nx], [0, 1, ny], [0, 0, 1]], dtype=np.float64)
    H_local = H_inv @ T_shift

    # H_local = K @ [r1 | r2 | t_local]  (up to scale λ)
    M    = np.linalg.inv(K) @ H_local
    lam  = (np.linalg.norm(M[:, 0]) + np.linalg.norm(M[:, 1])) / 2.0
    r1   = M[:, 0] / lam
    r2   = M[:, 1] / lam
    t_lo = M[:, 2] / lam
    r3   = np.cross(r1, r2)
    r3  /= np.linalg.norm(r3)
    R    = np.column_stack([r1, r2, r3])

    # Camera centre in local coords → back to global UTM.
    cam_local = -R.T @ t_lo
    cam_world = np.array([cam_local[0] + nx, cam_local[1] + ny, cam_local[2]])

    # P for global UTM: t_global absorbs the nadir shift so that
    # P @ [X, Y, 0, 1] == H_inv @ [X, Y, 1] for all ground points.
    t_global = t_lo - R @ np.array([nx, ny, 0.0])
    P = K @ np.column_stack([R, t_global.reshape(3, 1)])  # 3×4

    return P, cam_world


def _project_box_via_h(
    cx3d: float, cy3d: float, L: float, W: float, h_veh: float,
    heading_rad: float, H_inv: np.ndarray, cam_pos: np.ndarray,
) -> np.ndarray | None:
    """Project 3D box to image via ground-shadow + H^{-1}."""
    corners = _box3d_corners(cx3d, cy3d, L, W, h_veh, heading_rad)  # (8, 3)
    cz = cam_pos[2]
    if np.any(corners[:, 2] >= cz):
        return None
    scale = cz / (cz - corners[:, 2])                                # (8,)
    xg = cam_pos[0] + (corners[:, 0] - cam_pos[0]) * scale
    yg = cam_pos[1] + (corners[:, 1] - cam_pos[1]) * scale
    q = np.column_stack([xg, yg, np.ones(8)]) @ H_inv.T              # (8, 3)
    denom = q[:, 2]
    if np.any(np.abs(denom) < 1e-12):
        return None
    return q[:, :2] / denom[:, np.newaxis]


def _project_box_full_P(
    cx3d: float, cy3d: float, L: float, W: float, h_veh: float,
    heading_rad: float, P: np.ndarray,
) -> np.ndarray | None:
    """Project 3D box corners to image using the full 3×4 projection matrix P."""
    corners = _box3d_corners(cx3d, cy3d, L, W, h_veh, heading_rad)   # (8, 3)
    q = np.column_stack([corners, np.ones(8)]) @ P.T                  # (8, 3)
    denom = q[:, 2]
    if np.any((np.abs(denom) < 1e-12) | (denom < 0)):
        return None
    return q[:, :2] / denom[:, np.newaxis]


def _projected_bbox(pts_img: np.ndarray, yaw_img: float) -> tuple[float, float, float, float]:
    """Center + (width, height) of the enclosing rect aligned with yaw_img."""
    cos_y, sin_y = np.cos(-yaw_img), np.sin(-yaw_img)
    rx = pts_img[:, 0] * cos_y - pts_img[:, 1] * sin_y
    ry = pts_img[:, 0] * sin_y + pts_img[:, 1] * cos_y
    return pts_img[:, 0].mean(), pts_img[:, 1].mean(), rx.max() - rx.min(), ry.max() - ry.min()


def _fit_loss(
    params: np.ndarray,
    x0: float, y0: float,
    H_inv: np.ndarray, cam_pos: np.ndarray,
    heading_rad: float, h_veh: float,
    obs: tuple[float, float, float, float, float],
) -> float:
    dX, dY, L, W = params
    obs_cx, obs_cy, obs_w, obs_h, obs_yaw = obs
    pts = _project_box_via_h(x0 + dX, y0 + dY, L, W, h_veh, heading_rad, H_inv, cam_pos)
    if pts is None:
        return 1e6
    cx_p, cy_p, w_p, h_p = _projected_bbox(pts, obs_yaw)
    return 2.0 * ((cx_p - obs_cx) ** 2 + (cy_p - obs_cy) ** 2) + (w_p - obs_w) ** 2 + (h_p - obs_h) ** 2


class BboxCorrector:
    """Correct oblique-drone bboxes for height-induced position bias and dimension inflation."""

    def __init__(self, camera: DroneCamera, H: np.ndarray, mode: str = "analytical",
                 proj_string: str | None = None, use_gps_cam_pos: bool = False):
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
        self._H_inv = np.linalg.inv(H)
        self._nadir_xy = np.array(_apply_homography(H, camera.image_width / 2, camera.image_height / 2))
        # Viewing azimuth from H (like the camera position below): consistent with the georeference, and
        # independent of compass/gimbal-yaw errors in the flight record
        h_azimuth = _view_azimuth_deg(H, camera.image_width / 2, camera.image_height / 2)
        off = (camera.camera_azimuth_deg - h_azimuth + 180.0) % 360.0 - 180.0
        # Printed by the CLIs (azimuth_warning()); info here, else Python's last-resort handler prints it twice
        self.azimuth_mismatch = (camera.camera_azimuth_deg, off, h_azimuth) if abs(off) > AZIMUTH_WARN_DEG else None
        if self.azimuth_mismatch:
            log.info(self.azimuth_warning())
        camera = dataclasses.replace(camera, camera_azimuth_deg=h_azimuth)
        self.camera = camera

        # Camera position: H-derived is geometrically consistent with the calibrated H.
        # GPS (drone body position) can be 10–20m off from the optical centre and introduces bias.
        # Use GPS only when explicitly requested.
        h_pos = camera.camera_world_pos(self._nadir_xy)

        gps_pos = None
        if use_gps_cam_pos and camera.drone_lat is not None and proj_string:
            gps_pos = _cam_pos_from_gps(camera.drone_lat, camera.drone_lon,
                                         camera.drone_height, proj_string)

        if gps_pos is not None:
            dist = np.linalg.norm(gps_pos[:2] - h_pos[:2])
            log.info("cam_pos GPS=(%.1f,%.1f) vs H-derived=(%.1f,%.1f), diff=%.1fm",
                     gps_pos[0], gps_pos[1], h_pos[0], h_pos[1], dist)
            self._cam_pos = gps_pos
        else:
            log.info("cam_pos H-derived=(%.1f,%.1f,%.1f)", h_pos[0], h_pos[1], h_pos[2])
            self._cam_pos = h_pos

        # Full 3×4 projection matrix from H decomposition — used for 3D fitting.
        # This is fully consistent with H so any 3D point projects to the correct pixel.
        self._P, cam_decomp = _decompose_H_to_P(H, camera.K, self._nadir_xy)
        log.info("cam_pos H-decomp=(%.1f,%.1f,%.1f), diff from h_pos=%.1fm",
                 cam_decomp[0], cam_decomp[1], cam_decomp[2],
                 float(np.linalg.norm(cam_decomp[:2] - h_pos[:2])))

    def azimuth_warning(self) -> str | None:
        """Message when the flight-record camera azimuth disagrees with the georeference, else None."""
        if not self.azimuth_mismatch:
            return None
        recorded, off, used = self.azimuth_mismatch
        return (f"flight-record camera azimuth {recorded:.1f}° differs {off:+.1f}° from the georeference "
                f"({used:.1f}°); using the georeference")

    def with_camera(self, **changes) -> "BboxCorrector":
        """Copy with camera parameters changed (e.g. drone_height), for sensitivity analysis.

        The camera position follows the change: recomputed from H when it was H-derived, else (GPS) only
        its height changes. The 3x4 projection used by 3d mode is not updated.
        """
        import copy

        other = copy.copy(self)
        other.camera = dataclasses.replace(self.camera, **changes)
        h_pos = self.camera.camera_world_pos(self._nadir_xy)
        if np.allclose(self._cam_pos, h_pos):
            other._cam_pos = other.camera.camera_world_pos(self._nadir_xy)
        else:
            other._cam_pos = np.array([self._cam_pos[0], self._cam_pos[1], other.camera.drone_height])
        return other

    def correct(
        self,
        cx: float, cy: float,
        w_px: float, h_px: float,
        yaw_img: float,
        label_type: str,
        heading_rad: float,
        h_veh_override: float | None = None,
    ) -> CorrectionResult:
        analytical = self._correct_analytical(cx, cy, w_px, h_px, yaw_img, label_type, heading_rad, h_veh_override)
        if self.mode == "3d":
            return self._correct_3d(cx, cy, w_px, h_px, yaw_img, label_type, heading_rad, analytical, h_veh_override)
        return analytical

    def ground_corners(self, cx: float, cy: float, w_px: float, h_px: float,
                       yaw_img: float) -> list[tuple[float, float]]:
        """The 4 rbbox corners mapped to the ground (z=0) via H."""
        cos_a, sin_a = np.cos(yaw_img), np.sin(yaw_img)
        hw, hh = w_px / 2, h_px / 2
        corners_px = [
            (cx + dx * cos_a - dy * sin_a, cy + dx * sin_a + dy * cos_a)
            for dx, dy in ((-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh))
        ]
        return [_apply_homography(self.H, u, v) for u, v in corners_px]

    def measured_footprint(self, cx: float, cy: float, w_px: float, h_px: float, yaw_img: float,
                           heading_rad: float) -> tuple[np.ndarray, float, float, np.ndarray, np.ndarray]:
        """Ground-mapped box: centre, extents along the vehicle axes (length, width) and the axes u, v."""
        corners = np.array(self.ground_corners(cx, cy, w_px, h_px, yaw_img))
        u = np.array([np.cos(heading_rad), np.sin(heading_rad)])
        v = np.array([-u[1], u[0]])
        along, across = corners @ u, corners @ v
        return corners.mean(axis=0), float(np.ptp(along)), float(np.ptp(across)), u, v

    def roof_offset(self, centre: np.ndarray, h_veh) -> np.ndarray:
        """Ground displacement of the roof outline relative to the footprint, delta = (P - C) h / (H_c - h).

        The silhouette maps to the footprint stretched by delta, so the measured centre is P + delta/2;
        h_veh may be an array (one delta per height). Assumes the roof outline is the footprint raised by h.
        """
        cam = self._cam_pos[:2]
        k = np.asarray(h_veh, dtype=float) / (self.camera.drone_height - np.asarray(h_veh, dtype=float))
        foot = cam + (centre - cam) / (1.0 + k[..., None] / 2.0)
        return (foot - cam) * k[..., None]

    def _correct_analytical(
        self,
        cx: float, cy: float,
        w_px: float, h_px: float,
        yaw_img: float,
        label_type: str,
        heading_rad: float,
        h_veh_override: float | None = None,
    ) -> CorrectionResult:
        """Remove the roof stretch: centre - delta/2, each dimension minus |delta| along its axis."""
        h_veh = (h_veh_override if h_veh_override is not None
                 else DEFAULT_VEHICLE_HEIGHTS.get(label_type.lower(), DEFAULT_VEHICLE_HEIGHTS["other"]))
        centre, length, width, u, v = self.measured_footprint(cx, cy, w_px, h_px, yaw_img, heading_rad)
        delta = self.roof_offset(centre, h_veh)
        x, y = centre - delta / 2
        return CorrectionResult(
            x=float(x), y=float(y), z=0.0,
            length=max(length - abs(float(delta @ u)), _MIN_LENGTH),
            width=max(width - abs(float(delta @ v)), _MIN_WIDTH),
            height=h_veh,
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
        h_veh_override: float | None = None,
    ) -> CorrectionResult:
        from scipy.optimize import minimize  # type: ignore

        h_veh = h_veh_override if h_veh_override is not None else DEFAULT_VEHICLE_HEIGHTS.get(label_type.lower(), DEFAULT_VEHICLE_HEIGHTS["other"])
        x0, y0 = initial.x, initial.y
        L0, W0 = initial.length, initial.width
        obs = (cx, cy, w_px, h_px, yaw_img)

        # Bounds: dX/dY within ±5m, L/W within [0.5×, 2.5×] of analytical result
        bounds = [(-5.0, 5.0), (-5.0, 5.0), (max(0.3, L0 * 0.5), L0 * 2.5), (max(0.2, W0 * 0.5), W0 * 2.0)]

        result = minimize(
            _fit_loss,
            x0=np.array([0.0, 0.0, L0, W0]),
            args=(x0, y0, self._H_inv, self._cam_pos, heading_rad, h_veh, obs),
            method="L-BFGS-B",
            bounds=bounds,
            options={"maxiter": 50, "ftol": 1e-4},
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
