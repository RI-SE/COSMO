"""Companion OpenLABEL of cosmo convert (scenario-uncertainty `world` profile, SPEC P5).

Carries what OSI GroundTruth cannot: run- and object-level quality, tags and contexts, and (variant B,
`frames=True`) per-frame world cuboids with their uncertainty. Objects keep the OpenLABEL uids, which are
the OSI MovingObject ids when numeric; frame_properties.timestamp is the OSI timestamp (join key).
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from typing import Any, Dict, List, Optional

import numpy as np
from scenario_uncertainty.core import Q

from cosmo.converters.quality import REVIEWED_ORIGINS, SYNTHESIZED_ORIGINS

PROFILE = "world"
MAJOR = 1
QUALITY_SPEC = "scenario-uncertainty/0.1"
COMPANION_SUFFIX = ".companion.json"
WORLD_CS = "world"
# Sections copied from the input as they are
COPIED_SECTIONS = ("tags", "contexts", "relations", "actions", "events", "streams", "ontologies")


def q(value, unit: str, stat: str, level: str, basis: str, **extra) -> Dict[str, Any]:
    return Q(value, unit, stat, level, basis, **extra).to_dict()


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _cov_list(cov: Optional[np.ndarray]) -> Optional[List[float]]:
    return None if cov is None else [round(float(v), 6) for v in np.asarray(cov).ravel()]


def object_stats(timeline: List[Dict[str, Any]], heights: Optional[Dict[str, Any]] = None) -> Dict[str, Dict[str, Any]]:
    """Per-object quality (SPEC P5): size mean/std over measured frames, path residual RMS, shares."""
    rows: Dict[str, List[Dict[str, Any]]] = {}
    for frame in timeline:
        for s in frame["samples"]:
            rows.setdefault(s["oid"], []).append(s)
    stats = {}
    for oid, samples in rows.items():
        measured = [s for s in samples if s.get("origin") not in SYNTHESIZED_ORIGINS] or samples
        lengths = np.array([s["length"] for s in measured])
        widths = np.array([s["width"] for s in measured])
        residuals = [np.hypot(s["X_raw"] - s["smoothed"][0][0], s["Y_raw"] - s["smoothed"][0][1])
                     for s in samples if s.get("smoothed") is not None]
        p_exist = [float(s["p_exist"]) for s in samples if s.get("p_exist") is not None]
        stats[oid] = {
            "n_frames": len(samples),
            "size_mean_l": float(lengths.mean()), "size_mean_w": float(widths.mean()),
            "size_std_l": float(lengths.std()), "size_std_w": float(widths.std()), "n_size": len(measured),
            "path_residual_rms": float(math.sqrt(np.mean(np.square(residuals)))) if residuals else None,
            "share_interpolated": sum(s.get("origin") in SYNTHESIZED_ORIGINS for s in samples) / len(samples),
            "p_exist_mean": float(np.mean(p_exist)) if p_exist else None,
            "h_eff": (heights or {}).get(oid, (None, None))[0],
            "h_eff_sigma": (heights or {}).get(oid, (None, None))[1],
        }
    return stats


def _object_num(stats: Dict[str, Any]) -> List[Dict[str, Any]]:
    keys = ("n_frames", "size_mean_l", "size_mean_w", "size_std_l", "size_std_w", "path_residual_rms",
            "share_interpolated", "p_exist_mean", "h_eff", "h_eff_sigma")
    return [{"name": k, "val": round(stats[k], 4) if isinstance(stats[k], float) else stats[k]}
            for k in keys if stats.get(k) is not None]


def _objects(root_in: Dict[str, Any], stats: Dict[str, Dict[str, Any]], osi: Dict[str, Dict[str, Any]]):
    objects = {}
    for oid, meta in (root_in.get("objects") or {}).items():
        obj = {"name": meta.get("name", oid), "type": meta.get("type", "other")}
        for key in ("ontology_uid", "frame_intervals"):
            if key in meta:
                obj[key] = meta[key]
        data = {}
        if oid in osi:
            data["text"] = [{"name": "osi_type", "val": osi[oid]["type_name"]},
                            {"name": "osi_subtype", "val": osi[oid]["subtype_name"]}]
        if oid in stats:
            data["num"] = _object_num(stats[oid])
        if data:
            obj["object_data"] = data
        objects[oid] = obj
    return objects


def _frame_entry(frame: Dict[str, Any]) -> Dict[str, Any]:
    """Variant B: world cuboid and uncertainty per object (SPEC P5 per-frame table)."""
    objects = {}
    for s in frame["samples"]:
        vec = [
            {"name": "velocity", "val": [round(v, 4) for v in s["vel"][:2]]},
            {"name": "acceleration", "val": [round(v, 4) for v in s["acc"][:2]]},
        ]
        if s.get("cov_rel") is not None:
            vec += [{"name": "position_cov_rel", "val": _cov_list(s["cov_rel"])},
                    {"name": "sigma_heading", "val": [round(s["sigma_heading"], 5)]},
                    {"name": "sigma_size", "val": [round(v, 4) for v in s["sigma_size"]]}]
        if s.get("smoothed_output"):
            cov = s["smoothed"][1]
            vec += [{"name": "velocity_cov", "val": _cov_list(cov[2:4, 2:4])},
                    {"name": "acceleration_cov", "val": _cov_list(cov[4:6, 4:6])}]
        if s.get("size_deviation") is not None:
            vec.append({"name": "size_deviation", "val": [round(v, 4) for v in s["size_deviation"]]})
        if s.get("oblique") and isinstance(s["oblique"], list):
            vec.append({"name": "oblique", "val": s["oblique"]})
        if s.get("origin"):
            vec.append({"name": "origin", "val": [s["origin"]]})
        if s.get("p_exist") is not None:
            vec.append({"name": "p_exist", "val": [s["p_exist"]]})
        cuboid = [s["X"], s["Y"], s["Z"], 0.0, 0.0, s["yaw"], s["length"], s["width"], s["height"]]
        objects[s["oid"]] = {"object_data": {
            "cuboid": [{"name": "shape", "val": [round(v, 4) for v in cuboid], "coordinate_system": WORLD_CS}],
            "vec": vec}}
    # OSI timestamp in seconds: the join key with the MCAP
    return {"objects": objects, "frame_properties": {"timestamp": round(frame["t_sec"], 9)}}


def run_quality(propagation_run: Dict[str, Any], timeline: List[Dict[str, Any]], inputs: Dict[str, Any],
                settings: Dict[str, Any]) -> Dict[str, Any]:
    """metadata.quality of the companion (SPEC P3.1 keys plus P5 run-level terms)."""
    origins = [s.get("origin") for f in timeline for s in f["samples"]]
    n = len(origins)

    def share(names):
        if not n:
            return q(None, "1", "ratio", "none", "unknown", n=0)
        return q(round(sum(o in names for o in origins) / n, 4), "1", "ratio", "none", "measured", n=n)
    datum = propagation_run.get("datum")
    image = propagation_run.get("image")
    basis = propagation_run.get("georef_basis") or {}
    return {
        "spec": QUALITY_SPEC,
        "datum": q(_cov_list(datum), "m2", "cov", "datum", basis.get("datum", "unknown")),
        "image": q(_cov_list(image), "m2", "cov", "image", basis.get("image", "unknown")),
        "inputs": inputs,
        "defaults_used": propagation_run.get("defaults_used", []),
        "share_interpolated": share(("interpolated", "manual_interpolated")),
        "share_reviewed": share(REVIEWED_ORIGINS),
        **settings,
    }


def build(root_in: Dict[str, Any], timeline: List[Dict[str, Any]], quality: Dict[str, Any],
          proj_string: Optional[str], frames: bool, tool: str,
          heights: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The companion document; quality = run_quality(...)."""
    osi = {s["oid"]: s for f in timeline for s in f["samples"]}
    metadata = {"schema_version": "1.1", "annotator": tool, "quality": quality}
    if (root_in.get("metadata") or {}).get("tagged_file"):
        metadata["tagged_file"] = root_in["metadata"]["tagged_file"]
    cs = {"type": "geo", "parent": ""}
    if proj_string:
        cs["proj_string"] = proj_string
    stats = object_stats(timeline, heights)
    out: Dict[str, Any] = {"metadata": metadata, "coordinate_systems": {WORLD_CS: cs},
                           "objects": _objects(root_in, stats, osi)}
    for key in COPIED_SECTIONS:
        if root_in.get(key):
            out[key] = root_in[key]
    if frames:
        for f in timeline:
            for s in f["samples"]:
                mean = stats[s["oid"]]
                s["size_deviation"] = [s["length"] - mean["size_mean_l"], s["width"] - mean["size_mean_w"]]
        out["frames"] = {str(f["frame_no"]): _frame_entry(f) for f in timeline}
    return {"openlabel": out}


def write(path: str, document: Dict[str, Any]) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(document, f, separators=(",", ":"), allow_nan=False)
    return path
