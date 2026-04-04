"""DroneCamera model and helpers for oblique drone footage correction."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import numpy as np

log = logging.getLogger(__name__)

KNOWN_CAMERAS: dict[str, dict] = {
    "mavic3pro-standard": {"hfov_deg": 73.0, "image_width": 3840, "image_height": 2160},
    "mavic3pro-tele":     {"hfov_deg": 35.0, "image_width": 3840, "image_height": 2160},
}


@dataclass
class DroneCamera:
    drone_height: float         # AGL meters
    gimbal_pitch_deg: float     # negative = downward, e.g. -83
    camera_azimuth_deg: float   # drone_yaw + gimbal_yaw, absolute degrees
    image_width: int
    image_height: int
    hfov_deg: float

    @property
    def K(self) -> np.ndarray:
        """Pinhole intrinsics from HFOV and image dimensions."""
        fx = (self.image_width / 2) / np.tan(np.radians(self.hfov_deg / 2))
        return np.array([
            [fx, 0.0, self.image_width / 2],
            [0.0, fx, self.image_height / 2],
            [0.0, 0.0, 1.0],
        ])

    @property
    def elevation_angle_deg(self) -> float:
        """Angle from nadir in degrees (0 = straight down, 90 = horizontal)."""
        return 90.0 + self.gimbal_pitch_deg

    def camera_world_pos(self, nadir_xy: np.ndarray) -> np.ndarray:
        """Camera 3D world position given H(image_center) as the nadir ground point."""
        el_rad = np.radians(self.elevation_angle_deg)
        horiz_offset = self.drone_height * np.tan(el_rad)
        az = np.radians(self.camera_azimuth_deg)
        return np.array([
            nadir_xy[0] - horiz_offset * np.sin(az),
            nadir_xy[1] - horiz_offset * np.cos(az),
            self.drone_height,
        ])

    def rotation_matrix(self) -> np.ndarray:
        """World-to-camera rotation matrix R so that p_cam = R @ (p_world - cam_pos)."""
        el = np.radians(self.elevation_angle_deg)
        az = np.radians(self.camera_azimuth_deg)
        # Camera Z = viewing direction
        cam_z = np.array([np.sin(el) * np.sin(az), np.sin(el) * np.cos(az), -np.cos(el)])
        # Camera X = image right (horizontal, perpendicular to viewing direction)
        cam_x = np.array([np.cos(az), -np.sin(az), 0.0])
        # Camera Y = image down
        cam_y = np.cross(cam_z, cam_x)
        norm = np.linalg.norm(cam_y)
        if norm < 1e-12:
            cam_y = np.array([0.0, 0.0, 1.0])
        else:
            cam_y /= norm
        return np.stack([cam_x, cam_y, cam_z], axis=0)


def load_camera_from_flight_record(
    path: str,
    sequence_id: int = 0,
    camera_model: str = "mavic3pro-standard",
    hfov_deg: float | None = None,
    image_width: int | None = None,
    image_height: int | None = None,
) -> DroneCamera:
    """Build DroneCamera from a FlightRecord_*.video_stats.json file."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    seqs = data.get("sequences", data if isinstance(data, list) else [data])
    seq = seqs[sequence_id]
    stats = seq["stats"]

    drone_height = float(stats["osd"]["height_agl"]["mean"])
    drone_yaw = float(stats["osd"]["yaw"]["mean"])
    gimbal_pitch = float(stats["gimbal"]["pitch"]["mean"])
    gimbal_yaw = float(stats["gimbal"]["yaw"]["mean"])
    camera_azimuth = drone_yaw + gimbal_yaw

    cam_defaults = KNOWN_CAMERAS.get(camera_model, {})
    resolved_hfov = hfov_deg if hfov_deg is not None else cam_defaults.get("hfov_deg", 73.0)
    resolved_width = image_width if image_width is not None else cam_defaults.get("image_width", 3840)
    resolved_height = image_height if image_height is not None else cam_defaults.get("image_height", 2160)

    cam = DroneCamera(
        drone_height=drone_height,
        gimbal_pitch_deg=gimbal_pitch,
        camera_azimuth_deg=camera_azimuth,
        image_width=resolved_width,
        image_height=resolved_height,
        hfov_deg=resolved_hfov,
    )
    log.info(
        "DroneCamera: height=%.1fm, el=%.1f° from nadir, az=%.1f°, hfov=%.1f°",
        drone_height, cam.elevation_angle_deg, camera_azimuth, resolved_hfov,
    )
    return cam
