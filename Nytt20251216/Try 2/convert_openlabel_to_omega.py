
#!/usr/bin/env python3
"""
Convert ASAM OpenLABEL (SAVANT subset) to ASAM OSI GroundTruth and Omega-Prime outputs.

- Reads OpenLABEL JSON with frames & 2D rotated bounding boxes.
- Projects pixel centers to ground-plane (world) XY via a homography or camera model.
- Builds OSI GroundTruth messages per frame and writes MCAP (optionally embedding OpenDRIVE).
- Writes Omega-Prime style CSV (moving-object table) for quick inspection & pipelines.

Author: SYNERGIES tooling
"""

import argparse
import json, re
import math
import os
from typing import Dict, Any, List, Tuple, Optional

import numpy as np

# CSV writing (no heavy deps required)
import csv
from collections import defaultdict

# Optional: betterosi & omega-prime for MCAP / OSI
try:
    import betterosi  # pip install betterosi
except ImportError:
    betterosi = None

################################################################################
# Utilities
################################################################################

"""
def load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
"""

# --- replace load_json() in convert_openlabel_to_omega.py ---
def load_json(path: str) -> Dict[str, Any]:
    """Load JSON; tolerates // and /* */ comments for calibration files."""

    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()

    if path.lower().endswith("calibration.json"):
        # remove Markdown code fences if present
        raw = re.sub(r"^\s*```.*$", "", raw, flags=re.MULTILINE)
        # slice between the first '{' and last '}'
        s = raw.find('{'); e = raw.rfind('}')
        if s != -1 and e != -1 and e > s:
            raw = raw[s:e+1]
        # strip block comments and line comments
        raw = re.sub(r"/\*.*?\*/", "", raw, flags=re.S)
        raw = re.sub(r"(^|\s)//.*$", "", raw, flags=re.MULTILINE)
        # strip trailing commas before } or ]
        raw = re.sub(r",\s*([}\]])", r"\1", raw)
        return json.loads(raw)

    # default strict loader
    
    # Default strict loader for everything else (your OpenLABEL is strict JSON)
    return json.loads(raw)

   


def load_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

def ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)

def to_nanos(sec: float) -> int:
    return int(round(sec * 1_000_000_000))

def compute_homography_from_extrinsics(intr: Dict[str,float], ext: Dict[str,Any]) -> np.ndarray:
    """
    Compute planar homography from image pixels to ground plane (z = ground_z)
    using pinhole model: H = K * [r1 r2 t] * inv([e1 e2 e3]) for z=const plane.
    This is a simplified derivation: assumes the ground plane is horizontal in world.
    """
    K = np.array([[intr["fx"], 0, intr["cx"]],
                  [0, intr["fy"], intr["cy"]],
                  [0, 0, 1.0]], dtype=np.float64)

    R = np.array(ext["R"], dtype=np.float64)  # 3x3
    t = np.array(ext["t"], dtype=np.float64).reshape(3,1)
    ground_z = float(ext.get("ground_z", 0.0))

    # Plane z = ground_z in camera coordinates: we first express plane in world, then to camera.
    # world->cam: X_cam = R * X_world + t
    # Solve homography: x ~ K [r1 r2 t - r3*ground_z] * [X_world_plane], where r_i are columns of R
    r1, r2, r3 = R[:,0].reshape(3,1), R[:,1].reshape(3,1), R[:,2].reshape(3,1)
    H = K @ np.hstack([r1, r2, t - r3*ground_z])  # 3x3
    return H

def apply_homography(H: np.ndarray, u: float, v: float) -> Tuple[float, float]:
    """Project pixel (u,v) to ground-plane coordinates (X,Y) in meters (z=0)."""
    p = np.array([u, v, 1.0], dtype=np.float64)
    q = H @ p
    if abs(q[2]) < 1e-12:
        return float("nan"), float("nan")
    return float(q[0]/q[2]), float(q[1]/q[2])

def angle_wrap(yaw: float) -> float:
    """Wrap yaw to [-pi, pi]."""
    return (yaw + math.pi) % (2 * math.pi) - math.pi

def classify_openlabel_type(label_type: str) -> Tuple[str, int]:
    """
    Map OpenLABEL 'type' text to OSI MovingObjectType and a simple numeric code for CSV.
    You can extend as needed.
    """
    lt = (label_type or "").lower()
    if lt in ("car", "van", "taxi", "automobile"):
        return ("vehicle", 2)
    if lt in ("truck", "bus", "railvehicle"):
        return ("vehicle", 2)
    if lt in ("pedestrian", "human", "cyclist", "bicycle", "motorcycle"):
        return ("pedestrian", 3) if lt in ("pedestrian","human") else ("vehicle", 2)
    if lt in ("animal",):
        return ("animal", 4)
    return ("other", 1)

