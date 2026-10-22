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
import warnings
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

# Optional: betterosi for OSI/MCAP; if missing, we still write CSV
try:
    import betterosi  # pip install betterosi
except ImportError:  # pragma: no cover
    betterosi = None

# orbit-georef owns the ORBIT georef JSON format + pixel→ground transform.
try:
    from orbit_georef import GeoTransformer
except ImportError:  # pragma: no cover
    GeoTransformer = None

# opendrive-map owns the authoritative OpenDRIVE header <offset> (map frame origin).
try:
    from opendrive_map import read_offset as _read_map_offset
except ImportError:  # pragma: no cover
    _read_map_offset = None

from cosmo.converters import companion as companion_doc
from cosmo.converters.environment import (
    DEFAULT_TIMEZONE,
    EnvironmentSources,
    FrameClock,
    build_environmental_conditions,
    clock_from_srt,
    environment_fields,
    load_weather,
    parse_srt,
    read_flight_record_meta,
    shift_srt_frames,
)
from cosmo.converters.kinematics import DEFAULT_JERK_PSD, raw_finite_differences, smooth_tracks
from cosmo.converters.ontology_mapper import OntologyMapper
from cosmo.converters.quality import Propagation, box_quality
from cosmo.converters.sensor_data import TOPIC as SENSOR_DATA_TOPIC
from cosmo.converters.sensor_data import detected_object
from cosmo.converters.sensor_data import sensor_data as sensor_data_message

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


def _read_georef_geo(georef_data_path: Optional[str]) -> Tuple[Optional[str], Optional[float], Optional[float]]:
    """Return (proj_string, lat, lon) from a georef file; reference_point or first control point."""
    if not georef_data_path or not os.path.isfile(georef_data_path):
        return None, None, None
    try:
        with open(georef_data_path, encoding="utf-8") as f:
            georef = json.load(f)
    except (OSError, ValueError):
        return None, None, None
    proj_string = georef.get("proj_string") or None
    point = georef.get("reference_point")
    if not point:
        cps = georef.get("control_points") or []
        point = cps[0] if cps else None
    if not point:
        return proj_string, None, None
    return proj_string, point.get("latitude"), point.get("longitude")


def load_environment_sources(
    root: Dict[str, Any],
    fps: float,
    srt_path: Optional[str],
    timezone_name: str,
    flight_record_path: Optional[str],
    flight_record_sequence: int,
    georef_data_path: Optional[str],
    log: Callable[[str], None],
    frame_offset: int = 0,
) -> EnvironmentSources:
    """Collect the optional time/position/weather sources; the SRT clock takes precedence over the flight record."""
    clock: Optional[FrameClock] = None
    srt_by_frame = None
    fr_start, fr_lat, fr_lon = (None, None, None)
    if flight_record_path and os.path.isfile(flight_record_path):
        fr_start, fr_lat, fr_lon = read_flight_record_meta(flight_record_path, flight_record_sequence)

    if srt_path:
        srt_frames = shift_srt_frames(parse_srt(srt_path), frame_offset)
        if len(srt_frames) < 2:
            raise ValueError(f"SRT {srt_path!r} has fewer than two parsable entries")
        clock = clock_from_srt(srt_frames, timezone_name)
        srt_by_frame = {f.frame_idx: f for f in srt_frames}
        log(f"[COSMO] SRT clock: start={clock.t0_utc.isoformat()}, fps={1.0 / clock.period_s:.3f}, "
            f"max residual={clock.max_residual_s * 1000:.1f} ms")
        if clock.max_residual_s > clock.period_s:
            log("[COSMO] Warning: SRT times deviate from a constant frame rate by more than one frame "
                "(dropped frames or clock jump?)")
        if fr_start and abs((fr_start - clock.t0_utc).total_seconds()) > 5.0:
            log(f"[COSMO] Warning: flight-record start {fr_start.isoformat()} differs from SRT start by "
                f"more than 5 s; check --timezone ({timezone_name})")
    elif fr_start:
        clock = FrameClock(fr_start, 1.0 / fps)
        log(f"[COSMO] Absolute time from flight record start {fr_start.isoformat()} (no SRT)")

    lat_lon = (fr_lat, fr_lon) if fr_lat is not None and fr_lon is not None else None
    if lat_lon is None:
        _, g_lat, g_lon = _read_georef_geo(georef_data_path)
        lat_lon = (g_lat, g_lon) if g_lat is not None and g_lon is not None else None

    return EnvironmentSources(
        weather=load_weather(root), clock=clock, srt_by_frame=srt_by_frame,
        lat_lon=lat_lon, tz_name=timezone_name,
    )


