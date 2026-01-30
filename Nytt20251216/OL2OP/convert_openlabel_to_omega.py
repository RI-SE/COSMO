
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Convert ASAM OpenLABEL (SAVANT subset) to:
  • MCAP containing ASAM OSI GroundTruth
  • Omega-Prime style CSV (moving-object table)

- Reads OpenLABEL JSON (frames with rotated 2D bounding boxes).
- Projects pixel centers to ground-plane (XY) using a homography or camera model.
- Builds OSI GroundTruth per frame and writes MCAP (optionally embeds OpenDRIVE).
- Writes Omega-Prime-compatible CSV with:
    • type as INTEGER code (UNKNOWN=0, OTHER=1, VEHICLE=2, PEDESTRIAN=3, ANIMAL=4)
    • subtype/role as INTEGER codes (from betterosi enums)
    • type_name/subtype_name/role_name (UPPER-CASE) for human readability

v6:
  • Context manager for betterosi.Writer (correct MCAP finalization).
  • Always attaches vehicle_classification for vehicles (safe even if enums missing).
  • CSV uses integer 'type', 'subtype', 'role' codes to make omega_prime->betterosi conversion drop-in.

Author: SYNERGIES tooling (cleaned & v6 by M365 Copilot)
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


# --------------------------------------------------------------------------------------
# Utilities
# --------------------------------------------------------------------------------------

def load_json(path: str) -> Dict[str, Any]:
    """
    Load JSON; tolerates lightweight 'calibration.json' formatting issues (comments, fences).
    For strict OpenLABEL files, just regular json.load.
    """
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()

    if path.lower().endswith("calibration.json"):
        # Strip Markdown fences if present
        raw = re.sub(r"^\s*```.*$", "", raw, flags=re.MULTILINE)
        # Slice between first '{' and last '}'
        s = raw.find('{'); e = raw.rfind('}')
        if s != -1 and e != -1 and e > s:
            raw = raw[s:e+1]
        # Strip block and line comments
        raw = re.sub(r"/\*.*?\*/", "", raw, flags=re.S)
        raw = re.sub(r"(^|\s)//.*$", "", raw, flags=re.MULTILINE)
        # Remove trailing commas before } or ]
        raw = re.sub(r",\s*([}\]])", r"\1", raw)
        return json.loads(raw)

    # Default strict loader
    return json.loads(raw)


def load_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def to_nanos(sec: float) -> int:
    return int(round(sec * 1_000_000_000))


def compute_homography_from_extrinsics(intr: Dict[str, float],
                                       ext: Dict[str, Any]) -> np.ndarray:
    """
    Compute planar homography from image pixels to ground plane (z = ground_z)
    using a pinhole model: H = K * [r1 r2 t - r3*ground_z].
    """
    K = np.array([[intr["fx"], 0, intr["cx"]],
                  [0, intr["fy"], intr["cy"]],
                  [0, 0, 1.0]], dtype=np.float64)
    R = np.array(ext["R"], dtype=np.float64)  # 3x3
    t = np.array(ext["t"], dtype=np.float64).reshape(3, 1)
    ground_z = float(ext.get("ground_z", 0.0))
    r1, r2, r3 = R[:, 0].reshape(3, 1), R[:, 1].reshape(3, 1), R[:, 2].reshape(3, 1)
    H = K @ np.hstack([r1, r2, t - r3 * ground_z])  # 3x3
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


# --------------------------------------------------------------------------------------
# MovingObjectType classification (int code + name)
# --------------------------------------------------------------------------------------

def classify_openlabel_type(label_type: str) -> Tuple[int, str]:
    """
    Map OpenLABEL 'type' text to OSI MovingObjectType *integer code* and UPPER-CASE name.

    Returns:
        (type_code, type_name_upper)
        where type_code in {0:UNKNOWN, 1:OTHER, 2:VEHICLE, 3:PEDESTRIAN, 4:ANIMAL}
    """
    lt = (label_type or "").strip().lower()
    if lt in ("car", "van", "taxi", "automobile", "truck", "bus", "railvehicle",
              "bicycle", "cyclist", "motorcycle"):
        return (2, "VEHICLE")
    if lt in ("pedestrian", "human"):
        return (3, "PEDESTRIAN")
    if lt in ("animal",):
        return (4, "ANIMAL")
    if lt in ("unknown",):
        return (0, "UNKNOWN")
    return (1, "OTHER")


# --------------------------------------------------------------------------------------
# Vehicle subtype and role mappings (UPPER-CASE names)
# --------------------------------------------------------------------------------------

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


# --- Enum helpers: build name->code maps from betterosi enums (robust across naming) ---

