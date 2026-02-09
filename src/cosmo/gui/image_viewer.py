# src/cosmo/gui/image_viewer.py
from __future__ import annotations

from pathlib import Path
from typing import Optional

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


class ImageViewerWindow(QtWidgets.QMainWindow):
    """
    Floating image viewer window with zoom/pan.
    Default open mode: FIT-to-window.
      - Wheel: zoom
      - Drag: pan (hand)
      - Buttons: Fit, 1:1, 50%, 200%
    """

    def __init__(self, title: str = "Image viewer", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(1100, 750)

        self._pixmap_item: Optional[QtWidgets.QGraphicsPixmapItem] = None

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)

        # Toolbar row
        hb = QtWidgets.QHBoxLayout()
        self.btn_fit = QtWidgets.QPushButton("Fit")
        self.btn_1to1 = QtWidgets.QPushButton("1:1")
        self.btn_50 = QtWidgets.QPushButton("50%")
        self.btn_200 = QtWidgets.QPushButton("200%")
        self.lbl_path = QtWidgets.QLabel("")
        self.lbl_path.setStyleSheet("color:#555;")

        self.lbl_path.setTextInteractionFlags(
            QtCore.Qt.TextSelectableByMouse
            if hasattr(QtCore.Qt, "TextSelectableByMouse")
            else QtCore.Qt.TextInteractionFlag.TextSelectableByMouse
        )

        hb.addWidget(self.btn_fit)
        hb.addWidget(self.btn_1to1)
        hb.addWidget(self.btn_50)
        hb.addWidget(self.btn_200)
        hb.addSpacing(10)
        hb.addWidget(self.lbl_path, 1)
        root.addLayout(hb)

        # Graphics view
        self.view = QtWidgets.QGraphicsView()
        self.view.setRenderHint(QtGui.QPainter.Antialiasing, True)
        self.view.setDragMode(QtWidgets.QGraphicsView.ScrollHandDrag)
        self.scene = QtWidgets.QGraphicsScene(self.view)
        self.view.setScene(self.scene)
        root.addWidget(self.view, 1)

        # Wire
        self.btn_fit.clicked.connect(self.fit_to_window)
        self.btn_1to1.clicked.connect(lambda: self.set_zoom(1.0))
        self.btn_50.clicked.connect(lambda: self.set_zoom(0.5))
        self.btn_200.clicked.connect(lambda: self.set_zoom(2.0))

        # Override wheel to zoom
        self.view.wheelEvent = self._wheel_zoom  # type: ignore[assignment]

    def load_image(self, path: str) -> None:
        p = Path(path)
        self.scene.clear()
        self._pixmap_item = None

        self.lbl_path.setText(str(p))

        if not p.is_file():
            self.scene.addText(f"File not found:\n{p}")
            return

        pm = QtGui.QPixmap(str(p))
        if pm.isNull():
            self.scene.addText(f"Could not load image:\n{p}")
            return

        self._pixmap_item = self.scene.addPixmap(pm)
        self.scene.setSceneRect(QtCore.QRectF(pm.rect()))
        self.fit_to_window()  # default to FIT

    def fit_to_window(self) -> None:
        if self._pixmap_item is None:
            return
        self.view.fitInView(self.scene.sceneRect(), _qt_keep_aspect_ratio())

    def set_zoom(self, factor: float) -> None:
        if self._pixmap_item is None:
            return
        self.view.resetTransform()
        self.view.scale(factor, factor)

    def _wheel_zoom(self, event) -> None:
        angle = event.angleDelta().y()
        factor = 1.15 if angle > 0 else 1 / 1.15
        self.view.scale(factor, factor)