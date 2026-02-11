# src/cosmo/gui/image_viewer.py
from __future__ import annotations

from pathlib import Path

# Qt binding selection: PyQt5 → PySide6 → PyQt6
try:
    from PyQt5 import QtCore, QtGui, QtWidgets
except ImportError:  # pragma: no cover
    try:
        from PySide6 import QtCore, QtGui, QtWidgets
    except ImportError:  # pragma: no cover
        from PyQt6 import QtCore, QtGui, QtWidgets


def _qt_keep_aspect_ratio():
    if hasattr(QtCore.Qt, "KeepAspectRatio"):
        return QtCore.Qt.KeepAspectRatio
    return QtCore.Qt.AspectRatioMode.KeepAspectRatio


def _qt_smooth_transform():
    if hasattr(QtCore.Qt, "SmoothTransformation"):
        return QtCore.Qt.SmoothTransformation
    return QtCore.Qt.TransformationMode.SmoothTransformation


def _qt_scroll_hand_drag():
    if hasattr(QtWidgets.QGraphicsView, "DragMode"):
        return QtWidgets.QGraphicsView.DragMode.ScrollHandDrag
    return QtWidgets.QGraphicsView.ScrollHandDrag


def _qt_anchor_under_mouse():
    if hasattr(QtWidgets.QGraphicsView, "ViewportAnchor"):
        return QtWidgets.QGraphicsView.ViewportAnchor.AnchorUnderMouse
    return QtWidgets.QGraphicsView.AnchorUnderMouse


def _qt_anchor_center():
    if hasattr(QtWidgets.QGraphicsView, "ViewportAnchor"):
        return QtWidgets.QGraphicsView.ViewportAnchor.AnchorViewCenter
    return QtWidgets.QGraphicsView.AnchorViewCenter


class ImageViewerWindow(QtWidgets.QMainWindow):
    """Simple floating image viewer with zoom + pan."""

    def __init__(self, image_path: str, title: str = "Image viewer", parent=None):
        super().__init__(parent)
        self._path = str(image_path)
        self.setWindowTitle(title)
        self.resize(1200, 800)

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)

        # Toolbar row
        hb = QtWidgets.QHBoxLayout()
        self.btn_fit = QtWidgets.QPushButton("Fit")
        self.btn_100 = QtWidgets.QPushButton("100%")
        self.btn_200 = QtWidgets.QPushButton("200%")
        self.lbl_path = QtWidgets.QLabel("")
        self.lbl_path.setStyleSheet("color:#6b7280;")
        hb.addWidget(self.btn_fit)
        hb.addWidget(self.btn_100)
        hb.addWidget(self.btn_200)
        hb.addStretch(1)
        hb.addWidget(self.lbl_path)
        root.addLayout(hb)

        # Graphics view
        self.view = QtWidgets.QGraphicsView()
        self.view.setDragMode(_qt_scroll_hand_drag())
        try:
            self.view.setTransformationAnchor(_qt_anchor_under_mouse())
            self.view.setResizeAnchor(_qt_anchor_center())
        except Exception:
            pass

        self.scene = QtWidgets.QGraphicsScene(self.view)
        self.view.setScene(self.scene)
        root.addWidget(self.view, 1)

        self._pixmap_item = None
        self._pixmap = None

        self.btn_fit.clicked.connect(self.fit)
        self.btn_100.clicked.connect(lambda: self.set_zoom(1.0))
        self.btn_200.clicked.connect(lambda: self.set_zoom(2.0))

        # Mouse wheel zoom
        self.view.wheelEvent = self._wheel_zoom  # type: ignore[assignment]

        self.load(self._path)

    def load(self, image_path: str):
        p = Path(image_path)
        self._path = str(p)
        self.lbl_path.setText(str(p))
        if not p.is_file():
            self.scene.clear()
            self.scene.addText(f"File not found: {p}")
            return

        pm = QtGui.QPixmap(str(p))
        self.scene.clear()
        self._pixmap = pm
        self._pixmap_item = self.scene.addPixmap(pm)
        self.scene.setSceneRect(QtCore.QRectF(pm.rect()))
        self.fit()

    def fit(self):
        if self._pixmap is None or self._pixmap.isNull():
            return
        self.view.resetTransform()
        self.view.fitInView(self.scene.sceneRect(), _qt_keep_aspect_ratio())

    def set_zoom(self, scale: float):
        if self._pixmap is None or self._pixmap.isNull():
            return
        self.view.resetTransform()
        self.view.scale(scale, scale)

    def _wheel_zoom(self, event):
        try:
            delta = event.angleDelta().y()
        except Exception:
            delta = 0
        factor = 1.15 if delta > 0 else 1 / 1.15
        self.view.scale(factor, factor)