def _resolve_country_code(
    country_code: Optional[int],
    lat: Optional[float],
    lon: Optional[float],
    log: Callable[[str], None],
) -> int:
    """Resolve ISO 3166-1 numeric country code from an explicit value or georef lat/lon."""
    if country_code:
        return int(country_code)
    if lat is None or lon is None:
        log("[COSMO] No --country-code and no georef lat/lon; country_code left unset.")
        return 0
    try:
        import pycountry
        import reverse_geocoder
        alpha2 = reverse_geocoder.search((float(lat), float(lon)), mode=1)[0]["cc"]
        country = pycountry.countries.get(alpha_2=alpha2)
        if country is None:
            log(f"[COSMO] Could not map country '{alpha2}' to ISO numeric; country_code left unset.")
            return 0
        return int(country.numeric)
    except Exception as exc:  # geocoder/lookup unavailable or failed
        log(f"[COSMO] country_code derivation failed ({exc}); country_code left unset.")
        return 0


# ORBIT <= 0.8.0 labelled GUI homography exports "unknown" (fixed in ORBIT commit 0015a0f)
LEGACY_UNKNOWN_METHOD = "unknown"


def _is_projective(matrix: Any) -> bool:
    """True if a 3x3 matrix has perspective terms, which only a homography export can have."""
    try:
        m = np.asarray(matrix, dtype=np.float64)
    except (TypeError, ValueError):
        return False
    return m.shape == (3, 3) and bool(np.any(m[2, :2] != 0.0))


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

    # Explicit --fps wins; null/0 fps in the files counts as absent
    fps = float(fps_arg or georef.get("fps") or calib.get("fps") or 30.0)

    H: Optional[np.ndarray] = None

    if georef:
        tm = (georef.get("transform_method") or "").lower()
        if tm == LEGACY_UNKNOWN_METHOD and _is_projective(georef.get("transformation_matrix")):
            warnings.warn(
                f"{georef_data_path}: transform_method 'unknown' (legacy ORBIT export) read as homography, "
                "since the matrix has perspective terms",
                stacklevel=2,
            )
        elif tm and tm != "homography":
            raise ValueError(
                f"Unsupported ORBIT georef transform_method='{georef.get('transform_method')}'. "
                "Expected homography-style pixel->ground transform."
            )
        # orbit-georef owns the ORBIT georef format; use it for full exports and
        # fall back to inline parsing for partial/legacy calibration-style files.
        is_full_orbit_georef = all(
            k in georef for k in ("transformation_matrix", "inverse_matrix", "reference_point")
        )
        if GeoTransformer is not None and georef_data_path and is_full_orbit_georef:
            H = GeoTransformer.from_file(georef_data_path).transform_matrix
        elif "transformation_matrix" in georef:
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

# OSI vehicle role used when the object class implies no specific role.
DEFAULT_VEHICLE_ROLE = "CIVIL"


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
            vr_default = vr_name_to_code.get(DEFAULT_VEHICLE_ROLE, vr_default)
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

def _latest_vec_number(vec: Any, name: str) -> Optional[float]:
    """Newest value of a numeric vec entry, or None when absent (missing means unknown, not 1.0).

    SAVANT writes vec as a list of {name, val} with val newest first; a dict keyed by name is also accepted.
    """
    if isinstance(vec, dict):
        entry = vec.get(name)
    elif isinstance(vec, list):
        entry = next((e for e in vec if isinstance(e, dict) and e.get("name") == name), None)
    else:
        entry = None
    val = entry.get("val") if isinstance(entry, dict) else None
    if isinstance(val, list):
        val = val[0] if val else None
    try:
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None