################################################################################
# OpenLABEL parsing (SAVANT subset)
################################################################################

"""
def parse_openlabel(
    ol: Dict[str,Any]
) -> Tuple[Dict[str,Dict[str,Any]], Dict[str,Dict[str,Any]]]:
    #Return:
    #  objects_meta: {object_name: {"type": "..."}}
    #  frames: {frame_id: {"objects": {object_name: {"rbbox": [cx,cy,w,h,theta], "confidence": float}}}}
    
    # Many OpenLABEL files nest under root key "openlabel"; accept both
    root = ol.get("openlabel", ol)

    # Objects with static meta (names & types)
    objects_meta = {}
    if "objects" in root and isinstance(root["objects"], dict):
        for obj_id, obj in root["objects"].items():
            # SAVANT annotator tends to have 'name' and 'type'
            objects_meta[obj_id] = {
                "name": obj.get("name", obj_id),
                "type": obj.get("type", "other")
            }

    # Frames with dynamic data
    frames = {}
    # Some variants use list; others a dict keyed by frame number
    raw_frames = root.get("frames", {})
    if isinstance(raw_frames, dict):
        iterable = raw_frames.items()
    elif isinstance(raw_frames, list):
        iterable = enumerate(raw_frames)
    else:
        iterable = []

    for fkey, fval in iterable:
        # Accept string/int frame keys
        frame_id = str(fkey)
        fobj = fval.get("objects", {}) if isinstance(fval, dict) else {}
        objs_out = {}
        for oid, od in fobj.items():
            # Expect od["object_data"]["rbbox"]["val"] == [cx, cy, w, h, yaw]
            rbbox = None
            conf = None
            od_data = od.get("object_data", {})
            if "rbbox" in od_data:
                rb = od_data["rbbox"]
                val = rb.get("val", rb.get("shape", {}).get("val"))
                if isinstance(val, list) and len(val) >= 5:
                    rbbox = [float(val[0]), float(val[1]), float(val[2]), float(val[3]), float(val[4])]
            # Confidence may be stored in "vec" or attributes
            if "confidence" in od_data:
                conf = float(od_data["confidence"].get("val", 1.0))
            else:
                vec = od_data.get("vec", {})
                if isinstance(vec, dict) and "confidence" in vec:
                    conf = float(vec["confidence"].get("val", 1.0))
            if rbbox is not None:
                objs_out[oid] = {"rbbox": rbbox, "confidence": conf if conf is not None else 1.0}
        if objs_out:
            frames[frame_id] = {"objects": objs_out}
    return objects_meta, frames
"""


# --- replace the whole parse_openlabel() in convert_openlabel_to_omega.py ---

def parse_openlabel(
    ol: Dict[str,Any]
) -> Tuple[Dict[str,Dict[str,Any]], Dict[str,Dict[str,Any]]]:
    """
    Return:
    objects_meta: {object_id: {"name": str, "type": str}}
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
                    # list-form: [{ "name": "shape", "val": [...] }, ...]
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

# def parse_openlabel(
#     ol: Dict[str,Any]
# ) -> Tuple[Dict[str,Dict[str,Any]], Dict[str,Dict[str,Any]]]:
#     """
#     Return:
#     objects_meta: {object_id: {"name": str, "type": str}}
#     frames: {frame_id: {"objects": {object_id: {"rbbox": [cx,cy,w,h,yaw], "confidence": float}}}}
#     """
#     root = ol.get("openlabel", ol)

#     # 1) Static object metadata
#     objects_meta: Dict[str, Dict[str, Any]] = {}
#     if isinstance(root.get("objects"), dict):
#         for obj_id, obj in root["objects"].items():
#             objects_meta[obj_id] = {
#                 "name": obj.get("name", obj_id),
#                 "type": obj.get("type", "other"),
#             }

#     # 2) Dynamic per-frame data
#     frames_out: Dict[str, Dict[str, Any]] = {}
#     raw_frames = root.get("frames", {})
#     iterable = raw_frames.items() if isinstance(raw_frames, dict) else (
#         enumerate(raw_frames) if isinstance(raw_frames, list) else []
#     )

#     for fkey, fval in iterable:
#         frame_id = str(fkey)
#         fobj = fval.get("objects", {}) if isinstance(fval, dict) else {}
#         objs_out: Dict[str, Dict[str, Any]] = {}

#         for oid, od in fobj.items():
#             od_data = od.get("object_data", {})
#             rbbox = None

