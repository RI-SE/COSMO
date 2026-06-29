"""Standalone Qt viewer for OpenDRIVE road maps and object trajectories."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np

# opendrive-map is the shared source of lane geometry (all primitives, full width
# polynomials, laneOffset). The viewer draws in absolute UTM, so it adds the map
# <offset> back to opendrive-map's local coordinates.
import opendrive_map as odm
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


# Signal compat (PyQt5/6 vs PySide6)
_Sig = getattr(QtCore, "pyqtSignal", None) or QtCore.Signal

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


def _fmt_dims(dims: tuple[float, float, float] | None) -> str:
    """Format (L, W, H) as a compact string for tree labels. Returns '' if dims is None."""
    if dims is None:
        return ""
    parts = []
    for v, label in zip(dims, ("L", "W", "H")):
        if not math.isnan(v):
            parts.append(f"{label}{v:.2f}")
    return f"  [{' '.join(parts)}]" if parts else ""


# ---------------------------------------------------------------------------
# Lane polygons + centerlines (from the shared opendrive-map library)
# ---------------------------------------------------------------------------

def _poly_exterior_xy(poly, ox: float, oy: float) -> list[tuple[float, float]]:
    """Absolute (x+offset) exterior ring of a shapely polygon (largest part if multi)."""
    if poly.is_empty:
        return []
    geom = poly if poly.geom_type == "Polygon" else max(poly.geoms, key=lambda g: g.area)
    return [(x + ox, y + oy) for x, y in geom.exterior.coords]


def map_geometry_from_xodr(path: str | None = None, text: str | None = None):
    """Return (lane_polys, centerlines, parking_polys) in absolute UTM via opendrive-map.

    lane_polys: list of (lane_id, [(x, y), ...]); centerlines/parking_polys: list of [(x, y), ...].
    """
    if path:
        net = odm.RoadNetwork.from_file(path)
    elif text:
        net = odm.RoadNetwork.from_text(text)
    else:
        return [], [], []
    ox, oy, _ = net.offset
    lane_polys = [(ln.lane_id, _poly_exterior_xy(ln.polygon, ox, oy)) for ln in net.lanes]
    lane_polys = [(lid, pts) for lid, pts in lane_polys if pts]
    centerlines = [
        [(x + ox, y + oy) for _s, x, y, _h in odm.sample_reference_line(road, 0.5)]
        for road in net.roads
    ]
    parking_polys = [_poly_exterior_xy(p, ox, oy) for p in net.parking_polygons]
    parking_polys = [pts for pts in parking_polys if pts]
    return lane_polys, centerlines, parking_polys


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
    Produces the same full contract as load_trajectories_full (nanos, df, etc.).
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

    obj_rows: dict[int, list[dict]] = {}
    obj_meta: dict[int, tuple[str, str]] = {}

    for gt in betterosi.read(path, return_ground_truth=True, mcap_topics=["ground_truth"]):
        ts = gt.timestamp
        ts_nanos = int(ts.seconds) * 1_000_000_000 + int(ts.nanos)
        # Normalise to absolute (proj_string CRS) so local-frame mcaps overlay the
        # absolute map: world = R(yaw)·osi + proj_frame_offset (offset absent => no-op).
        pfo = getattr(gt, "proj_frame_offset", None)
        ofx = ofy = 0.0
        ccos, csin = 1.0, 0.0
        if pfo is not None and getattr(pfo, "position", None) is not None:
            ofx, ofy = pfo.position.x, pfo.position.y
            ccos, csin = math.cos(pfo.yaw), math.sin(pfo.yaw)
        for mo in gt.moving_object:
            oid = int(mo.id.value)
            if oid not in obj_meta:
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
                obj_meta[oid] = (type_name, subtype_name)
            b = mo.base
            obj_rows.setdefault(oid, []).append({
                "total_nanos": ts_nanos,
                "x": float(b.position.x) * ccos - float(b.position.y) * csin + ofx,
                "y": float(b.position.x) * csin + float(b.position.y) * ccos + ofy,
                "vel_x": float(b.velocity.x),
                "vel_y": float(b.velocity.y),
                "yaw": float(b.orientation.yaw),
                "length": float(b.dimension.length),
                "width": float(b.dimension.width),
                "height": float(b.dimension.height),
            })

    trajectories = {}
    for oid, rows in obj_rows.items():
        if len(rows) < 2:
            continue
        type_name, subtype_name = obj_meta[oid]
        df = pd.DataFrame(rows).sort_values("total_nanos").set_index("total_nanos")
        nanos = df.index.values.astype(np.int64)
        trajectories[oid] = {
            "type": type_name,
            "subtype": subtype_name,
            "xy": df[["x", "y"]].values,
            "nanos": nanos,
            "nanos_set": set(nanos.tolist()),
            "df": df,
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


def _openlabel_needs_fps(path: str) -> bool:
    """Return True if this OpenLabel file has no timestamps and requires an fps hint."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    root = data.get("openlabel", data)
    return not any(
        fd.get("frame_properties", {}).get("timestamp")
        for fd in root.get("frames", {}).values()
    )