def parse_openlabel(ol: Dict[str, Any]) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    root = ol.get("openlabel", ol)

    objects_meta: Dict[str, Dict[str, Any]] = {}
    if isinstance(root.get("objects"), dict):
        for obj_id, obj in root["objects"].items():
            objects_meta[obj_id] = {
                "name": obj.get("name", obj_id),
                "type": obj.get("type", "other"),
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

            conf = _latest_vec_number(od_data.get("vec"), "confidence")

            if rbbox is not None:
                objs_out[oid] = {"rbbox": rbbox, "confidence": conf, "quality": box_quality(od_data.get("vec"))}

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
    country_code: Optional[int] = None,
    object_frame: str = "global",
    srt_path: Optional[str] = None,
    timezone_name: str = DEFAULT_TIMEZONE,
    frame_offset: int = 0,
    flight_record_path: Optional[str] = None,
    flight_record_sequence: int = 0,
    video_quality_path: Optional[str] = None,
    companion_path: Optional[str] = None,
    companion_frames: bool = False,
    smooth_kinematics: bool = False,
    jerk_psd: float = DEFAULT_JERK_PSD,
    sensor_data: bool = False,
    quality_report: Optional[Dict[str, Any]] = None,
    height_model=None,
    camera_uncertainty: bool = False,
    estimate_height: bool = False,
) -> float:
    """
    Convert OpenLABEL -> Omega-Prime CSV and optionally OSI GroundTruth MCAP; returns the fps used.

    The first parameters match the former script version. Uncertainty (scenario-uncertainty SPEC P5) is
    computed when a companion file, SensorData, smoothing or a quality_report (filled in place) is asked for.
    height_model (an analytical BboxCorrector) gives the vehicle-height uncertainty of input that `cosmo correct`
    already corrected; it does not change any position. Opt-in: camera_uncertainty adds camera height, pitch
    and azimuth error to the oblique terms; estimate_height replaces the class-default height of objects whose
    track determines it (needs corrector; changes their position and size).
    """
    log = _logger(log_fn)
    ol = load_json(openlabel_path)
    objects_meta, frames = parse_openlabel(ol)
    root = ol.get("openlabel", ol)

    mapper = OntologyMapper.from_openlabel(root)
    if georef_data_path and odr_path and os.path.isfile(odr_path):
        _check_proj_string_match(georef_data_path, odr_path)

    fps, H, defaults = load_alignment(calibration_path, georef_data_path, fps_arg)
    alignment_source = "georef-data" if georef_data_path else ("calibration" if calibration_path else "none")
    log(f"[COSMO] Alignment source: {alignment_source} (H={'present' if H is not None else 'none'}, fps={fps})")
    log(f"[COSMO] Applied xy_offset={xy_offset}, yaw_offset_deg={yaw_offset_rad * 180.0 / math.pi:.3f}, swap_xy={swap_xy}, flip_x={flip_x}, flip_y={flip_y}")
    log(f"[COSMO] OpenDRIVE embedded: {'yes' if (odr_path and write_mcap and betterosi is not None and os.path.isfile(odr_path)) else 'no'}")
    if write_mcap and betterosi is None:
        log('[COSMO] MCAP requested but betterosi is not installed; will write CSV only.')

    heights = _estimate_heights(frames, objects_meta, H, corrector, log) if estimate_height else {}
    geometry = _Geometry(
        H=H, corrector=corrector, defaults=defaults, towed_by=build_towed_by(root, objects_meta), heights=heights,
        post=dict(swap_xy=swap_xy, flip_x=flip_x, flip_y=flip_y, yaw_offset_rad=yaw_offset_rad,
                  xy_offset=xy_offset),
        frame_off=_object_frame_offset(object_frame, odr_path, log),
    )
    obj_name_to_idx = _object_indices(objects_meta, frames, log)

    dt = 1.0 / fps if fps > 0 else 1.0 / 30.0
    env_sources = load_environment_sources(
        root, 1.0 / dt, srt_path, timezone_name,
        flight_record_path, flight_record_sequence, georef_data_path, log,
        frame_offset=frame_offset,
    )
    if srt_path and env_sources.clock is not None:
        dt = env_sources.clock.period_s

    sorted_frames = _sorted_frames(frames)
    if env_sources.srt_by_frame is not None and sorted_frames:
        last_frame = sorted_frames[-1][1][0]
        if last_frame not in env_sources.srt_by_frame:
            log(f"[COSMO] Warning: OpenLABEL frame {last_frame} has no SRT entry; "
                "is the SRT from the same (untrimmed) video?")

    propagation = None
    if H is not None and (companion_path or sensor_data or smooth_kinematics or quality_report is not None):
        video_quality = _read_video_quality(video_quality_path, log)
        precorrected = corrector is None and is_precorrected(root)
        propagation = Propagation(H, georef_data_path, geometry.post, video_quality,
                                  corrector or (height_model if precorrected else None), precorrected=precorrected,
                                  camera_uncertainty=camera_uncertainty, heights=heights)

    timeline = _build_timeline(sorted_frames, frames, objects_meta, geometry, _Classifier(mapper),
                               obj_name_to_idx, dt, propagation)
    if stabilize_size:
        _apply_mean_size(timeline)
    raw_finite_differences((f["t_sec"], f["samples"]) for f in timeline)
    if propagation is not None:
        # Always smoothed for the path-residual statistic; replaces the output only when asked for
        smooth_tracks(((f["t_sec"], f["samples"]) for f in timeline), jerk_psd, replace=smooth_kinematics)
        for s in (s for f in timeline for s in f["samples"]):
            s["smoothed_output"] = smooth_kinematics and s.get("smoothed") is not None
            if s["smoothed_output"]:  # averaged noise plus the bias that smoothing cannot reduce
                s["cov_rel"] = s["smoothed"][1][:2, :2] + s["cov_bias"]
    datum = propagation.cov_datum if propagation is not None else None

    if write_mcap and betterosi is not None:
        _write_mcap(
            f"{out_prefix}.mcap", timeline, odr_path, strip_xodr_namespace, georef_data_path, country_code,
            env_sources, geometry.frame_off if object_frame == "local" else None, log,
            sensor_datum=datum if (sensor_data and propagation is not None) else None,
            sensor_data=sensor_data and propagation is not None,
        )
    if propagation is not None:
        settings = {"frame_offset_applied": frame_offset, "camera_uncertainty": camera_uncertainty,
                    "height_estimate": {"objects": len(heights)} if estimate_height else None,
                    "kinematics": {"method": "rts_constant_acceleration" if smooth_kinematics else "finite_difference",
                                   "jerk_psd": jerk_psd if smooth_kinematics else None},
                    "oblique_correction": _oblique_label(corrector, propagation)}
        run = companion_doc.run_quality(
            propagation.run_quality(), timeline,
            _quality_inputs(openlabel_path, georef_data_path, video_quality_path), settings)
        if companion_path:
            proj_string, _, _ = _read_georef_geo(georef_data_path)
            companion_doc.write(companion_path, companion_doc.build(
                root, timeline, run, proj_string, companion_frames, f"COSMO {_cosmo_version()}", heights))
            log(f"[COSMO] Companion OpenLABEL: {companion_path} ({'with' if companion_frames else 'without'} frames)")
        if quality_report is not None:
            quality_report.update({"run": run, "companion_path": companion_path,
                                   "objects": companion_doc.object_stats(timeline, heights)})
    elif companion_path or sensor_data or smooth_kinematics:
        log("[COSMO] Warning: no homography; uncertainty, companion file, SensorData and smoothing skipped")
    if write_csv:
        _write_csv(f"{out_prefix}.csv", timeline)

    print(
        "Done. Wrote: "
        f"{'[MCAP ' + out_prefix + '.mcap] ' if (write_mcap and betterosi is not None) else ''}"
        f"{'[CSV ' + out_prefix + '.csv] ' if write_csv else ''}"
    )
    return 1.0 / dt


