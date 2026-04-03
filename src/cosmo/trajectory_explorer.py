"""Standalone Qt viewer for OpenDRIVE road maps and object trajectories."""
from __future__ import annotations

import argparse
import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

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
]


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

    # Read header offset (OpenDRIVE spec: global position of local (0,0))
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

        # Geometry segments
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

        # Lane sections
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

        # Parking objects
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
                # approximate heading from derivative
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

    # Map s -> (x, y, hdg) for fast lookup
    sample_arr = np.array([(s, x, y, h) for s, x, y, h in samples])

    def interp_at(s_query: float):
        idx = np.searchsorted(sample_arr[:, 0], s_query)
        idx = min(max(idx, 0), len(sample_arr) - 1)
        return sample_arr[idx]

    polygons: list[tuple[int, list[tuple[float, float]]]] = []

    for ls_idx, ls in enumerate(road.lane_sections):
        # Determine s range for this section
        s_start = ls.s
        s_end = road.lane_sections[ls_idx + 1].s if ls_idx + 1 < len(road.lane_sections) else road.length

        # Filter samples to this section
        sec_samples = [(s, x, y, h) for s, x, y, h in samples if s_start <= s <= s_end + 0.01]
        if not sec_samples:
            continue

        for lane in ls.lanes:
            if lane.id == 0:
                continue
            is_right = lane.id < 0
            perp_sign = -1 if is_right else 1

            # Stack widths from center to this lane
            same_side = [l for l in ls.lanes if (l.id < 0) == is_right and l.id != 0]
            same_side.sort(key=lambda l: abs(l.id))
            lane_order = [abs(l.id) for l in same_side]
            this_rank = abs(lane.id)
            inner_lanes = [l for l in same_side if abs(l.id) < this_rank]

            inner_pts: list[tuple[float, float]] = []
            outer_pts: list[tuple[float, float]] = []

            for s, x, y, hdg in sec_samples:
                s_rel = s - s_start
                perp_dir = hdg + perp_sign * math.pi / 2

                # Accumulate inner offset
                d_inner = sum(_lane_width_at(l, s_rel) for l in inner_lanes)
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

    # Interpolate position at s
    s_arr = np.array([s for s, *_ in samples])
    idx = int(np.searchsorted(s_arr, obj.s))
    idx = min(max(idx, 0), len(samples) - 1)
    _, rx, ry, rhdg = samples[idx]

    # Lateral offset
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
    df = pd.read_csv(path)
    df = df.sort_values("total_nanos")
    result = {}
    for idx, group in df.groupby("idx"):
        xy = group[["x", "y"]].values
        type_name = group["type_name"].iloc[0] if "type_name" in group.columns else "OTHER"
        subtype_name = group["subtype_name"].iloc[0] if "subtype_name" in group.columns else "OTHER"
        result[int(idx)] = {"type": str(type_name), "subtype": str(subtype_name), "xy": xy}
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


# ---------------------------------------------------------------------------
# Scene building
# ---------------------------------------------------------------------------

def _qpoly(pts: list[tuple[float, float]]) -> QtGui.QPolygonF:
    return QtGui.QPolygonF([QtCore.QPointF(x, -y) for x, y in pts])


