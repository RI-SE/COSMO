"""Position, size and heading uncertainty of converted objects (scenario-uncertainty SPEC P3, P5, P7).

Error levels are kept apart (SPEC §5): `datum` (one per recording, from the georef), `image` (georef
local error), `frame` (stabilisation, per frame) and `object` (box pixel error, height assumption).
A sample's `cov_rel` holds every level except datum; absolute uncertainty = cov_rel + datum.

`cov_rel` = `cov_noise` + `cov_bias`. Noise (box pixel error, stabilisation) is taken as independent from
frame to frame, so a track smoother may average it down; bias (vehicle height, georef image error) varies
slowly along a track and must be added back unreduced after smoothing.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

try:
    from orbit_georef import GeoTransformer
    from orbit_georef.uncertainty import jacobian, position_covariance
except ImportError:  # pragma: no cover
    GeoTransformer = None
    jacobian = position_covariance = None

SIGMA_ENTRY = "sigma_px"
ORIGIN_ENTRY = "origin"
GAP_DISTANCE_ENTRY = "gap_distance"
P_EXIST_ENTRY = "p_exist"

# SPEC P7 defaults (all basis "assumed")
DEFAULT_SIGMA_REL = (0.02, 0.02, 0.04, 0.04)  # cx, cy, w, h as a fraction of sqrt(w*h)
DEFAULT_SIGMA_ROTATION = 0.05  # rad
HUMAN_FACTOR = 0.5
GAP_GROWTH = 0.1
UNKNOWN_GAP = 15
HEIGHT_REL_SIGMA = 0.15  # vehicle height sigma as a fraction of h
REVIEWED_ORIGINS = ("manual", "reviewed")
SYNTHESIZED_ORIGINS = ("interpolated", "tracked", "manual_interpolated", "manual_tracked")
# Origins whose box was measured in its own frame (OSI measurement_state MEASURED)
MEASURED_ORIGINS = ("detected", "merged", "manual", "reviewed")

# Finite-difference steps for the oblique correction (px, m)
PIXEL_STEP = 0.5
HEIGHT_STEP = 0.1
# Camera parameters of the oblique correction: (DroneCamera field, step, assumed systematic sigma). The
# flight-record stddev (drift over the sequence) is combined in quadrature with the assumed sensor error:
# barometric height above the take-off point vs the ground under the camera, gimbal and compass bias.
CAMERA_PARAMETERS = (("drone_height", 0.5, 1.0), ("gimbal_pitch_deg", 0.2, 0.5), ("camera_azimuth_deg", 0.5, 1.0))


def box_quality(vec: Any) -> Dict[str, Any]:
    """sigma_px, newest origin, gap_distance and p_exist of one box; None where absent.

    Origins of files without them are inferred from the annotator names (SPEC P7).
    """
    values = {e.get("name"): e.get("val") for e in vec if isinstance(e, dict)} if isinstance(vec, list) else {}

    def newest(name):
        val = values.get(name)
        return (val[0] if val else None) if isinstance(val, list) else val
    sigma = values.get(SIGMA_ENTRY)
    origin = newest(ORIGIN_ENTRY) or _inferred_origin(newest("annotator"))
    return {
        "sigma_px": [float(v) for v in sigma] if isinstance(sigma, list) and len(sigma) == 5 else None,
        "origin": origin,
        "gap_distance": newest(GAP_DISTANCE_ENTRY),
        "p_exist": newest(P_EXIST_ENTRY),
    }


def _inferred_origin(annotator: Optional[str]) -> Optional[str]:
    if annotator is None:
        return None
    annotator = str(annotator)
    if annotator.startswith("markit_housekeeping(gap"):
        return "interpolated"
    return "detected" if annotator.startswith("markit_") else "manual"


def default_sigma_px(w_px: float, h_px: float, origin: Optional[str], gap: Optional[int]) -> List[float]:
    """SPEC P7 default [σcx, σcy, σw, σh, σr] for a box without its own."""
    size = math.sqrt(max(w_px, 0.0) * max(h_px, 0.0))
    factor = 1.0
    if origin in SYNTHESIZED_ORIGINS:
        factor = 1.0 + GAP_GROWTH * (UNKNOWN_GAP if gap is None else gap)
    elif origin in REVIEWED_ORIGINS:
        factor = HUMAN_FACTOR
    return [f * size * factor for f in DEFAULT_SIGMA_REL] + [DEFAULT_SIGMA_ROTATION * factor]


def post_transform_matrix(swap_xy: bool = False, flip_x: bool = False, flip_y: bool = False,
                          yaw_offset_rad: float = 0.0, **_) -> np.ndarray:
    """Linear part of cosmo's post-projection tweaks (swap, flips, rotation), in that order."""
    T = np.array([[0.0, 1.0], [1.0, 0.0]]) if swap_xy else np.eye(2)
    T = np.diag([-1.0 if flip_x else 1.0, -1.0 if flip_y else 1.0]) @ T
    c, s = math.cos(yaw_offset_rad), math.sin(yaw_offset_rad)
    return np.array([[c, -s], [s, c]]) @ T