def _estimate_heights(frames, objects_meta, H, corrector, log) -> Dict[str, Tuple[float, float]]:
    """Per-object effective height from the raw boxes (SAVANT origins other than synthesized ones)."""
    if corrector is None or H is None:
        log("[COSMO] Warning: height estimation needs --bbox-correction (analytical or 3d); skipped")
        return {}
    from cosmo.converters.quality import SYNTHESIZED_ORIGINS
    from cosmo.corrections.height_estimate import estimate_heights

    observations: Dict[str, List[Tuple[Any, float]]] = {}
    for frame_id, _ in _sorted_frames(frames):
        for oid, od in frames[frame_id]["objects"].items():
            if od["quality"]["origin"] in SYNTHESIZED_ORIGINS:
                continue
            cx, cy, _, _, yaw_img = od["rbbox"]
            observations.setdefault(oid, []).append(
                (od["rbbox"], angle_wrap(-float(yaw_img) + _h_rotation_angle(H, cx, cy))))
    heights = estimate_heights(corrector, observations)
    log(f"[COSMO] Height estimated for {len(heights)} of {len(observations)} object(s); others use class defaults")
    return heights


def is_precorrected(root: Dict[str, Any]) -> bool:
    """True for OpenLABEL written by `cosmo correct` (world cuboids or its size_deviation entries)."""
    for frame in (root.get("frames") or {}).values():
        objects = (frame.get("objects") or {}).values()
        for od in objects:
            data = od.get("object_data") or {}
            if any(c.get("coordinate_system") == "world" for c in data.get("cuboid") or [] if isinstance(c, dict)):
                return True
            if any(str(e.get("name", "")).startswith("size_deviation") for e in data.get("vec") or []
                   if isinstance(e, dict)):
                return True
        if objects:  # cosmo correct marks every box; the first frame with boxes decides
            return False
    return False


