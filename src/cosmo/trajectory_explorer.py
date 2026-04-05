"""Standalone Qt viewer for OpenDRIVE road maps and object trajectories."""
from __future__ import annotations

import argparse
import json
import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

try:
    from PyQt5 import QtCore, QtGui, QtWidgets
except ImportError:
    try:
        from PySide6 import QtCore, QtGui, QtWidgets
    except ImportError:
        from PyQt6 import QtCore, QtGui, QtWidgets


# ---------------------------------------------------------------------------
# Qt compat helpers
# ---------------------------------------------------------------------------

def _scroll_hand_drag():
    if hasattr(QtWidgets.QGraphicsView, "DragMode"):
        return QtWidgets.QGraphicsView.DragMode.ScrollHandDrag
    return QtWidgets.QGraphicsView.ScrollHandDrag


def _anchor_under_mouse():
    if hasattr(QtWidgets.QGraphicsView, "ViewportAnchor"):
        return QtWidgets.QGraphicsView.ViewportAnchor.AnchorUnderMouse
    return QtWidgets.QGraphicsView.AnchorUnderMouse


def _anchor_center():
    if hasattr(QtWidgets.QGraphicsView, "ViewportAnchor"):
        return QtWidgets.QGraphicsView.ViewportAnchor.AnchorViewCenter
    return QtWidgets.QGraphicsView.AnchorViewCenter


def _keep_aspect_ratio():
    if hasattr(QtCore.Qt, "KeepAspectRatio"):
        return QtCore.Qt.KeepAspectRatio
    return QtCore.Qt.AspectRatioMode.KeepAspectRatio


def _dash_line():
    if hasattr(QtCore.Qt, "PenStyle"):
        return QtCore.Qt.PenStyle.DashLine
    return QtCore.Qt.DashLine


def _dash_dot_line():
    if hasattr(QtCore.Qt, "PenStyle"):
        return QtCore.Qt.PenStyle.DashDotLine
    return QtCore.Qt.DashDotLine


# Qt CheckState constants (compat)
_Qt = QtCore.Qt
_CHECKED = _Qt.CheckState.Checked if hasattr(_Qt, "CheckState") else _Qt.Checked
_UNCHECKED = _Qt.CheckState.Unchecked if hasattr(_Qt, "CheckState") else _Qt.Unchecked
_PARTIAL = _Qt.CheckState.PartiallyChecked if hasattr(_Qt, "CheckState") else _Qt.PartiallyChecked

_PALETTE_RGB = [
    (220,  60,  60),
    ( 60, 120, 220),
    ( 60, 190,  60),
    (220, 160,   0),
    (160,  60, 220),
    (  0, 190, 190),
    (220, 100,   0),
    (190, 190,  60),
    (220,  60, 160),
    (100, 160,  60),
    (  0, 160, 120),
    (200,  80, 120),
    ( 80, 200, 200),
    (140, 100,  40),
    ( 40, 140, 200),
    (200, 140,  80),
    (120,  40, 160),
    ( 40, 200,  80),
    (180, 180,  40),
    (100, 100, 200),
]

# Default bounding box dimensions (length_m, width_m, height_m) by type
_DEFAULT_DIMENSIONS_M: dict[str, tuple[float, float, float]] = {
    "VEHICLE": (4.5, 2.0, 1.5),
    "TRUCK": (8.0, 2.5, 3.0),
    "MOTORCYCLE": (2.2, 0.8, 1.2),
    "BICYCLE": (1.8, 0.6, 1.0),
    "PEDESTRIAN": (0.5, 0.5, 1.7),
}
_DEFAULT_DIM_FALLBACK = (2.0, 1.0, 1.5)


def _make_type_colors(pairs: list[tuple[str, str]]) -> dict[tuple[str, str], QtGui.QColor]:
    """Assign palette colors to (type_name, subtype_name) pairs."""
    return {
        pair: QtGui.QColor(*_PALETTE_RGB[i % len(_PALETTE_RGB)])
        for i, pair in enumerate(pairs)
    }


def _set_color_swatch(item: QtWidgets.QTreeWidgetItem, color: QtGui.QColor) -> None:
    pix = QtGui.QPixmap(12, 12)
    pix.fill(color)
    item.setIcon(0, QtGui.QIcon(pix))


def _update_parent_state(parent: QtWidgets.QTreeWidgetItem) -> None:
    n = parent.childCount()
    checked = sum(1 for i in range(n) if parent.child(i).checkState(0) == _CHECKED)
    if checked == 0:
        parent.setCheckState(0, _UNCHECKED)
    elif checked == n:
        parent.setCheckState(0, _CHECKED)
    else:
        parent.setCheckState(0, _PARTIAL)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class GeomSegment:
    s: float
    x: float
    y: float
    hdg: float
    length: float
    kind: str  # "line" or "paramPoly3"
    poly3_params: dict = field(default_factory=dict)


@dataclass
class LaneWidth:
    sOffset: float
    a: float
    b: float
    c: float
    d: float


@dataclass
class Lane:
    id: int
    widths: list[LaneWidth]


@dataclass
class LaneSection:
    s: float
    lanes: list[Lane]


@dataclass
class Road:
    id: str
    length: float
    geom_segments: list[GeomSegment]
    lane_sections: list[LaneSection]


@dataclass
class ParkingObject:
    road_id: str
    s: float
    t: float
    hdg: float
    width: float
    length: float
    outline_corners: list[tuple[float, float]] = field(default_factory=list)  # (u, v) local coords


# ---------------------------------------------------------------------------
# XODR parsing
# ---------------------------------------------------------------------------

def parse_xodr(path: str) -> tuple[list[Road], list[ParkingObject], tuple[float, float]]:
    return parse_xodr_text(open(path, encoding="utf-8").read())


def parse_xodr_text(text: str) -> tuple[list[Road], list[ParkingObject], tuple[float, float]]:
    text = re.sub(r'\s+xmlns="[^"]+"', "", text)
    root = ET.fromstring(text)

    offset_x, offset_y = 0.0, 0.0
    header = root.find("header")
    if header is not None:
        off = header.find("offset")
        if off is not None:
            offset_x = float(off.get("x", 0.0))
            offset_y = float(off.get("y", 0.0))

    roads: list[Road] = []
    parking: list[ParkingObject] = []

    for road_el in root.findall("road"):
        road_id = road_el.get("id", "")
        length = float(road_el.get("length", 0))

        geom_segments: list[GeomSegment] = []
        for g in road_el.findall("./planView/geometry"):
            s = float(g.get("s", 0))
            x = float(g.get("x", 0)) + offset_x
            y = float(g.get("y", 0)) + offset_y
            hdg = float(g.get("hdg", 0))
            seg_len = float(g.get("length", 0))
            children = list(g)
            kind = children[0].tag if children else "line"
            params = children[0].attrib if children else {}
            geom_segments.append(GeomSegment(s, x, y, hdg, seg_len, kind, params))

        lane_sections: list[LaneSection] = []
        for ls_el in road_el.findall("./lanes/laneSection"):
            ls_s = float(ls_el.get("s", 0))
            lanes: list[Lane] = []
            for side in ("left", "right"):
                side_el = ls_el.find(side)
                if side_el is None:
                    continue
                for lane_el in side_el.findall("lane"):
                    lid = int(lane_el.get("id", 0))
                    widths = []
                    for w_el in lane_el.findall("width"):
                        widths.append(LaneWidth(
                            sOffset=float(w_el.get("sOffset", 0)),
                            a=float(w_el.get("a", 0)),
                            b=float(w_el.get("b", 0)),
                            c=float(w_el.get("c", 0)),
                            d=float(w_el.get("d", 0)),
                        ))
                    lanes.append(Lane(id=lid, widths=widths))
            lane_sections.append(LaneSection(s=ls_s, lanes=lanes))

        for obj in road_el.findall("./objects/object"):
            if obj.get("type") == "parking":
                corners = [
                    (float(c.get("u", 0)), float(c.get("v", 0)))
                    for c in obj.findall("./outline/cornerLocal")
                ]
                parking.append(ParkingObject(
                    road_id=road_id,
                    s=float(obj.get("s", 0)),
                    t=float(obj.get("t", 0)),
                    hdg=float(obj.get("hdg", 0)),
                    width=float(obj.get("width", 2)),
                    length=float(obj.get("length", 5)),
                    outline_corners=corners,
                ))

        roads.append(Road(id=road_id, length=length,
                          geom_segments=geom_segments,
                          lane_sections=lane_sections))

    return roads, parking, (offset_x, offset_y)