def _canonical(name: str) -> str:
    """Strip common prefixes to yield a canonical UPPER-CASE name (e.g., TYPE_CAR -> CAR)."""
    name = name.strip().upper()
    for prefix in ("TYPE_", "ROLE_"):
        if name.startswith(prefix):
            return name[len(prefix):]
    return name

def build_enum_code_maps():
    """
    Build mapping dictionaries for VehicleType/VehicleRole:
        vt_name_to_code['CAR'] -> int
        vr_name_to_code['MOVING'] -> int
    Returns defaults as well (CAR/MOVING) to use when names aren't found.
    """
    vt_name_to_code, vr_name_to_code = {}, {}
    vt_default, vr_default = 0, 0  # sensible 0 defaults if enums missing

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


def make_vehicle_classification(subtype_upper: str, role_upper: str,
                                vt_name_to_code: Dict[str, int],
                                vr_name_to_code: Dict[str, int],
                                vt_default: int, vr_default: int):
    """
    Return a betterosi.MovingObjectVehicleClassification with *enum members or integer codes*,
    or an EMPTY classification message if enums are unavailable.
    """
    if betterosi is None:
        return None  # not writing MCAP in this branch

    # Determine integer codes first (robust even if enums missing)
    vt_code = vt_name_to_code.get(subtype_upper, vt_default)
    vr_code = vr_name_to_code.get(role_upper, vr_default)

    VT = getattr(betterosi, "VehicleType", None)
    VR = getattr(betterosi, "VehicleRole", None)

    if VT is None or VR is None:
        # Enums not present -> return a classification using default empty values
        return betterosi.MovingObjectVehicleClassification()

    # Try to use enum constructors by VALUE
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
        # As last resort, return empty classification
        return betterosi.MovingObjectVehicleClassification()

    # Build classification; if a member is missing, omit that field
    kwargs = {}
    if vt_member is not None:
        kwargs["type"] = vt_member
    if vr_member is not None:
        kwargs["role"] = vr_member
    return betterosi.MovingObjectVehicleClassification(**kwargs)


# --------------------------------------------------------------------------------------
# OpenLABEL parsing (SAVANT subset)
# --------------------------------------------------------------------------------------

def parse_openlabel(ol: Dict[str, Any]) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """
    Return:
        objects_meta: {object_id: {"name": str, "type": str, "subtype": Optional[str], "role": Optional[str]}}
        frames: {frame_id: {"objects": {object_id: {"rbbox": [cx,cy,w,h,yaw], "confidence": float}}}}
    """
    root = ol.get("openlabel", ol)

    # 1) Static object metadata
    objects_meta: Dict[str, Dict[str, Any]] = {}
    if isinstance(root.get("objects"), dict):
        for obj_id, obj in root["objects"].items():
            objects_meta[obj_id] = {
                "name": obj.get("name", obj_id),
                "type": obj.get("type", "other"),
                "subtype": obj.get("subtype", None),
                "role": obj.get("role", None),
            }

    # 2) Dynamic per-frame data
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

            # rbbox may be stored as dict or as a list of attribute dicts
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

            # confidence may be under object_data.vec.confidence.val
            conf = 1.0
            vec = od_data.get("vec", {})
            if isinstance(vec, dict) and "confidence" in vec and isinstance(vec["confidence"], dict):
                conf = float(vec["confidence"].get("val", 1.0))

            if rbbox is not None:
                objs_out[oid] = {"rbbox": rbbox, "confidence": conf}

        if objs_out:
            frames_out[frame_id] = {"objects": objs_out}

    return objects_meta, frames_out


# --------------------------------------------------------------------------------------
# Core conversion
# --------------------------------------------------------------------------------------

