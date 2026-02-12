# src/cosmo/gui/pixel_pairs_editor.py
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Qt binding selection: PyQt5 → PySide6 → PyQt6
try:
    from PyQt5 import QtCore, QtGui, QtWidgets
except ImportError:  # pragma: no cover
    try:
        from PySide6 import QtCore, QtGui, QtWidgets
    except ImportError:  # pragma: no cover
        from PyQt6 import QtCore, QtGui, QtWidgets


def _dialog_exec(dlg) -> int:
    fn = getattr(dlg, "exec", None) or getattr(dlg, "exec_", None)
    return int(fn())


def _qt_keep_aspect_ratio():
    if hasattr(QtCore.Qt, "KeepAspectRatio"):
        return QtCore.Qt.KeepAspectRatio
    return QtCore.Qt.AspectRatioMode.KeepAspectRatio


def _qt_item_is_editable_flag():
    # Qt5: QtCore.Qt.ItemIsEditable
    # Qt6: QtCore.Qt.ItemFlag.ItemIsEditable
    if hasattr(QtCore.Qt, "ItemIsEditable"):
        return QtCore.Qt.ItemIsEditable
    return QtCore.Qt.ItemFlag.ItemIsEditable


def _qt_horizontal():
    if hasattr(QtCore.Qt, "Horizontal"):
        return QtCore.Qt.Horizontal
    return QtCore.Qt.Orientation.Horizontal


@dataclass
class PixelPair:
    name: str
    u: float
    v: float


# ----------------------------
# Undo/Redo action definitions
# ----------------------------
class _ActionType:
    MOVE = "move"
    ADD = "add"
    DELETE = "delete"


@dataclass
class _UndoAction:
    type: str
    name: str
    before: Optional[Tuple[float, float]] = None
    after: Optional[Tuple[float, float]] = None
    row_data: Optional[Tuple[str, float, float]] = None  # for add/delete restore


class DraggablePoint(QtWidgets.QGraphicsEllipseItem):
    """
    Draggable point in scene coordinates (u,v) with an attached label.
    - Snapping is handled by itemChange(ItemPositionChange) returning a modified QPointF.
    - Undo/redo uses mousePress+mouseRelease to commit moves only when finished dragging.
    """

    def __init__(self, name: str, u: float, v: float, radius: float = 6.0):
        super().__init__(-radius, -radius, 2 * radius, 2 * radius)
        self.name = name

        # Callbacks (set by parent dialog)
        self.snap_enabled = None          # () -> bool
        self.on_moving = None             # (name, u, v) -> None
        self.on_move_committed = None     # (name, (u0,v0), (u1,v1)) -> None

        # Visual style
        self.setBrush(QtGui.QBrush(QtGui.QColor("yellow")))
        self.setPen(QtGui.QPen(QtGui.QColor("black"), 1))

        # Make draggable/selectable
        self.setFlag(QtWidgets.QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QtWidgets.QGraphicsItem.ItemSendsScenePositionChanges, True)
        self.setFlag(QtWidgets.QGraphicsItem.ItemIsSelectable, True)

        # Create label BEFORE setPos() (setPos can trigger itemChange)
        self.label = QtWidgets.QGraphicsTextItem(name)
        self.label.setDefaultTextColor(QtGui.QColor("yellow"))

        self._drag_start: Optional[QtCore.QPointF] = None

        # Place
        self.setPos(u, v)
        p = self.pos()
        self.label.setPos(p.x() + 8, p.y() - 8)

    def itemChange(self, change, value):
        # Snap during drag: modify the proposed position before it is applied.
        if change == QtWidgets.QGraphicsItem.ItemPositionChange:
            try:
                if callable(self.snap_enabled) and self.snap_enabled():
                    if isinstance(value, QtCore.QPointF):
                        return QtCore.QPointF(round(value.x()), round(value.y()))
            except Exception:
                pass

        # After the position changed, update label + notify
        if change == QtWidgets.QGraphicsItem.ItemPositionHasChanged:
            p = self.pos()
            if hasattr(self, "label") and self.label is not None:
                self.label.setPos(p.x() + 8, p.y() - 8)
            if callable(self.on_moving):
                self.on_moving(self.name, float(p.x()), float(p.y()))

        return super().itemChange(change, value)

    def mousePressEvent(self, event):
        self._drag_start = self.pos()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if self._drag_start is None:
            return
        start = self._drag_start
        end = self.pos()
        self._drag_start = None

        # Commit only if moved
        if (start.x() != end.x()) or (start.y() != end.y()):
            if callable(self.on_move_committed):
                self.on_move_committed(
                    self.name,
                    (float(start.x()), float(start.y())),
                    (float(end.x()), float(end.y())),
                )