# ---------------------------------------------------------------------------
# Geometry sampling
# ---------------------------------------------------------------------------

def _sample_road(road: Road) -> list[tuple[float, float, float, float]]:
    """Return [(s, x, y, hdg), ...] sampled along the road centerline."""
    n_total = max(20, int(road.length / 0.5))
    result: list[tuple[float, float, float, float]] = []

    for seg in road.geom_segments:
        if seg.length <= 0:
            continue
        n_seg = max(2, int(n_total * seg.length / road.length))
        x0, y0, hdg0 = seg.x, seg.y, seg.hdg

        for i in range(n_seg):
            t = seg.length * i / (n_seg - 1)

            if seg.kind == "paramPoly3":
                p = seg.poly3_params
                p_range = p.get("pRange", "normalized")
                p_max = 1.0 if p_range == "normalized" else seg.length
                p_val = p_max * t / seg.length
                aU, bU = float(p.get("aU", 0)), float(p.get("bU", 0))
                cU, dU = float(p.get("cU", 0)), float(p.get("dU", 0))
                aV, bV = float(p.get("aV", 0)), float(p.get("bV", 0))
                cV, dV = float(p.get("cV", 0)), float(p.get("dV", 0))
                u = aU + bU * p_val + cU * p_val**2 + dU * p_val**3
                v = aV + bV * p_val + cV * p_val**2 + dV * p_val**3
                x = x0 + u * math.cos(hdg0) - v * math.sin(hdg0)
                y = y0 + u * math.sin(hdg0) + v * math.cos(hdg0)
                du = bU + 2 * cU * p_val + 3 * dU * p_val**2
                dv = bV + 2 * cV * p_val + 3 * dV * p_val**2
                hdg = hdg0 + math.atan2(dv, du) if (du**2 + dv**2) > 1e-12 else hdg0
            else:  # line
                x = x0 + t * math.cos(hdg0)
                y = y0 + t * math.sin(hdg0)
                hdg = hdg0

            result.append((seg.s + t, x, y, hdg))

    return result


# ---------------------------------------------------------------------------
# Lane polygon building
# ---------------------------------------------------------------------------

def _lane_width_at(lane: Lane, s_rel: float) -> float:
    """Evaluate lane width polynomial at s_rel from lane section start."""
    best_w = LaneWidth(0, 0, 0, 0, 0)
    for w in lane.widths:
        if w.sOffset <= s_rel:
            best_w = w
    ds = s_rel - best_w.sOffset
    return best_w.a + best_w.b * ds + best_w.c * ds**2 + best_w.d * ds**3


def build_lane_polygons(road: Road) -> list[tuple[int, list[tuple[float, float]]]]:
    """Return list of (lane_id, [(x,y), ...]) closed polygons."""
    samples = _sample_road(road)
    if not samples:
        return []

    polygons: list[tuple[int, list[tuple[float, float]]]] = []

    for ls_idx, ls in enumerate(road.lane_sections):
        s_start = ls.s
        s_end = road.lane_sections[ls_idx + 1].s if ls_idx + 1 < len(road.lane_sections) else road.length

        sec_samples = [(s, x, y, h) for s, x, y, h in samples if s_start <= s <= s_end + 0.01]
        if not sec_samples:
            continue

        for lane in ls.lanes:
            if lane.id == 0:
                continue
            is_right = lane.id < 0
            perp_sign = -1 if is_right else 1

            same_side = [ln for ln in ls.lanes if (ln.id < 0) == is_right and ln.id != 0]
            same_side.sort(key=lambda ln: abs(ln.id))
            this_rank = abs(lane.id)
            inner_lanes = [ln for ln in same_side if abs(ln.id) < this_rank]

            inner_pts: list[tuple[float, float]] = []
            outer_pts: list[tuple[float, float]] = []

            for s, x, y, hdg in sec_samples:
                s_rel = s - s_start
                perp_dir = hdg + perp_sign * math.pi / 2
                d_inner = sum(_lane_width_at(ln, s_rel) for ln in inner_lanes)
                d_outer = d_inner + _lane_width_at(lane, s_rel)
                xi = x + d_inner * math.cos(perp_dir)
                yi = y + d_inner * math.sin(perp_dir)
                xo = x + d_outer * math.cos(perp_dir)
                yo = y + d_outer * math.sin(perp_dir)
                inner_pts.append((xi, yi))
                outer_pts.append((xo, yo))

            poly = inner_pts + list(reversed(outer_pts))
            polygons.append((lane.id, poly))

    return polygons


# ---------------------------------------------------------------------------
# Parking rectangle
# ---------------------------------------------------------------------------

def build_parking_rect(obj: ParkingObject, road: Road) -> list[tuple[float, float]]:
    samples = _sample_road(road)
    if not samples:
        return []

    s_arr = np.array([s for s, *_ in samples])
    idx = int(np.searchsorted(s_arr, obj.s))
    idx = min(max(idx, 0), len(samples) - 1)
    _, rx, ry, rhdg = samples[idx]

    perp = rhdg + math.pi / 2
    cx = rx + obj.t * math.cos(perp)
    cy = ry + obj.t * math.sin(perp)

    obj_hdg = rhdg + obj.hdg
    if obj.outline_corners:
        corners_local = obj.outline_corners
    else:
        hl, hw = obj.length / 2, obj.width / 2
        corners_local = [(-hl, -hw), (hl, -hw), (hl, hw), (-hl, hw)]
    result = []
    for u, v in corners_local:
        wx = cx + u * math.cos(obj_hdg) - v * math.sin(obj_hdg)
        wy = cy + u * math.sin(obj_hdg) + v * math.cos(obj_hdg)
        result.append((wx, wy))
    return result


# ---------------------------------------------------------------------------
# CSV loading
# ---------------------------------------------------------------------------

def load_trajectories(path: str) -> dict[int, dict]:
    """Sparse loader — kept for MCAP compatibility."""
    df = pd.read_csv(path)
    df = df.sort_values("total_nanos")
    result = {}
    for idx, group in df.groupby("idx"):
        xy = group[["x", "y"]].values
        type_name = group["type_name"].iloc[0] if "type_name" in group.columns else "OTHER"
        subtype_name = group["subtype_name"].iloc[0] if "subtype_name" in group.columns else "OTHER"
        result[int(idx)] = {"type": str(type_name), "subtype": str(subtype_name), "xy": xy}
    return result