def build_scene(
    scene: QtWidgets.QGraphicsScene,
    roads: list[Road],
    parking: list[ParkingObject],
    road_map: dict[str, Road],
    trajectories: dict[int, dict],
    type_colors: dict[tuple[str, str], QtGui.QColor],
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

    # 4. Trajectories
    traj_items: dict[int, QtWidgets.QGraphicsPathItem] = {}
    for obj_id, obj_info in trajectories.items():
        xy = obj_info["xy"]
        if len(xy) < 2:
            continue
        tname = obj_info["type"]
        sname = obj_info.get("subtype", "OTHER")
        color = type_colors.get((tname, sname), QtGui.QColor(220, 0, 0))
        pen = QtGui.QPen(color)
        pen.setWidthF(0.3)
        path = QtGui.QPainterPath()
        path.moveTo(float(xy[0, 0]), -float(xy[0, 1]))
        for px, py in xy[1:]:
            path.lineTo(float(px), -float(py))
        traj_items[obj_id] = scene.addPath(path, pen)
    return traj_items


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class TrajectoryExplorer(QtWidgets.QMainWindow):
    """Interactive viewer for OpenDRIVE maps and CSV trajectories."""

    def __init__(self, xodr_path: str | None = None, csv_path: str | None = None,
                 mcap_path: str | None = None):
        super().__init__()
        self._xodr_path = xodr_path or ""
        self._csv_path = csv_path or ""
        self._mcap_path = mcap_path or ""
        self.setWindowTitle("Trajectory Explorer")
        self.resize(1400, 900)

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(4)

        # Toolbar
        tb = QtWidgets.QHBoxLayout()
        self.btn_xodr = QtWidgets.QPushButton("Open XODR")
        self.lbl_xodr = QtWidgets.QLabel(self._xodr_path or "—")
        self.lbl_xodr.setStyleSheet("color:#6b7280; max-width:400px;")
        self.btn_csv = QtWidgets.QPushButton("Open CSV")
        self.lbl_csv = QtWidgets.QLabel(self._csv_path or "—")
        self.lbl_csv.setStyleSheet("color:#6b7280; max-width:400px;")
        self.btn_mcap = QtWidgets.QPushButton("Open MCAP")
        self.lbl_mcap = QtWidgets.QLabel(self._mcap_path or "—")
        self.lbl_mcap.setStyleSheet("color:#6b7280; max-width:400px;")
        self.btn_fit = QtWidgets.QPushButton("Fit View")
        self.btn_clear_csv = QtWidgets.QPushButton("Clear CSV")
        self.btn_clear_mcap = QtWidgets.QPushButton("Clear MCAP")

        for w in (self.btn_xodr, self.lbl_xodr, self.btn_csv, self.lbl_csv,
                  self.btn_mcap, self.lbl_mcap,
                  self.btn_fit, self.btn_clear_csv, self.btn_clear_mcap):
            tb.addWidget(w)
        tb.addStretch(1)
        root.addLayout(tb)

        # Graphics view
        self.scene = QtWidgets.QGraphicsScene()
        self.view = QtWidgets.QGraphicsView(self.scene)
        self.view.setDragMode(_scroll_hand_drag())
        try:
            self.view.setTransformationAnchor(_anchor_under_mouse())
            self.view.setResizeAnchor(_anchor_center())
        except Exception:
            pass
        self.view.setRenderHint(QtGui.QPainter.Antialiasing if hasattr(QtGui.QPainter, "Antialiasing")
                                else QtGui.QPainter.RenderHint.Antialiasing)

        # Splitter: view (left) + tree panel (right)
        splitter = QtWidgets.QSplitter(
            QtCore.Qt.Orientation.Horizontal if hasattr(QtCore.Qt, "Orientation")
            else QtCore.Qt.Horizontal
        )
        splitter.addWidget(self.view)

        right = QtWidgets.QWidget()
        right.setMinimumWidth(160)
        rl = QtWidgets.QVBoxLayout(right)
        rl.setContentsMargins(4, 4, 4, 4)
        rl.setSpacing(4)
        btn_row = QtWidgets.QHBoxLayout()
        btn_all = QtWidgets.QPushButton("Select All")
        btn_none = QtWidgets.QPushButton("Deselect All")
        btn_row.addWidget(btn_all)
        btn_row.addWidget(btn_none)
        rl.addLayout(btn_row)
        sep = QtWidgets.QFrame()
        sep.setFrameShape(
            QtWidgets.QFrame.Shape.HLine if hasattr(QtWidgets.QFrame, "Shape")
            else QtWidgets.QFrame.HLine
        )
        rl.addWidget(sep)
        self._tree = QtWidgets.QTreeWidget()
        self._tree.setHeaderHidden(True)
        rl.addWidget(self._tree)
        splitter.addWidget(right)
        splitter.setSizes([1140, 260])
        root.addWidget(splitter, 1)

        # Per-type state
        self._traj_items: dict[int, QtWidgets.QGraphicsPathItem] = {}
        self._type_colors: dict[tuple[str, str], QtGui.QColor] = {}
        self._type_subtype_objs: dict[str, dict[str, list[int]]] = {}

        self.btn_xodr.clicked.connect(self._open_xodr)
        self.btn_csv.clicked.connect(self._open_csv)
        self.btn_mcap.clicked.connect(self._open_mcap)
        self.btn_fit.clicked.connect(self._fit)
        self.btn_clear_csv.clicked.connect(self._clear_csv)
        self.btn_clear_mcap.clicked.connect(self._clear_mcap)
        self.view.wheelEvent = self._wheel_zoom  # type: ignore[assignment]
        btn_all.clicked.connect(lambda: self._set_all(_CHECKED))
        btn_none.clicked.connect(lambda: self._set_all(_UNCHECKED))
        self._tree.itemChanged.connect(self._on_item_changed)

        if self._mcap_path or self._xodr_path:
            self._reload()

    def _open_xodr(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Open XODR", "", "OpenDRIVE (*.xodr);;All (*)")
        if path:
            self._xodr_path = path
            self.lbl_xodr.setText(path)
            self._reload()

    def _open_csv(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Open CSV", "", "CSV (*.csv);;All (*)")
        if path:
            self._csv_path = path
            self.lbl_csv.setText(path)
            self._reload()

    def _open_mcap(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Open MCAP", "", "MCAP (*.mcap);;All (*)")
        if path:
            self._mcap_path = path
            self.lbl_mcap.setText(path)
            self._reload()

    def _clear_csv(self):
        self._csv_path = ""
        self.lbl_csv.setText("—")
        self._tree.clear()
        self._traj_items = {}
        self._type_subtype_objs = {}
        self._type_colors = {}
        self._reload()

    def _clear_mcap(self):
        self._mcap_path = ""
        self.lbl_mcap.setText("—")
        self._tree.clear()
        self._traj_items = {}
        self._type_subtype_objs = {}
        self._type_colors = {}
        self._reload()

    def _fit(self):
        self.view.fitInView(self.scene.sceneRect(), _keep_aspect_ratio())

    def _wheel_zoom(self, event):
        try:
            delta = event.angleDelta().y()
        except Exception:
            delta = 0
        factor = 1.15 if delta > 0 else 1 / 1.15
        self.view.scale(factor, factor)

    def _reload(self):
        self.scene.clear()
        self._traj_items = {}
        if self._mcap_path:
            self._reload_mcap()
        elif self._xodr_path:
            self._reload_xodr_csv()

    def _reload_mcap(self):
        try:
            xodr_xml, trajectories = load_from_mcap(self._mcap_path)
        except Exception as exc:
            self.scene.addText(f"Error loading MCAP: {exc}")
            return
        roads, parking = [], []
        if xodr_xml:
            try:
                roads, parking, _ = parse_xodr_text(xodr_xml)
            except Exception as exc:
                print(f"Warning: could not parse embedded XODR: {exc}")
        self._finish_reload(roads, parking, trajectories)

    def _reload_xodr_csv(self):
        try:
            roads, parking, _ = parse_xodr(self._xodr_path)
        except Exception as exc:
            self.scene.addText(f"Error loading XODR: {exc}")
            return
        trajectories: dict[int, dict] = {}
        if self._csv_path:
            try:
                trajectories = load_trajectories(self._csv_path)
            except Exception as exc:
                print(f"Warning: could not load CSV: {exc}")
        self._finish_reload(roads, parking, trajectories)

    def _finish_reload(self, roads, parking, trajectories):
        road_map = {r.id: r for r in roads}
        self._type_subtype_objs = {}
        for oid, info in trajectories.items():
            tname, sname = info["type"], info.get("subtype", "OTHER")
            self._type_subtype_objs.setdefault(tname, {}).setdefault(sname, []).append(oid)
        pairs = sorted(
            (tname, sname)
            for tname, sub_dict in self._type_subtype_objs.items()
            for sname in sub_dict
        )
        self._type_colors = _make_type_colors(pairs)
        self._traj_items = build_scene(self.scene, roads, parking, road_map, trajectories, self._type_colors)
        self._build_tree()
        self.view.fitInView(self.scene.sceneRect(), _keep_aspect_ratio())

    def _build_tree(self):
        self._tree.blockSignals(True)
        self._tree.clear()
        for type_name in sorted(self._type_subtype_objs):
            sub_dict = self._type_subtype_objs[type_name]
            total = sum(len(ids) for ids in sub_dict.values())
            type_item = QtWidgets.QTreeWidgetItem(self._tree, [f"{type_name}  ({total})"])
            type_item.setCheckState(0, _CHECKED)
            for subtype_name in sorted(sub_dict):
                obj_ids = sub_dict[subtype_name]
                color = self._type_colors[(type_name, subtype_name)]
                sub_item = QtWidgets.QTreeWidgetItem(type_item, [f"{subtype_name}  ({len(obj_ids)})"])
                sub_item.setCheckState(0, _CHECKED)
                sub_item.setForeground(0, QtGui.QBrush(color))
                _set_color_swatch(sub_item, color)
                for oid in obj_ids:
                    leaf = QtWidgets.QTreeWidgetItem(sub_item, [str(oid)])
                    leaf.setCheckState(0, _CHECKED)
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
                # type group → cascade all subtypes and leaves
                for i in range(item.childCount()):
                    sub = item.child(i)
                    sub.setCheckState(0, item.checkState(0))
                    for j in range(sub.childCount()):
                        leaf = sub.child(j)
                        leaf.setCheckState(0, item.checkState(0))
                        oid = int(leaf.text(0))
                        if oid in self._traj_items:
                            self._traj_items[oid].setVisible(checked)
            elif parent.parent() is None:
                # subtype group → cascade leaves + update type parent
                for i in range(item.childCount()):
                    leaf = item.child(i)
                    leaf.setCheckState(0, item.checkState(0))
                    oid = int(leaf.text(0))
                    if oid in self._traj_items:
                        self._traj_items[oid].setVisible(checked)
                _update_parent_state(parent)
            else:
                # obj leaf → update visibility + update subtype + type
                oid = int(item.text(0))
                if oid in self._traj_items:
                    self._traj_items[oid].setVisible(checked)
                _update_parent_state(parent)
                _update_parent_state(parent.parent())
        finally:
            self._tree.blockSignals(False)

    def _set_all(self, state):
        for i in range(self._tree.topLevelItemCount()):
            self._tree.topLevelItem(i).setCheckState(0, state)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="XODR + trajectory viewer")
    parser.add_argument("--xodr", help="Path to .xodr file")
    parser.add_argument("--csv", help="Path to trajectory CSV file")
    parser.add_argument("--mcap", help="Path to MCAP file (contains map + trajectories)")
    args = parser.parse_args()

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    win = TrajectoryExplorer(xodr_path=args.xodr, csv_path=args.csv, mcap_path=args.mcap)
    win.show()
    try:
        app.exec_()
    except AttributeError:
        app.exec()


if __name__ == "__main__":
    main()