def _oblique_label(corrector, propagation: Propagation) -> str:
    if corrector is not None:
        return getattr(corrector, "mode", "analytical")
    return "cosmo_correct" if propagation.precorrected else "none"


def _build_timeline(sorted_frames, frames, objects_meta, geometry: "_Geometry", classifier: "_Classifier",
                    obj_name_to_idx: Dict[str, int], dt: float, propagation: Optional[Propagation]):
    """Pass 1: world state (and uncertainty) of every box, frame by frame."""
    timeline = []
    for seq_idx, (frame_id, frame_key) in enumerate(sorted_frames):
        frame_no = _frame_number(seq_idx, frame_key)
        t_sec = frame_no * dt
        samples = []
        for oid, od in frames[frame_id]["objects"].items():
            label_type = objects_meta.get(oid, {}).get("type", "other")
            s = {**geometry.world(oid, label_type, od), "oid": oid, "idx": obj_name_to_idx.get(oid),
                 **classifier.codes(label_type)}
            if propagation is not None:
                s.update(propagation.sample(s, od["quality"], frame_no, label_type), X_raw=s["X"], Y_raw=s["Y"])
            samples.append(s)
        timeline.append({"frame_no": frame_no, "t_sec": t_sec, "total_nanos": to_nanos(t_sec), "samples": samples})
    return timeline


def _read_video_quality(path: Optional[str], log) -> Optional[Dict[str, Any]]:
    """The FieldDataLab video sidecar (scenario-uncertainty `video` profile), or None."""
    if not path:
        return None
    from scenario_uncertainty import video as video_profile

    document = video_profile.read(path)
    if document is None:
        log(f"[COSMO] Warning: video quality {path} missing or of an unsupported version; frame term unknown")
    return document


def _quality_inputs(openlabel_path: str, georef_data_path: Optional[str],
                    video_quality_path: Optional[str]) -> Dict[str, Any]:
    """Files the uncertainty came from, with their sha256 (SPEC P5 `inputs`)."""
    inputs = {}
    for name, path in (("openlabel", openlabel_path), ("georef", georef_data_path),
                       ("video_quality", video_quality_path)):
        if path and os.path.isfile(path):
            inputs[name] = {"file": os.path.basename(path), "sha256": companion_doc.sha256_file(path)}
    return inputs


def _cosmo_version() -> str:
    try:
        from importlib.metadata import version
        return version("cosmo")
    except Exception:
        return "unknown"


def _logger(log_fn: Optional[Callable[[str], None]]) -> Callable[[str], None]:
    """log_fn if it works, else print."""
    def _log(msg: str) -> None:
        if callable(log_fn):
            try:
                log_fn(msg)
                return
            except Exception:
                pass
        print(msg, flush=True)
    return _log


def _object_frame_offset(object_frame: str, odr_path: Optional[str], log) -> Tuple[float, float, float]:
    """Offset subtracted from object positions.

    "global": absolute coords in the proj_string CRS (default). "local": coords shifted by the map's
    header <offset> so objects and the embedded OpenDRIVE share one frame; the offset is recorded in
    OSI proj_frame_offset.
    """
    if object_frame == "global":
        return (0.0, 0.0, 0.0)
    if object_frame != "local":
        raise ValueError(f"object_frame must be 'global' or 'local', got {object_frame!r}")
    if not (odr_path and _read_map_offset and os.path.isfile(odr_path)):
        raise ValueError(
            "object_frame='local' requires --odr with a header <offset> "
            "(and opendrive-map installed)."
        )
    frame_off = _read_map_offset(odr_path)
    log(f"[COSMO] object_frame=local; subtracting map offset {frame_off}")
    return frame_off