#             # rbbox may be stored as dict or as a list of attribute dicts
#             if "rbbox" in od_data:
#                 rb = od_data["rbbox"]
#                 val = None

#                 if isinstance(rb, dict):
#                     # dict-form: rb = {"val": [...]} or rb = {"shape": {"val": [...]} }
#                     val = rb.get("val")
#                     if val is None and isinstance(rb.get("shape"), dict):
#                         val = rb["shape"].get("val")

#                 elif isinstance(rb, list):
#                     # list-form: rb = [ {"name":"shape","val":[...]}, ... ]
#                     for entry in rb:
#                         if isinstance(entry, dict) and (entry.get("name") == "shape" or "val" in entry):
#                             val = entry.get("val")
#                             if isinstance(val, list) and len(val) >= 5:
#                                 break

#                 if isinstance(val, list) and len(val) >= 5:
#                     rbbox = [float(val[0]), float(val[1]), float(val[2]), float(val[3]), float(val[4])]

#             # confidence can be under object_data.vec.confidence.val
#             conf = 1.0
#             vec = od_data.get("vec", {})
#             if isinstance(vec, dict) and "confidence" in vec and isinstance(vec["confidence"], dict):
#                 conf = float(vec["confidence"].get("val", 1.0))

#             if rbbox is not None:
#                 objs_out[oid] = {"rbbox": rbbox, "confidence": conf}

#         if objs_out:
#             frames_out[frame_id] = {"objects": objs_out}

    return objects_meta, frames_out


################################################################################
# Conversion core
################################################################################