class _Homography:
    """Stand-in for a GeoTransformer when only a matrix is known (calibration files)."""

    method = "homography"

    def __init__(self, H: np.ndarray):
        self.transform_matrix = H


class Propagation:
    """Uncertainty of each converted sample, from the georef quality, a video sidecar and box sigma."""

    def __init__(self, H: np.ndarray, georef_path: Optional[str], post: Dict[str, Any],
                 video_quality: Optional[Dict[str, Any]], corrector=None, precorrected: bool = False,
                 camera_uncertainty: bool = False, heights: Optional[Dict[str, Tuple[float, float]]] = None):
        """corrector: the oblique correction applied (or, for precorrected input, a model of it).

        camera_uncertainty adds the camera height/pitch/azimuth terms; heights gives per-object
        (h_eff, sigma) estimates that replace the class-default height prior.
        """
        self.defaults_used: set = set()
        self.transformer = self._transformer(H, georef_path)
        self.T = post_transform_matrix(**post)
        self.corrector = corrector
        self.precorrected = precorrected
        self.heights = heights or {}
        self.camera_models = self._camera_models(corrector) if camera_uncertainty and corrector is not None else []
        georef = position_covariance(self.transformer) if self.transformer.__class__ is not _Homography else None
        self.georef = georef or {"image": None, "datum": None, "basis": {"image": "unknown", "datum": "unknown"},
                                 "source": {"image": "none", "datum": "none"}}
        self.cov_image = self._rotated(self.georef["image"])
        self.cov_datum = self._rotated(self.georef["datum"])
        self.stabilization = (video_quality or {}).get("stabilization") or {}
        if self.cov_image is None:
            self.defaults_used.add("georef")

    @staticmethod
    def _transformer(H: np.ndarray, georef_path: Optional[str]):
        if GeoTransformer is not None and georef_path:
            try:
                return GeoTransformer.from_file(georef_path)
            except (KeyError, ValueError, OSError):
                pass
        return _Homography(H)

    def _rotated(self, cov: Optional[np.ndarray]) -> Optional[np.ndarray]:
        return None if cov is None else self.T @ np.asarray(cov, dtype=float) @ self.T.T

    def frame_cov(self, frame_no: int, J: np.ndarray) -> np.ndarray:
        """Stabilisation error of this frame in m²; 0 with a recorded default when unknown (SPEC P7)."""
        per_frame = self.stabilization.get("per_frame") or {}
        errors = per_frame.get("reproj_err_px") or []
        filled = per_frame.get("filled") or []
        err = errors[frame_no] if 0 <= frame_no < len(errors) else None
        if err is None or (frame_no < len(filled) and filled[frame_no]):
            self.defaults_used.add("frame")
            return np.zeros((2, 2))
        # reproj_err_px is a 2D RMS: per-axis variance rms²/2
        return J @ (np.eye(2) * (err ** 2 / 2)) @ J.T

    def sample(self, s: Dict[str, Any], quality: Dict[str, Any], frame_no: int, label_type: str) -> Dict[str, Any]:
        """Uncertainty and pass-through quality of one sample (see module docstring)."""
        cx, cy, w_px, h_px, yaw_img = s["rbbox"]
        sigma = quality["sigma_px"]
        if sigma is None:
            sigma = default_sigma_px(w_px, h_px, quality["origin"], quality["gap_distance"])
            self.defaults_used.add("sigma_px")
        J = self.T @ jacobian(self.transformer, cx, cy)
        if self.corrector is not None:
            cov_px, cov_bias, size_h, oblique = self._oblique_terms(s, label_type, sigma)
        else:
            cov_px = J @ np.diag([sigma[0] ** 2, sigma[1] ** 2]) @ J.T
            cov_bias, size_h = np.zeros((2, 2)), (0.0, 0.0)
            oblique = ["cosmo_correct" if self.precorrected else "none", "unknown" if self.precorrected else "0",
                       f"{s['h_veh']:g}"]
        cov_noise = cov_px + self.frame_cov(frame_no, J)
        if self.cov_image is not None:
            cov_bias = cov_bias + self.cov_image
        scale = math.sqrt(abs(np.linalg.det(J)))
        return {
            "cov_rel": cov_noise + cov_bias, "cov_noise": cov_noise, "cov_bias": cov_bias,
            "sigma_size": [math.hypot(scale * sigma[2], size_h[0]), math.hypot(scale * sigma[3], size_h[1])],
            "sigma_heading": sigma[4],
            "origin": quality["origin"], "p_exist": quality["p_exist"], "oblique": oblique,
        }

    def _oblique_terms(self, s: Dict[str, Any], label_type: str, sigma: Sequence[float]):
        """Position covariance from pixel and height error through the oblique correction (finite differences).

        Uses the analytical model also in 3d mode (the 3d fit starts from it); returns
        (pixel cov 2x2, height cov 2x2, size sigma from the height (L, W), [method, shift_m, h_used]).
        """
        cx, cy, w_px, h_px, yaw_img = s["rbbox"]
        heading, h = s["heading_world"], s["h_veh"]

        def corrected(dcx=0.0, dcy=0.0, dh=0.0, corrector=self.corrector):
            r = corrector._correct_analytical(cx + dcx, cy + dcy, w_px, h_px, yaw_img, label_type, heading, h + dh)
            return np.array([r.x, r.y]), np.array([r.length, r.width])
        base_xy, base_lw = corrected()
        d_cx = (corrected(dcx=PIXEL_STEP)[0] - base_xy) / PIXEL_STEP
        d_cy = (corrected(dcy=PIXEL_STEP)[0] - base_xy) / PIXEL_STEP
        xy_h, lw_h = corrected(dh=HEIGHT_STEP)
        sigma_h = self._height_sigma(s["oid"], h)
        Jc = self.T @ np.column_stack([d_cx, d_cy])
        g = self.T @ ((xy_h - base_xy) / HEIGHT_STEP)
        cov_px = Jc @ np.diag([sigma[0] ** 2, sigma[1] ** 2]) @ Jc.T
        cov_h = np.outer(g, g) * sigma_h ** 2
        size_var = ((lw_h - base_lw) / HEIGHT_STEP * sigma_h) ** 2
        for model, step, sigma_p in self.camera_models:  # shared by all objects: bias, not noise
            xy_p, lw_p = corrected(corrector=model)
            g = self.T @ ((xy_p - base_xy) / step)
            cov_h = cov_h + np.outer(g, g) * sigma_p ** 2
            size_var = size_var + ((lw_p - base_lw) / step * sigma_p) ** 2
        size_h = np.sqrt(size_var)
        if self.precorrected:  # the box is already corrected: its raw position is not known here
            return cov_px, cov_h, (float(size_h[0]), float(size_h[1])), ["cosmo_correct", "unknown", f"{h:g}"]
        shift = float(np.linalg.norm(base_xy - np.array(self._raw_xy(cx, cy))))
        method = s["oblique"].method if s.get("oblique") is not None else "analytical"
        return cov_px, cov_h, (float(size_h[0]), float(size_h[1])), [method, f"{shift:.3f}", f"{h:g}"]

    def _height_sigma(self, oid: str, h: float) -> float:
        """Sigma of the vehicle height: the per-object estimate if there is one, else the class prior."""
        if oid in self.heights:
            return self.heights[oid][1]
        self.defaults_used.add("vehicle_height")
        return HEIGHT_REL_SIGMA * h

    def _camera_models(self, corrector) -> List[Tuple[Any, float, float]]:
        """(perturbed corrector, step, sigma) per camera parameter."""
        stddev = getattr(corrector.camera, "stddev", None) or {}
        self.defaults_used.add("camera")
        models = []
        for name, step, assumed in CAMERA_PARAMETERS:
            sigma = math.hypot(stddev.get(name) or 0.0, assumed)
            models.append((corrector.with_camera(**{name: getattr(corrector.camera, name) + step}), step, sigma))
        return models

    def _raw_xy(self, cx: float, cy: float) -> Tuple[float, float]:
        H = self.transformer.transform_matrix
        p = H @ np.array([cx, cy, 1.0])
        return float(p[0] / p[2]), float(p[1] / p[2])

    def run_quality(self) -> Dict[str, Any]:
        """Run-level terms for metadata.quality and provenance."""
        return {"datum": self.cov_datum, "image": self.cov_image, "georef_basis": self.georef["basis"],
                "georef_source": self.georef["source"], "defaults_used": sorted(self.defaults_used)}