def _object_indices(objects_meta: Dict[str, Any], frames: Dict[str, Any], log) -> Dict[str, int]:
    """OSI ids: the numeric UID if possible, else sequential; also for objects only seen in frames."""
    obj_name_to_idx: Dict[str, int] = {}
    for i, oid in enumerate(sorted(objects_meta.keys())):
        try:
            obj_name_to_idx[oid] = int(oid)
        except ValueError:
            obj_name_to_idx[oid] = i + 1
            log(f"[COSMO] Warning: object UID {oid!r} is not numeric; assigned sequential idx {i + 1}")

    # Assigning fallback IDs prevents None from propagating into int() calls later.
    all_frame_oids = {oid for fd in frames.values() for oid in fd["objects"]}
    for oid in sorted(all_frame_oids - set(obj_name_to_idx)):
        try:
            obj_name_to_idx[oid] = int(oid)
        except ValueError:
            obj_name_to_idx[oid] = max(obj_name_to_idx.values(), default=0) + 1
        log(f"[COSMO] Warning: object {oid!r} found in frames but not in objects metadata; "
            f"assigned id={obj_name_to_idx[oid]}, type defaults to 'other'")
    return obj_name_to_idx


def _frame_key(k: str) -> Tuple[int, str]:
    try:
        return int(k), k
    except Exception:
        return 0, k


def _sorted_frames(frames: Dict[str, Any]) -> List[Tuple[str, Tuple[int, str]]]:
    return sorted(((fid, _frame_key(fid)) for fid in frames.keys()), key=lambda t: t[1])


def _frame_number(seq_idx: int, frame_key: Tuple[int, str]) -> int:
    """Video frame number; falls back to sequence position for non-numeric frame keys."""
    if isinstance(frame_key[0], int) and (seq_idx == 0 or frame_key[0] != 0):
        return frame_key[0]
    return seq_idx


class _Geometry:
    """Pixel box -> world position, heading and size, shared by every sample of one conversion."""

    def __init__(self, H, corrector, defaults, towed_by, post, frame_off, heights=None):
        self.H, self.corrector, self.defaults, self.towed_by = H, corrector, defaults, towed_by
        self.post, self.frame_off = post, frame_off
        self.heights = heights or {}  # oid -> (estimated height, sigma)

    def world(self, oid: str, label_type: str, od: Dict[str, Any]) -> Dict[str, Any]:
        """World state of one box (heading in the world frame before the yaw offset)."""
        cx, cy, w_px, h_px, yaw_img = od["rbbox"]
        H = self.H
        heading_world = angle_wrap(-float(yaw_img) + (_h_rotation_angle(H, cx, cy) if H is not None else 0.0))
        h_veh = self.heights[oid][0] if oid in self.heights else _resolve_height(label_type, oid, self.towed_by,
                                                                                 self.defaults)
        oblique = None
        if self.corrector is not None:
            oblique = self.corrector.correct(cx, cy, w_px, h_px, yaw_img, label_type, heading_world, h_veh)
            X, Y = post_transform_xy(oblique.x, oblique.y, **self.post)
            Z = oblique.z
            length, width, height = oblique.length, oblique.width, oblique.height
        else:
            X, Y, Z = self._project(cx, cy)
            if H is not None:
                length, width = _footprint_from_homography(H, cx, cy, w_px, h_px, yaw_img, heading_world)
                height = h_veh
            else:
                length, width, height = self._default_dims(label_type, w_px, h_px, oid)
        off = self.frame_off
        return {
            "X": X - off[0], "Y": Y - off[1], "Z": Z - off[2],
            "length": length, "width": width, "height": height,
            "yaw": angle_wrap(heading_world + float(self.post["yaw_offset_rad"])),
            "rbbox": od["rbbox"], "heading_world": heading_world, "h_veh": h_veh, "oblique": oblique,
        }

    def _project(self, cx: float, cy: float) -> Tuple[float, float, float]:
        if self.H is not None:
            X, Y = apply_homography(self.H, cx, cy)
            X, Y = post_transform_xy(X, Y, **self.post)
            return X, Y, 0.0
        return cx * 0.01, cy * 0.01, 0.0

    def _default_dims(self, label_type: str, w_px: float, h_px: float, obj_uid: str) -> Tuple[float, float, float]:
        dims = self.defaults.get(label_type.lower(), self.defaults.get("other", {}))
        length = float(dims.get("length", w_px * 0.01))
        width = float(dims.get("width", h_px * 0.01))
        return length, width, _resolve_height(label_type, obj_uid, self.towed_by, self.defaults)


