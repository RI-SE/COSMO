# src/cosmo/calibration/compute.py
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Callable

import numpy as np
import pandas as pd

import matplotlib.pyplot as plt
from PIL import Image

LogFn = Callable[[str], None]


# -----------------------------------------------------------------------------
# WGS84 -> ECEF -> ENU (copied/refactored from scripts/compute_calibration.py) [2](https://mcap.dev/docs/python/_modules/mcap/stream_reader)
# -----------------------------------------------------------------------------

A = 6378137.0
F = 1 / 298.257223563
E_SQ = F * (2 - F)



# Default object dimensions (meters) used in legacy calibration JSON output.
# Downstream code expects this key to exist; adjust values to match your domain.
DEFAULT_DIMENSIONS_M = {
    'car': {'length': 4.5, 'width': 1.8, 'height': 1.5},
    'van': {'length': 5.2, 'width': 2.0, 'height': 2.2},
    'truck': {'length': 12.0, 'width': 2.5, 'height': 3.5},
    'bus': {'length': 12.0, 'width': 2.55, 'height': 3.3},
    'trailer': {'length': 13.6, 'width': 2.5, 'height': 3.7},
    'motorcycle': {'length': 2.2, 'width': 0.8, 'height': 1.2},
    'bicycle': {'length': 1.8, 'width': 0.6, 'height': 1.2},
    'pedestrian': {'length': 0.6, 'width': 0.6, 'height': 1.7},
}

def geodetic_to_ecef(lat_deg: float, lon_deg: float, h_m: float) -> np.ndarray:
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)
    sin_lat, cos_lat = math.sin(lat), math.cos(lat)
    sin_lon, cos_lon = math.sin(lon), math.cos(lon)
    N = A / math.sqrt(1 - E_SQ * sin_lat * sin_lat)
    X = (N + h_m) * cos_lat * cos_lon
    Y = (N + h_m) * cos_lat * sin_lon
    Z = (N * (1 - E_SQ) + h_m) * sin_lat
    return np.array([X, Y, Z], dtype=float)


def ecef_to_enu_matrix(lat0_deg: float, lon0_deg: float) -> np.ndarray:
    lat0 = math.radians(lat0_deg)
    lon0 = math.radians(lon0_deg)
    sin_lat0, cos_lat0 = math.sin(lat0), math.cos(lat0)
    sin_lon0, cos_lon0 = math.sin(lon0), math.cos(lon0)
    return np.array(
        [
            [-sin_lon0, cos_lon0, 0],
            [-sin_lat0 * cos_lon0, -sin_lat0 * sin_lon0, cos_lat0],
            [cos_lat0 * cos_lon0, cos_lat0 * sin_lon0, sin_lat0],
        ],
        dtype=float,
    )


def convert_latlon_to_enu(
    lat_deg: float,
    lon_deg: float,
    h_m: float,
    lat0_deg: float,
    lon0_deg: float,
    h0: float = 0.0,
) -> Tuple[float, float, float]:
    ref_ecef = geodetic_to_ecef(lat0_deg, lon0_deg, h0)
    R = ecef_to_enu_matrix(lat0_deg, lon0_deg)
    ecef = geodetic_to_ecef(lat_deg, lon_deg, h_m)
    enu = R @ (ecef - ref_ecef)
    E, N, U = enu.tolist()
    return float(E), float(N), float(U)


# -----------------------------------------------------------------------------
# OpenDRIVE geoReference parser (+lat_0, +lon_0) [2](https://mcap.dev/docs/python/_modules/mcap/stream_reader)
# -----------------------------------------------------------------------------

def extract_georef_latlon(xodr_path: str) -> Tuple[float, float]:
    with open(xodr_path, "r", encoding="utf-8", errors="ignore") as f:
        txt = f.read()
    m = re.search(r"<geoReference>(.*?)</geoReference>", txt, re.S)
    if not m:
        raise RuntimeError("No <geoReference> found in OpenDRIVE.")
    georef = m.group(1)
    mlat = re.search(r"\+lat_0=([\d\.\-]+)", georef)
    mlon = re.search(r"\+lon_0=([\d\.\-]+)", georef)
    if not (mlat and mlon):
        raise RuntimeError("Could not extract +lat_0 / +lon_0 from geoReference.")
    return float(mlat.group(1)), float(mlon.group(1))