def load_trajectories_full(path: str) -> dict[int, dict]:
    """Full per-frame loader with nanos index and all columns for playback."""
    df = pd.read_csv(path)
    df = df.sort_values("total_nanos")
    result = {}
    for idx, group in df.groupby("idx"):
        tname = str(group["type_name"].iloc[0]) if "type_name" in group.columns else "OTHER"
        sname = str(group["subtype_name"].iloc[0]) if "subtype_name" in group.columns else "OTHER"
        xy = group[["x", "y"]].values
        nanos = group["total_nanos"].values.astype(np.int64)
        frame_df = group.set_index("total_nanos")
        result[int(idx)] = {
            "type": tname,
            "subtype": sname,
            "xy": xy,
            "nanos": nanos,
            "nanos_set": set(nanos.tolist()),
            "df": frame_df,
        }
    return result


def load_from_mcap(path: str) -> tuple[str | None, dict[int, dict]]:
    """Read MCAP and return (xodr_xml_or_none, trajectories dict).

    Requires betterosi. Topics read: ground_truth_map + ground_truth.
    """
    try:
        import betterosi
    except ImportError:
        raise ImportError("betterosi is required for MCAP loading: uv pip install betterosi")

    xodr_xml: str | None = None
    for msg in betterosi.read(path, mcap_topics=["ground_truth_map"]):
        xodr_xml = getattr(msg, "open_drive_xml_content", None) or getattr(msg, "content", None)
        if xodr_xml:
            break

    obj_data: dict[int, dict] = {}
    for gt in betterosi.read(path, return_ground_truth=True, mcap_topics=["ground_truth"]):
        for mo in gt.moving_object:
            oid = int(mo.id.value)
            x, y = float(mo.base.position.x), float(mo.base.position.y)
            if oid not in obj_data:
                raw_type = mo.type
                type_name = raw_type.name if hasattr(raw_type, "name") else str(raw_type)
                vc = getattr(mo, "vehicle_classification", None)
                subtype_name = "OTHER"
                if vc is not None:
                    vt = getattr(vc, "type", None)
                    if vt is not None:
                        vt_name = vt.name if hasattr(vt, "name") else str(vt)
                        if vt_name not in ("UNKNOWN", "OTHER", "0"):
                            subtype_name = vt_name
                obj_data[oid] = {"type": type_name, "subtype": subtype_name, "points": []}
            obj_data[oid]["points"].append((x, y))

    trajectories = {
        oid: {"type": d["type"], "subtype": d["subtype"], "xy": np.array(d["points"])}
        for oid, d in obj_data.items()
        if len(d["points"]) >= 2
    }
    return xodr_xml, trajectories


_OL_TYPE_MAP: dict[str, tuple[str, str]] = {
    "car": ("VEHICLE", "CAR"), "van": ("VEHICLE", "VAN"),
    "taxi": ("VEHICLE", "CAR"), "automobile": ("VEHICLE", "CAR"),
    "truck": ("VEHICLE", "TRUCK"), "bus": ("VEHICLE", "BUS"),
    "railvehicle": ("VEHICLE", "RAILVEHICLE"), "tram": ("VEHICLE", "RAILVEHICLE"),
    "train": ("VEHICLE", "RAILVEHICLE"), "bicycle": ("VEHICLE", "BICYCLE"),
    "cyclist": ("VEHICLE", "BICYCLE"), "motorcycle": ("VEHICLE", "MOTORCYCLE"),
    "trailer": ("VEHICLE", "TRAILER"), "tractor": ("VEHICLE", "TRACTOR"),
    "pedestrian": ("PEDESTRIAN", "OTHER"), "human": ("PEDESTRIAN", "OTHER"),
    "animal": ("ANIMAL", "OTHER"),
    "unknown": ("UNKNOWN", "OTHER"),
}


def _classify_openlabel_type(label_type: str) -> tuple[str, str]:
    """Map raw OpenLabel type string to (category, subtype) matching CSV/MCAP hierarchy."""
    return _OL_TYPE_MAP.get(label_type.strip().lower(), ("OTHER", "OTHER"))


class NeedsFpsError(Exception):
    """Raised when OpenLabel has no timestamps and fps is needed."""


def _parse_timestamp_nanos(ts: str) -> int:
    """Parse "HH:MM:SS.ffffff" to integer nanoseconds."""
    h, m, rest = ts.split(":")
    if "." in rest:
        s, frac = rest.split(".")
    else:
        s, frac = rest, "0"
    us = int(frac.ljust(6, "0")[:6])
    return (int(h) * 3600 + int(m) * 60 + int(s)) * 1_000_000_000 + us * 1000


def _vec_named(od_data: dict, *names: str) -> list[float] | None:
    """Return first matching named vec entry (>=3 elements) from object_data dict."""
    for entry in od_data.get("vec", []) or []:
        if isinstance(entry, dict) and entry.get("name") in names:
            v = entry.get("val")
            if isinstance(v, list) and len(v) >= 3:
                return [float(x) for x in v[:3]]
    return None