class _Classifier:
    """OSI type/subtype/role codes per SAVANT label type."""

    def __init__(self, mapper: OntologyMapper):
        self.mapper = mapper
        self.vt_name_to_code, self.vr_name_to_code, self.vt_default, self.vr_default = build_enum_code_maps()

    def codes(self, label_type: str) -> Dict[str, Any]:
        type_code, type_name_upper, subtype_upper = self.mapper.classify(label_type)
        role_upper = self.mapper.role(label_type) or DEFAULT_VEHICLE_ROLE
        return {
            "type_code": type_code, "type_name": type_name_upper,
            "subtype_name": subtype_upper, "role_name": role_upper,
            "vt_code": self.vt_name_to_code.get(subtype_upper, self.vt_default),
            "vr_code": self.vr_name_to_code.get(role_upper, self.vr_default),
            "vehicle_classification": (
                make_vehicle_classification(subtype_upper, role_upper, self.vt_name_to_code,
                                            self.vr_name_to_code, self.vt_default, self.vr_default)
                if type_code == 2 else None),
        }


def _apply_mean_size(timeline: List[Dict[str, Any]]) -> None:
    """Replace every sample's size by its object's mean over the whole recording."""
    sizes: Dict[str, List[Tuple[float, float, float]]] = {}
    for frame in timeline:
        for s in frame["samples"]:
            sizes.setdefault(s["oid"], []).append((s["length"], s["width"], s["height"]))
    mean = {oid: tuple(np.array(v).mean(axis=0).tolist()) for oid, v in sizes.items()}
    for frame in timeline:
        for s in frame["samples"]:
            s["length"], s["width"], s["height"] = mean[s["oid"]]


CSV_COLUMNS = [
    "total_nanos", "idx", "x", "y", "z",
    "vel_x", "vel_y", "vel_z",
    "acc_x", "acc_y", "acc_z",
    "length", "width", "height",
    "roll", "pitch", "yaw",
    "type", "subtype", "role",
    "type_name", "subtype_name", "role_name",
]


def _write_csv(csv_path: str, timeline: List[Dict[str, Any]]) -> None:
    rows = [
        [f["total_nanos"], s["idx"], s["X"], s["Y"], s["Z"], *s["vel"], *s["acc"],
         s["length"], s["width"], s["height"], 0.0, 0.0, s["yaw"],
         s["type_code"], s["vt_code"], s["vr_code"], s["type_name"], s["subtype_name"], s["role_name"]]
        for f in timeline for s in f["samples"]
    ]
    os.makedirs(os.path.dirname(os.path.abspath(csv_path)) or ".", exist_ok=True)
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(CSV_COLUMNS)
        for row in sorted(rows, key=lambda r: r[0]):
            writer.writerow(row)


def _moving_object_type(type_code: int):
    try:
        return betterosi.MovingObjectType(type_code)
    except Exception:
        return {2: betterosi.MovingObjectType.VEHICLE, 3: betterosi.MovingObjectType.PEDESTRIAN,
                4: betterosi.MovingObjectType.ANIMAL, 0: betterosi.MovingObjectType.UNKNOWN,
                }.get(type_code, betterosi.MovingObjectType.OTHER)


def _base_moving(s: Dict[str, Any]):
    vel, acc = s["vel"], s["acc"]
    return betterosi.BaseMoving(
        dimension=betterosi.Dimension3D(length=s["length"], width=s["width"], height=s["height"]),
        position=betterosi.Vector3D(x=s["X"], y=s["Y"], z=s["Z"]),
        orientation=betterosi.Orientation3D(roll=0.0, pitch=0.0, yaw=s["yaw"]),
        velocity=betterosi.Vector3D(x=vel[0], y=vel[1], z=vel[2]),
        acceleration=betterosi.Vector3D(x=acc[0], y=acc[1], z=acc[2]),
    )