# -----------------------------------------------------------------------------
# Homography fitting (DLT + RANSAC) [2](https://mcap.dev/docs/python/_modules/mcap/stream_reader)
# -----------------------------------------------------------------------------

def normalize_points(pts: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """
    Hartley normalization: returns normalized homogeneous points and transform T such that:
    npts = (T @ pts_h.T).T
    """
    mean = np.mean(pts, axis=0)
    std = np.std(pts)
    s = math.sqrt(2) / std if std > 0 else 1.0
    T = np.array([[s, 0, -s * mean[0]],
                  [0, s, -s * mean[1]],
                  [0, 0, 1]], dtype=float)
    pts_h = np.hstack([pts, np.ones((pts.shape[0], 1))])
    npts = (T @ pts_h.T).T
    return npts, T


def fit_homography_dlt(pix: np.ndarray, world: np.ndarray) -> np.ndarray:
    """
    Fit 3x3 homography H mapping [u v 1]^T -> [X Y 1]^T via normalized DLT.
    """
    pix_n, T_pix = normalize_points(pix)
    world_n, T_world = normalize_points(world)

    A = []
    for (u, v, _), (X, Y, _) in zip(pix_n, world_n):
        A.append([0, 0, 0, -u, -v, -1, Y * u, Y * v, Y])
        A.append([u, v, 1, 0, 0, 0, -X * u, -X * v, -X])
    A = np.array(A, dtype=float)

    _, _, Vt = np.linalg.svd(A)
    Hn = Vt[-1].reshape(3, 3)
    H = np.linalg.inv(T_world) @ Hn @ T_pix
    return H / H[2, 2]


def apply_homography(H: np.ndarray, pts: np.ndarray) -> np.ndarray:
    pts_h = np.hstack([pts, np.ones((pts.shape[0], 1))])
    proj = (H @ pts_h.T).T
    proj = proj / proj[:, [2]]
    return proj[:, :2]


def ransac_homography(
    pix: np.ndarray,
    world: np.ndarray,
    *,
    n_iter: int = 800,
    thresh_m: float = 0.50,
    seed: int = 42,
) -> Tuple[np.ndarray, np.ndarray, float]:
    """
    RANSAC wrapper for homography.
    thresh_m: inlier threshold in meters on ground plane.

    Returns:
      H_refit, inlier_idx, rmse_all
    """
    n = pix.shape[0]
    if n < 4:
        raise ValueError("Need at least 4 correspondences for homography.")

    rng = np.random.default_rng(seed)
    best_H = None
    best_inliers = None
    best_rmse = float("inf")

    for _ in range(n_iter):
        idx = rng.choice(n, size=4, replace=False)
        try:
            H_try = fit_homography_dlt(pix[idx], world[idx])
        except Exception:
            continue

        pred = apply_homography(H_try, pix)
        err = np.linalg.norm(pred - world, axis=1)
        inliers = np.where(err < thresh_m)[0]

        if len(inliers) >= 4:
            rmse = float(np.sqrt(np.mean(np.sum((pred[inliers] - world[inliers]) ** 2, axis=1))))
            if best_inliers is None or rmse < best_rmse or len(inliers) > len(best_inliers):
                best_rmse = rmse
                best_H = H_try
                best_inliers = inliers

    if best_inliers is not None and len(best_inliers) >= 4:
        H_refit = fit_homography_dlt(pix[best_inliers], world[best_inliers])
        pred_all = apply_homography(H_refit, pix)
        rmse_all = float(np.sqrt(np.mean(np.sum((pred_all - world) ** 2, axis=1))))
        return H_refit, best_inliers, rmse_all

    # Fallback to all points
    H_all = fit_homography_dlt(pix, world)
    pred_all = apply_homography(H_all, pix)
    rmse_all = float(np.sqrt(np.mean(np.sum((pred_all - world) ** 2, axis=1))))
    return H_all, np.arange(n), rmse_all


# -----------------------------------------------------------------------------
# Optional OpenLABEL validator (copied/refactored) [2](https://mcap.dev/docs/python/_modules/mcap/stream_reader)
# -----------------------------------------------------------------------------

def openlabel_first_frame_pixel_centers(openlabel_path: str) -> Dict[str, Tuple[float, float]]:
    """
    Parse OpenLABEL JSON and return {object_id: (cx, cy)} for the first frame found.
    SAVANT subset: object_data.rbbox often as list of dicts with name='shape' and val=[cx,cy,w,h,yaw]
    """
    with open(openlabel_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    root = data.get("openlabel", data)
    frames = root.get("frames", {})
    if not frames:
        return {}

    first_key = sorted(frames.keys(), key=lambda k: int(k) if str(k).isdigit() else 0)[0]
    fobj = frames[first_key].get("objects", {}) if isinstance(frames[first_key], dict) else {}
    out: Dict[str, Tuple[float, float]] = {}

    for oid, od in fobj.items():
        od_data = od.get("object_data", {})
        rb = od_data.get("rbbox", [])
        cx, cy = None, None

        if isinstance(rb, list):
            for entry in rb:
                if isinstance(entry, dict) and entry.get("name") == "shape":
                    val = entry.get("val")
                    if isinstance(val, list) and len(val) >= 2:
                        cx, cy = float(val[0]), float(val[1])
                        break
        elif isinstance(rb, dict):
            if "val" in rb and isinstance(rb["val"], list) and len(rb["val"]) >= 2:
                cx, cy = float(rb["val"][0]), float(rb["val"][1])

        if cx is not None and cy is not None:
            out[str(oid)] = (cx, cy)

    return out


# -----------------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class CalibrationComputation:
    H: np.ndarray
    rmse_m: float
    inlier_idx: np.ndarray
    names_used: List[str]
    pix_points: np.ndarray          # Nx2
    world_points: np.ndarray        # Nx2 (ENU meters)
    lat0: Optional[float] = None
    lon0: Optional[float] = None
    origin_source: Optional[str] = None  # 'from_opendrive' | 'mean_markers_fallback' | 'user_override'


@dataclass(frozen=True)
class CalibrationOutputs:
    calibration_json_path: str
    summary_json_path: str
    residuals_png_path: Optional[str]
    overlay_png_path: Optional[str]
    openlabel_validation_count: Optional[int]


def compute_calibration(
    pixel_pairs_csv: str,
    visual_markers_csv: str,
    opendrive_path: str,
    *,
    ransac_thresh_m: float = 0.50,
    n_iter: int = 800,
    seed: int = 42,
    origin_lat0: Optional[float] = None,
    origin_lon0: Optional[float] = None,
    log_fn: Optional[LogFn] = None,
) -> CalibrationComputation:
    """
    Compute homography H mapping pixel (u,v) -> ground plane (E,N) in meters.

    visual_markers.csv can contain either:
      - (point_name, latitude, longitude, altitude)  OR
      - (point_name, E, N)
    If lat/lon is used, lat0/lon0 are read from OpenDRIVE <geoReference>. [2](https://mcap.dev/docs/python/_modules/mcap/stream_reader)
    """
    pix_df = pd.read_csv(pixel_pairs_csv)
    if not {"point_name", "u", "v"}.issubset(pix_df.columns):
        raise RuntimeError("pixel_pairs CSV must have columns: point_name,u,v")

    pix_map = {
        str(r["point_name"]).strip(): (float(r["u"]), float(r["v"]))
        for _, r in pix_df.iterrows()
    }

    vm_df = pd.read_csv(visual_markers_csv)
    cols = set(vm_df.columns)

    have_latlon = {"point_name", "latitude", "longitude", "altitude"}.issubset(cols)
    have_en = {"point_name", "E", "N"}.issubset(cols)

    if not (have_latlon or have_en):
        raise RuntimeError(
            "visual_markers CSV must have either (point_name,latitude,longitude,altitude) or (point_name,E,N)"
        )
    lat0 = lon0 = None
    origin_source: Optional[str] = None
    if have_latlon:
        # 1) User override wins (if both provided)
        if origin_lat0 is not None and origin_lon0 is not None:
            lat0, lon0 = float(origin_lat0), float(origin_lon0)
            origin_source = 'user_override'
            if log_fn:
                log_fn(f"[COSMO] Using user-specified origin lat0/lon0: {lat0}, {lon0}")
        else:
            # 2) Try OpenDRIVE geoReference (+lat_0/+lon_0)
            try:
                lat0, lon0 = extract_georef_latlon(opendrive_path)
                origin_source = 'from_opendrive'
                if log_fn:
                    log_fn(f"[COSMO] Using OpenDRIVE geoReference origin lat0/lon0: {lat0}, {lon0}")
            except Exception as e:
                # 3) Fallback: mean marker lat/lon (robust for UTM geoReference etc.)
                lat0 = float(vm_df['latitude'].astype(float).mean())
                lon0 = float(vm_df['longitude'].astype(float).mean())
                origin_source = 'mean_markers_fallback'
                if log_fn:
                    log_fn(
                        '[COSMO] OpenDRIVE geoReference did not provide +lat_0/+lon_0 '
                        f'({e}). Falling back to mean marker lat/lon: {lat0}, {lon0}'
                    )

    name_to_row = {str(r["point_name"]).strip(): r for _, r in vm_df.iterrows()}

    pix_list: List[Tuple[float, float]] = []
    world_list: List[Tuple[float, float]] = []
    names_used: List[str] = []

    for name, (u, v) in pix_map.items():
        if name not in name_to_row:
            continue
        r = name_to_row[name]
        if have_latlon:
            E, N, _ = convert_latlon_to_enu(
                float(r["latitude"]),
                float(r["longitude"]),
                float(r["altitude"]),
                float(lat0),
                float(lon0),
            )
        else:
            E, N = float(r["E"]), float(r["N"])

        pix_list.append((u, v))
        world_list.append((E, N))
        names_used.append(name)

    if len(pix_list) < 4:
        raise RuntimeError(f"Need at least 4 matched points; found {len(pix_list)}")

    pix = np.array(pix_list, dtype=float)
    world = np.array(world_list, dtype=float)

    H, inliers, rmse_all = ransac_homography(
        pix,
        world,
        n_iter=n_iter,
        thresh_m=ransac_thresh_m,
        seed=seed,
    )

    return CalibrationComputation(
        H=H,
        rmse_m=float(rmse_all),
        inlier_idx=inliers,
        names_used=names_used,
        pix_points=pix,
        world_points=world,
        lat0=lat0,
        lon0=lon0,
        origin_source=origin_source,
    )


def write_calibration_outputs(
    computation: CalibrationComputation,
    *,
    calibration_json_path: str,
    summary_json_path: str,
    fps: float = 30.0,
    image_width: int = 3840,
    image_height: int = 2160,
    default_dimensions_m: Optional[Dict[str, Dict[str, float]]] = None,
    residuals_png_path: Optional[str] = None,
    overlay_png_path: Optional[str] = None,
    image_path: Optional[str] = None,
    openlabel_path: Optional[str] = None,
) -> CalibrationOutputs:
    """
    Write Calibration JSON + summary JSON, optionally residual plot and overlay image.
    All output paths are explicit (no CWD dependence), solving the issue in the legacy script. [2](https://mcap.dev/docs/python/_modules/mcap/stream_reader)
    """
    H = computation.H
    pix = computation.pix_points
    world = computation.world_points

    pred = apply_homography(H, pix)
    res = world - pred

    if default_dimensions_m is None:
        default_dimensions_m = DEFAULT_DIMENSIONS_M

    calibration = {
        "fps": float(fps),
        "image_width": int(image_width),
        "image_height": int(image_height),
        "homography": H.tolist(),
        # Keep placeholders to match expectations of legacy downstream code
        "intrinsics": {"fx": 2100.0, "fy": 2100.0, "cx": image_width / 2.0, "cy": image_height / 2.0},
        "extrinsics": {"R": [[1, 0, 0], [0, 1, 0], [0, 0, 1]], "t": [0.0, 0.0, 3.5], "ground_z": 0.0},
        "default_dimensions_m": default_dimensions_m,
    }

    Path(calibration_json_path).parent.mkdir(parents=True, exist_ok=True)
    with open(calibration_json_path, "w", encoding="utf-8") as f:
        json.dump(calibration, f, indent=2)

    summary = {
        "rmse_m": float(np.sqrt(np.mean(np.sum(res ** 2, axis=1)))),
        "inliers_count": int(len(computation.inlier_idx)),
        "pairs_used": computation.names_used,
        "pixel_points": {n: list(pix[i]) for i, n in enumerate(computation.names_used)},
        "world_points_ENU_m": {n: list(world[i]) for i, n in enumerate(computation.names_used)},
        "homography": H.tolist(),
        "lat0": computation.lat0,
        "lon0": computation.lon0,
        "origin_source": computation.origin_source,
    }

    Path(summary_json_path).parent.mkdir(parents=True, exist_ok=True)
    with open(summary_json_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    # Residual plot
    if residuals_png_path:
        try:
            plt.figure(figsize=(8, 6))
            plt.title("Ground-plane residuals after homography fit")
            plt.scatter(world[:, 0], world[:, 1], c="tab:green", s=50, label="Ground truth")
            plt.scatter(pred[:, 0], pred[:, 1], c="tab:orange", s=40, label="Projected")
            for i, n in enumerate(computation.names_used):
                plt.arrow(
                    pred[i, 0],
                    pred[i, 1],
                    world[i, 0] - pred[i, 0],
                    world[i, 1] - pred[i, 1],
                    color="tab:orange",
                    length_includes_head=True,
                    head_width=0.2,
                )
                plt.text(world[i, 0] + 0.2, world[i, 1] + 0.2, n, fontsize=9)
            plt.axis("equal")
            plt.xlabel("E (m)")
            plt.ylabel("N (m)")
            plt.grid(True, alpha=0.3)
            plt.legend()
            plt.tight_layout()
            Path(residuals_png_path).parent.mkdir(parents=True, exist_ok=True)
            plt.savefig(residuals_png_path, dpi=150)
            plt.close()
        except Exception:
            residuals_png_path = None

    # Overlay markers onto image
    if overlay_png_path and image_path and Path(image_path).is_file():
        try:
            img = Image.open(image_path).convert("RGB")
            w, h = img.size
            H_inv = np.linalg.inv(H)

            pts_img: List[Tuple[str, float, float]] = []
            for i, n in enumerate(computation.names_used):
                X, Y = world[i]
                g = np.array([X, Y, 1.0], dtype=float)
                p = H_inv @ g
                p = p / p[2]
                u, v = float(p[0]), float(p[1])
                pts_img.append((n, u, v))

            plt.figure(figsize=(12, 7))
            plt.imshow(img)
            plt.title("Visual markers projected onto image")
            for n, u, v in pts_img:
                if 0 <= u < w and 0 <= v < h:
                    plt.scatter([u], [v], c="yellow", s=40, marker="o")
                    plt.text(
                        u + 5, v - 5, n, color="yellow", fontsize=9,
                        bbox=dict(facecolor="black", alpha=0.5, edgecolor="none")
                    )
                else:
                    uc = min(max(u, 5), w - 5)
                    vc = min(max(v, 5), h - 5)
                    plt.scatter([uc], [vc], c="red", s=40, marker="x")
                    plt.text(
                        uc + 5, vc - 5, f"{n} (off-img)", color="red", fontsize=9,
                        bbox=dict(facecolor="black", alpha=0.5, edgecolor="none")
                    )
            plt.axis("off")
            plt.tight_layout()
            Path(overlay_png_path).parent.mkdir(parents=True, exist_ok=True)
            plt.savefig(overlay_png_path, dpi=150)
            plt.close()
        except Exception:
            overlay_png_path = None

    # Optional OpenLABEL validation (count only; you can extend later)
    openlabel_validation_count: Optional[int] = None
    if openlabel_path and Path(openlabel_path).is_file():
        try:
            centers = openlabel_first_frame_pixel_centers(openlabel_path)
            if centers:
                pts = np.array(list(centers.values()), dtype=float)
                _ = apply_homography(H, pts)  # projection computed, but not plotted here
                openlabel_validation_count = int(len(pts))
            else:
                openlabel_validation_count = 0
        except Exception:
            openlabel_validation_count = None

    return CalibrationOutputs(
        calibration_json_path=str(Path(calibration_json_path).resolve()),
        summary_json_path=str(Path(summary_json_path).resolve()),
        residuals_png_path=str(Path(residuals_png_path).resolve()) if residuals_png_path else None,
        overlay_png_path=str(Path(overlay_png_path).resolve()) if overlay_png_path else None,
        openlabel_validation_count=openlabel_validation_count,
    )