def convert_openlabel_to_omega(
    openlabel_path: str,
    odr_path: Optional[str],
    out_prefix: str,
    calibration_path: Optional[str],
    fps_arg: Optional[float] = None,
    write_csv: bool = True,
    write_mcap: bool = True,
):
    # Load inputs
    ol = load_json(openlabel_path)
    objects_meta, frames = parse_openlabel(ol)

    # Load calibration
    calib = load_json(calibration_path) if calibration_path else {}
    fps = float(calib.get("fps", fps_arg if fps_arg is not None else 30.0))  # default 30 Hz

    # Homography (either provided or derived)
    H = None
    if "homography" in calib:
        H = np.array(calib["homography"], dtype=np.float64)
    elif "intrinsics" in calib and "extrinsics" in calib:
        H = compute_homography_from_extrinsics(calib["intrinsics"], calib["extrinsics"])

    defaults = calib.get("default_dimensions_m", {})  # per-class fallbacks

    # Build enum code maps for subtype/role
    vt_name_to_code, vr_name_to_code, vt_default, vr_default = build_enum_code_maps()

    # Assign integer indices to objects
    obj_name_to_idx: Dict[str, int] = {}
    for i, oid in enumerate(sorted(objects_meta.keys())):
        obj_name_to_idx[oid] = i + 1  # start at 1

    # CSV columns (Omega-Prime moving-object table)
    # IMPORTANT: 'type', 'subtype', 'role' are INT codes, plus *_name for readability
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
            return (int(k), k)
        except Exception:
            return (0, k)

    sorted_frames = sorted(((fid, _frame_key(fid)) for fid in frames.keys()), key=lambda t: t[1])

    # Helper to pull per-object meta (including subtype/role)
    def _object_meta(oid: str) -> Tuple[str, str]:
        meta = objects_meta.get(oid, {})
        subtype_upper = normalize_subtype(meta.get("subtype"))
        role_upper = normalize_role(meta.get("role"))
        return subtype_upper, role_upper

    # MCAP writer (context manager; guarantees footer & trailing magic)
    writer_mcap_ctx = (
        betterosi.Writer(f"{out_prefix}.mcap")
        if (write_mcap and betterosi is not None)
        else None
    )

    # Add OpenDRIVE map at t=0 if provided
    def _add_opendrive(writer_mcap):
        if writer_mcap is None:
            return
        if odr_path and os.path.isfile(odr_path):
            odr_xml = load_text(odr_path)
            map_msg = betterosi.MapAsamOpenDrive(open_drive_xml_content=odr_xml)
            writer_mcap.add(map_msg, topic="/ground_truth_map", log_time=0)

    # Main writing block (MCAP path)
    if writer_mcap_ctx is not None:
        with writer_mcap_ctx as writer_mcap:
            _add_opendrive(writer_mcap)

            for seq_idx, (frame_id, frame_key) in enumerate(sorted_frames):
                frame = frames[frame_id]
                # Timestamp in seconds:
                if isinstance(frame_key[0], int) and (seq_idx == 0 or frame_key[0] != 0):
                    t_sec = frame_key[0] * dt
                else:
                    t_sec = seq_idx * dt
                total_nanos = to_nanos(t_sec)

                moving_objects_osi = []

                for oid, od in frame["objects"].items():
                    cx, cy, w_px, h_px, yaw_img = od["rbbox"]

                    # Project to ground or fallback
                    if H is not None:
                        X, Y = apply_homography(H, cx, cy)
                        Z = 0.0
                    else:
                        X, Y, Z = cx * 0.01, cy * 0.01, 0.0

                    idx = obj_name_to_idx.get(oid)
                    meta = objects_meta.get(oid, {})
                    label_type = meta.get("type", "other")
                    type_code, type_name_upper = classify_openlabel_type(label_type)

                    # Subtype/role normalized names
                    subtype_upper, role_upper = _object_meta(oid)
                    # Resolve integer codes using enums (or defaults)
                    vt_code = vt_name_to_code.get(subtype_upper, vt_default)
                    vr_code = vr_name_to_code.get(role_upper, vr_default)

                    # Dimensions: defaults per class or crude bbox scaling
                    dims = defaults.get(label_type.lower(), defaults.get("other", {}))
                    length = float(dims.get("length", w_px * 0.01))
                    width = float(dims.get("width", h_px * 0.01))
                    height = float(dims.get("height", 1.5))

                    # Velocity / Acceleration
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
                            acc = ((vel[0] - last_v[0]) / dt,
                                   (vel[1] - last_v[1]) / dt,
                                   (vel[2] - last_v[2]) / dt)

                    yaw = angle_wrap(float(yaw_img))

                    # Append CSV row (codes + readable names)
                    csv_rows.append([
                        total_nanos, idx, X, Y, Z,
                        vel[0], vel[1], vel[2],
                        acc[0], acc[1], acc[2],
                        length, width, height,
                        0.0, 0.0, yaw,
                        type_code, vt_code, vr_code,
                        type_name_upper, subtype_upper, role_upper,
                    ])

                    # Build OSI MovingObject for MCAP
                    if writer_mcap is not None:
                        # MovingObjectType: use int code if possible
                        mo_type = None
                        try:
                            mo_type = betterosi.MovingObjectType(type_code)
                        except Exception:
                            # fallback by name
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

                        # ALWAYS attach a classification message for vehicles
                        if type_code == 2:  # VEHICLE
                            veh_class = make_vehicle_classification(
                                subtype_upper, role_upper,
                                vt_name_to_code, vr_name_to_code,
                                vt_default, vr_default
                            )
                            if veh_class is None:
                                veh_class = betterosi.MovingObjectVehicleClassification()
                            mo_kwargs["vehicle_classification"] = veh_class

                        mo = betterosi.MovingObject(**mo_kwargs)
                        moving_objects_osi.append(mo)

                    # Update caches
                    last_positions[idx] = (X, Y, Z)
                    last_velocities[idx] = vel

                # Write one OSI GroundTruth per frame
                if writer_mcap is not None:
                    gt = betterosi.GroundTruth(
                        version=betterosi.InterfaceVersion(version_major=3, version_minor=7, version_patch=0),
                        timestamp=betterosi.Timestamp(
                            seconds=int(t_sec),
                            nanos=int((t_sec - int(t_sec)) * 1_000_000_000),
                        ),
                        moving_object=moving_objects_osi,
                        host_vehicle_id=betterosi.Identifier(value=0),  # roadside recordings: unknown host
                    )
                    writer_mcap.add(gt, topic="/ground_truth")

            # 'with' ensures writer_mcap.close() is called -> Footer + trailing magic are written

    else:
        # MCAP disabled or betterosi missing: still build the CSV
        for seq_idx, (frame_id, frame_key) in enumerate(sorted_frames):
            frame = frames[frame_id]
            t_sec = (frame_key[0] * dt) if isinstance(frame_key[0], int) else (seq_idx * dt)
            total_nanos = to_nanos(t_sec)

            for oid, od in frame["objects"].items():
                cx, cy, w_px, h_px, yaw_img = od["rbbox"]

                if H is not None:
                    X, Y = apply_homography(H, cx, cy)
                    Z = 0.0
                else:
                    X, Y, Z = cx * 0.01, cy * 0.01, 0.0

                idx = obj_name_to_idx.get(oid)
                meta = objects_meta.get(oid, {})
                label_type = meta.get("type", "other")
                type_code, type_name_upper = classify_openlabel_type(label_type)

                subtype_upper, role_upper = _object_meta(oid)
                vt_code = vt_name_to_code.get(subtype_upper, vt_default)
                vr_code = vr_name_to_code.get(role_upper, vr_default)

                dims = defaults.get(label_type.lower(), defaults.get("other", {}))
                length = float(dims.get("length", w_px * 0.01))
                width = float(dims.get("width", h_px * 0.01))
                height = float(dims.get("height", 1.5))

                last_p = last_positions.get(idx)
                if last_p is None:
                    vel = (0.0, 0.0, 0.0)
                    acc = (0.0, 0.0, 0.0)
                else:
                    vel = ((X - last_p[0]) / dt, (Y - last_p[1]) / dt, (Z - last_p[2]) / dt)
                    last_v = last_velocities.get(idx)
                    acc = (0.0, 0.0, 0.0) if last_v is None else (
                        (vel[0] - last_v[0]) / dt,
                        (vel[1] - last_v[1]) / dt,
                        (vel[2] - last_v[2]) / dt
                    )

                yaw = angle_wrap(float(yaw_img))

                csv_rows.append([
                    total_nanos, idx, X, Y, Z,
                    vel[0], vel[1], vel[2],
                    acc[0], acc[1], acc[2],
                    length, width, height,
                    0.0, 0.0, yaw,
                    type_code, vt_code, vr_code,
                    type_name_upper, subtype_upper, role_upper,
                ])

                last_positions[idx] = (X, Y, Z)
                last_velocities[idx] = vel

    # Write CSV
    if write_csv:
        csv_path = f"{out_prefix}.csv"
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(csv_cols)
            # Sort rows by total_nanos for consistency
            csv_rows_sorted = sorted(csv_rows, key=lambda r: r[0])
            for row in csv_rows_sorted:
                writer.writerow(row)

    print(
        "Done. Wrote: "
        f"{'[MCAP ' + out_prefix + '.mcap] ' if (write_mcap and betterosi is not None) else ''}"
        f"{'[CSV ' + out_prefix + '.csv] ' if write_csv else ''}"
    )


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="OpenLABEL ➜ OSI (MCAP) + Omega-Prime CSV")
    ap.add_argument("--openlabel", required=True, help="Path to OpenLABEL JSON (e.g., Saro_roundabout.json)")
    ap.add_argument("--odr", required=False, help="Path to OpenDRIVE XML (or .txt containing XML)")
    ap.add_argument("--out-prefix", required=True, help="Output file prefix (no extension), e.g., Saro_roundabout")
    ap.add_argument("--calibration", required=False, help="Path to calibration.json with homography or camera model")
    ap.add_argument("--fps", type=float, required=False, help="Override FPS (if not given in calibration)")
    ap.add_argument("--no-csv", action="store_true", help="Skip CSV writing")
    ap.add_argument("--no-mcap", action="store_true", help="Skip MCAP writing")
    args = ap.parse_args()

    convert_openlabel_to_omega(
        openlabel_path=args.openlabel,
        odr_path=args.odr,
        out_prefix=args.out_prefix,
        calibration_path=args.calibration,
        fps_arg=args.fps,
        write_csv=(not args.no_csv),
        write_mcap=(not args.no_mcap),
    )


if __name__ == "__main__":
    main()
