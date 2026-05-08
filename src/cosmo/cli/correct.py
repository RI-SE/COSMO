"""
cosmo.cli.correct

Standalone CLI for correcting OpenLABEL bboxes from oblique drone footage.

  cosmo correct input.json --output corrected.json \
    --georef-data georef.json --flight-record video_stats.json

  cosmo correct --input input.json --output corrected.json \
    --georef-data georef.json --flight-record video_stats.json
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
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
    ap.add_argument("input", nargs="?", help="Path to OpenLABEL JSON (positional alternative to --input/-i)")
    ap.add_argument("--input", "-i", dest="input_flag", help="Path to OpenLABEL JSON")
    ap.add_argument("--output", "-o", required=True, help="Output path for corrected OpenLABEL JSON")
    ap.add_argument("--georef-data", required=False, help="Path to ORBIT *_georef_data.json")
    ap.add_argument("--calibration", required=False, help="Path to legacy calibration JSON")
    ap.add_argument("--flight-record", required=True, metavar="PATH",
                    help="Path to FlightRecord_*.video_stats.json")
    ap.add_argument("--flight-record-sequence", type=int, default=0, metavar="N",
                    help="Sequence index within the flight record (default: 0)")
    ap.add_argument("--bbox-correction", choices=["analytical", "3d"], default="analytical",
                    help="Bbox correction mode (default: analytical)")
    ap.add_argument("--camera-model", default="mavic3pro-standard",
                    help="Camera model key (default: mavic3pro-standard)")
    ap.add_argument("--hfov-deg", type=float, default=None, metavar="FLOAT",
                    help="Override horizontal FOV in degrees")
    ap.add_argument(
        "--use-gps-cam-pos", action="store_true",
        help=(
            "Use GPS drone position as the camera world position instead of the H-derived position. "
            "GPS measures the drone body, not the gimbal optical centre, and may be 10–20 m off; "
            "H-derived is geometrically consistent with the calibrated homography (default)."
        ),
    )
    ap.add_argument(
        "--output-coords", choices=["pixel", "geo", "both"], default="pixel",
        help=(
            "Output coordinate format. "
            "'pixel': update rbbox with corrected pixel coords (default). "
            "'geo': replace rbbox with cuboid in world coords (proj_string from georef). "
            "'both': update rbbox AND add cuboid."
        ),
    )
    ap.add_argument(
        "--stabilize-size", action="store_true",
        help="Replace per-frame dimensions with per-object average; add size_mean/size_std/size_deviation fields.",
    )

    # Provenance
    prov = ap.add_argument_group("provenance")
    prov.add_argument("--prov-out", metavar="PATH", help="Write W3C-PROV provenance to this file (omit to skip)")
    prov.add_argument("--prov-in", metavar="PATH", help="Continue an existing upstream provenance chain (optional)")
    prov.add_argument("--flight-record-prov", metavar="PATH",
                      help="Provenance file for --flight-record input (will be inlined into output DPR)")
    prov.add_argument("--georef-prov", metavar="PATH",
                      help="Provenance file for --georef-data input (will be inlined into output DPR)")

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


def _record_provenance_correct(
    args, input_path: str, out_path: Path, start_time: datetime, end_time: datetime,
) -> None:
    """Record a dataprov provenance step for cosmo-correct."""
    try:
        from importlib.metadata import version as _pkg_version
        tool_version = _pkg_version("cosmo")
    except Exception:
        tool_version = "unknown"

    from dataprov import ProvenanceChain

    prov_in = args.prov_in
    if prov_in and Path(prov_in).exists():
        chain = ProvenanceChain.load(prov_in)
    else:
        chain = ProvenanceChain.create(
            entity_id=f"correct:{Path(input_path).stem}",
            initial_source=input_path,
            description=f"Oblique bbox correction of {Path(input_path).name}",
        )

    inputs = [input_path, args.flight_record]
    input_formats = ["json", "json"]
    # Index 0 (OpenLABEL): its provenance IS prov_in; index 1 (flight_record): inline if prov provided.
    flight_rec_prov = (args.flight_record_prov
                       if args.flight_record_prov and Path(args.flight_record_prov).exists() else None)
    input_prov_files: list[str | None] = [None, flight_rec_prov]

    if args.georef_data:
        inputs.append(args.georef_data)
        input_formats.append("json")
        georef_prov = (args.georef_prov
                       if args.georef_prov and Path(args.georef_prov).exists() else None)
        input_prov_files.append(georef_prov)
    if args.calibration:
        inputs.append(args.calibration)
        input_formats.append("json")
        input_prov_files.append(None)

    has_secondary_prov = any(p is not None for p in input_prov_files[1:])

    chain.add(
        tool_name="cosmo-correct",
        tool_version=tool_version,
        operation="correct",
        inputs=inputs,
        input_formats=input_formats,
        outputs=[str(out_path)],
        output_formats=["json"],
        arguments=" ".join(sys.argv),
        started_at=start_time.isoformat().replace("+00:00", "Z"),
        ended_at=end_time.isoformat().replace("+00:00", "Z"),
        input_provenance_files=input_prov_files if (prov_in or has_secondary_prov) else None,
        capture_agent=True,
        capture_environment=True,
    )
    chain.save(args.prov_out, input_prov="inline" if has_secondary_prov else "reference")
    print(f"Provenance: {args.prov_out}")


def main(argv=None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)

    if args.input and args.input_flag:
        ap.error("Provide either a positional input OR --input/-i, not both.")
    input_path_raw = args.input_flag or args.input
    if not input_path_raw:
        ap.error("Missing input. Provide OpenLABEL JSON as positional argument or via --input/-i.")
    try:
        input_path = _existing_file(input_path_raw)
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
    from cosmo.converters.openlabel_to_omega import (
        DEFAULT_DIMENSIONS_M,
        _h_rotation_angle,
        _resolve_height,
        angle_wrap,
        build_towed_by,
        load_alignment,
        parse_openlabel,
    )
    _, H, _ = load_alignment(args.calibration, args.georef_data, None)
    if H is None:
        ap.error("No homography found. Provide --georef-data or --calibration.")

    H_inv = np.linalg.inv(H)

    proj_string = _read_proj_string(args.georef_data)

    # Build camera + corrector
    from cosmo.corrections import BboxCorrector, load_camera_from_flight_record
    cam = load_camera_from_flight_record(
        args.flight_record, args.flight_record_sequence,
        args.camera_model, args.hfov_deg,
    )
    corrector = BboxCorrector(cam, H, mode=args.bbox_correction, proj_string=proj_string,
                              use_gps_cam_pos=args.use_gps_cam_pos)

    # For geo output: add coordinate_systems to root
    root = ol.get("openlabel", ol)
    if needs_geo:
        _ensure_coordinate_system(root, proj_string)
        if not proj_string:
            print("Warning: no proj_string found in georef; coordinate_system written without projection info.")

    # Parse to get object metadata + parsed frame data
    objects_meta, frames_parsed = parse_openlabel(ol)
    towed_by = build_towed_by(root, objects_meta)

    raw_frames = root.get("frames", {})
    frame_items = list(raw_frames.items()) if isinstance(raw_frames, dict) else list(enumerate(raw_frames))

    stabilize_size: bool = args.stabilize_size

    # Pre-pass: collect per-object dims for stabilize_size
    geo_sizes: dict[str, list] = {}
    px_sizes: dict[str, list] = {}
    correction_cache: dict[str, dict[str, object]] = {}
    n_frames = len(frame_items)
    if stabilize_size:
        for i, (fkey, _) in enumerate(frame_items):
            if i % 200 == 0:
                print(f"  Pre-pass {i}/{n_frames} frames...", flush=True)
            frame_id = str(fkey)
            parsed_frame = frames_parsed.get(frame_id, {})
            for oid, od in parsed_frame.get("objects", {}).items():
                cx, cy, w_px, h_px, yaw_img = od["rbbox"]
                meta = objects_meta.get(oid, {})
                label_type = meta.get("type", "other")
                h_veh = _resolve_height(label_type, oid, towed_by, DEFAULT_DIMENSIONS_M)
                heading_world = angle_wrap(-float(yaw_img) + _h_rotation_angle(H, cx, cy))
                res = corrector.correct(cx, cy, w_px, h_px, yaw_img, label_type, heading_world, h_veh_override=h_veh)
                correction_cache.setdefault(frame_id, {})[oid] = res
                geo_sizes.setdefault(oid, []).append((res.length, res.width, res.height))
                if needs_pixel:
                    J = _px_per_m_at(H_inv, res.x, res.y)
                    along = np.array([math.cos(yaw_img), math.sin(yaw_img)])
                    across = np.array([-math.sin(yaw_img), math.cos(yaw_img)])
                    px_sizes.setdefault(oid, []).append((
                        res.length * float(np.linalg.norm(J @ along)),
                        res.width * float(np.linalg.norm(J @ across)),
                    ))

        geo_mean = {oid: tuple(np.array(v).mean(axis=0).tolist()) for oid, v in geo_sizes.items()}
        geo_std = {oid: tuple(np.array(v).std(axis=0).tolist()) for oid, v in geo_sizes.items()}
        px_mean = {oid: tuple(np.array(v).mean(axis=0).tolist()) for oid, v in px_sizes.items()}
        px_std = {oid: tuple(np.array(v).std(axis=0).tolist()) for oid, v in px_sizes.items()}

    n_corrected = 0
    start_time = datetime.now(timezone.utc)
    for i, (fkey, fval) in enumerate(frame_items):
        if i % 200 == 0:
            print(f"  Processing {i}/{n_frames} frames...", flush=True)
        frame_id = str(fkey)
        parsed_frame = frames_parsed.get(frame_id, {})
        frame_raw = fval if isinstance(fval, dict) else raw_frames[fkey]

        for oid, od in parsed_frame.get("objects", {}).items():
            cx, cy, w_px, h_px, yaw_img = od["rbbox"]
            meta = objects_meta.get(oid, {})
            label_type = meta.get("type", "other")
            heading_world = angle_wrap(-float(yaw_img) + _h_rotation_angle(H, cx, cy))
            if stabilize_size:
                res = correction_cache[frame_id][oid]
            else:
                h_veh = _resolve_height(label_type, oid, towed_by, DEFAULT_DIMENSIONS_M)
                res = corrector.correct(cx, cy, w_px, h_px, yaw_img, label_type, heading_world, h_veh_override=h_veh)

            geo_L, geo_W, geo_H = res.length, res.width, res.height

            od_data = _get_object_data(frame_raw, oid)
            if od_data is None:
                continue

            if needs_pixel:
                cx_new, cy_new = _world_to_pixel(H_inv, res.x, res.y)
                J = _px_per_m_at(H_inv, res.x, res.y)
                along_world = np.array([math.cos(yaw_img), math.sin(yaw_img)])
                across_world = np.array([-math.sin(yaw_img), math.cos(yaw_img)])
                w_new = geo_L * float(np.linalg.norm(J @ along_world))
                h_new = geo_W * float(np.linalg.norm(J @ across_world))

                if stabilize_size:
                    avg_px = px_mean.get(oid, (w_new, h_new))
                    if output_coords in ("pixel", "both"):
                        dev_pw, dev_ph = w_new - avg_px[0], h_new - avg_px[1]
                    w_new, h_new = avg_px

                _update_rbbox(od_data, [cx_new, cy_new, w_new, h_new, yaw_img])

            if needs_geo:
                if stabilize_size:
                    avg_geo = geo_mean.get(oid, (geo_L, geo_W, geo_H))
                    if output_coords in ("geo", "both"):
                        dev_gL = geo_L - avg_geo[0]
                        dev_gW = geo_W - avg_geo[1]
                        dev_gH = geo_H - avg_geo[2]
                    geo_L, geo_W, geo_H = avg_geo
                _add_cuboid(od_data, res.x, res.y, heading_world, geo_L, geo_W, geo_H)
                if output_coords == "geo":
                    _drop_rbbox(od_data)

            if stabilize_size:
                vec_list = od_data.get("vec")
                if not isinstance(vec_list, list):
                    od_data["vec"] = []
                    vec_list = od_data["vec"]
                if output_coords == "pixel":
                    vec_list.append({"name": "size_deviation", "val": [dev_pw, dev_ph]})
                elif output_coords == "geo":
                    vec_list.append({"name": "size_deviation", "val": [dev_gL, dev_gW, dev_gH]})
                else:  # both
                    vec_list.append({"name": "size_deviation_pixel", "val": [dev_pw, dev_ph]})
                    vec_list.append({"name": "size_deviation_geo", "val": [dev_gL, dev_gW, dev_gH]})

            n_corrected += 1

    # Object-level mean/std entries
    if stabilize_size:
        for oid in geo_mean:
            obj_entry = root.get("objects", {}).get(oid)
            if obj_entry is None:
                continue
            obj_entry.setdefault("object_data", {})
            vec_list = obj_entry["object_data"].get("vec")
            if not isinstance(vec_list, list):
                obj_entry["object_data"]["vec"] = []
                vec_list = obj_entry["object_data"]["vec"]
            if output_coords == "pixel":
                vec_list.append({"name": "size_mean", "val": list(px_mean.get(oid, []))})
                vec_list.append({"name": "size_std", "val": list(px_std.get(oid, []))})
            elif output_coords == "geo":
                vec_list.append({"name": "size_mean", "val": list(geo_mean[oid])})
                vec_list.append({"name": "size_std", "val": list(geo_std[oid])})
            else:  # both
                vec_list.append({"name": "size_mean_pixel", "val": list(px_mean.get(oid, []))})
                vec_list.append({"name": "size_std_pixel", "val": list(px_std.get(oid, []))})
                vec_list.append({"name": "size_mean_geo", "val": list(geo_mean[oid])})
                vec_list.append({"name": "size_std_geo", "val": list(geo_std[oid])})

    # Write output
    end_time = datetime.now(timezone.utc)
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(ol, f, indent=2, ensure_ascii=False)

    print(f"Corrected {n_corrected} bboxes ({args.bbox_correction}/{output_coords}) → {out_path}")

    if args.prov_out:
        _record_provenance_correct(args, input_path, out_path, start_time, end_time)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