def convert_openlabel_to_omega(
    openlabel_path: str,
    odr_path: Optional[str],
    out_prefix: str,
    calibration_path: Optional[str],
    fps_arg: Optional[float] = None,
    write_csv: bool = True,
    write_mcap: bool = True
):
    # Load inputs
    ol = load_json(openlabel_path)
    objects_meta, frames = parse_openlabel(ol)

    # Load calibration
    calib = load_json(calibration_path) if calibration_path else {}
    fps = float(calib.get("fps", fps_arg if fps_arg is not None else 30.0))

    # Homography
    H = None
    if "homography" in calib:
        H = np.array(calib["homography"], dtype=np.float64)
    elif "intrinsics" in calib and "extrinsics" in calib:
        H = compute_homography_from_extrinsics(calib["intrinsics"], calib["extrinsics"])

    defaults = calib.get("default_dimensions_m", {})

    # Assign numeric indices to objects (Omega-Prime uses integer idx per moving object)
    obj_name_to_idx = {}
    for i, oid in enumerate(sorted(objects_meta.keys())):
        obj_name_to_idx[oid] = i + 1  # start at 1

    # Prepare CSV columns (Omega-Prime moving-object table style)
    csv_cols = [
        "total_nanos","idx","x","y","z",
        "vel_x","vel_y","vel_z",
        "acc_x","acc_y","acc_z",
        "length","width","height",
        "roll","pitch","yaw",
        "type","subtype","role"
    ]

    csv_rows = []

    # For OSI building
    writer_mcap = None
    if write_mcap and betterosi is not None:
        writer_mcap = betterosi.Writer(f"{out_prefix}.mcap")
        # --- in convert_openlabel_to_omega.py, inside convert_openlabel_to_omega() ---
        # Attach OpenDRIVE if provided
        if odr_path and os.path.isfile(odr_path):
            odr_xml = load_text(odr_path)
            # Write an OSI map message (OpenDRIVE packaged) at time 0
            if writer_mcap is not None:     # <-- add this guard
                map_msg = betterosi.MapAsamOpenDrive(open_drive_xml_content=odr_xml)
                writer_mcap.add(map_msg, topic="/ground_truth_map", log_time=0)


    """
        # Attach OpenDRIVE if provided
        if odr_path and os.path.isfile(odr_path):
            odr_xml = load_text(odr_path)
            # Write an OSI map message (OpenDRIVE packaged) at time 0
            map_msg = betterosi.MapAsamOpenDrive(content=odr_xml)
            writer_mcap.add(map_msg, topic="/ground_truth_map", log_time=0)
    """
    
    


    # For velocity / acceleration computation
    last_positions: Dict[int, Tuple[float,float,float]] = {}
    last_velocities: Dict[int, Tuple[float,float,float]] = {}
    dt = 1.0 / fps

    # Iterate frames in time order
    # Use integer sorting if frame_id are numeric strings
    def _frame_key(k: str) -> Tuple[int,str]:
        try:
            return (int(k), k)
        except:
            return (0, k)
    for frame_id, frame_key in sorted(((fid, _frame_key(fid)) for fid in frames.keys()), key=lambda t: t[1]):
        frame = frames[frame_id]
        #t_sec = (frame_key[0] if isinstance(frame_key[0], int) else len(csv_rows)*dt) * dt
        t_sec = (frame_key[0] if isinstance(frame_key[0], int) else len(csv_rows)) * dt
        total_nanos = to_nanos(t_sec)

        moving_objects_osi = []

        for oid, od in frame["objects"].items():
            rb = od["rbbox"]  # [cx, cy, w, h, theta]
            cx, cy, w_px, h_px, yaw_img = rb
            # Project center to ground
            if H is not None:
                X, Y = apply_homography(H, cx, cy)
                Z = 0.0
            else:
                # Fallback (NO real-world meaning): put image pixels into meters via crude scale
                X, Y, Z = cx*0.01, cy*0.01, 0.0

            idx = obj_name_to_idx.get(oid, None)
            meta = objects_meta.get(oid, {})
            label_type = meta.get("type", "other")
            type_str, type_code = classify_openlabel_type(label_type)

            # Dimensions: either from defaults by class or scaled from bbox (very crude)
            dims = defaults.get(label_type.lower(), defaults.get("other", {}))
            length = float(dims.get("length", w_px*0.01))
            width  = float(dims.get("width",  h_px*0.01))
            height = float(dims.get("height", 1.5))

            # Kinematics
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

            # Orientation: we map image yaw to ground yaw (best effort; depends on camera mounting)
            yaw = angle_wrap(float(yaw_img))

            # Append CSV row
            csv_rows.append([
                total_nanos, idx, X, Y, Z,
                vel[0], vel[1], vel[2],
                acc[0], acc[1], acc[2],
                length, width, height,
                0.0, 0.0, yaw,
                type_str, "", ""  # subtype/role empty; extend if you have that info
            ])

            # Build OSI MovingObject (if MCAP requested)
            if writer_mcap is not None:
                mo = betterosi.MovingObject(
                    id=betterosi.Identifier(value=int(idx)),
                    type=betterosi.MovingObjectType.VEHICLE if type_code==2 else (
                          betterosi.MovingObjectType.PEDESTRIAN if type_code==3 else
                          betterosi.MovingObjectType.ANIMAL if type_code==4 else
                          betterosi.MovingObjectType.OTHER),
                    base=betterosi.BaseMoving(
                        dimension=betterosi.Dimension3D(length=length, width=width, height=height),
                        position=betterosi.Vector3D(x=X, y=Y, z=Z),
                        orientation=betterosi.Orientation3D(roll=0.0, pitch=0.0, yaw=yaw),
                        velocity=betterosi.Vector3D(x=vel[0], y=vel[1], z=vel[2]),
                        acceleration=betterosi.Vector3D(x=acc[0], y=acc[1], z=acc[2]),
                    )
                )
                moving_objects_osi.append(mo)

            # Update caches
            last_positions[idx] = (X, Y, Z)
            last_velocities[idx] = vel

        # Write one OSI GroundTruth per frame
        if writer_mcap is not None:
            gt = betterosi.GroundTruth(
                version=betterosi.InterfaceVersion(version_major=3, version_minor=7, version_patch=0),
                timestamp=betterosi.Timestamp(seconds=int(t_sec), nanos=int((t_sec-int(t_sec))*1_000_000_000)),
                moving_object=moving_objects_osi,
                host_vehicle_id=betterosi.Identifier(value=0)  # unknown host in roadside recordings
            )
            writer_mcap.add(gt, topic="/ground_truth")

    # Close MCAP
    """
    if writer_mcap is not None:
        writer_mcap.close()
    """
    
    # Write CSV
    if write_csv:
        csv_path = f"{out_prefix}.csv"
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(csv_cols)
            for row in csv_rows:
                writer.writerow(row)

    print(f"Done. Wrote: "
          f"{'[MCAP '+out_prefix+'.mcap] ' if writer_mcap is not None else ''}"
          f"{'[CSV '+out_prefix+'.csv] ' if write_csv else ''}")


################################################################################
# CLI
################################################################################

def main():
    ap = argparse.ArgumentParser(description="OpenLABEL ➜ OSI (MCAP) + Omega-Prime CSV")
    ap.add_argument("--openlabel", required=True, help="Path to OpenLABEL JSON (e.g., Saro_roundabout.json)")
    ap.add_argument("--odr", required=False, help="Path to OpenDRIVE XML (or .txt containing XML), e.g., saro-roundabout.xodr.txt")
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
        write_mcap=(not args.no_mcap)
    )

if __name__ == "__main__":
    main()
