"""
cosmo.cli.correct

Standalone CLI for correcting OpenLABEL bboxes from oblique drone footage.

  cosmo correct input.json --output corrected.json \
    --georef-data georef.json --flight-record video_stats.json
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np

_COORD_SYSTEM_NAME = "world"


def _existing_file(p: str) -> str:
    path = Path(p)
    if not path.exists():
        raise argparse.ArgumentTypeError(f"File not found: {p}")
    if not path.is_file():
        raise argparse.ArgumentTypeError(f"Not a file: {p}")
    return str(path)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="cosmo correct",
        description="Correct oblique-drone bboxes in an OpenLABEL file (openlabel → corrected openlabel).",
    )
    ap.add_argument("input", help="Path to OpenLABEL JSON")
    ap.add_argument("--output", "-o", required=True, help="Output path for corrected OpenLABEL JSON")
    ap.add_argument("--georef-data", required=False, help="Path to ORBIT *_georef_data.json")
    ap.add_argument("--calibration", required=False, help="Path to legacy calibration JSON")
    ap.add_argument("--flight-record", required=True, metavar="PATH",
                    help="Path to FlightRecord_*.video_stats.json")
    ap.add_argument("--flight-record-sequence", type=int, default=0, metavar="N",
                    help="Sequence index within the flight record (default: 0)")
    ap.add_argument("--correction", choices=["analytical", "3d"], default="analytical",
                    help="Correction mode (default: analytical)")
    ap.add_argument("--camera-model", default="mavic3pro-standard",
                    help="Camera model key (default: mavic3pro-standard)")
    ap.add_argument("--hfov-deg", type=float, default=None, metavar="FLOAT",
                    help="Override horizontal FOV in degrees")
    ap.add_argument(
        "--output-coords", choices=["pixel", "geo", "both"], default="pixel",
        help=(
            "Output coordinate format. "
            "'pixel': update rbbox with corrected pixel coords (default). "
            "'geo': replace rbbox with cuboid in world coords (proj_string from georef). "
            "'both': update rbbox AND add cuboid."
        ),
    )
    return ap


def _apply_homography(H: np.ndarray, u: float, v: float) -> tuple[float, float]:
    q = H @ np.array([u, v, 1.0], dtype=np.float64)
    if abs(q[2]) < 1e-12:
        return float("nan"), float("nan")
    return float(q[0] / q[2]), float(q[1] / q[2])


def _world_to_pixel(H_inv: np.ndarray, X: float, Y: float) -> tuple[float, float]:
    return _apply_homography(H_inv, X, Y)


def _px_per_m_at(H_inv: np.ndarray, X: float, Y: float, eps: float = 0.01) -> np.ndarray:
    """2×2 Jacobian d(u,v)/d(X,Y) at world point (X, Y)."""
    u0, v0 = _world_to_pixel(H_inv, X, Y)
    u1, v1 = _world_to_pixel(H_inv, X + eps, Y)
    u2, v2 = _world_to_pixel(H_inv, X, Y + eps)
    return np.array([[(u1 - u0) / eps, (u2 - u0) / eps],
                     [(v1 - v0) / eps, (v2 - v0) / eps]])


def _get_object_data(frame_dict: dict, oid: str) -> dict | None:
    obj = frame_dict.get("objects", {}).get(oid)
    return obj.get("object_data") if isinstance(obj, dict) else None


def _update_rbbox(od_data: dict, new_val: list[float]) -> None:
    """Patch the rbbox val in-place."""
    rb = od_data.get("rbbox")
    if rb is None:
        return
    if isinstance(rb, dict):
        if "val" in rb:
            rb["val"] = new_val
        elif isinstance(rb.get("shape"), dict):
            rb["shape"]["val"] = new_val
    elif isinstance(rb, list):
        for entry in rb:
            if isinstance(entry, dict) and (entry.get("name") == "shape" or "val" in entry):
                if isinstance(entry.get("val"), list) and len(entry["val"]) >= 5:
                    entry["val"] = new_val
                    break


def _drop_rbbox(od_data: dict) -> None:
    od_data.pop("rbbox", None)


def _add_cuboid(od_data: dict, X: float, Y: float, yaw: float, L: float, W: float, H_veh: float) -> None:
    """Add/replace a cuboid entry with world-space corrected values."""
    # val = [x, y, z, rx, ry, rz, sx, sy, sz]  (Euler angles, z-up: only rz = yaw)
    cuboid_entry = {
        "name": "shape",
        "val": [X, Y, 0.0, 0.0, 0.0, yaw, L, W, H_veh],
        "coordinate_system": _COORD_SYSTEM_NAME,
    }
    existing = od_data.get("cuboid")
    if isinstance(existing, list):
        # Replace entry with same name if present, else append
        for i, e in enumerate(existing):
            if isinstance(e, dict) and e.get("name") == "shape":
                existing[i] = cuboid_entry
                return
        existing.append(cuboid_entry)
    else:
        od_data["cuboid"] = [cuboid_entry]


def _ensure_coordinate_system(root: dict, proj_string: str | None) -> None:
    """Add the world coordinate system declaration to the openlabel root."""
    cs: dict = {"type": "geo", "parent": ""}
    if proj_string:
        cs["proj_string"] = proj_string
    root.setdefault("coordinate_systems", {})[_COORD_SYSTEM_NAME] = cs


def _read_proj_string(georef_data_path: str | None) -> str | None:
    if not georef_data_path:
        return None
    try:
        with open(georef_data_path, encoding="utf-8") as f:
            return json.load(f).get("proj_string")
    except Exception:
        return None


def main(argv=None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)

    try:
        input_path = _existing_file(args.input)
    except argparse.ArgumentTypeError as e:
        ap.error(str(e))
        raise

    output_coords: str = args.output_coords
    needs_pixel = output_coords in ("pixel", "both")
    needs_geo = output_coords in ("geo", "both")

    # Load OpenLABEL
    with open(input_path, encoding="utf-8") as f:
        ol = json.load(f)

    # Load homography
    from cosmo.converters.openlabel_to_omega import load_alignment, parse_openlabel
    _, H, _ = load_alignment(args.calibration, args.georef_data, None)
    if H is None:
        ap.error("No homography found. Provide --georef-data or --calibration.")

    H_inv = np.linalg.inv(H)

    # Build camera + corrector
    from cosmo.corrections import BboxCorrector, load_camera_from_flight_record
    cam = load_camera_from_flight_record(
        args.flight_record, args.flight_record_sequence,
        args.camera_model, args.hfov_deg,
    )
    corrector = BboxCorrector(cam, H, mode=args.correction)

    # For geo output: add coordinate_systems to root
    root = ol.get("openlabel", ol)
    if needs_geo:
        proj_string = _read_proj_string(args.georef_data)
        _ensure_coordinate_system(root, proj_string)
        if not proj_string:
            print("Warning: no proj_string found in georef; coordinate_system written without projection info.")

    # Parse to get object metadata + parsed frame data
    objects_meta, frames_parsed = parse_openlabel(ol)

    raw_frames = root.get("frames", {})
    frame_items = list(raw_frames.items()) if isinstance(raw_frames, dict) else list(enumerate(raw_frames))

    n_corrected = 0
    for fkey, fval in frame_items:
        frame_id = str(fkey)
        parsed_frame = frames_parsed.get(frame_id, {})
        frame_raw = fval if isinstance(fval, dict) else raw_frames[fkey]

        for oid, od in parsed_frame.get("objects", {}).items():
            cx, cy, w_px, h_px, yaw_img = od["rbbox"]
            meta = objects_meta.get(oid, {})
            label_type = meta.get("type", "other")
            res = corrector.correct(cx, cy, w_px, h_px, yaw_img, label_type, float(yaw_img))

            od_data = _get_object_data(frame_raw, oid)
            if od_data is None:
                continue

            if needs_pixel:
                cx_new, cy_new = _world_to_pixel(H_inv, res.x, res.y)
                J = _px_per_m_at(H_inv, res.x, res.y)
                along_world = np.array([math.cos(yaw_img), math.sin(yaw_img)])
                across_world = np.array([-math.sin(yaw_img), math.cos(yaw_img)])
                w_new = res.length * float(np.linalg.norm(J @ along_world))
                h_new = res.width * float(np.linalg.norm(J @ across_world))
                _update_rbbox(od_data, [cx_new, cy_new, w_new, h_new, yaw_img])

            if needs_geo:
                _add_cuboid(od_data, res.x, res.y, yaw_img, res.length, res.width, res.height)
                if output_coords == "geo":
                    _drop_rbbox(od_data)

            n_corrected += 1

    # Write output
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(ol, f, indent=2, ensure_ascii=False)

    print(f"Corrected {n_corrected} bboxes ({args.correction}/{output_coords}) → {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