class PixelPairsEditorDialog(QtWidgets.QDialog):
    """
    Interactive editor for pixel_pairs.csv.

    - Left: image with draggable points.
    - Right: table with point_name, u, v.
    - Snap-to-pixel toggle.
    - Undo/Redo stack.
    - Apply: saves *_edited.csv next to original and returns that path.

    Expected CSV columns: point_name,u,v
    """

    def __init__(self, image_path: str, pixel_pairs_csv: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit pixel pairs")
        self.resize(1200, 740)

        self.image_path = str(image_path)
        self.csv_path = str(pixel_pairs_csv)

        self._items: Dict[str, DraggablePoint] = {}
        self._pixmap_item: Optional[QtWidgets.QGraphicsPixmapItem] = None

        # Undo stacks
        self._undo: List[_UndoAction] = []
        self._redo: List[_UndoAction] = []

        # Guards to prevent feedback loops
        self._suppress_table_changed = False
        self._suppress_undo_push = False

        # UI
        root = QtWidgets.QVBoxLayout(self)

        split = QtWidgets.QSplitter()
        split.setOrientation(_qt_horizontal())
        root.addWidget(split, 1)

        # Left: image + points
        left = QtWidgets.QWidget()
        left_l = QtWidgets.QVBoxLayout(left)
        left_l.setContentsMargins(0, 0, 0, 0)

        self.view = QtWidgets.QGraphicsView()
        self.view.setRenderHint(QtGui.QPainter.Antialiasing, True)
        self.view.setDragMode(QtWidgets.QGraphicsView.ScrollHandDrag)
        self.scene = QtWidgets.QGraphicsScene(self.view)
        self.view.setScene(self.scene)
        left_l.addWidget(self.view, 1)

        hint = QtWidgets.QLabel(
            "Tips: Drag points to adjust. Mouse wheel zoom. "
            "Edit u/v in table for precise values. "
            "Snap-to-pixel rounds to integer pixels. "
            "Undo/Redo: Ctrl+Z / Ctrl+Y."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#555;")
        left_l.addWidget(hint)

        split.addWidget(left)

        # Right: table + controls
        right = QtWidgets.QWidget()
        right_l = QtWidgets.QVBoxLayout(right)
        right_l.setContentsMargins(0, 0, 0, 0)

        # Top controls: snap + undo/redo
        hb_top = QtWidgets.QHBoxLayout()
        self.chk_snap = QtWidgets.QCheckBox("Snap to pixel (round u/v)")
        self.chk_snap.setChecked(False)

        self.btn_undo = QtWidgets.QPushButton("Undo")
        self.btn_redo = QtWidgets.QPushButton("Redo")
        self.btn_undo.setEnabled(False)
        self.btn_redo.setEnabled(False)

        hb_top.addWidget(self.chk_snap)
        hb_top.addStretch(1)
        hb_top.addWidget(self.btn_undo)
        hb_top.addWidget(self.btn_redo)
        right_l.addLayout(hb_top)

        self.lbl_history = QtWidgets.QLabel("")
        self.lbl_history.setStyleSheet("color:#555;")
        right_l.addWidget(self.lbl_history)

        self.table = QtWidgets.QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["point_name", "u", "v"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        right_l.addWidget(self.table, 1)

        hb_tbl = QtWidgets.QHBoxLayout()
        self.btn_fit_view = QtWidgets.QPushButton("Fit view")
        self.btn_add_row = QtWidgets.QPushButton("Add point…")
        self.btn_delete_row = QtWidgets.QPushButton("Delete selected")
        hb_tbl.addWidget(self.btn_fit_view)
        hb_tbl.addWidget(self.btn_add_row)
        hb_tbl.addWidget(self.btn_delete_row)
        hb_tbl.addStretch(1)
        right_l.addLayout(hb_tbl)

        split.addWidget(right)
        split.setSizes([800, 400])

        # Bottom buttons
        hb = QtWidgets.QHBoxLayout()
        self.btn_save_as = QtWidgets.QPushButton("Save As…")
        self.btn_apply = QtWidgets.QPushButton("Apply (use this file)")
        self.btn_cancel = QtWidgets.QPushButton("Cancel")
        hb.addWidget(self.btn_save_as)
        hb.addWidget(self.btn_apply)
        hb.addStretch(1)
        hb.addWidget(self.btn_cancel)
        root.addLayout(hb)

        # Signals
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_save_as.clicked.connect(self._save_as)
        self.btn_apply.clicked.connect(self._apply)
        self.btn_fit_view.clicked.connect(self._fit_view)
        self.btn_delete_row.clicked.connect(self._delete_selected)
        self.btn_add_row.clicked.connect(self._add_point_dialog)

        self.btn_undo.clicked.connect(self.undo)
        self.btn_redo.clicked.connect(self.redo)

        self.table.itemChanged.connect(self._on_table_changed)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)

        # Snap toggle: if enabled, immediately snap all points and table values
        self.chk_snap.toggled.connect(self._apply_snap_to_all)

        # Shortcuts
        QtWidgets.QShortcut(QtGui.QKeySequence.Undo, self, activated=self.undo)
        QtWidgets.QShortcut(QtGui.QKeySequence.Redo, self, activated=self.redo)

        # Mouse wheel zoom
        self.view.wheelEvent = self._wheel_zoom  # type: ignore[assignment]

        self._load()
        self._update_history_ui()

    # ---------------------------------------------------------------------
    # State helpers
    # ---------------------------------------------------------------------
    def _snap_enabled(self) -> bool:
        return bool(self.chk_snap.isChecked())

    def _push_action(self, action: _UndoAction) -> None:
        if self._suppress_undo_push:
            return
        self._undo.append(action)
        self._redo.clear()
        self._update_history_ui()

    def _update_history_ui(self) -> None:
        self.btn_undo.setEnabled(len(self._undo) > 0)
        self.btn_redo.setEnabled(len(self._redo) > 0)
        self.lbl_history.setText(f"History: undo={len(self._undo)}  redo={len(self._redo)}")

    # ---------------------------------------------------------------------
    # Loading
    # ---------------------------------------------------------------------
    def _load(self):
        img_path = Path(self.image_path)
        if not img_path.is_file():
            raise FileNotFoundError(f"Image not found: {img_path}")

        csv_path = Path(self.csv_path)
        if not csv_path.is_file():
            raise FileNotFoundError(f"Pixel pairs CSV not found: {csv_path}")

        pm = QtGui.QPixmap(str(img_path))
        if pm.isNull():
            raise RuntimeError(f"Could not load image: {img_path}")

        self.scene.clear()
        self._items = {}
        self._undo.clear()
        self._redo.clear()

        self._pixmap_item = self.scene.addPixmap(pm)
        self.scene.setSceneRect(QtCore.QRectF(pm.rect()))

        # Load CSV
        pairs: List[PixelPair] = []
        with open(str(csv_path), "r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                name = str(row.get("point_name", "")).strip()
                if not name:
                    continue
                u = float(row.get("u", 0.0))
                v = float(row.get("v", 0.0))
                pairs.append(PixelPair(name=name, u=u, v=v))

        # Fill table
        self._suppress_table_changed = True
        self.table.setRowCount(len(pairs))
        editable_flag = _qt_item_is_editable_flag()
        for i, p in enumerate(pairs):
            it_name = QtWidgets.QTableWidgetItem(p.name)
            it_name.setFlags(it_name.flags() & ~editable_flag)  # name read-only
            self.table.setItem(i, 0, it_name)
            self.table.setItem(i, 1, QtWidgets.QTableWidgetItem(f"{p.u:.3f}"))
            self.table.setItem(i, 2, QtWidgets.QTableWidgetItem(f"{p.v:.3f}"))
        self._suppress_table_changed = False

        # Add points
        for p in pairs:
            item = DraggablePoint(p.name, p.u, p.v)
            item.snap_enabled = self._snap_enabled
            item.on_moving = self._on_point_moving
            item.on_move_committed = self._on_point_move_committed
            self.scene.addItem(item)
            self.scene.addItem(item.label)
            self._items[p.name] = item

        self._fit_view()
        self._update_history_ui()

    # ---------------------------------------------------------------------
    # View behavior
    # ---------------------------------------------------------------------
    def _fit_view(self):
        self.view.fitInView(self.scene.sceneRect(), _qt_keep_aspect_ratio())

    def _wheel_zoom(self, event):
        angle = event.angleDelta().y()
        factor = 1.15 if angle > 0 else 1 / 1.15
        self.view.scale(factor, factor)

    # ---------------------------------------------------------------------
    # Sync: scene -> table (live move)
    # ---------------------------------------------------------------------
    def _on_point_moving(self, name: str, u: float, v: float):
        # Update table cells but do NOT push undo here (only on commit)
        for r in range(self.table.rowCount()):
            if self.table.item(r, 0).text() == name:
                self._suppress_table_changed = True
                self.table.item(r, 1).setText(f"{u:.3f}")
                self.table.item(r, 2).setText(f"{v:.3f}")
                self._suppress_table_changed = False
                break

    def _on_point_move_committed(self, name: str, before: Tuple[float, float], after: Tuple[float, float]):
        # Commit as an undoable action
        if before != after:
            self._push_action(_UndoAction(type=_ActionType.MOVE, name=name, before=before, after=after))

    # ---------------------------------------------------------------------
    # Sync: table -> scene (edit)
    # ---------------------------------------------------------------------
    def _on_table_changed(self, item: QtWidgets.QTableWidgetItem):
        if self._suppress_table_changed:
            return
        r = item.row()
        name = self.table.item(r, 0).text()
        pt = self._items.get(name)
        if pt is None:
            return

        try:
            u = float(self.table.item(r, 1).text())
            v = float(self.table.item(r, 2).text())
        except Exception:
            return

        if self._snap_enabled():
            u, v = float(round(u)), float(round(v))
            # reflect snapped values in table
            self._suppress_table_changed = True
            self.table.item(r, 1).setText(f"{u:.3f}")
            self.table.item(r, 2).setText(f"{v:.3f}")
            self._suppress_table_changed = False

        before = (float(pt.pos().x()), float(pt.pos().y()))
        after = (u, v)
        if before == after:
            return

        # Apply move without re-pushing via commit callback (we push ourselves)
        self._suppress_undo_push = True
        pt.setPos(u, v)
        self._suppress_undo_push = False

        self._push_action(_UndoAction(type=_ActionType.MOVE, name=name, before=before, after=after))

    def _on_selection_changed(self):
        sel = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not sel:
            return
        r = sel[0].row()
        name = self.table.item(r, 0).text()
        pt = self._items.get(name)
        if pt:
            self.scene.clearSelection()
            pt.setSelected(True)
            self.view.centerOn(pt)

    # ---------------------------------------------------------------------
    # Snap utilities
    # ---------------------------------------------------------------------
    def _apply_snap_to_all(self):
        if not self._snap_enabled():
            return
        # Snap all points + table values (undoable as a batch of moves)
        # We'll push one action per point so undo behaves naturally.
        for name, pt in list(self._items.items()):
            before = (float(pt.pos().x()), float(pt.pos().y()))
            after = (float(round(before[0])), float(round(before[1])))
            if before != after:
                self._suppress_undo_push = True
                pt.setPos(after[0], after[1])
                self._suppress_undo_push = False
                self._push_action(_UndoAction(type=_ActionType.MOVE, name=name, before=before, after=after))

        # Ensure table reflects current positions
        for r in range(self.table.rowCount()):
            nm = self.table.item(r, 0).text()
            pt = self._items.get(nm)
            if pt:
                self._suppress_table_changed = True
                self.table.item(r, 1).setText(f"{pt.pos().x():.3f}")
                self.table.item(r, 2).setText(f"{pt.pos().y():.3f}")
                self._suppress_table_changed = False

    # ---------------------------------------------------------------------
    # CRUD operations (add/delete) with undo support
    # ---------------------------------------------------------------------
    def _delete_selected(self):
        sel = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not sel:
            return
        r = sel[0].row()
        name = self.table.item(r, 0).text()
        try:
            u = float(self.table.item(r, 1).text())
            v = float(self.table.item(r, 2).text())
        except Exception:
            u, v = 0.0, 0.0

        # Remove graphics items
        pt = self._items.pop(name, None)
        if pt is not None:
            try:
                self.scene.removeItem(pt.label)
            except Exception:
                pass
            try:
                self.scene.removeItem(pt)
            except Exception:
                pass

        # Remove table row
        self._suppress_table_changed = True
        self.table.removeRow(r)
        self._suppress_table_changed = False

        # Push undo action
        self._push_action(_UndoAction(type=_ActionType.DELETE, name=name, row_data=(name, u, v)))

    def _add_point_dialog(self):
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("Add point")
        form = QtWidgets.QFormLayout(dlg)

        ed_name = QtWidgets.QLineEdit()
        ed_u = QtWidgets.QDoubleSpinBox()
        ed_v = QtWidgets.QDoubleSpinBox()
        ed_u.setRange(-1e7, 1e7)
        ed_v.setRange(-1e7, 1e7)
        ed_u.setDecimals(3)
        ed_v.setDecimals(3)

        form.addRow("point_name:", ed_name)
        form.addRow("u:", ed_u)
        form.addRow("v:", ed_v)

        hb = QtWidgets.QHBoxLayout()
        btn_ok = QtWidgets.QPushButton("Add")
        btn_cancel = QtWidgets.QPushButton("Cancel")
        hb.addWidget(btn_ok)
        hb.addWidget(btn_cancel)
        form.addRow(hb)

        btn_cancel.clicked.connect(dlg.reject)
        btn_ok.clicked.connect(dlg.accept)

        if not _dialog_exec(dlg):
            return

        name = ed_name.text().strip()
        if not name:
            QtWidgets.QMessageBox.warning(self, "Missing", "point_name cannot be empty.")
            return
        if name in self._items:
            QtWidgets.QMessageBox.warning(self, "Duplicate", f"A point named '{name}' already exists.")
            return

        u = float(ed_u.value())
        v = float(ed_v.value())
        if self._snap_enabled():
            u, v = float(round(u)), float(round(v))

        self._add_point(name, u, v, push_undo=True)

    def _add_point(self, name: str, u: float, v: float, push_undo: bool):
        # Add to table
        self._suppress_table_changed = True
        r = self.table.rowCount()
        self.table.insertRow(r)
        it_name = QtWidgets.QTableWidgetItem(name)
        it_name.setFlags(it_name.flags() & ~_qt_item_is_editable_flag())
        self.table.setItem(r, 0, it_name)
        self.table.setItem(r, 1, QtWidgets.QTableWidgetItem(f"{u:.3f}"))
        self.table.setItem(r, 2, QtWidgets.QTableWidgetItem(f"{v:.3f}"))
        self._suppress_table_changed = False

        # Add to scene
        item = DraggablePoint(name, u, v)
        item.snap_enabled = self._snap_enabled
        item.on_moving = self._on_point_moving
        item.on_move_committed = self._on_point_move_committed
        self.scene.addItem(item)
        self.scene.addItem(item.label)
        self._items[name] = item

        self.table.selectRow(r)
        self.view.centerOn(item)

        if push_undo:
            self._push_action(_UndoAction(type=_ActionType.ADD, name=name, row_data=(name, u, v)))

    # ---------------------------------------------------------------------
    # Undo/Redo
    # ---------------------------------------------------------------------
    def undo(self):
        if not self._undo:
            return
        action = self._undo.pop()
        self._apply_action_inverse(action)
        self._redo.append(action)
        self._update_history_ui()

    def redo(self):
        if not self._redo:
            return
        action = self._redo.pop()
        self._apply_action(action)
        self._undo.append(action)
        self._update_history_ui()

    def _apply_action(self, action: _UndoAction):
        # Apply "forward" effect (redo)
        self._suppress_undo_push = True
        try:
            if action.type == _ActionType.MOVE and action.after is not None:
                pt = self._items.get(action.name)
                if pt:
                    pt.setPos(action.after[0], action.after[1])

            elif action.type == _ActionType.ADD and action.row_data is not None:
                name, u, v = action.row_data
                if name not in self._items:
                    self._add_point(name, u, v, push_undo=False)

            elif action.type == _ActionType.DELETE and action.row_data is not None:
                # redo delete => delete again if exists
                name, _u, _v = action.row_data
                if name in self._items:
                    # find row
                    row = self._find_row(name)
                    if row is not None:
                        self.table.selectRow(row)
                        self._delete_selected()
                        # _delete_selected pushed undo; suppress it
                        if self._undo and self._undo[-1].type == _ActionType.DELETE and self._undo[-1].name == name:
                            self._undo.pop()
        finally:
            self._suppress_undo_push = False

        self._sync_table_from_scene(action.name)

    def _apply_action_inverse(self, action: _UndoAction):
        # Apply inverse effect (undo)
        self._suppress_undo_push = True
        try:
            if action.type == _ActionType.MOVE and action.before is not None:
                pt = self._items.get(action.name)
                if pt:
                    pt.setPos(action.before[0], action.before[1])

            elif action.type == _ActionType.ADD and action.row_data is not None:
                # undo add => delete
                name, _u, _v = action.row_data
                if name in self._items:
                    row = self._find_row(name)
                    if row is not None:
                        self.table.selectRow(row)
                        self._delete_selected()
                        # _delete_selected pushed undo; suppress it
                        if self._undo and self._undo[-1].type == _ActionType.DELETE and self._undo[-1].name == name:
                            self._undo.pop()

            elif action.type == _ActionType.DELETE and action.row_data is not None:
                # undo delete => re-add
                name, u, v = action.row_data
                if name not in self._items:
                    self._add_point(name, u, v, push_undo=False)
        finally:
            self._suppress_undo_push = False

        self._sync_table_from_scene(action.name)

    def _find_row(self, name: str) -> Optional[int]:
        for r in range(self.table.rowCount()):
            if self.table.item(r, 0).text() == name:
                return r
        return None

    def _sync_table_from_scene(self, name: str):
        # Update table row values from point position, if exists
        pt = self._items.get(name)
        if not pt:
            return
        row = self._find_row(name)
        if row is None:
            return
        self._suppress_table_changed = True
        self.table.item(row, 1).setText(f"{pt.pos().x():.3f}")
        self.table.item(row, 2).setText(f"{pt.pos().y():.3f}")
        self._suppress_table_changed = False

    # ---------------------------------------------------------------------
    # Save / Apply
    # ---------------------------------------------------------------------
    def _write_csv(self, out_path: str):
        out_path = str(Path(out_path))
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["point_name", "u", "v"])
            for r in range(self.table.rowCount()):
                name = self.table.item(r, 0).text().strip()
                u = float(self.table.item(r, 1).text())
                v = float(self.table.item(r, 2).text())
                w.writerow([name, f"{u:.6f}", f"{v:.6f}"])

    def _save_as(self):
        start = str(Path(self.csv_path).with_suffix(""))
        fn, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Save pixel pairs CSV",
            start + "_edited.csv",
            "CSV (*.csv)",
        )
        if not fn:
            return
        self._write_csv(fn)
        QtWidgets.QMessageBox.information(self, "Saved", f"Saved:\n{fn}")

    def _apply(self):
        p = Path(self.csv_path)
        out = str(p.with_name(p.stem + "_edited.csv"))
        self._write_csv(out)
        self.csv_path = out
        self.accept()

    def result_csv_path(self) -> str:
        return self.csv_path