# ---------------------------------------------------------------------------
# Background loader
# ---------------------------------------------------------------------------

class _LoadWorker(QtCore.QThread):
    """Loads trajectory files and XODR on a background thread."""
    progress = _Sig(str)
    done = _Sig(object)
    failed = _Sig(str)

    def __init__(self, xodr_path: str, paths: dict, fps: dict):
        super().__init__()
        self._xodr_path = xodr_path
        self._paths = paths   # {'a': path|None, 'b': path|None, 'c': path|None}
        self._fps = fps       # {'a': float, ...} for OpenLabel without timestamps
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        result: dict = {"trajs_a": {}, "trajs_b": {}, "trajs_c": {},
                        "lane_polys": [], "centerlines": [], "parking_polys": []}
        xodr_xmls: list = []
        try:
            for slot in ("a", "b", "c"):
                if self._cancelled:
                    return
                path = self._paths.get(slot)
                if not path:
                    continue
                self.progress.emit(f"Loading {slot.upper()}: {Path(path).name}…")
                ext = Path(path).suffix.lower()
                try:
                    if ext == ".csv":
                        trajs = load_trajectories_full(path)
                    elif ext == ".mcap":
                        xodr_xml, trajs = load_from_mcap(path)
                        if xodr_xml:
                            xodr_xmls.append(xodr_xml)
                    elif ext == ".json":
                        trajs = load_from_openlabel(path, fps=self._fps.get(slot))
                    else:
                        self.failed.emit(f"Unsupported file type: {path}")
                        return
                except Exception as exc:
                    self.failed.emit(f"Could not load {Path(path).name}: {exc}")
                    return
                result[f"trajs_{slot}"] = trajs

            if self._cancelled:
                return

            # Lane polygons + centerlines + parking all come from opendrive-map.
            if self._xodr_path or xodr_xmls:
                self.progress.emit("Building map geometry…")
                try:
                    geom = (map_geometry_from_xodr(path=self._xodr_path) if self._xodr_path
                            else map_geometry_from_xodr(text=xodr_xmls[0]))
                    result["lane_polys"], result["centerlines"], result["parking_polys"] = geom
                except Exception as exc:
                    print(f"Warning: opendrive-map geometry failed: {exc}")
            if not self._cancelled:
                self.done.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))


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
    lane_polys: list[tuple[int, list[tuple[float, float]]]],
    centerlines: list[list[tuple[float, float]]],
    parking_polys: list[list[tuple[float, float]]],
    trajectories: dict[int, dict],
    obj_colors: dict[int, QtGui.QColor],
    path_alpha: int = 255,
) -> dict[int, QtWidgets.QGraphicsPathItem]:
    no_pen = QtGui.QPen(QtCore.Qt.NoPen if hasattr(QtCore.Qt, "NoPen") else QtCore.Qt.PenStyle.NoPen)

    # 1. Lane polygons (absolute UTM, from opendrive-map)
    for lane_id, poly in lane_polys:
        if lane_id < 0:
            brush = QtGui.QBrush(QtGui.QColor(0, 180, 0, 80))
        else:
            brush = QtGui.QBrush(QtGui.QColor(0, 0, 200, 80))
        scene.addPolygon(_qpoly(poly), no_pen, brush)

    # 2. Centerlines (road reference lines, absolute UTM)
    center_pen = QtGui.QPen(QtGui.QColor(0, 0, 0))
    center_pen.setWidthF(0.15)
    for samples in centerlines:
        if len(samples) < 2:
            continue
        path = QtGui.QPainterPath()
        path.moveTo(samples[0][0], -samples[0][1])
        for x, y in samples[1:]:
            path.lineTo(x, -y)
        scene.addPath(path, center_pen)

    # 3. Parking (absolute UTM, from opendrive-map)
    park_brush = QtGui.QBrush(QtGui.QColor(128, 128, 128, 100))
    park_pen = QtGui.QPen(QtGui.QColor(64, 64, 64))
    park_pen.setWidthF(0.1)
    for rect_pts in parking_polys:
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
                 smooth_window: int = 5, smooth_sigma: float | None = None,
                 live_table: bool = True):
        super().__init__()
        self._xodr_path = xodr_path or ""
        self._slot_path_a: str | None = slot_a or None
        self._slot_path_b: str | None = slot_b or None
        self._slot_path_c: str | None = slot_c or None
        self._smooth_window = smooth_window
        self._smooth_sigma = smooth_sigma if smooth_sigma is not None else smooth_window / 2
        self._live_table = live_table
        self._load_worker: QtCore.QThread | None = None
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
        self.view.fitInView(self.scene.itemsBoundingRect(), _keep_aspect_ratio())

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
        # Cancel any in-flight load
        if self._load_worker is not None and self._load_worker.isRunning():
            try:
                self._load_worker.cancel()
                self._load_worker.done.disconnect()
                self._load_worker.failed.disconnect()
                self._load_worker.progress.disconnect()
            except Exception:
                pass

        self._play_timer.stop()
        self._btn_play.setText("▶")
        self.scene.clear()
        self.scene.setSceneRect(QtCore.QRectF())  # let the rect recompute from new items
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
        self._trajs_a, self._trajs_b, self._trajs_c = {}, {}, {}

        if not any([self._slot_path_a, self._slot_path_b, self._slot_path_c, self._xodr_path]):
            return

        # Ask for FPS upfront for any OpenLabel files that need it (must be on main thread)
        fps_by_slot: dict = {}
        for slot in ("a", "b", "c"):
            path = getattr(self, f"_slot_path_{slot}")
            if not path or Path(path).suffix.lower() != ".json":
                continue
            try:
                if _openlabel_needs_fps(path):
                    fps, ok = QtWidgets.QInputDialog.getDouble(
                        self, "OpenLabel FPS", "Frames per second:", 30.0, 0.1, 1000.0, 2
                    )
                    if not ok:
                        return
                    fps_by_slot[slot] = fps
            except Exception:
                pass

        self._load_worker = _LoadWorker(
            xodr_path=self._xodr_path,
            paths={"a": self._slot_path_a, "b": self._slot_path_b, "c": self._slot_path_c},
            fps=fps_by_slot,
        )
        self._load_worker.progress.connect(self._on_load_progress)
        self._load_worker.done.connect(self._on_load_done)
        self._load_worker.failed.connect(self._on_load_failed)

        # Simple status dialog — no progress bar, just label + cancel button
        _wm = (QtCore.Qt.WindowModality.WindowModal
               if hasattr(QtCore.Qt, "WindowModality") else QtCore.Qt.WindowModal)
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("Loading")
        dlg.setWindowModality(_wm)
        _vl = QtWidgets.QVBoxLayout(dlg)
        _vl.setContentsMargins(20, 16, 20, 16)
        _vl.setSpacing(10)
        self._load_label = QtWidgets.QLabel("Starting…")
        self._load_label.setMinimumWidth(300)
        _vl.addWidget(self._load_label)
        _btn_cancel = QtWidgets.QPushButton("Cancel")
        _btn_cancel.clicked.connect(self._on_load_cancel)
        _vl.addWidget(_btn_cancel)
        dlg.finished.connect(self._on_load_cancel)
        self._progress_dlg = dlg
        dlg.show()

        self._load_worker.start()

    def _on_load_progress(self, msg: str) -> None:
        if hasattr(self, "_load_label"):
            self._load_label.setText(msg)

    def _on_load_cancel(self) -> None:
        if self._load_worker is not None:
            self._load_worker.cancel()
        if hasattr(self, "_progress_dlg"):
            try:
                self._progress_dlg.finished.disconnect(self._on_load_cancel)
            except Exception:
                pass
            self._progress_dlg.close()

    def _on_load_failed(self, msg: str) -> None:
        if hasattr(self, "_progress_dlg"):
            try:
                self._progress_dlg.finished.disconnect(self._on_load_cancel)
            except Exception:
                pass
            self._progress_dlg.close()
        # Show the error on a clean scene and frame it, so it is visible regardless of
        # the previous view transform and does not pollute the next load's scene rect.
        self.scene.clear()
        self.scene.setSceneRect(QtCore.QRectF())
        self.scene.addText(f"Error loading: {msg}")
        self.scene.setSceneRect(self.scene.itemsBoundingRect())
        self.view.fitInView(self.scene.itemsBoundingRect(), _keep_aspect_ratio())

    def _on_load_done(self, result: dict) -> None:
        self._trajs_a = result["trajs_a"]
        self._trajs_b = result["trajs_b"]
        self._trajs_c = result["trajs_c"]
        if hasattr(self, "_load_label"):
            self._load_label.setText("Building scene…")
        self._rebuild_ui_and_scene(result["lane_polys"], result["centerlines"],
                                   result["parking_polys"])
        if hasattr(self, "_progress_dlg"):
            try:
                self._progress_dlg.finished.disconnect(self._on_load_cancel)
            except Exception:
                pass
            self._progress_dlg.close()

    def _rebuild_ui_and_scene(self, lane_polys, centerlines, parking_polys) -> None:
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

        self._traj_items_a = build_scene(
            self.scene, lane_polys, centerlines, parking_polys,
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

        # Reset the scene rect to the current items: QGraphicsScene.sceneRect() only
        # grows (never shrinks), so after a clear+reload a stale rect would make
        # fitInView zoom past the new content and show nothing.
        self.scene.setSceneRect(self.scene.itemsBoundingRect())
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
            nanos = obj.get("nanos")
            if nanos is None:
                continue
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
        if self._live_table or not self._play_timer.isActive():
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
                     f"{yaw_deg:.1f}", f"{L:.2f}×{W:.2f}×{H:.2f}", dev_str, tname, sname, rname]
            for c_idx, val in enumerate(cells):
                self._data_table.setItem(r_idx, c_idx, QtWidgets.QTableWidgetItem(val))

    # ------------------------------------------------------------------
    # Playback controls
    # ------------------------------------------------------------------

    def _on_play_pause(self):
        if self._play_timer.isActive():
            self._play_timer.stop()
            self._btn_play.setText("▶")
            if not self._live_table and self._all_nanos:
                self._update_data_table(self._all_nanos[self._frame_idx])
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
            if not self._live_table:
                self._update_data_table(self._all_nanos[self._frame_idx])
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
            # Compute mean dims across all objects in this type
            type_dims = self._mean_dims_for_oids(
                [oid for ids in sub_dict.values() for oid in ids])
            type_label = f"{type_name}  ({total}){_fmt_dims(type_dims)}"
            type_item = QtWidgets.QTreeWidgetItem(self._tree, [type_label])
            type_item.setCheckState(0, _CHECKED)
            for subtype_name in sorted(sub_dict):
                obj_ids = sub_dict[subtype_name]
                sub_dims = self._mean_dims_for_oids(obj_ids)
                sub_label = f"{subtype_name}  ({len(obj_ids)}){_fmt_dims(sub_dims)}"
                sub_item = QtWidgets.QTreeWidgetItem(type_item, [sub_label])
                sub_item.setCheckState(0, _CHECKED)
                for oid in obj_ids:
                    label = str(oid)
                    if oid in self._frameless_oids:
                        label += "  ∅"
                    else:
                        obj_dims = self._mean_dims_for_oids([oid])
                        label += _fmt_dims(obj_dims)
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
                            df = obj.get("df") if obj else None
                            if df is not None and "length" in df.columns and len(df) > 0:
                                mu = [df["length"].iloc[0], df["width"].iloc[0], df["height"].iloc[0]]
                                pct = [s / m * 100 if m else float("nan") for s, m in zip(std, mu)]
                                tt += (f"\nSize σ: {std[0]:.2f}×{std[1]:.2f}×{std[2]:.2f} m"
                                       f"  ({pct[0]:.0f}%×{pct[1]:.0f}%×{pct[2]:.0f}%)")
                            else:
                                tt += f"\nSize σ: {std[0]:.2f}×{std[1]:.2f}×{std[2]:.2f} m"
                        leaf.setToolTip(0, tt)
                    _set_color_swatch(leaf, color)
                    self._checked_oids.add(oid)
                sub_item.setExpanded(True)
            type_item.setExpanded(True)
        self._tree.blockSignals(False)

    def _mean_dims_for_oids(self, oids: list[int]) -> tuple[float, float, float] | None:
        """Return (mean_L, mean_W, mean_H) across all frames for the given object ids, or None."""
        Ls, Ws, Hs = [], [], []
        for oid in oids:
            obj = (self._trajs_a.get(oid) or self._trajs_b.get(oid)
                   or self._trajs_c.get(oid))
            if obj is None:
                continue
            df = obj.get("df")
            if df is None or len(df) == 0:
                continue
            if "length" in df.columns:
                vals = df["length"].dropna()
                if len(vals):
                    Ls.append(float(vals.mean()))
            if "width" in df.columns:
                vals = df["width"].dropna()
                if len(vals):
                    Ws.append(float(vals.mean()))
            if "height" in df.columns:
                vals = df["height"].dropna()
                if len(vals):
                    Hs.append(float(vals.mean()))
        if not Ls and not Ws and not Hs:
            return None
        L = float(np.mean(Ls)) if Ls else float("nan")
        W = float(np.mean(Ws)) if Ws else float("nan")
        H = float(np.mean(Hs)) if Hs else float("nan")
        return (L, W, H)

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
    """Build the playback timeline from the union of all object timestamps.

    Previously this reconstructed a uniform grid via fps inference, but that
    introduced rounding mismatches vs. MCAP timestamps (floor-truncated) causing
    ~50% of frames to appear empty. Using the actual timestamps is always correct.
    """
    return sorted(nanos_set)

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
    parser.add_argument("--no-live-table", action="store_true", default=False,
        help="Skip data table updates during playback (faster with many objects)")
    args = parser.parse_args()

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    win = TrajectoryExplorer(
        xodr_path=args.xodr, slot_a=args.slot_a, slot_b=args.slot_b, slot_c=args.slot_c,
        smooth_window=args.smooth_window, smooth_sigma=args.smooth_sigma,
        live_table=not args.no_live_table,
    )
    win.show()
    try:
        app.exec_()
    except AttributeError:
        app.exec()


if __name__ == "__main__":
    main()