def _moving_object(s: Dict[str, Any]):
    mo_kwargs = dict(id=betterosi.Identifier(value=int(s["idx"])), type=_moving_object_type(s["type_code"]),
                     base=_base_moving(s))
    if s["type_code"] == 2:
        mo_kwargs["vehicle_classification"] = (
            s["vehicle_classification"] or betterosi.MovingObjectVehicleClassification())
    return betterosi.MovingObject(**mo_kwargs)


def _write_mcap(
    mcap_path: str, timeline: List[Dict[str, Any]], odr_path: Optional[str], strip_xodr_namespace: bool,
    georef_data_path: Optional[str], country_code: Optional[int], env_sources: EnvironmentSources,
    local_frame_off: Optional[Tuple[float, float, float]], log,
    sensor_datum: Optional[np.ndarray] = None, sensor_data: bool = False,
) -> None:
    """OSI GroundTruth per frame plus the OpenDRIVE map, and optionally a SensorData topic.

    betterosi.Writer is used as a context manager so the MCAP footer/summary are finalized; topic
    names have no leading "/" to match common omega-prime usage.
    """
    os.makedirs(os.path.dirname(os.path.abspath(mcap_path)) or ".", exist_ok=True)
    proj_string, lat, lon = _read_georef_geo(georef_data_path)
    country = _resolve_country_code(country_code, lat, lon, log)
    with betterosi.Writer(mcap_path) as writer_mcap:
        if odr_path and os.path.isfile(odr_path):
            writer_mcap.add(_map_message(odr_path, strip_xodr_namespace), topic="ground_truth_map", log_time=0)
        for frame in timeline:
            gt = _ground_truth(frame, proj_string, country, env_sources, local_frame_off)
            # Provide log_time explicitly; readers often build indices from it
            writer_mcap.add(gt, topic="ground_truth", log_time=frame["total_nanos"])
            if sensor_data:
                detected = [detected_object(betterosi, s, _base_moving(s), _moving_object_type(s["type_code"]),
                                            sensor_datum) for s in frame["samples"]]
                message = sensor_data_message(betterosi, frame, _osi_timestamp(frame["t_sec"]), detected)
                writer_mcap.add(message, topic=SENSOR_DATA_TOPIC, log_time=frame["total_nanos"])


def _map_message(odr_path: str, strip_xodr_namespace: bool):
    odr_xml = load_text(odr_path)
    if strip_xodr_namespace:
        odr_xml = re.sub(r'\s+xmlns(?::\w+)?="[^"]*"', "", odr_xml)
    try:
        return betterosi.MapAsamOpenDrive(open_drive_xml_content=odr_xml)
    except TypeError:
        return betterosi.MapAsamOpenDrive(content=odr_xml)


def _osi_timestamp(t_sec: float):
    return betterosi.Timestamp(seconds=int(t_sec), nanos=int((t_sec - int(t_sec)) * 1_000_000_000))


def _ground_truth(frame: Dict[str, Any], proj_string, country: int, env_sources: EnvironmentSources,
                  local_frame_off: Optional[Tuple[float, float, float]]):
    gt = betterosi.GroundTruth(
        version=betterosi.InterfaceVersion(version_major=3, version_minor=7, version_patch=0),
        timestamp=_osi_timestamp(frame["t_sec"]),
        moving_object=[_moving_object(s) for s in frame["samples"]],
        host_vehicle_id=betterosi.Identifier(value=0),
    )
    if proj_string:
        gt.proj_string = proj_string
    if country:
        gt.country_code = country
    env = build_environmental_conditions(betterosi, environment_fields(frame["frame_no"], env_sources))
    if env is not None:
        gt.environmental_conditions = env
    if local_frame_off is not None and any(local_frame_off):
        # OSI: world = proj_frame_offset + osi, then proj_string. Records the local→projected-CRS
        # shift so local coords stay fully georeferenced.
        gt.proj_frame_offset = betterosi.GroundTruthProjFrameOffset(
            position=betterosi.Vector3D(x=local_frame_off[0], y=local_frame_off[1], z=local_frame_off[2]),
            yaw=0.0,
        )
    return gt