def load_from_openlabel(path: str, fps: float | None = None) -> dict[int, dict]:
    """Load corrected OpenLABEL (cuboid required). Returns same format as load_trajectories_full."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    root = data.get("openlabel", data)

    obj_meta: dict[str, tuple[str, str]] = {}
    for oid_str, obj in root.get("objects", {}).items():
        obj_meta[oid_str] = _classify_openlabel_type(obj.get("type", ""))

    frames_dict = root.get("frames", {})

    has_timestamps = any(
        fd.get("frame_properties", {}).get("timestamp")
        for fd in frames_dict.values()
    )
    if not has_timestamps and fps is None:
        raise NeedsFpsError("No timestamps found in OpenLabel; fps required")

    obj_frames: dict[str, list[dict]] = {}
    has_cuboid_overall = False

    for frame_key in sorted(frames_dict.keys(), key=lambda k: int(k)):
        frame_idx = int(frame_key)
        fdata = frames_dict[frame_key]
        fp = fdata.get("frame_properties", {})
        ts = fp.get("timestamp")
        nano = _parse_timestamp_nanos(ts) if ts else int(round(frame_idx * 1e9 / fps))

        for oid_str, odata in fdata.get("objects", {}).items():
            cuboids = odata.get("object_data", {}).get("cuboid", [])
            if not cuboids:
                continue
            has_cuboid_overall = True
            val = cuboids[0]["val"]  # [X, Y, Z, rx, ry, rz, L, W, H]
            dev = _vec_named(odata.get("object_data", {}), "size_deviation_geo", "size_deviation")
            obj_frames.setdefault(oid_str, []).append({
                "total_nanos": nano,
                "x": float(val[0]), "y": float(val[1]),
                "length": float(val[6]), "width": float(val[7]),
                "height": float(val[8]), "yaw": float(val[5]),
                "dev_l": dev[0] if dev else float("nan"),
                "dev_w": dev[1] if dev else float("nan"),
                "dev_h": dev[2] if dev else float("nan"),
            })

    if not has_cuboid_overall:
        raise ValueError(
            "pixel-only OpenLabel not supported; re-run cosmo correct with "
            "--output-coords geo or both"
        )

    result: dict[int, dict] = {}
    for oid_str, frame_rows in obj_frames.items():
        oid = int(oid_str)
        tname, sname = obj_meta.get(oid_str, ("OTHER", "OTHER"))
        frame_df = pd.DataFrame(frame_rows).set_index("total_nanos")
        xy = frame_df[["x", "y"]].values
        nanos = frame_df.index.values.astype(np.int64)
        size_std = _vec_named(
            root.get("objects", {}).get(oid_str, {}).get("object_data", {}),
            "size_std_geo", "size_std",
        )
        result[oid] = {
            "type": tname,
            "subtype": sname,
            "xy": xy,
            "nanos": nanos,
            "nanos_set": set(nanos.tolist()),
            "df": frame_df,
            "size_std": size_std,
        }

    return result


# ---------------------------------------------------------------------------
# Scene building helpers
# ---------------------------------------------------------------------------

def _qpoly(pts: list[tuple[float, float]]) -> QtGui.QPolygonF:
    return QtGui.QPolygonF([QtCore.QPointF(x, -y) for x, y in pts])


def _obj_rect_polygon(
    x: float, y: float, length: float, width: float, yaw: float
) -> QtGui.QPolygonF:
    """Build a rotated rectangle polygon in scene coords (y-flipped)."""
    hl, hw = length / 2, width / 2
    cos_y, sin_y = math.cos(yaw), math.sin(yaw)
    pts = []
    for u, v in [(-hl, -hw), (hl, -hw), (hl, hw), (-hl, hw)]:
        wx = x + u * cos_y - v * sin_y
        wy = y + u * sin_y + v * cos_y
        pts.append(QtCore.QPointF(wx, -wy))
    return QtGui.QPolygonF(pts)


def _add_traj_paths(
    scene: QtWidgets.QGraphicsScene,
    trajectories: dict[int, dict],
    obj_colors: dict[int, QtGui.QColor],
    path_alpha: int = 255,
    pen_style=None,
) -> dict[int, QtWidgets.QGraphicsPathItem]:
    """Add trajectory path items to scene; return {obj_id: item}."""
    items: dict[int, QtWidgets.QGraphicsPathItem] = {}
    for obj_id, obj_info in trajectories.items():
        xy = obj_info["xy"]
        if len(xy) < 2:
            continue
        color = QtGui.QColor(obj_colors.get(obj_id, QtGui.QColor(220, 0, 0)))
        color.setAlpha(path_alpha)
        pen = QtGui.QPen(color)
        pen.setWidthF(0.3)
        if pen_style is not None:
            pen.setStyle(pen_style)
        path = QtGui.QPainterPath()
        path.moveTo(float(xy[0, 0]), -float(xy[0, 1]))
        for px, py in xy[1:]:
            path.lineTo(float(px), -float(py))
        items[obj_id] = scene.addPath(path, pen)
    return items


def build_scene(
    scene: QtWidgets.QGraphicsScene,
    roads: list[Road],
    parking: list[ParkingObject],
    road_map: dict[str, Road],
    trajectories: dict[int, dict],
    obj_colors: dict[int, QtGui.QColor],
    path_alpha: int = 255,
) -> dict[int, QtWidgets.QGraphicsPathItem]:
    no_pen = QtGui.QPen(QtCore.Qt.NoPen if hasattr(QtCore.Qt, "NoPen") else QtCore.Qt.PenStyle.NoPen)

    # 1. Lane polygons
    for road in roads:
        for lane_id, poly in build_lane_polygons(road):
            if lane_id < 0:
                brush = QtGui.QBrush(QtGui.QColor(0, 180, 0, 80))
            else:
                brush = QtGui.QBrush(QtGui.QColor(0, 0, 200, 80))
            scene.addPolygon(_qpoly(poly), no_pen, brush)

    # 2. Centerlines
    center_pen = QtGui.QPen(QtGui.QColor(0, 0, 0))
    center_pen.setWidthF(0.15)
    for road in roads:
        samples = _sample_road(road)
        if len(samples) < 2:
            continue
        path = QtGui.QPainterPath()
        path.moveTo(samples[0][1], -samples[0][2])
        for _, x, y, _ in samples[1:]:
            path.lineTo(x, -y)
        scene.addPath(path, center_pen)

    # 3. Parking
    park_brush = QtGui.QBrush(QtGui.QColor(128, 128, 128, 100))
    park_pen = QtGui.QPen(QtGui.QColor(64, 64, 64))
    park_pen.setWidthF(0.1)
    for obj in parking:
        road = road_map.get(obj.road_id)
        if road is None:
            continue
        rect_pts = build_parking_rect(obj, road)
        if rect_pts:
            scene.addPolygon(_qpoly(rect_pts), park_pen, park_brush)

    # 4. Trajectory paths (A source)
    return _add_traj_paths(scene, trajectories, obj_colors, path_alpha=path_alpha)


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class TrajectoryExplorer(QtWidgets.QMainWindow):
    """Interactive viewer for OpenDRIVE maps and CSV trajectories."""

    def __init__(self, xodr_path: str | None = None, slot_a: str | None = None,
                 slot_b: str | None = None, slot_c: str | None = None,
                 smooth_window: int = 5, smooth_sigma: float | None = None):
        super().__init__()
        self._xodr_path = xodr_path or ""
        self._slot_path_a: str | None = slot_a or None
        self._slot_path_b: str | None = slot_b or None
        self._slot_path_c: str | None = slot_c or None
        self._smooth_window = smooth_window
        self._smooth_sigma = smooth_sigma if smooth_sigma is not None else smooth_window / 2
        self.setWindowTitle("Trajectory Explorer")
        self.resize(1400, 900)

        self._trajs_a: dict[int, dict] = {}
        self._trajs_b: dict[int, dict] = {}
        self._trajs_c: dict[int, dict] = {}
        self._all_nanos: list[int] = []
        self._frame_idx: int = 0
        self._fps: int = 30
        self._obj_colors: dict[int, QtGui.QColor] = {}
        self._checked_oids: set[int] = set()

        self._traj_items_a: dict[int, QtWidgets.QGraphicsPathItem] = {}
        self._traj_items_b: dict[int, QtWidgets.QGraphicsPathItem] = {}
        self._traj_items_c: dict[int, QtWidgets.QGraphicsPathItem] = {}
        self._rect_items_a: dict[int, QtWidgets.QGraphicsPolygonItem] = {}
        self._rect_items_b: dict[int, QtWidgets.QGraphicsPolygonItem] = {}
        self._rect_items_c: dict[int, QtWidgets.QGraphicsPolygonItem] = {}

        self._type_subtype_objs: dict[str, dict[str, list[int]]] = {}
        self._frameless_oids: set[int] = set()
        self._speed_kmh: bool = False

        self._play_timer = QtCore.QTimer(self)
        self._play_timer.setInterval(1000 // self._fps)
        self._play_timer.timeout.connect(self._on_timer_tick)

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(4)

        root.addLayout(self._build_toolbar())
        root.addWidget(self._build_main_splitter(), 1)

        self._connect_signals()

        if any([self._slot_path_a, self._slot_path_b, self._slot_path_c, self._xodr_path]):
            self._reload()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_toolbar(self) -> QtWidgets.QHBoxLayout:
        tb = QtWidgets.QHBoxLayout()

        self.btn_xodr = QtWidgets.QPushButton("Open XODR")
        self.lbl_xodr = QtWidgets.QLabel(self._xodr_path or "—")
        self.lbl_xodr.setStyleSheet("color:#6b7280; max-width:200px;")

        self.btn_a = QtWidgets.QPushButton("Open A")
        self.lbl_a = QtWidgets.QLabel(self._slot_path_a or "—")
        self.lbl_a.setStyleSheet("color:#6b7280; max-width:200px;")
        self.btn_clear_a = QtWidgets.QPushButton("Clear A")

        self.btn_b = QtWidgets.QPushButton("Open B")
        self.lbl_b = QtWidgets.QLabel(self._slot_path_b or "—")
        self.lbl_b.setStyleSheet("color:#6b7280; max-width:200px;")
        self.btn_clear_b = QtWidgets.QPushButton("Clear B")

        self.btn_c = QtWidgets.QPushButton("Open C")
        self.lbl_c = QtWidgets.QLabel(self._slot_path_c or "—")
        self.lbl_c.setStyleSheet("color:#6b7280; max-width:200px;")
        self.btn_clear_c = QtWidgets.QPushButton("Clear C")

        self.btn_fit = QtWidgets.QPushButton("Fit View")

        for w in (
            self.btn_xodr, self.lbl_xodr,
            self.btn_a, self.lbl_a, self.btn_clear_a,
            self.btn_b, self.lbl_b, self.btn_clear_b,
            self.btn_c, self.lbl_c, self.btn_clear_c,
            self.btn_fit,
        ):
            tb.addWidget(w)
        tb.addStretch(1)
        return tb

    def _build_main_splitter(self) -> QtWidgets.QSplitter:
        h_orient = (QtCore.Qt.Orientation.Horizontal if hasattr(QtCore.Qt, "Orientation")
                    else QtCore.Qt.Horizontal)
        v_orient = (QtCore.Qt.Orientation.Vertical if hasattr(QtCore.Qt, "Orientation")
                    else QtCore.Qt.Vertical)

        splitter_main = QtWidgets.QSplitter(h_orient)

        # Left: view above data table
        splitter_left = QtWidgets.QSplitter(v_orient)

        self.scene = QtWidgets.QGraphicsScene()
        self.view = QtWidgets.QGraphicsView(self.scene)
        self.view.setDragMode(_scroll_hand_drag())
        try:
            self.view.setTransformationAnchor(_anchor_under_mouse())
            self.view.setResizeAnchor(_anchor_center())
        except Exception:
            pass
        self.view.setRenderHint(
            QtGui.QPainter.Antialiasing if hasattr(QtGui.QPainter, "Antialiasing")
            else QtGui.QPainter.RenderHint.Antialiasing
        )
        splitter_left.addWidget(self.view)

        self._data_table = QtWidgets.QTableWidget()
        self._data_table.setColumnCount(12)
        self._data_table.setHorizontalHeaderLabels(
            ["Src", "ID", "X", "Y", "Speed m/s", "Speed-s m/s", "Yaw°", "L×W×H", "ΔL×W×H", "Type", "Subtype", "Role"]
        )
        self._data_table.setMinimumHeight(120)
        self._data_table.setEditTriggers(
            QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers
            if hasattr(QtWidgets.QAbstractItemView, "EditTrigger")
            else QtWidgets.QAbstractItemView.NoEditTriggers
        )
        self._data_table.horizontalHeader().setStretchLastSection(True)
        splitter_left.addWidget(self._data_table)
        splitter_left.setSizes([700, 200])

        splitter_main.addWidget(splitter_left)

        # Right: tree + playback
        right = QtWidgets.QWidget()
        right.setMinimumWidth(200)
        rl = QtWidgets.QVBoxLayout(right)
        rl.setContentsMargins(4, 4, 4, 4)
        rl.setSpacing(4)

        btn_row = QtWidgets.QHBoxLayout()
        self._btn_select_all = QtWidgets.QPushButton("Select All")
        self._btn_deselect_all = QtWidgets.QPushButton("Deselect All")
        btn_row.addWidget(self._btn_select_all)
        btn_row.addWidget(self._btn_deselect_all)
        rl.addLayout(btn_row)

        sep = QtWidgets.QFrame()
        sep.setFrameShape(
            QtWidgets.QFrame.Shape.HLine if hasattr(QtWidgets.QFrame, "Shape")
            else QtWidgets.QFrame.HLine
        )
        rl.addWidget(sep)

        self._tree = QtWidgets.QTreeWidget()
        self._tree.setHeaderHidden(True)
        rl.addWidget(self._tree, 1)

        rl.addWidget(self._build_playback_widget())

        splitter_main.addWidget(right)
        splitter_main.setSizes([1140, 260])
        return splitter_main

    def _build_playback_widget(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        w.setFixedHeight(90)
        vl = QtWidgets.QVBoxLayout(w)
        vl.setContentsMargins(0, 4, 0, 0)
        vl.setSpacing(4)

        btn_row = QtWidgets.QHBoxLayout()
        self._btn_step_back = QtWidgets.QPushButton("◀◀")
        self._btn_play = QtWidgets.QPushButton("▶")
        self._btn_step_fwd = QtWidgets.QPushButton("▶▶")
        for b in (self._btn_step_back, self._btn_play, self._btn_step_fwd):
            b.setFixedWidth(44)
            btn_row.addWidget(b)
        self._btn_speed_unit = QtWidgets.QPushButton("m/s")
        self._btn_speed_unit.setFixedWidth(44)
        self._btn_speed_unit.setCheckable(True)
        btn_row.addWidget(self._btn_speed_unit)
        btn_row.addStretch(1)
        vl.addLayout(btn_row)

        self._slider = QtWidgets.QSlider(
            QtCore.Qt.Orientation.Horizontal if hasattr(QtCore.Qt, "Orientation")
            else QtCore.Qt.Horizontal
        )
        self._slider.setRange(0, 0)
        vl.addWidget(self._slider)

        self._lbl_frame = QtWidgets.QLabel("frame 0/0  t=0.0s")
        self._lbl_frame.setStyleSheet("color:#6b7280; font-size:11px;")
        vl.addWidget(self._lbl_frame)

        return w

    def _connect_signals(self) -> None:
        self.btn_xodr.clicked.connect(self._open_xodr)
        self.btn_a.clicked.connect(lambda: self._open_slot("a"))
        self.btn_b.clicked.connect(lambda: self._open_slot("b"))
        self.btn_c.clicked.connect(lambda: self._open_slot("c"))
        self.btn_clear_a.clicked.connect(lambda: self._clear_slot("a"))
        self.btn_clear_b.clicked.connect(lambda: self._clear_slot("b"))
        self.btn_clear_c.clicked.connect(lambda: self._clear_slot("c"))
        self.btn_fit.clicked.connect(self._fit)
        self.view.wheelEvent = self._wheel_zoom  # type: ignore[assignment]
        self._btn_select_all.clicked.connect(lambda: self._set_all(_CHECKED))
        self._btn_deselect_all.clicked.connect(lambda: self._set_all(_UNCHECKED))
        self._tree.itemChanged.connect(self._on_item_changed)
        self._btn_play.clicked.connect(self._on_play_pause)
        self._btn_step_back.clicked.connect(lambda: self._on_step(-1))
        self._btn_step_fwd.clicked.connect(lambda: self._on_step(1))
        self._slider.sliderMoved.connect(self._on_slider_moved)
        self._btn_speed_unit.clicked.connect(self._toggle_speed_unit)
        self._tree.itemDoubleClicked.connect(self._on_tree_item_double_clicked)

    # ------------------------------------------------------------------
    # File open / clear
    # ------------------------------------------------------------------

    def _open_xodr(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Open XODR", "", "OpenDRIVE (*.xodr);;All (*)")
        if path:
            self._xodr_path = path
            self.lbl_xodr.setText(path)
            self._reload()

    def _open_slot(self, slot: str) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, f"Open {slot.upper()}", "",
            "Trajectory file (*.csv *.mcap *.json);;All (*)",
        )
        if path:
            setattr(self, f"_slot_path_{slot}", path)
            getattr(self, f"lbl_{slot}").setText(path)
            self._reload()

    def _clear_slot(self, slot: str) -> None:
        setattr(self, f"_slot_path_{slot}", None)
        getattr(self, f"lbl_{slot}").setText("—")
        self._reload()

    def _load_slot(self, path: str) -> tuple[str | None, dict[int, dict]]:
        """Detect format by extension and load trajectories."""
        ext = Path(path).suffix.lower()
        if ext == ".csv":
            return None, load_trajectories_full(path)
        elif ext == ".mcap":
            return load_from_mcap(path)
        elif ext == ".json":
            try:
                return None, load_from_openlabel(path)
            except NeedsFpsError:
                fps, ok = QtWidgets.QInputDialog.getDouble(
                    self, "OpenLabel FPS", "Frames per second:", 30.0, 0.1, 1000.0, 2
                )
                if not ok:
                    return None, {}
                return None, load_from_openlabel(path, fps=fps)
        else:
            raise ValueError(f"Unsupported file type: {path}")

    def _fit(self):
        self.view.fitInView(self.scene.sceneRect(), _keep_aspect_ratio())

    def _wheel_zoom(self, event):
        try:
            delta = event.angleDelta().y()
        except Exception:
            delta = 0
        factor = 1.15 if delta > 0 else 1 / 1.15
        self.view.scale(factor, factor)

    # ------------------------------------------------------------------
    # Reload
    # ------------------------------------------------------------------

    def _reload(self):
        self._play_timer.stop()
        self._btn_play.setText("▶")
        self.scene.clear()
        self._traj_items_a = {}
        self._traj_items_b = {}
        self._traj_items_c = {}
        self._rect_items_a = {}
        self._rect_items_b = {}
        self._rect_items_c = {}
        self._all_nanos = []
        self._frame_idx = 0
        self._slider.setRange(0, 0)
        self._lbl_frame.setText("frame 0/0  t=0.0s")
        self._data_table.setRowCount(0)

        xodr_xml_a = xodr_xml_b = xodr_xml_c = None
        self._trajs_a, self._trajs_b, self._trajs_c = {}, {}, {}

        for attr, path in [
            ("_trajs_a", self._slot_path_a),
            ("_trajs_b", self._slot_path_b),
            ("_trajs_c", self._slot_path_c),
        ]:
            if not path:
                continue
            try:
                xodr_xml, trajs = self._load_slot(path)
                setattr(self, attr, trajs)
                if attr == "_trajs_a":
                    xodr_xml_a = xodr_xml
                elif attr == "_trajs_b":
                    xodr_xml_b = xodr_xml
                else:
                    xodr_xml_c = xodr_xml
            except Exception as exc:
                print(f"Warning: could not load {path}: {exc}")

        roads, parking = [], []
        if self._xodr_path:
            try:
                roads, parking, _ = parse_xodr(self._xodr_path)
            except Exception as exc:
                self.scene.addText(f"Error loading XODR: {exc}")
                return
        else:
            for xodr_xml in [xodr_xml_a, xodr_xml_b, xodr_xml_c]:
                if xodr_xml:
                    try:
                        roads, parking, _ = parse_xodr_text(xodr_xml)
                    except Exception as exc:
                        print(f"Warning: could not parse embedded XODR: {exc}")
                    break

        self._rebuild_ui_and_scene(roads, parking)

    def _rebuild_ui_and_scene(self, roads, parking) -> None:
        all_ids = set(self._trajs_a) | set(self._trajs_b) | set(self._trajs_c)
        self._obj_colors = _build_obj_colors(all_ids)
        self._build_type_subtype_objs()
        self._frameless_oids = {
            oid for oid in all_ids
            if not self._trajs_a.get(oid, {}).get("nanos_set")
            and not self._trajs_b.get(oid, {}).get("nanos_set")
            and not self._trajs_c.get(oid, {}).get("nanos_set")
        }

        nanos_set: set[int] = set()
        for trajs in (self._trajs_a, self._trajs_b, self._trajs_c):
            for obj in trajs.values():
                if obj.get("nanos_set"):
                    nanos_set.update(obj["nanos_set"])
        self._all_nanos = _build_full_timeline(nanos_set)

        path_alpha = 80 if self._all_nanos else 255
        road_map = {r.id: r for r in roads}

        self._traj_items_a = build_scene(
            self.scene, roads, parking, road_map,
            self._trajs_a, self._obj_colors, path_alpha=path_alpha,
        )
        if self._trajs_b:
            self._traj_items_b = _add_traj_paths(
                self.scene, self._trajs_b, self._obj_colors,
                path_alpha=path_alpha, pen_style=_dash_line(),
            )
        if self._trajs_c:
            self._traj_items_c = _add_traj_paths(
                self.scene, self._trajs_c, self._obj_colors,
                path_alpha=path_alpha, pen_style=_dash_dot_line(),
            )

        for trajs in (self._trajs_a, self._trajs_b, self._trajs_c):
            if trajs:
                self._compute_smooth_speed(trajs)

        self._build_playback_items()
        self._build_tree()

        if self._all_nanos:
            self._slider.setRange(0, len(self._all_nanos) - 1)
            self._slider.setValue(0)
            self._update_frame(0)

        self.view.fitInView(self.scene.sceneRect(), _keep_aspect_ratio())

    def _build_type_subtype_objs(self) -> None:
        """Populate _type_subtype_objs from A∪B∪C; A wins over B wins over C for shared ids."""
        combined: dict[int, tuple[str, str]] = {}
        for oid, info in self._trajs_c.items():
            combined[oid] = (info["type"], info.get("subtype", "OTHER"))
        for oid, info in self._trajs_b.items():
            combined[oid] = (info["type"], info.get("subtype", "OTHER"))
        for oid, info in self._trajs_a.items():
            combined[oid] = (info["type"], info.get("subtype", "OTHER"))

        self._type_subtype_objs = {}
        for oid, (tname, sname) in combined.items():
            self._type_subtype_objs.setdefault(tname, {}).setdefault(sname, []).append(oid)

    def _compute_smooth_speed(self, trajs: dict[int, dict]) -> None:
        """Add 'speed_smooth' and 'nanos_to_idx' to each trajectory dict."""
        half = self._smooth_window
        offsets = np.arange(-half, half + 1)
        kernel = np.exp(-offsets**2 / (2 * self._smooth_sigma**2))

        for obj in trajs.values():
            nanos = obj["nanos"]
            df = obj["df"]
            raw = np.array([
                math.sqrt(
                    _get_col(df.loc[n] if not isinstance(df.loc[n], pd.DataFrame)
                             else df.loc[n].iloc[0], "vel_x", float("nan"))**2 +
                    _get_col(df.loc[n] if not isinstance(df.loc[n], pd.DataFrame)
                             else df.loc[n].iloc[0], "vel_y", float("nan"))**2
                )
                for n in nanos
            ])
            valid = np.isfinite(raw)
            filled = np.where(valid, raw, 0.0)
            weight_sum = np.convolve(valid.astype(float), kernel, mode="same")
            value_sum = np.convolve(filled, kernel, mode="same")
            safe_denom = np.where(weight_sum > 0, weight_sum, 1.0)
            obj["speed_smooth"] = np.where(weight_sum > 0, value_sum / safe_denom, float("nan"))
            obj["nanos_to_idx"] = {int(n): i for i, n in enumerate(nanos)}

    # ------------------------------------------------------------------
    # Playback item creation
    # ------------------------------------------------------------------

    def _build_playback_items(self) -> None:
        """Create hidden polygon items (one per object per source) for frame animation."""
        empty = QtGui.QPolygonF()

        for oid in self._trajs_a:
            color = self._obj_colors.get(oid, QtGui.QColor(220, 0, 0))
            fill = QtGui.QColor(color)
            fill.setAlpha(90)
            pen = QtGui.QPen(color)
            pen.setWidthF(0.2)
            item = self.scene.addPolygon(empty, pen, QtGui.QBrush(fill))
            item.setZValue(10)
            item.setVisible(False)
            self._rect_items_a[oid] = item

        for oid in self._trajs_b:
            color = self._obj_colors.get(oid, QtGui.QColor(220, 0, 0))
            fill = QtGui.QColor(color)
            fill.setAlpha(50)
            pen = QtGui.QPen(color)
            pen.setWidthF(0.3)
            pen.setStyle(_dash_line())
            item = self.scene.addPolygon(empty, pen, QtGui.QBrush(fill))
            item.setZValue(10)
            item.setVisible(False)
            self._rect_items_b[oid] = item

        for oid in self._trajs_c:
            color = self._obj_colors.get(oid, QtGui.QColor(220, 0, 0))
            fill = QtGui.QColor(color)
            fill.setAlpha(50)
            pen = QtGui.QPen(color)
            pen.setWidthF(0.3)
            pen.setStyle(_dash_dot_line())
            item = self.scene.addPolygon(empty, pen, QtGui.QBrush(fill))
            item.setZValue(10)
            item.setVisible(False)
            self._rect_items_c[oid] = item

    # ------------------------------------------------------------------
    # Frame update
    # ------------------------------------------------------------------

    def _update_frame(self, idx: int) -> None:
        self._frame_idx = idx
        nanos = self._all_nanos[idx]

        self._update_rect_items(self._trajs_a, self._rect_items_a, nanos)
        self._update_rect_items(self._trajs_b, self._rect_items_b, nanos)
        self._update_rect_items(self._trajs_c, self._rect_items_c, nanos)
        self._update_data_table(nanos)

        total = len(self._all_nanos)
        t_s = (nanos - self._all_nanos[0]) / 1e9 if total > 1 else 0.0
        minutes = int(t_s // 60)
        seconds = t_s % 60
        self._lbl_frame.setText(f"frame {idx + 1}/{total}  {minutes}:{seconds:05.2f}")
        self._slider.setValue(idx)

    def _update_rect_items(
        self,
        trajs: dict[int, dict],
        items: dict[int, QtWidgets.QGraphicsPolygonItem],
        nanos: int,
    ) -> None:
        for oid, item in items.items():
            if oid not in self._checked_oids or oid not in trajs:
                item.setVisible(False)
                continue
            obj = trajs[oid]
            if nanos not in obj["nanos_set"]:
                item.setVisible(False)
                continue
            row = obj["df"].loc[nanos]
            if isinstance(row, pd.DataFrame):
                row = row.iloc[0]
            x = _get_col(row, "x", 0.0)
            y = _get_col(row, "y", 0.0)
            yaw = _get_col(row, "yaw", 0.0)
            dims = _DEFAULT_DIMENSIONS_M.get(obj["type"], _DEFAULT_DIM_FALLBACK)
            length = _get_col(row, "length", dims[0])
            width = _get_col(row, "width", dims[1])
            item.setPolygon(_obj_rect_polygon(x, y, length, width, yaw))
            item.setVisible(True)

    def _update_data_table(self, nanos: int) -> None:
        rows = []
        all_oids = sorted(set(self._trajs_a) | set(self._trajs_b) | set(self._trajs_c))
        for oid in all_oids:
            for src_label, trajs in [("A", self._trajs_a), ("B", self._trajs_b), ("C", self._trajs_c)]:
                if oid not in trajs or oid not in self._checked_oids:
                    continue
                obj = trajs[oid]
                if nanos not in obj["nanos_set"]:
                    continue
                row = obj["df"].loc[nanos]
                if isinstance(row, pd.DataFrame):
                    row = row.iloc[0]
                rows.append((src_label, oid, row, obj))

        self._data_table.setRowCount(len(rows))
        for r_idx, (src, oid, row, obj) in enumerate(rows):
            x = _get_col(row, "x", 0.0)
            y = _get_col(row, "y", 0.0)
            vx = _get_col(row, "vel_x", 0.0)
            vy = _get_col(row, "vel_y", 0.0)
            speed = math.sqrt(vx**2 + vy**2)
            idx = obj.get("nanos_to_idx", {}).get(nanos)
            speed_s = obj["speed_smooth"][idx] if idx is not None and "speed_smooth" in obj else float("nan")
            if self._speed_kmh:
                speed *= 3.6
                speed_s *= 3.6
            speed_s_str = f"{speed_s:.2f}" if math.isfinite(speed_s) else "-"
            yaw_deg = math.degrees(_get_col(row, "yaw", 0.0))
            dims = _DEFAULT_DIMENSIONS_M.get(obj["type"], _DEFAULT_DIM_FALLBACK)
            L = _get_col(row, "length", dims[0])
            W = _get_col(row, "width", dims[1])
            H = _get_col(row, "height", dims[2])
            tname = _get_str_col(row, "type_name", obj["type"])
            sname = _get_str_col(row, "subtype_name", obj.get("subtype", ""))
            rname = _get_str_col(row, "role_name", "")

            dev_l = _get_col(row, "dev_l", float("nan"))
            dev_w = _get_col(row, "dev_w", float("nan"))
            dev_h = _get_col(row, "dev_h", float("nan"))
            if all(math.isfinite(v) for v in [dev_l, dev_w, dev_h]):
                dev_str = f"{dev_l:+.2f}×{dev_w:+.2f}×{dev_h:+.2f}"
            else:
                dev_str = "—"
            cells = [src, str(oid), f"{x:.1f}", f"{y:.1f}", f"{speed:.2f}", speed_s_str,
                     f"{yaw_deg:.1f}", f"{L:.1f}×{W:.1f}×{H:.1f}", dev_str, tname, sname, rname]
            for c_idx, val in enumerate(cells):
                self._data_table.setItem(r_idx, c_idx, QtWidgets.QTableWidgetItem(val))

    # ------------------------------------------------------------------
    # Playback controls
    # ------------------------------------------------------------------

    def _on_play_pause(self):
        if self._play_timer.isActive():
            self._play_timer.stop()
            self._btn_play.setText("▶")
        else:
            if not self._all_nanos:
                return
            self._play_timer.start()
            self._btn_play.setText("⏸")

    def _on_step(self, delta: int):
        if not self._all_nanos:
            return
        new_idx = max(0, min(self._frame_idx + delta, len(self._all_nanos) - 1))
        self._update_frame(new_idx)

    def _on_slider_moved(self, val: int):
        if not self._all_nanos:
            return
        self._play_timer.stop()
        self._btn_play.setText("▶")
        self._update_frame(val)

    def _on_timer_tick(self):
        next_idx = self._frame_idx + 1
        if next_idx >= len(self._all_nanos):
            self._play_timer.stop()
            self._btn_play.setText("▶")
            return
        self._update_frame(next_idx)

    # ------------------------------------------------------------------
    # Tree
    # ------------------------------------------------------------------

    def _build_tree(self):
        self._tree.blockSignals(True)
        self._tree.clear()
        self._checked_oids = set()
        for type_name in sorted(self._type_subtype_objs):
            sub_dict = self._type_subtype_objs[type_name]
            total = sum(len(ids) for ids in sub_dict.values())
            type_item = QtWidgets.QTreeWidgetItem(self._tree, [f"{type_name}  ({total})"])
            type_item.setCheckState(0, _CHECKED)
            for subtype_name in sorted(sub_dict):
                obj_ids = sub_dict[subtype_name]
                sub_item = QtWidgets.QTreeWidgetItem(type_item, [f"{subtype_name}  ({len(obj_ids)})"])
                sub_item.setCheckState(0, _CHECKED)
                for oid in obj_ids:
                    label = str(oid)
                    if oid in self._frameless_oids:
                        label += "  ∅"
                    leaf = QtWidgets.QTreeWidgetItem(sub_item, [label])
                    leaf.setCheckState(0, _CHECKED)
                    color = self._obj_colors.get(oid, QtGui.QColor(200, 200, 200))
                    if oid in self._frameless_oids:
                        leaf.setForeground(0, QtGui.QBrush(QtGui.QColor(150, 150, 150)))
                        leaf.setToolTip(0, "No frames in timeline")
                    else:
                        leaf.setForeground(0, QtGui.QBrush(color))
                        obj = (self._trajs_a.get(oid) or self._trajs_b.get(oid)
                               or self._trajs_c.get(oid))
                        tt = "Double-click to jump to first frame"
                        std = obj.get("size_std") if obj else None
                        if std:
                            tt += f"\nSize σ: {std[0]:.2f}×{std[1]:.2f}×{std[2]:.2f} m"
                        leaf.setToolTip(0, tt)
                    _set_color_swatch(leaf, color)
                    self._checked_oids.add(oid)
                sub_item.setExpanded(True)
            type_item.setExpanded(True)
        self._tree.blockSignals(False)

    def _on_item_changed(self, item, col):
        if col != 0:
            return
        checked = item.checkState(0) == _CHECKED
        self._tree.blockSignals(True)
        try:
            parent = item.parent()
            if parent is None:
                # Type group → cascade all
                for i in range(item.childCount()):
                    sub = item.child(i)
                    sub.setCheckState(0, item.checkState(0))
                    for j in range(sub.childCount()):
                        leaf = sub.child(j)
                        leaf.setCheckState(0, item.checkState(0))
                        self._set_oid_visible(int(leaf.text(0).split()[0]), checked)
            elif parent.parent() is None:
                # Subtype group → cascade leaves
                for i in range(item.childCount()):
                    leaf = item.child(i)
                    leaf.setCheckState(0, item.checkState(0))
                    self._set_oid_visible(int(leaf.text(0).split()[0]), checked)
                _update_parent_state(parent)
            else:
                # Leaf
                oid = int(item.text(0).split()[0])
                self._set_oid_visible(oid, checked)
                _update_parent_state(parent)
                _update_parent_state(parent.parent())
        finally:
            self._tree.blockSignals(False)

        # Refresh rect visibilities for current frame
        if self._all_nanos:
            self._update_frame(self._frame_idx)

    def _set_oid_visible(self, oid: int, visible: bool) -> None:
        """Update checked set and path item visibility for one oid."""
        if visible:
            self._checked_oids.add(oid)
        else:
            self._checked_oids.discard(oid)
        for items in (self._traj_items_a, self._traj_items_b, self._traj_items_c):
            if oid in items:
                items[oid].setVisible(visible)
        # Rect items visibility handled by next _update_frame call

    def _toggle_speed_unit(self) -> None:
        self._speed_kmh = self._btn_speed_unit.isChecked()
        self._btn_speed_unit.setText("km/h" if self._speed_kmh else "m/s")
        header = self._data_table.horizontalHeaderItem(4)
        if header:
            header.setText("Speed km/h" if self._speed_kmh else "Speed m/s")
        header5 = self._data_table.horizontalHeaderItem(5)
        if header5:
            header5.setText("Speed-s km/h" if self._speed_kmh else "Speed-s m/s")
        if self._all_nanos:
            self._update_frame(self._frame_idx)

    def _on_tree_item_double_clicked(self, item, col) -> None:
        """Jump to the first frame where the double-clicked object exists."""
        if item.parent() is None or item.parent().parent() is None:
            return  # type or subtype group, not a leaf
        try:
            oid = int(item.text(0).split()[0])
        except (ValueError, IndexError):
            return
        first_nano = None
        for trajs in (self._trajs_a, self._trajs_b, self._trajs_c):
            if oid in trajs and trajs[oid].get("nanos_set"):
                candidate = min(trajs[oid]["nanos_set"])
                if first_nano is None or candidate < first_nano:
                    first_nano = candidate
        if first_nano is not None and first_nano in self._all_nanos:
            idx = self._all_nanos.index(first_nano)
            self._play_timer.stop()
            self._btn_play.setText("▶")
            self._update_frame(idx)

    def _set_all(self, state):
        for i in range(self._tree.topLevelItemCount()):
            self._tree.topLevelItem(i).setCheckState(0, state)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _build_full_timeline(nanos_set: set[int]) -> list[int]:
    """Reconstruct a uniform frame grid from sparse CSV timestamps.

    CSV timestamps are round(k/fps * 1e9); infer fps from min gap and
    regenerate the complete grid so empty frames are included.
    """
    if len(nanos_set) < 2:
        return sorted(nanos_set)
    nanos_arr = np.array(sorted(nanos_set), dtype=np.int64)
    diffs = np.diff(nanos_arr)
    dt_nanos = int(np.min(diffs[diffs > 0]))
    fps = round(1e9 / dt_nanos)
    start_frame = round(int(nanos_arr[0]) * fps / 1e9)
    end_frame = round(int(nanos_arr[-1]) * fps / 1e9)
    return [int(round(k / fps * 1e9)) for k in range(start_frame, end_frame + 1)]

def _build_obj_colors(all_ids: set[int]) -> dict[int, QtGui.QColor]:
    """Assign a palette color per unique object id."""
    return {
        oid: QtGui.QColor(*_PALETTE_RGB[i % len(_PALETTE_RGB)])
        for i, oid in enumerate(sorted(all_ids))
    }


def _get_col(row: pd.Series, col: str, default: float = 0.0) -> float:
    try:
        return float(row[col])
    except (KeyError, TypeError, ValueError):
        return default


def _get_str_col(row: pd.Series, col: str, default: str = "") -> str:
    try:
        return str(row[col])
    except (KeyError, TypeError):
        return default


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="XODR + trajectory viewer")
    parser.add_argument("--xodr", help="Path to .xodr file")
    parser.add_argument("--a", dest="slot_a", metavar="PATH",
                        help="Slot A: .csv, .mcap, or .json (OpenLabel)")
    parser.add_argument("--b", dest="slot_b", metavar="PATH", help="Slot B")
    parser.add_argument("--c", dest="slot_c", metavar="PATH", help="Slot C")
    parser.add_argument("--smooth-window", type=int, default=5,
        help="Half-width of Gaussian smoothing window in frames (default: 5)")
    parser.add_argument("--smooth-sigma", type=float, default=None,
        help="Gaussian sigma in frames (default: smooth_window/2)")
    args = parser.parse_args()

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    win = TrajectoryExplorer(
        xodr_path=args.xodr, slot_a=args.slot_a, slot_b=args.slot_b, slot_c=args.slot_c,
        smooth_window=args.smooth_window, smooth_sigma=args.smooth_sigma,
    )
    win.show()
    try:
        app.exec_()
    except AttributeError:
        app.exec()


if __name__ == "__main__":
    main()
