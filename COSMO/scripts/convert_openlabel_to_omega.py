#!/usr/bin/env python3
# -*- coding: utf-8 -*-
""" 
Convert ASAM OpenLABEL (SAVANT subset) to:
 • MCAP containing ASAM OSI GroundTruth
 • Omega-Prime style CSV (moving-object table)

This version adds support for ORBIT georeferencing exports (xxx_georef_data.json):
 • Uses ORBIT "transformation_matrix" (pixel -> local meters) as the homography
   for projecting OpenLABEL pixel centers to ground-plane XY.
 • Keeps legacy --calibration.json support for fps and default_dimensions_m.

Notes on coordinate frames:
 • ORBIT-generated OpenDRIVE should usually share the same local XY frame as the
   ORBIT georef export from the same project.
 • If your OpenDRIVE XY axis convention differs, you can use optional CLI flags
   (swap/flip/offset/rotation) to align frames.

Outputs
-------
1) CSV: Omega-Prime compatible moving-object table.
2) MCAP: OSI GroundTruth stream (requires betterosi).

Author: SYNERGIES tooling (v6 cleaned) + georef update (v7)
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
from typing import Dict, Any, List, Tuple, Optional

import numpy as np

# Optional: betterosi for OSI/MCAP; if missing, we still write CSV
try:
    import betterosi  # pip install betterosi
except ImportError:
    betterosi = None


# -----------------------------------------------------------------------------
# Defaults
# -----------------------------------------------------------------------------

# Fallback dimensions (meters) if no calibration.json is supplied.
DEFAULT_DIMENSIONS_M: Dict[str, Dict[str, float]] = {
    "car": {"length": 4.5, "width": 1.8, "height": 1.5},
    "truck": {"length": 12.0, "width": 2.5, "height": 3.5},
    "bus": {"length": 12.0, "width": 2.5, "height": 3.2},
    "van": {"length": 5.0, "width": 2.0, "height": 2.2},
    "pedestrian": {"length": 0.5, "width": 0.5, "height": 1.7},
    "other": {"length": 3.0, "width": 1.5, "height": 1.5},
}


# -----------------------------------------------------------------------------
# Utilities
# -----------------------------------------------------------------------------

def load_json(path: str) -> Dict[str, Any]:
    """Load JSON.

    Historically the SAVANT calibration.json files have sometimes been wrapped in
    markdown fences or included comments. This loader tolerates that *only* for
    calibration-like filenames, and uses strict JSON otherwise.
    """
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()

    basename = os.path.basename(path).lower()
    is_calib_like = basename.endswith("calibration.json") or "calibration" in basename

    if is_calib_like:
        # Strip Markdown fences if present
        raw = re.sub(r"^\s*```.*$", "", raw, flags=re.MULTILINE)
        # Slice between first '{' and last '}'
        s = raw.find('{')
        e = raw.rfind('}')
        if s != -1 and e != -1 and e > s:
            raw = raw[s:e + 1]
        # Strip block and line comments
        raw = re.sub(r"/\*.*?\*/", "", raw, flags=re.S)
        raw = re.sub(r"(^\s*)//.*$", r"\1", raw, flags=re.MULTILINE)
        # Remove trailing commas before } or ]
        raw = re.sub(r",\s*([}\]])", r"\1", raw)

    return json.loads(raw)


def load_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def to_nanos(sec: float) -> int:
    return int(round(sec * 1_000_000_000))


def compute_homography_from_extrinsics(intr: Dict[str, float], ext: Dict[str, Any]) -> np.ndarray:
    """Compute planar homography from image pixels to ground plane (z = ground_z)
    using a pinhole model: H = K * [r1 r2 t - r3*ground_z].
    """
    K = np.array(
        [[intr["fx"], 0, intr["cx"]],
         [0, intr["fy"], intr["cy"]],
         [0, 0, 1.0]],
        dtype=np.float64,
    )
    R = np.array(ext["R"], dtype=np.float64)  # 3x3
    t = np.array(ext["t"], dtype=np.float64).reshape(3, 1)
    ground_z = float(ext.get("ground_z", 0.0))

    r1 = R[:, 0].reshape(3, 1)
    r2 = R[:, 1].reshape(3, 1)
    r3 = R[:, 2].reshape(3, 1)

    H = K @ np.hstack([r1, r2, t - r3 * ground_z])
    return H


def apply_homography(H: np.ndarray, u: float, v: float) -> Tuple[float, float]:
    """Project pixel (u,v) to ground-plane coordinates (X,Y) in meters."""
    p = np.array([u, v, 1.0], dtype=np.float64)
    q = H @ p
    if abs(q[2]) < 1e-12:
        return float("nan"), float("nan")
    return float(q[0] / q[2]), float(q[1] / q[2])


def angle_wrap(yaw: float) -> float:
    """Wrap yaw to [-pi, pi]."""
    return (yaw + math.pi) % (2 * math.pi) - math.pi


# -----------------------------------------------------------------------------
# ORBIT georef support
# -----------------------------------------------------------------------------

def load_alignment(
    calibration_path: Optional[str],
    georef_data_path: Optional[str],
    fps_arg: Optional[float],
) -> Tuple[float, Optional[np.ndarray], Dict[str, Dict[str, float]]]:
    """Load alignment information and return (fps, H, default_dimensions_m).

    H is a 3x3 homography mapping pixel (u,v) -> local ground plane (X,Y) in meters.

    Priority:
      - If --georef-data is provided and contains transformation_matrix, use it.
      - Else fall back to calibration.json homography or intrinsics/extrinsics.

    default_dimensions_m:
      - prefer calibration.json, then georef-data (if ever added), else fallback defaults.
    """
    calib = load_json(calibration_path) if calibration_path else {}
    georef = load_json(georef_data_path) if georef_data_path else {}

    fps = float(
        georef.get(
            "fps",
            calib.get("fps", fps_arg if fps_arg is not None else 30.0),
        )
    )

    H = None

    # Prefer ORBIT georef (pixel -> local meters)
    if georef:
        tm = (georef.get("transform_method") or "").lower()
        if tm and tm != "homography":
            raise ValueError(
                f"Unsupported ORBIT georef transform_method='{georef.get('transform_method')}'. "
                "This converter expects a homography-style pixel->ground transform."
            )

        if "transformation_matrix" in georef:
            H = np.array(georef["transformation_matrix"], dtype=np.float64)
        elif "homography" in georef:
            H = np.array(georef["homography"], dtype=np.float64)
        elif "inverse_matrix" in georef:
            # inverse_matrix is typically ground->pixel; invert to get pixel->ground
            H = np.linalg.inv(np.array(georef["inverse_matrix"], dtype=np.float64))

    # Fallback to calibration.json
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
    """Optional post-transform for XY to match OpenDRIVE coordinate conventions.

    Order:
      1) swap
      2) flip
      3) rotate by yaw_offset around origin
      4) translate by xy_offset

    yaw_offset_rad rotates (x,y) counterclockwise.
    """
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
# MovingObjectType classification (int code + name)
# -----------------------------------------------------------------------------

def classify_openlabel_type(label_type: str) -> Tuple[int, str]:
    """Map OpenLABEL 'type' text to OSI MovingObjectType integer code + name.

    Returns:
      (type_code, type_name_upper)

    type_code in {0:UNKNOWN, 1:OTHER, 2:VEHICLE, 3:PEDESTRIAN, 4:ANIMAL}
    """
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


# -----------------------------------------------------------------------------
# Vehicle subtype and role mappings (UPPER-CASE names)
# -----------------------------------------------------------------------------

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
    # fallbacks
    "unknown": "UNKNOWN",
    "other": "OTHER",
}

VEHICLE_ROLE_MAP: Dict[str, str] = {
    "ego": "EGO",
    "moving": "MOVING",
    "parked": "PARKED",
    "stopped": "STOPPED",
    "standing": "PARKED",
    # fallback
    "unknown": "UNKNOWN",
}


def normalize_subtype(subtype_in: Optional[str]) -> str:
    s = (subtype_in or "").strip().lower()
    return VEHICLE_SUBTYPE_MAP.get(s, "CAR")  # default to CAR


def normalize_role(role_in: Optional[str]) -> str:
    r = (role_in or "").strip().lower()
    return VEHICLE_ROLE_MAP.get(r, "MOVING")  # default to MOVING


# -----------------------------------------------------------------------------
# Enum helpers: build name->code maps from betterosi enums (robust across naming)
# -----------------------------------------------------------------------------

def _canonical(name: str) -> str:
    """Strip common prefixes to yield canonical UPPER-CASE name."""
    name = name.strip().upper()
    for prefix in ("TYPE_", "ROLE_"):
        if name.startswith(prefix):
            return name[len(prefix):]
    return name


def build_enum_code_maps():
    """Build mapping dictionaries for VehicleType/VehicleRole.

    Returns:
      vt_name_to_code, vr_name_to_code, vt_default, vr_default
    """
    vt_name_to_code, vr_name_to_code = {}, {}
    vt_default, vr_default = 0, 0

    if betterosi is not None:
        VT = getattr(betterosi, "VehicleType", None)
        VR = getattr(betterosi, "VehicleRole", None)

        if VT is not None:
            for m in VT:
                vt_name_to_code[_canonical(m.name)] = m.value
            vt_default = vt_name_to_code.get("CAR", vt_default)

        if VR is not None:
            for m in VR:
                vr_name_to_code[_canonical(m.name)] = m.value
            vr_default = vr_name_to_code.get("MOVING", vr_default)

    return vt_name_to_code, vr_name_to_code, vt_default, vr_default


def make_vehicle_classification(
    subtype_upper: str,
    role_upper: str,
    vt_name_to_code: Dict[str, int],
    vr_name_to_code: Dict[str, int],
    vt_default: int,
    vr_default: int,
):
    """Create betterosi vehicle classification message (or None if betterosi missing)."""
    if betterosi is None:
        return None

    vt_code = vt_name_to_code.get(subtype_upper, vt_default)
    vr_code = vr_name_to_code.get(role_upper, vr_default)

    VT = getattr(betterosi, "VehicleType", None)
    VR = getattr(betterosi, "VehicleRole", None)

    if VT is None or VR is None:
        return betterosi.MovingObjectVehicleClassification()

    vt_member = None
    vr_member = None

    try:
        vt_member = VT(vt_code)
    except Exception:
        pass

    try:
        vr_member = VR(vr_code)
    except Exception:
        pass

    if vt_member is None and vr_member is None:
        return betterosi.MovingObjectVehicleClassification()

    kwargs = {}
    if vt_member is not None:
        kwargs["type"] = vt_member
    if vr_member is not None:
        kwargs["role"] = vr_member

    return betterosi.MovingObjectVehicleClassification(**kwargs)


# -----------------------------------------------------------------------------
# OpenLABEL parsing (SAVANT subset)
# -----------------------------------------------------------------------------

def parse_openlabel(ol: Dict[str, Any]) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """Parse OpenLABEL.

    Returns:
      objects_meta: {object_id: {name, type, subtype, role}}
      frames: {frame_id: {objects: {object_id: {rbbox: [cx,cy,w,h,yaw], confidence}}}}
    """
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
                    # list-form: [{"name":"shape","val":[...]}, ...]
                    for entry in rb:
                        if isinstance(entry, dict) and (entry.get("name") == "shape" or "val" in entry):
                            val = entry.get("val")
                            if isinstance(val, list) and len(val) >= 5:
                                break

                if isinstance(val, list) and len(val) >= 5:
                    rbbox = [float(val[0]), float(val[1]), float(val[2]), float(val[3]), float(val[4])]

            conf = 1.0
            vec = od_data.get("vec", {})
            if isinstance(vec, dict) and "confidence" in vec and isinstance(vec["confidence"], dict):
                conf = float(vec["confidence"].get("val", 1.0))

            if rbbox is not None:
                objs_out[oid] = {"rbbox": rbbox, "confidence": conf}

        if objs_out:
            frames_out[frame_id] = {"objects": objs_out}

    return objects_meta, frames_out


# -----------------------------------------------------------------------------
# Core conversion
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
):
    # Load inputs
    ol = load_json(openlabel_path)
    objects_meta, frames = parse_openlabel(ol)

    fps, H, defaults = load_alignment(calibration_path, georef_data_path, fps_arg)

    # Build enum code maps for subtype/role
    vt_name_to_code, vr_name_to_code, vt_default, vr_default = build_enum_code_maps()

    # Assign integer indices to objects
    obj_name_to_idx: Dict[str, int] = {}
    for i, oid in enumerate(sorted(objects_meta.keys())):
        obj_name_to_idx[oid] = i + 1  # start at 1

    # CSV columns (Omega-Prime moving-object table)
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

    # Kinematics state caches
    last_positions: Dict[int, Tuple[float, float, float]] = {}
    last_velocities: Dict[int, Tuple[float, float, float]] = {}

    dt = 1.0 / fps

    # Prepare frame ordering and timestamps
    def _frame_key(k: str) -> Tuple[int, str]:
        try:
            return int(k), k
        except Exception:
            return 0, k

    sorted_frames = sorted(((fid, _frame_key(fid)) for fid in frames.keys()), key=lambda t: t[1])

    def _object_meta(oid: str) -> Tuple[str, str]:
        meta = objects_meta.get(oid, {})
        subtype_upper = normalize_subtype(meta.get("subtype"))
        role_upper = normalize_role(meta.get("role"))
        return subtype_upper, role_upper

    # MCAP writer context
    writer_mcap_ctx = (
        betterosi.Writer(f"{out_prefix}.mcap")
        if (write_mcap and betterosi is not None)
        else None
    )

    def _add_opendrive(writer_mcap):
        if writer_mcap is None:
            return
        if odr_path and os.path.isfile(odr_path):
            odr_xml = load_text(odr_path)
            map_msg = betterosi.MapAsamOpenDrive(open_drive_xml_content=odr_xml)
            writer_mcap.add(map_msg, topic="/ground_truth_map", log_time=0)

    def project_pixel_to_xy(cx: float, cy: float) -> Tuple[float, float, float]:
        # Project to ground or fallback
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
            Z = 0.0
        else:
            # crude fallback (pixels -> meters)
            X, Y, Z = cx * 0.01, cy * 0.01, 0.0
        return X, Y, Z

    def estimate_dims(label_type: str, w_px: float, h_px: float) -> Tuple[float, float, float]:
        dims = defaults.get(label_type.lower(), defaults.get("other", {}))
        length = float(dims.get("length", w_px * 0.01))
        width = float(dims.get("width", h_px * 0.01))
        height = float(dims.get("height", 1.5))
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
                acc = (
                    (vel[0] - last_v[0]) / dt,
                    (vel[1] - last_v[1]) / dt,
                    (vel[2] - last_v[2]) / dt,
                )
        return vel, acc

    def update_caches(idx: int, X: float, Y: float, Z: float, vel: Tuple[float, float, float]):
        last_positions[idx] = (X, Y, Z)
        last_velocities[idx] = vel

    # Main writing block
    if writer_mcap_ctx is not None:
        with writer_mcap_ctx as writer_mcap:
            _add_opendrive(writer_mcap)

            for seq_idx, (frame_id, frame_key) in enumerate(sorted_frames):
                frame = frames[frame_id]

                # Timestamp in seconds
                if isinstance(frame_key[0], int) and (seq_idx == 0 or frame_key[0] != 0):
                    t_sec = frame_key[0] * dt
                else:
                    t_sec = seq_idx * dt

                total_nanos = to_nanos(t_sec)

                moving_objects_osi = []

                for oid, od in frame["objects"].items():
                    cx, cy, w_px, h_px, yaw_img = od["rbbox"]

                    X, Y, Z = project_pixel_to_xy(cx, cy)

                    idx = obj_name_to_idx.get(oid)
                    meta = objects_meta.get(oid, {})
                    label_type = meta.get("type", "other")

                    type_code, type_name_upper = classify_openlabel_type(label_type)

                    subtype_upper, role_upper = _object_meta(oid)
                    vt_code = vt_name_to_code.get(subtype_upper, vt_default)
                    vr_code = vr_name_to_code.get(role_upper, vr_default)

                    length, width, height = estimate_dims(label_type, w_px, h_px)

                    vel, acc = compute_kinematics(idx, X, Y, Z)

                    yaw = angle_wrap(float(yaw_img) + float(yaw_offset_rad))

                    csv_rows.append([
                        total_nanos, idx, X, Y, Z,
                        vel[0], vel[1], vel[2],
                        acc[0], acc[1], acc[2],
                        length, width, height,
                        0.0, 0.0, yaw,
                        type_code, vt_code, vr_code,
                        type_name_upper, subtype_upper, role_upper,
                    ])

                    if writer_mcap is not None:
                        # MovingObjectType enum
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

                if writer_mcap is not None:
                    gt = betterosi.GroundTruth(
                        version=betterosi.InterfaceVersion(version_major=3, version_minor=7, version_patch=0),
                        timestamp=betterosi.Timestamp(
                            seconds=int(t_sec),
                            nanos=int((t_sec - int(t_sec)) * 1_000_000_000),
                        ),
                        moving_object=moving_objects_osi,
                        host_vehicle_id=betterosi.Identifier(value=0),
                    )
                    writer_mcap.add(gt, topic="/ground_truth")

    else:
        # MCAP disabled or betterosi missing
        for seq_idx, (frame_id, frame_key) in enumerate(sorted_frames):
            frame = frames[frame_id]
            t_sec = (frame_key[0] * dt) if isinstance(frame_key[0], int) else (seq_idx * dt)
            total_nanos = to_nanos(t_sec)

            for oid, od in frame["objects"].items():
                cx, cy, w_px, h_px, yaw_img = od["rbbox"]

                X, Y, Z = project_pixel_to_xy(cx, cy)

                idx = obj_name_to_idx.get(oid)
                meta = objects_meta.get(oid, {})
                label_type = meta.get("type", "other")

                type_code, type_name_upper = classify_openlabel_type(label_type)

                subtype_upper, role_upper = _object_meta(oid)
                vt_code = vt_name_to_code.get(subtype_upper, vt_default)
                vr_code = vr_name_to_code.get(role_upper, vr_default)

                length, width, height = estimate_dims(label_type, w_px, h_px)

                vel, acc = compute_kinematics(idx, X, Y, Z)

                yaw = angle_wrap(float(yaw_img) + float(yaw_offset_rad))

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


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="OpenLABEL -> OSI (MCAP) + Omega-Prime CSV (with ORBIT georef support)")

    ap.add_argument("--openlabel", required=True, help="Path to OpenLABEL JSON")
    ap.add_argument("--odr", required=False, help="Path to OpenDRIVE XML (.xodr or .txt containing XML)")
    ap.add_argument("--out-prefix", required=True, help="Output file prefix (no extension)")

    ap.add_argument(
        "--georef-data",
        required=False,
        help="Path to ORBIT xxx_georef_data.json (uses transformation_matrix as pixel->ground homography)",
    )

    ap.add_argument(
        "--calibration",
        required=False,
        help="(Optional) Path to calibration.json (legacy). Used for fps/default dimensions or homography.",
    )

    ap.add_argument("--fps", type=float, required=False, help="Override FPS (if not given in georef/calibration)")

    # Optional alignment tweaks if coordinate conventions differ
    ap.add_argument("--swap-xy", action="store_true", help="Swap projected X and Y")
    ap.add_argument("--flip-x", action="store_true", help="Flip X -> -X")
    ap.add_argument("--flip-y", action="store_true", help="Flip Y -> -Y")

    ap.add_argument(
        "--xy-offset",
        nargs=2,
        type=float,
        metavar=("DX", "DY"),
        default=(0.0, 0.0),
        help="Translate projected XY by (DX,DY) meters after swap/flip/rotation",
    )

    ap.add_argument(
        "--yaw-offset-deg",
        type=float,
        default=0.0,
        help="Rotate projected XY counterclockwise by this many degrees (applied after swap/flip)",
    )

    ap.add_argument("--no-csv", action="store_true", help="Skip CSV writing")
    ap.add_argument("--no-mcap", action="store_true", help="Skip MCAP writing")

    args = ap.parse_args()

    convert_openlabel_to_omega(
        openlabel_path=args.openlabel,
        odr_path=args.odr,
        out_prefix=args.out_prefix,
        calibration_path=args.calibration,
        georef_data_path=args.georef_data,
        fps_arg=args.fps,
        write_csv=(not args.no_csv),
        write_mcap=(not args.no_mcap),
        swap_xy=args.swap_xy,
        flip_x=args.flip_x,
        flip_y=args.flip_y,
        xy_offset=(float(args.xy_offset[0]), float(args.xy_offset[1])),
        yaw_offset_rad=math.radians(float(args.yaw_offset_deg)),
    )


if __name__ == "__main__":
    main()
