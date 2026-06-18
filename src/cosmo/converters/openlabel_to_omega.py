# src/cosmo/converters/openlabel_to_omega.py
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cosmo.converters.openlabel_to_omega

Convert ASAM OpenLABEL (SAVANT subset) to:
 • Omega-Prime style CSV (moving-object table)
 • MCAP containing ASAM OSI GroundTruth (optional, requires betterosi)

Optionally embeds OpenDRIVE map into the MCAP.

This module is the in-package refactor of the former script:
  scripts/convert_openlabel_to_omega.py

Public API:
  convert_openlabel_to_omega(...)  # Signature kept identical to the script version.

Notes:
- Supports ORBIT georef exports (xxx_georef_data.json) using "transformation_matrix" as homography.
- Supports legacy calibration.json providing homography or intrinsics+extrinsics.
- Optional coordinate alignment tweaks (swap/flip/offset/rotation).
"""

from __future__ import annotations

import csv
import json
import math
import os
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

# Optional: betterosi for OSI/MCAP; if missing, we still write CSV
try:
    import betterosi  # pip install betterosi
except ImportError:  # pragma: no cover
    betterosi = None

from cosmo.converters.ontology_mapper import OntologyMapper

# -----------------------------------------------------------------------------
# Defaults
# -----------------------------------------------------------------------------

DEFAULT_DIMENSIONS_M: Dict[str, Dict[str, float]] = {
    "car": {"length": 4.5, "width": 1.8, "height": 1.5},
    "truck": {"length": 12.0, "width": 2.5, "height": 3.5},
    "bus": {"length": 12.0, "width": 2.5, "height": 3.2},
    "van": {"length": 5.0, "width": 2.0, "height": 2.2},
    "trailer": {"length": 6.0, "width": 2.0, "height": 2.5},
    "pedestrian": {"length": 0.5, "width": 0.5, "height": 1.7},
    "other": {"length": 3.0, "width": 1.5, "height": 1.5},
}


# -----------------------------------------------------------------------------
# Utilities
# -----------------------------------------------------------------------------

def load_json(path: str) -> Dict[str, Any]:
    """
    Load JSON.

    Historically calibration files have sometimes been wrapped in markdown fences or included comments.
    This loader tolerates that *only* for calibration-like filenames, and uses strict JSON otherwise.
    """
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()

    basename = os.path.basename(path).lower()
    is_calib_like = basename.endswith("calibration.json") or "calibration" in basename
    if is_calib_like:
        raw = re.sub(r"^\s*\`\`\`.*$", "", raw, flags=re.MULTILINE)
        s = raw.find("{")
        e = raw.rfind("}")
        if s != -1 and e != -1 and e > s:
            raw = raw[s : e + 1]
        raw = re.sub(r"/\*.*?\*/", "", raw, flags=re.S)
        raw = re.sub(r"(^\s*)//.*$", r"\1", raw, flags=re.MULTILINE)
        raw = re.sub(r",\s*([\}\]])", r"\1", raw)

    return json.loads(raw)


def load_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def to_nanos(sec: float) -> int:
    return int(round(sec * 1_000_000_000))


def compute_homography_from_extrinsics(intr: Dict[str, float], ext: Dict[str, Any]) -> np.ndarray:
    """
    Compute planar homography from image pixels to ground plane (z = ground_z)
    using pinhole model: H = K * [r1 r2 t - r3*ground_z].
    """
    K = np.array(
        [
            [intr["fx"], 0, intr["cx"]],
            [0, intr["fy"], intr["cy"]],
            [0, 0, 1.0],
        ],
        dtype=np.float64,
    )
    R = np.array(ext["R"], dtype=np.float64)
    t = np.array(ext["t"], dtype=np.float64).reshape(3, 1)
    ground_z = float(ext.get("ground_z", 0.0))

    r1 = R[:, 0].reshape(3, 1)
    r2 = R[:, 1].reshape(3, 1)
    r3 = R[:, 2].reshape(3, 1)

    return K @ np.hstack([r1, r2, t - r3 * ground_z])


def apply_homography(H: np.ndarray, u: float, v: float) -> Tuple[float, float]:
    p = np.array([u, v, 1.0], dtype=np.float64)
    q = H @ p
    if abs(q[2]) < 1e-12:
        return float("nan"), float("nan")
    return float(q[0] / q[2]), float(q[1] / q[2])


def angle_wrap(yaw: float) -> float:
    return (yaw + math.pi) % (2 * math.pi) - math.pi


def _h_rotation_angle(H: np.ndarray, cx: float, cy: float) -> float:
    """Angle (rad) that image +x direction maps to in world space via H."""
    X0, Y0 = apply_homography(H, cx, cy)
    X1, Y1 = apply_homography(H, cx + 1.0, cy)
    return math.atan2(Y1 - Y0, X1 - X0)


def _footprint_from_homography(
    H: np.ndarray,
    cx: float, cy: float,
    w_px: float, h_px: float,
    yaw_img: float,
    heading_rad: float,
) -> Tuple[float, float]:
    """Return (length_m, width_m) by projecting rbbox corners through H.

    Identical to BboxCorrector._correct_analytical steps 1–3, without the
    height-induced projection correction (no camera pose needed).
    """
    cos_a, sin_a = math.cos(yaw_img), math.sin(yaw_img)
    hw, hh = w_px / 2, h_px / 2
    corners_px = [
        (cx + dx * cos_a - dy * sin_a, cy + dx * sin_a + dy * cos_a)
        for dx, dy in ((-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh))
    ]
    corners_world = [apply_homography(H, u, v) for u, v in corners_px]
    cos_h, sin_h = math.cos(-heading_rad), math.sin(-heading_rad)
    veh_xs = [x * cos_h - y * sin_h for x, y in corners_world]
    veh_ys = [x * sin_h + y * cos_h for x, y in corners_world]
    return max(veh_xs) - min(veh_xs), max(veh_ys) - min(veh_ys)


def _resolve_height(
    label_type: str,
    obj_uid: str,
    towed_by: Dict[str, str],
    defaults: Dict[str, Dict[str, float]],
) -> float:
    """Return height for an object, using towing vehicle type for trailers."""
    key = label_type.lower()
    if key == "trailer":
        tower_type = towed_by.get(obj_uid)
        if tower_type:
            key = tower_type
    return float(defaults.get(key, defaults.get("other", {})).get("height", 1.5))


def build_towed_by(root: Dict[str, Any], objects_meta: Dict[str, Any]) -> Dict[str, str]:
    """Map trailer_uid → towing vehicle type from openlabel 'towed-by' relations."""
    result: Dict[str, str] = {}
    for rel in (root.get("relations") or {}).values():
        if rel.get("type") == "towed-by":
            subjects = rel.get("rdf_subjects", [])
            objects_ = rel.get("rdf_objects", [])
            if subjects and objects_:
                trailer_uid = str(subjects[0].get("uid", ""))
                tower_uid = str(objects_[0].get("uid", ""))
                tower_type = objects_meta.get(tower_uid, {}).get("type", "")
                if trailer_uid and tower_type:
                    result[trailer_uid] = tower_type.lower()
    return result


# -----------------------------------------------------------------------------
# ORBIT georef + calibration loading
# -----------------------------------------------------------------------------

def _check_proj_string_match(georef_data_path: str, odr_path: str) -> None:
    """Raise ValueError if georef and XODR describe different coordinate systems."""
    with open(georef_data_path, encoding="utf-8") as f:
        georef = json.load(f)
    georef_proj = georef.get("proj_string")

    with open(odr_path, encoding="utf-8", errors="ignore") as f:
        xodr_text = f.read()
    m = re.search(r"<geoReference>(.*?)</geoReference>", xodr_text, re.S)
    xodr_proj = m.group(1).strip() if m else None

    if not georef_proj and not xodr_proj:
        return  # Both old-format, nothing to compare
    if not georef_proj:
        raise ValueError(
            "Georef file has no proj_string (old v1.0 format). "
            "Re-export the georef from ORBIT to get a v1.1 file with projection info."
        )
    if not xodr_proj:
        raise ValueError(
            "XODR file has no <geoReference>. Cannot verify projection match with georef."
        )

    def _normalize(s: str) -> str:
        # Strip origin-only params that may differ between XODR and georef for UTM
        s = re.sub(r'\+lat_0=\S+', '', s)
        s = re.sub(r'\+lon_0=\S+', '', s)
        return ' '.join(s.split())

    if _normalize(georef_proj) != _normalize(xodr_proj):
        raise ValueError(
            f"Projection mismatch between georef and XODR:\n"
            f"  georef: {georef_proj}\n"
            f"  XODR:   {xodr_proj}\n"
            "Re-export both files from ORBIT using the same projection."
        )


def load_alignment(
    calibration_path: Optional[str],
    georef_data_path: Optional[str],
    fps_arg: Optional[float],
) -> Tuple[float, Optional[np.ndarray], Dict[str, Dict[str, float]]]:
    """
    Returns (fps, H, default_dimensions_m).
    H maps pixel (u,v) -> ground plane (X,Y) in meters.
    """
    calib = load_json(calibration_path) if calibration_path else {}
    georef = load_json(georef_data_path) if georef_data_path else {}

    fps = float(
        georef.get(
            "fps",
            calib.get("fps", fps_arg if fps_arg is not None else 30.0),
        )
    )

    H: Optional[np.ndarray] = None

    if georef:
        tm = (georef.get("transform_method") or "").lower()
        if tm and tm != "homography":
            raise ValueError(
                f"Unsupported ORBIT georef transform_method='{georef.get('transform_method')}'. "
                "Expected homography-style pixel->ground transform."
            )
        if "transformation_matrix" in georef:
            H = np.array(georef["transformation_matrix"], dtype=np.float64)
        elif "homography" in georef:
            H = np.array(georef["homography"], dtype=np.float64)
        elif "inverse_matrix" in georef:
            H = np.linalg.inv(np.array(georef["inverse_matrix"], dtype=np.float64))

    if H is None:
        if "homography" in calib:
            H = np.array(calib["homography"], dtype=np.float64)
        elif "intrinsics" in calib and "extrinsics" in calib:
            H = compute_homography_from_extrinsics(calib["intrinsics"], calib["extrinsics"])

    default_dimensions_m = (
        calib.get("default_dimensions_m")
        or georef.get("default_dimensions_m")
        or DEFAULT_DIMENSIONS_M
    )

    return fps, H, default_dimensions_m


def post_transform_xy(
    x: float,
    y: float,
    swap_xy: bool = False,
    flip_x: bool = False,
    flip_y: bool = False,
    yaw_offset_rad: float = 0.0,
    xy_offset: Tuple[float, float] = (0.0, 0.0),
) -> Tuple[float, float]:
    if swap_xy:
        x, y = y, x
    if flip_x:
        x = -x
    if flip_y:
        y = -y
    if abs(yaw_offset_rad) > 1e-12:
        c = math.cos(yaw_offset_rad)
        s = math.sin(yaw_offset_rad)
        xr = c * x - s * y
        yr = s * x + c * y
        x, y = xr, yr
    x += float(xy_offset[0])
    y += float(xy_offset[1])
    return x, y


# -----------------------------------------------------------------------------
# Type/subtype/role helpers
# -----------------------------------------------------------------------------

def classify_openlabel_type(label_type: str) -> Tuple[int, str]:
    lt = (label_type or "").strip().lower()
    if lt in (
        "car", "van", "taxi", "automobile", "truck", "bus", "railvehicle",
        "bicycle", "cyclist", "motorcycle",
    ):
        return 2, "VEHICLE"
    if lt in ("pedestrian", "human"):
        return 3, "PEDESTRIAN"
    if lt in ("animal",):
        return 4, "ANIMAL"
    if lt in ("unknown",):
        return 0, "UNKNOWN"
    return 1, "OTHER"


VEHICLE_SUBTYPE_MAP: Dict[str, str] = {
    "car": "CAR",
    "truck": "TRUCK",
    "bus": "BUS",
    "bicycle": "BICYCLE",
    "motorcycle": "MOTORCYCLE",
    "van": "VAN",
    "tram": "TRAM",
    "train": "RAILVEHICLE",
    "railvehicle": "RAILVEHICLE",
    "tractor": "TRACTOR",
    "trailer": "TRAILER",
    "unknown": "UNKNOWN",
    "other": "OTHER",
}

VEHICLE_ROLE_MAP: Dict[str, str] = {
    "ego": "EGO",
    "moving": "MOVING",
    "parked": "PARKED",
    "stopped": "STOPPED",
    "standing": "PARKED",
    "unknown": "UNKNOWN",
}


def normalize_subtype(subtype_in: Optional[str]) -> str:
    s = (subtype_in or "").strip().lower()
    return VEHICLE_SUBTYPE_MAP.get(s, "CAR")


def normalize_role(role_in: Optional[str]) -> str:
    r = (role_in or "").strip().lower()
    return VEHICLE_ROLE_MAP.get(r, "MOVING")


def _canonical(name: str) -> str:
    name = name.strip().upper()
    for prefix in ("TYPE_", "ROLE_"):
        if name.startswith(prefix):
            return name[len(prefix):]
    return name


def build_enum_code_maps():
    vt_name_to_code, vr_name_to_code = {}, {}
    vt_default, vr_default = 0, 0
    if betterosi is not None:
        VT = getattr(betterosi, "MovingObjectVehicleClassificationType", None)
        VR = getattr(betterosi, "MovingObjectVehicleClassificationRole", None)
        if VT is not None:
            for m in VT:
                vt_name_to_code[_canonical(m.name)] = m.value
            vt_default = vt_name_to_code.get("CAR", vt_default)
        if VR is not None:
            for m in VR:
                vr_name_to_code[_canonical(m.name)] = m.value
            vr_default = vr_name_to_code.get("CIVIL", vr_default)
    return vt_name_to_code, vr_name_to_code, vt_default, vr_default


def make_vehicle_classification(
    subtype_upper: str,
    role_upper: str,
    vt_name_to_code: Dict[str, int],
    vr_name_to_code: Dict[str, int],
    vt_default: int,
    vr_default: int,
):
    if betterosi is None:
        return None

    vt_code = vt_name_to_code.get(subtype_upper, vt_default)
    vr_code = vr_name_to_code.get(role_upper, vr_default)

    VT = getattr(betterosi, "MovingObjectVehicleClassificationType", None)
    VR = getattr(betterosi, "MovingObjectVehicleClassificationRole", None)

    kwargs = {}
    if VT is not None:
        try:
            kwargs["type"] = VT(vt_code)
        except Exception:
            pass
    if VR is not None:
        try:
            kwargs["role"] = VR(vr_code)
        except Exception:
            pass

    return betterosi.MovingObjectVehicleClassification(**kwargs) if kwargs else betterosi.MovingObjectVehicleClassification()


# -----------------------------------------------------------------------------
# OpenLABEL parsing (SAVANT subset)
# -----------------------------------------------------------------------------

def parse_openlabel(ol: Dict[str, Any]) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    root = ol.get("openlabel", ol)

    objects_meta: Dict[str, Dict[str, Any]] = {}
    if isinstance(root.get("objects"), dict):
        for obj_id, obj in root["objects"].items():
            objects_meta[obj_id] = {
                "name": obj.get("name", obj_id),
                "type": obj.get("type", "other"),
                "subtype": obj.get("subtype", None),
                "role": obj.get("role", None),
            }

    frames_out: Dict[str, Dict[str, Any]] = {}
    raw_frames = root.get("frames", {})
    iterable = raw_frames.items() if isinstance(raw_frames, dict) else (
        enumerate(raw_frames) if isinstance(raw_frames, list) else []
    )

    for fkey, fval in iterable:
        frame_id = str(fkey)
        fobj = fval.get("objects", {}) if isinstance(fval, dict) else {}
        objs_out: Dict[str, Dict[str, Any]] = {}

        for oid, od in fobj.items():
            od_data = od.get("object_data", {})
            rbbox = None

            if "rbbox" in od_data:
                rb = od_data["rbbox"]
                val = None

                if isinstance(rb, dict):
                    val = rb.get("val")
                    if val is None and isinstance(rb.get("shape"), dict):
                        val = rb["shape"].get("val")
                elif isinstance(rb, list):
                    for entry in rb:
                        if isinstance(entry, dict) and (entry.get("name") == "shape" or "val" in entry):
                            val = entry.get("val")
                            if isinstance(val, list) and len(val) >= 5:
                                break

                if isinstance(val, list) and len(val) >= 5:
                    rbbox = [float(val[0]), float(val[1]), float(val[2]), float(val[3]), float(val[4])]

            conf = 1.0
            vec = od_data.get("vec", {})
            if isinstance(vec, dict) and "confidence" in vec and isinstance(vec.get("confidence"), dict):
                conf = float(vec["confidence"].get("val", 1.0))

            if rbbox is not None:
                objs_out[oid] = {"rbbox": rbbox, "confidence": conf}

        if objs_out:
            frames_out[frame_id] = {"objects": objs_out}

    return objects_meta, frames_out


# -----------------------------------------------------------------------------
# Core conversion (signature kept identical)
# -----------------------------------------------------------------------------

def convert_openlabel_to_omega(
    openlabel_path: str,
    odr_path: Optional[str],
    out_prefix: str,
    calibration_path: Optional[str] = None,
    georef_data_path: Optional[str] = None,
    fps_arg: Optional[float] = None,
    write_csv: bool = True,
    write_mcap: bool = True,
    swap_xy: bool = False,
    flip_x: bool = False,
    flip_y: bool = False,
    xy_offset: Tuple[float, float] = (0.0, 0.0),
    yaw_offset_rad: float = 0.0,
    strip_xodr_namespace: bool = False,
    log_fn: Optional[Callable[[str], None]] = None,
    corrector=None,
    stabilize_size: bool = False,
):
    """
    Convert OpenLABEL -> Omega-Prime CSV and optionally OSI GroundTruth MCAP.

    Signature matches the former script version for Phase 1 compatibility.
    """
    def _log(msg: str) -> None:
        if callable(log_fn):
            try:
                log_fn(msg)
                return
            except Exception:
                pass
        print(msg, flush=True)


    ol = load_json(openlabel_path)
    objects_meta, frames = parse_openlabel(ol)

    root = ol.get("openlabel", ol)
    towed_by = build_towed_by(root, objects_meta)

    _ont_urls = [
        url for url in (root.get("ontologies") or {}).values()
        if isinstance(url, str) and url.startswith("http")
    ]
    mapper = OntologyMapper(_ont_urls)
    if georef_data_path and odr_path and os.path.isfile(odr_path):
        _check_proj_string_match(georef_data_path, odr_path)

    fps, H, defaults = load_alignment(calibration_path, georef_data_path, fps_arg)
    alignment_source = 'none'
    if georef_data_path:
        alignment_source = 'georef-data'
    elif calibration_path:
        alignment_source = 'calibration'
    _log(f"[COSMO] Alignment source: {alignment_source} (H={'present' if H is not None else 'none'}, fps={fps})")
    _log(f"[COSMO] Applied xy_offset={xy_offset}, yaw_offset_deg={yaw_offset_rad * 180.0 / math.pi:.3f}, swap_xy={swap_xy}, flip_x={flip_x}, flip_y={flip_y}")
    _log(f"[COSMO] OpenDRIVE embedded: {'yes' if (odr_path and write_mcap and betterosi is not None and os.path.isfile(odr_path)) else 'no'}")
    if write_mcap and betterosi is None:
        _log('[COSMO] MCAP requested but betterosi is not installed; will write CSV only.')


    vt_name_to_code, vr_name_to_code, vt_default, vr_default = build_enum_code_maps()

    # Assign indices: use numeric UID directly if possible, else fall back to sequential
    obj_name_to_idx: Dict[str, int] = {}
    for i, oid in enumerate(sorted(objects_meta.keys())):
        try:
            obj_name_to_idx[oid] = int(oid)
        except ValueError:
            obj_name_to_idx[oid] = i + 1
            _log(f"[COSMO] Warning: object UID {oid!r} is not numeric; assigned sequential idx {i + 1}")

    # Handle objects referenced in frames but missing from the objects metadata section.
    # Assigning fallback IDs prevents None from propagating into int() calls later.
    _all_frame_oids: set = {oid for fd in frames.values() for oid in fd["objects"]}
    for oid in sorted(_all_frame_oids - set(obj_name_to_idx)):
        try:
            obj_name_to_idx[oid] = int(oid)
        except ValueError:
            obj_name_to_idx[oid] = max(obj_name_to_idx.values(), default=0) + 1
        _log(f"[COSMO] Warning: object {oid!r} found in frames but not in objects metadata; "
             f"assigned id={obj_name_to_idx[oid]}, type defaults to 'other'")

    csv_cols = [
        "total_nanos", "idx", "x", "y", "z",
        "vel_x", "vel_y", "vel_z",
        "acc_x", "acc_y", "acc_z",
        "length", "width", "height",
        "roll", "pitch", "yaw",
        "type", "subtype", "role",
        "type_name", "subtype_name", "role_name",
    ]
    csv_rows: List[List[Any]] = []

    last_positions: Dict[int, Tuple[float, float, float]] = {}
    last_velocities: Dict[int, Tuple[float, float, float]] = {}
    dt = 1.0 / fps if fps > 0 else 1.0 / 30.0

    def _frame_key(k: str) -> Tuple[int, str]:
        try:
            return int(k), k
        except Exception:
            return 0, k

    sorted_frames = sorted(((fid, _frame_key(fid)) for fid in frames.keys()), key=lambda t: t[1])

    def _object_meta(oid: str) -> Tuple[str, str]:
        meta = objects_meta.get(oid, {})
        return normalize_subtype(meta.get("subtype")), normalize_role(meta.get("role"))

    def project_pixel_to_xyz(cx: float, cy: float) -> Tuple[float, float, float]:
        if H is not None:
            X, Y = apply_homography(H, cx, cy)
            X, Y = post_transform_xy(
                X, Y,
                swap_xy=swap_xy,
                flip_x=flip_x,
                flip_y=flip_y,
                yaw_offset_rad=yaw_offset_rad,
                xy_offset=xy_offset,
            )
            return X, Y, 0.0
        return cx * 0.01, cy * 0.01, 0.0

    def estimate_dims(label_type: str, w_px: float, h_px: float, obj_uid: str = "") -> Tuple[float, float, float]:
        dims = defaults.get(label_type.lower(), defaults.get("other", {}))
        length = float(dims.get("length", w_px * 0.01))
        width = float(dims.get("width", h_px * 0.01))
        height = _resolve_height(label_type, obj_uid, towed_by, defaults)
        return length, width, height

    def compute_kinematics(idx: int, X: float, Y: float, Z: float) -> Tuple[Tuple[float, float, float], Tuple[float, float, float]]:
        last_p = last_positions.get(idx)
        if last_p is None:
            vel = (0.0, 0.0, 0.0)
            acc = (0.0, 0.0, 0.0)
        else:
            vel = ((X - last_p[0]) / dt, (Y - last_p[1]) / dt, (Z - last_p[2]) / dt)
            last_v = last_velocities.get(idx)
            if last_v is None:
                acc = (0.0, 0.0, 0.0)
            else:
                acc = ((vel[0] - last_v[0]) / dt, (vel[1] - last_v[1]) / dt, (vel[2] - last_v[2]) / dt)
        return vel, acc

    def update_caches(idx: int, X: float, Y: float, Z: float, vel: Tuple[float, float, float]):
        last_positions[idx] = (X, Y, Z)
        last_velocities[idx] = vel

    def _collect_all_dims() -> Dict[str, Tuple[float, float, float]]:
        """Pass over all frames collecting per-object geo dims; return per-object mean."""
        geo_sizes: Dict[str, List[Tuple[float, float, float]]] = {}
        for frame_id, _ in sorted_frames:
            frame = frames[frame_id]
            for oid, od in frame["objects"].items():
                cx, cy, w_px, h_px, yaw_img = od["rbbox"]
                meta = objects_meta.get(oid, {})
                label_type = meta.get("type", "other")
                heading_world = angle_wrap(
                    -float(yaw_img) + (_h_rotation_angle(H, cx, cy) if H is not None else 0.0)
                )
                h_veh = _resolve_height(label_type, oid, towed_by, defaults)
                if corrector is not None:
                    _r = corrector.correct(cx, cy, w_px, h_px, yaw_img, label_type, heading_world, h_veh)
                    dims = (_r.length, _r.width, _r.height)
                elif H is not None:
                    lw = _footprint_from_homography(H, cx, cy, w_px, h_px, yaw_img, heading_world)
                    dims = (lw[0], lw[1], h_veh)
                else:
                    dims = estimate_dims(label_type, w_px, h_px, oid)
                geo_sizes.setdefault(oid, []).append(dims)
        return {
            oid: tuple(np.array(v).mean(axis=0).tolist())
            for oid, v in geo_sizes.items()
        }

    size_avg: Dict[str, Tuple[float, float, float]] = _collect_all_dims() if stabilize_size else {}

    # ----------------------------
    # MCAP writing (patched)
    # ----------------------------
    # IMPORTANT:
    # Use betterosi.Writer as a context manager to ensure MCAP footer/summary are finalized properly. [1](https://deepwiki.com/ika-rwth-aachen/omega-prime/3.1-loading-and-saving-recordings)[4](https://ika-rwth-aachen.github.io/omega-prime/notebooks/tutorial/)
    # Also write topic names without leading "/" to match common omega-prime usage. [3](https://risecloud-my.sharepoint.com/personal/anders_thorsen_ri_se/Documents/Microsoft%20Copilot%20Chat%20Files/convert_app.py)[2](https://risecloud-my.sharepoint.com/personal/anders_thorsen_ri_se/Documents/Microsoft%20Copilot%20Chat%20Files/openlabel_to_omega.py)

    def _write_map(writer_mcap):
        if odr_path and os.path.isfile(odr_path):
            odr_xml = load_text(odr_path)
            if strip_xodr_namespace:
                odr_xml = re.sub(r'\s+xmlns(?::\w+)?="[^"]*"', "", odr_xml)
            try:
                map_msg = betterosi.MapAsamOpenDrive(open_drive_xml_content=odr_xml)
            except TypeError:
                map_msg = betterosi.MapAsamOpenDrive(content=odr_xml)
            writer_mcap.add(map_msg, topic="ground_truth_map", log_time=0)

    def _write_ground_truth(writer_mcap, t_sec: float, total_nanos: int, moving_objects_osi: List[Any]):
        gt = betterosi.GroundTruth(
            version=betterosi.InterfaceVersion(version_major=3, version_minor=7, version_patch=0),
            timestamp=betterosi.Timestamp(
                seconds=int(t_sec),
                nanos=int((t_sec - int(t_sec)) * 1_000_000_000),
            ),
            moving_object=moving_objects_osi,
            host_vehicle_id=betterosi.Identifier(value=0),
        )
        # Provide log_time explicitly; readers often build indices from it. [4](https://ika-rwth-aachen.github.io/omega-prime/notebooks/tutorial/)[3](https://risecloud-my.sharepoint.com/personal/anders_thorsen_ri_se/Documents/Microsoft%20Copilot%20Chat%20Files/convert_app.py)
        writer_mcap.add(gt, topic="ground_truth", log_time=total_nanos)

    # If MCAP enabled, write with context manager; else only build CSV rows
    if write_mcap and betterosi is not None:
        mcap_path = f"{out_prefix}.mcap"
        os.makedirs(os.path.dirname(os.path.abspath(mcap_path)) or ".", exist_ok=True)

        with betterosi.Writer(mcap_path) as writer_mcap:
            _write_map(writer_mcap)

            for seq_idx, (frame_id, frame_key) in enumerate(sorted_frames):
                frame = frames[frame_id]

                if isinstance(frame_key[0], int) and (seq_idx == 0 or frame_key[0] != 0):
                    t_sec = frame_key[0] * dt
                else:
                    t_sec = seq_idx * dt
                total_nanos = to_nanos(t_sec)

                moving_objects_osi = []

                for oid, od in frame["objects"].items():
                    cx, cy, w_px, h_px, yaw_img = od["rbbox"]
                    idx = obj_name_to_idx.get(oid)

                    meta = objects_meta.get(oid, {})
                    label_type = meta.get("type", "other")
                    type_code, type_name_upper, subtype_upper = mapper.classify(label_type)
                    role_upper = normalize_role(meta.get("role"))
                    vt_code = vt_name_to_code.get(subtype_upper, vt_default)
                    vr_code = vr_name_to_code.get(role_upper, vr_default)

                    heading_world = angle_wrap(-float(yaw_img) + (_h_rotation_angle(H, cx, cy) if H is not None else 0.0))
                    h_veh = _resolve_height(label_type, oid, towed_by, defaults)
                    if corrector is not None:
                        _res = corrector.correct(cx, cy, w_px, h_px, yaw_img, label_type, heading_world, h_veh)
                        X, Y = post_transform_xy(
                            _res.x, _res.y,
                            swap_xy=swap_xy, flip_x=flip_x, flip_y=flip_y,
                            yaw_offset_rad=yaw_offset_rad, xy_offset=xy_offset,
                        )
                        Z = _res.z
                        length, width, height = _res.length, _res.width, _res.height
                    else:
                        X, Y, Z = project_pixel_to_xyz(cx, cy)
                        if H is not None:
                            length, width = _footprint_from_homography(
                                H, cx, cy, w_px, h_px, yaw_img, heading_world
                            )
                            height = h_veh
                        else:
                            length, width, height = estimate_dims(label_type, w_px, h_px, oid)

                    if stabilize_size:
                        length, width, height = size_avg.get(oid, (length, width, height))

                    vel, acc = compute_kinematics(idx, X, Y, Z)
                    yaw = angle_wrap(heading_world + float(yaw_offset_rad))

                    csv_rows.append([
                        total_nanos, idx, X, Y, Z,
                        vel[0], vel[1], vel[2],
                        acc[0], acc[1], acc[2],
                        length, width, height,
                        0.0, 0.0, yaw,
                        type_code, vt_code, vr_code,
                        type_name_upper, subtype_upper, role_upper,
                    ])

                    # Create OSI MovingObject
                    try:
                        mo_type = betterosi.MovingObjectType(type_code)
                    except Exception:
                        if type_code == 2:
                            mo_type = betterosi.MovingObjectType.VEHICLE
                        elif type_code == 3:
                            mo_type = betterosi.MovingObjectType.PEDESTRIAN
                        elif type_code == 4:
                            mo_type = betterosi.MovingObjectType.ANIMAL
                        elif type_code == 0:
                            mo_type = betterosi.MovingObjectType.UNKNOWN
                        else:
                            mo_type = betterosi.MovingObjectType.OTHER

                    mo_kwargs = dict(
                        id=betterosi.Identifier(value=int(idx)),
                        type=mo_type,
                        base=betterosi.BaseMoving(
                            dimension=betterosi.Dimension3D(length=length, width=width, height=height),
                            position=betterosi.Vector3D(x=X, y=Y, z=Z),
                            orientation=betterosi.Orientation3D(roll=0.0, pitch=0.0, yaw=yaw),
                            velocity=betterosi.Vector3D(x=vel[0], y=vel[1], z=vel[2]),
                            acceleration=betterosi.Vector3D(x=acc[0], y=acc[1], z=acc[2]),
                        ),
                    )

                    if type_code == 2:
                        veh_class = make_vehicle_classification(
                            subtype_upper, role_upper,
                            vt_name_to_code, vr_name_to_code,
                            vt_default, vr_default,
                        )
                        if veh_class is None:
                            veh_class = betterosi.MovingObjectVehicleClassification()
                        mo_kwargs["vehicle_classification"] = veh_class

                    mo = betterosi.MovingObject(**mo_kwargs)
                    moving_objects_osi.append(mo)

                    update_caches(idx, X, Y, Z, vel)

                _write_ground_truth(writer_mcap, t_sec, total_nanos, moving_objects_osi)

    else:
        # MCAP disabled or betterosi missing — still produce CSV rows
        for seq_idx, (frame_id, frame_key) in enumerate(sorted_frames):
            frame = frames[frame_id]
            if isinstance(frame_key[0], int) and (seq_idx == 0 or frame_key[0] != 0):
                t_sec = frame_key[0] * dt
            else:
                t_sec = seq_idx * dt
            total_nanos = to_nanos(t_sec)

            for oid, od in frame["objects"].items():
                cx, cy, w_px, h_px, yaw_img = od["rbbox"]
                idx = obj_name_to_idx.get(oid)

                meta = objects_meta.get(oid, {})
                label_type = meta.get("type", "other")
                type_code, type_name_upper, subtype_upper = mapper.classify(label_type)
                role_upper = normalize_role(meta.get("role"))
                vt_code = vt_name_to_code.get(subtype_upper, vt_default)
                vr_code = vr_name_to_code.get(role_upper, vr_default)

                heading_world = angle_wrap(-float(yaw_img) + (_h_rotation_angle(H, cx, cy) if H is not None else 0.0))
                h_veh = _resolve_height(label_type, oid, towed_by, defaults)
                if corrector is not None:
                    _res = corrector.correct(cx, cy, w_px, h_px, yaw_img, label_type, heading_world, h_veh)
                    X, Y = post_transform_xy(
                        _res.x, _res.y,
                        swap_xy=swap_xy, flip_x=flip_x, flip_y=flip_y,
                        yaw_offset_rad=yaw_offset_rad, xy_offset=xy_offset,
                    )
                    Z = _res.z
                    length, width, height = _res.length, _res.width, _res.height
                else:
                    X, Y, Z = project_pixel_to_xyz(cx, cy)
                    if H is not None:
                        length, width = _footprint_from_homography(
                            H, cx, cy, w_px, h_px, yaw_img, heading_world
                        )
                        height = h_veh
                    else:
                        length, width, height = estimate_dims(label_type, w_px, h_px, oid)

                if stabilize_size:
                    length, width, height = size_avg.get(oid, (length, width, height))

                vel, acc = compute_kinematics(idx, X, Y, Z)
                yaw = angle_wrap(heading_world + float(yaw_offset_rad))

                csv_rows.append([
                    total_nanos, idx, X, Y, Z,
                    vel[0], vel[1], vel[2],
                    acc[0], acc[1], acc[2],
                    length, width, height,
                    0.0, 0.0, yaw,
                    type_code, vt_code, vr_code,
                    type_name_upper, subtype_upper, role_upper,
                ])

                update_caches(idx, X, Y, Z, vel)

    # Write CSV
    if write_csv:
        csv_path = f"{out_prefix}.csv"
        os.makedirs(os.path.dirname(os.path.abspath(csv_path)) or ".", exist_ok=True)
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(csv_cols)
            for row in sorted(csv_rows, key=lambda r: r[0]):
                writer.writerow(row)

    print(
        "Done. Wrote: "
        f"{'[MCAP ' + out_prefix + '.mcap] ' if (write_mcap and betterosi is not None) else ''}"
        f"{'[CSV ' + out_prefix + '.csv] ' if write_csv else ''}"
    )
