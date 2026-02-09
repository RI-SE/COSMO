# src/cosmo/gui/main_window.py
from __future__ import annotations

import os
import sys
import subprocess
from dataclasses import asdict
from pathlib import Path
from typing import Optional, Tuple

# Qt binding selection: PyQt5 → PySide6 → PyQt6 (same as your current GUI). [1](https://risecloud-my.sharepoint.com/personal/anders_thorsen_ri_se/Documents/Microsoft%20Copilot%20Chat%20Files/convert_openlabel_to_omega.py)
try:
    from PyQt5 import QtCore, QtGui, QtWidgets
    _QT_API = "PyQt5"
except ImportError:  # pragma: no cover
    try:
        from PySide6 import QtCore, QtGui, QtWidgets
        _QT_API = "PySide6"
    except ImportError:  # pragma: no cover
        from PyQt6 import QtCore, QtGui, QtWidgets
        _QT_API = "PyQt6"

from cosmo.app.convert_app import ConvertConfig, ConvertResult
from cosmo.app.calibrate_app import CalibrateConfig, CalibrateResult
from cosmo.gui.workers import ConvertWorker, CalibrateWorker
from cosmo.gui.plotting import PlotController
from cosmo.gui.marker_converter import detect_odr_utm_with_offset, convert_visual_markers_latlon_to_odr_local


APP_NAME = "COSMO"
ORG_NAME = "SYNERGIES"
SETTINGS_GROUP = "cosmo_gui"


def _open_in_file_manager(path: str) -> None:
    p = Path(path)
    target = p if (p.exists() and p.is_dir()) else p.parent
    if not target.exists():
        return
    if sys.platform.startswith("win"):
        os.startfile(str(target))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(target)])
    else:
        subprocess.Popen(["xdg-open", str(target)])


def _qt_no_wrap():
    if hasattr(QtWidgets.QPlainTextEdit, "LineWrapMode"):
        return QtWidgets.QPlainTextEdit.LineWrapMode.NoWrap
    return QtWidgets.QPlainTextEdit.NoWrap


def _qsettings_ini_format():
    if hasattr(QtCore.QSettings, "Format"):
        return QtCore.QSettings.Format.IniFormat
    return QtCore.QSettings.IniFormat


def _qsettings_user_scope():
    if hasattr(QtCore.QSettings, "Scope"):
        return QtCore.QSettings.Scope.UserScope
    return QtCore.QSettings.UserScope


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} ({_QT_API})")
        self.setMinimumSize(980, 680)

        self._settings = QtCore.QSettings(_qsettings_ini_format(), _qsettings_user_scope(), ORG_NAME, APP_NAME)

        self._convert_worker: Optional[ConvertWorker] = None
        self._calib_worker: Optional[CalibrateWorker] = None

        self._last_run_dir: Optional[str] = None
        self._last_mcap_path: Optional[str] = None
        self._selected_mcap_path: Optional[str] = None

        self.plotter = PlotController()

        self._build_ui()
        self._wire_signals()
        self._load_settings()
        self._refresh_plot_buttons()

    # ---------------- UI construction ----------------

    def _build_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)

        header = QtWidgets.QLabel(
            "<h2 style='margin:0'>COSMO</h2>"
            "<div style='color:#555'>Convert OpenLABEL → Omega-Prime CSV and OSI/MCAP, and compute calibration</div>"
        )
        header.setTextFormat(QtCore.Qt.RichText if hasattr(QtCore.Qt, "RichText") else QtCore.Qt.TextFormat.RichText)
        root.addWidget(header)

        self.tabs = QtWidgets.QTabWidget()
        root.addWidget(self.tabs, 1)

        # ----- Run tab
        self.tab_run = QtWidgets.QWidget()
        self.tabs.addTab(self.tab_run, "Run")
        run_layout = QtWidgets.QVBoxLayout(self.tab_run)
        run_layout.setSpacing(10)

        gb_in = QtWidgets.QGroupBox("Inputs")
        run_layout.addWidget(gb_in)
        g = QtWidgets.QGridLayout(gb_in)
        g.setColumnStretch(1, 1)

        self.ed_openlabel = QtWidgets.QLineEdit()
        self.btn_openlabel = QtWidgets.QToolButton(text="…")
        g.addWidget(QtWidgets.QLabel("OpenLABEL (.json):"), 0, 0)
        g.addWidget(self.ed_openlabel, 0, 1)
        g.addWidget(self.btn_openlabel, 0, 2)

        self.ed_odr = QtWidgets.QLineEdit()
        self.btn_odr = QtWidgets.QToolButton(text="…")
        g.addWidget(QtWidgets.QLabel("OpenDRIVE (.xodr/.xml/.txt):"), 1, 0)
        g.addWidget(self.ed_odr, 1, 1)
        g.addWidget(self.btn_odr, 1, 2)

        self.ed_georef = QtWidgets.QLineEdit()
        self.btn_georef = QtWidgets.QToolButton(text="…")
        g.addWidget(QtWidgets.QLabel("ORBIT georef-data (.json):"), 2, 0)
        g.addWidget(self.ed_georef, 2, 1)
        g.addWidget(self.btn_georef, 2, 2)

        self.ed_calib = QtWidgets.QLineEdit()
        self.btn_calib = QtWidgets.QToolButton(text="…")
        g.addWidget(QtWidgets.QLabel("Calibration (legacy .json):"), 3, 0)
        g.addWidget(self.ed_calib, 3, 1)
        g.addWidget(self.btn_calib, 3, 2)

        # Alignment choice (simplified)
        self.rb_georef = QtWidgets.QRadioButton("Use ORBIT georef-data (recommended)")
        self.rb_calib = QtWidgets.QRadioButton("Use Calibration (legacy)")
        self.rb_none = QtWidgets.QRadioButton("No alignment file (pixel→meter fallback)")
        self.rb_georef.setChecked(True)
        vb = QtWidgets.QVBoxLayout()
        vb.addWidget(self.rb_georef)
        vb.addWidget(self.rb_calib)
        vb.addWidget(self.rb_none)
        gb_align = QtWidgets.QGroupBox("Alignment method")
        gb_align.setLayout(vb)
        g.addWidget(gb_align, 4, 1, 1, 2)

        # Output options (Option A + B)
        gb_out = QtWidgets.QGroupBox("Output (Option A: run folder; B: names from input)")
        run_layout.addWidget(gb_out)
        og = QtWidgets.QGridLayout(gb_out)
        og.setColumnStretch(1, 1)

        self.ed_runs_base = QtWidgets.QLineEdit()
        self.btn_runs_base = QtWidgets.QToolButton(text="…")
        og.addWidget(QtWidgets.QLabel("Runs base dir (optional):"), 0, 0)
        og.addWidget(self.ed_runs_base, 0, 1)
        og.addWidget(self.btn_runs_base, 0, 2)

        self.lbl_out_preview = QtWidgets.QLabel("")
        self.lbl_out_preview.setWordWrap(True)
        self.lbl_out_preview.setStyleSheet("color:#555;")
        og.addWidget(QtWidgets.QLabel("Preview:"), 1, 0)
        og.addWidget(self.lbl_out_preview, 1, 1, 1, 2)

        self.chk_csv = QtWidgets.QCheckBox("Write CSV")
        self.chk_csv.setChecked(True)
        self.chk_mcap = QtWidgets.QCheckBox("Write MCAP (requires betterosi)")
        self.chk_mcap.setChecked(True)
        og.addWidget(self.chk_csv, 2, 1)
        og.addWidget(self.chk_mcap, 3, 1)

        gb_opts = QtWidgets.QGroupBox("Options")
        run_layout.addWidget(gb_opts)
        opt = QtWidgets.QGridLayout(gb_opts)
        opt.setColumnStretch(1, 1)

        self.sp_fps = QtWidgets.QDoubleSpinBox()
        self.sp_fps.setRange(0.0, 240.0)
        self.sp_fps.setValue(0.0)
        self.sp_fps.setToolTip("0 = auto (use georef/calibration/default)")
        opt.addWidget(QtWidgets.QLabel("FPS override (0 = auto):"), 0, 0)
        opt.addWidget(self.sp_fps, 0, 1)

        self.chk_swap_xy = QtWidgets.QCheckBox("Swap X↔Y")
        self.chk_flip_x = QtWidgets.QCheckBox("Flip X")
        self.chk_flip_y = QtWidgets.QCheckBox("Flip Y")
        hb = QtWidgets.QHBoxLayout()
        hb.addWidget(self.chk_swap_xy)
        hb.addWidget(self.chk_flip_x)
        hb.addWidget(self.chk_flip_y)
        hb.addStretch(1)
        opt.addWidget(QtWidgets.QLabel("Alignment tweaks:"), 1, 0)
        opt.addLayout(hb, 1, 1)

        self.sp_dx = QtWidgets.QDoubleSpinBox()
        self.sp_dy = QtWidgets.QDoubleSpinBox()
        for sp in (self.sp_dx, self.sp_dy):
            sp.setRange(-1e6, 1e6)
            sp.setDecimals(3)
            sp.setSingleStep(0.1)
        hb2 = QtWidgets.QHBoxLayout()
        hb2.addWidget(QtWidgets.QLabel("DX"))
        hb2.addWidget(self.sp_dx)
        hb2.addSpacing(10)
        hb2.addWidget(QtWidgets.QLabel("DY"))
        hb2.addWidget(self.sp_dy)
        hb2.addStretch(1)
        opt.addWidget(QtWidgets.QLabel("XY offset (m):"), 2, 0)
        opt.addLayout(hb2, 2, 1)

        self.sp_yaw_deg = QtWidgets.QDoubleSpinBox()
        self.sp_yaw_deg.setRange(-360.0, 360.0)
        self.sp_yaw_deg.setDecimals(3)
        self.sp_yaw_deg.setValue(0.0)
        opt.addWidget(QtWidgets.QLabel("Yaw offset (deg CCW):"), 3, 0)
        opt.addWidget(self.sp_yaw_deg, 3, 1)

        ctrl = QtWidgets.QHBoxLayout()
        self.btn_run = QtWidgets.QPushButton("Run conversion")
        self.btn_cancel = QtWidgets.QPushButton("Cancel")
        self.btn_cancel.setEnabled(False)
        self.btn_open_run = QtWidgets.QPushButton("Open run folder")
        self.btn_open_run.setEnabled(False)
        ctrl.addWidget(self.btn_run)
        ctrl.addWidget(self.btn_cancel)
        ctrl.addStretch(1)
        ctrl.addWidget(self.btn_open_run)
        run_layout.addLayout(ctrl)

        self.log_run = QtWidgets.QPlainTextEdit()
        self.log_run.setReadOnly(True)
        self.log_run.setLineWrapMode(_qt_no_wrap())
        self.log_run.setMaximumBlockCount(20000)
        run_layout.addWidget(QtWidgets.QLabel("Log"))
        run_layout.addWidget(self.log_run, 1)

        # ----- Calibration tab
        self.tab_cal = QtWidgets.QWidget()
        self.tabs.addTab(self.tab_cal, "Calibration")
        cal_layout = QtWidgets.QVBoxLayout(self.tab_cal)
        cal_layout.setSpacing(10)

        note = QtWidgets.QLabel(
            "<b>Compute legacy Calibration JSON</b><br/>"
            "This computes a pixel→ground homography from pixel pairs and ground markers."
        )
        note.setWordWrap(True)
        note.setTextFormat(QtCore.Qt.RichText if hasattr(QtCore.Qt, "RichText") else QtCore.Qt.TextFormat.RichText)
        cal_layout.addWidget(note)

        gb_ci = QtWidgets.QGroupBox("Calibration inputs")
        cal_layout.addWidget(gb_ci)
        cg = QtWidgets.QGridLayout(gb_ci)
        cg.setColumnStretch(1, 1)

        self.ed_pixel_pairs = QtWidgets.QLineEdit()
        self.btn_pixel_pairs = QtWidgets.QToolButton(text="…")
        cg.addWidget(QtWidgets.QLabel("Pixel pairs CSV:"), 0, 0)
        cg.addWidget(self.ed_pixel_pairs, 0, 1)
        cg.addWidget(self.btn_pixel_pairs, 0, 2)

        self.ed_visual_markers = QtWidgets.QLineEdit()
        self.btn_visual_markers = QtWidgets.QToolButton(text="…")
        cg.addWidget(QtWidgets.QLabel("Visual markers CSV:"), 1, 0)
        cg.addWidget(self.ed_visual_markers, 1, 1)
        cg.addWidget(self.btn_visual_markers, 1, 2)

        self.btn_convert_markers = QtWidgets.QPushButton("Convert visual_markers.csv (lat/lon) → OpenDRIVE-local E/N")
        self.btn_convert_markers.setEnabled(False)
        cg.addWidget(self.btn_convert_markers, 2, 1, 1, 2)

        self.ed_cal_odr = QtWidgets.QLineEdit()
        self.btn_cal_odr = QtWidgets.QToolButton(text="…")
        cg.addWidget(QtWidgets.QLabel("OpenDRIVE (geoReference):"), 2, 0)
        cg.addWidget(self.ed_cal_odr, 2, 1)
        cg.addWidget(self.btn_cal_odr, 2, 2)

        self.ed_cal_image = QtWidgets.QLineEdit()
        self.btn_cal_image = QtWidgets.QToolButton(text="…")
        cg.addWidget(QtWidgets.QLabel("Image (optional):"), 3, 0)
        cg.addWidget(self.ed_cal_image, 3, 1)
        cg.addWidget(self.btn_cal_image, 3, 2)

        self.ed_cal_openlabel = QtWidgets.QLineEdit()
        self.btn_cal_openlabel = QtWidgets.QToolButton(text="…")
        cg.addWidget(QtWidgets.QLabel("OpenLABEL (optional validation):"), 4, 0)
        cg.addWidget(self.ed_cal_openlabel, 4, 1)
        cg.addWidget(self.btn_cal_openlabel, 4, 2)

        gb_cp = QtWidgets.QGroupBox("Parameters")
        cal_layout.addWidget(gb_cp)
        pg = QtWidgets.QGridLayout(gb_cp)
        pg.setColumnStretch(1, 1)

        self.sp_cal_fps = QtWidgets.QDoubleSpinBox()
        self.sp_cal_fps.setRange(0.0, 240.0)
        self.sp_cal_fps.setValue(30.0)
        pg.addWidget(QtWidgets.QLabel("FPS:"), 0, 0)
        pg.addWidget(self.sp_cal_fps, 0, 1)

        self.sp_cal_w = QtWidgets.QSpinBox()
        self.sp_cal_w.setRange(1, 20000)
        self.sp_cal_w.setValue(3840)
        self.sp_cal_h = QtWidgets.QSpinBox()
        self.sp_cal_h.setRange(1, 20000)
        self.sp_cal_h.setValue(2160)
        hbwh = QtWidgets.QHBoxLayout()
        hbwh.addWidget(QtWidgets.QLabel("W"))
        hbwh.addWidget(self.sp_cal_w)
        hbwh.addSpacing(10)
        hbwh.addWidget(QtWidgets.QLabel("H"))
        hbwh.addWidget(self.sp_cal_h)
        hbwh.addStretch(1)
        pg.addWidget(QtWidgets.QLabel("Image size (px):"), 1, 0)
        pg.addLayout(hbwh, 1, 1)

        self.sp_cal_thresh = QtWidgets.QDoubleSpinBox()
        self.sp_cal_thresh.setRange(0.01, 10.0)
        self.sp_cal_thresh.setDecimals(3)
        self.sp_cal_thresh.setValue(0.50)
        pg.addWidget(QtWidgets.QLabel("RANSAC thresh (m):"), 2, 0)
        pg.addWidget(self.sp_cal_thresh, 2, 1)



        # Optional origin override (lat0/lon0) for ENU conversion

        self.chk_origin_override = QtWidgets.QCheckBox("Override origin (lat0/lon0)")

        self.sp_origin_lat0 = QtWidgets.QDoubleSpinBox()

        self.sp_origin_lon0 = QtWidgets.QDoubleSpinBox()

        self.sp_origin_lat0.setRange(-90.0, 90.0)

        self.sp_origin_lon0.setRange(-180.0, 180.0)

        self.sp_origin_lat0.setDecimals(10)

        self.sp_origin_lon0.setDecimals(10)

        self.sp_origin_lat0.setSingleStep(0.00001)

        self.sp_origin_lon0.setSingleStep(0.00001)

        self.sp_origin_lat0.setEnabled(False)

        self.sp_origin_lon0.setEnabled(False)


        hb_origin = QtWidgets.QHBoxLayout()

        hb_origin.addWidget(self.chk_origin_override)

        hb_origin.addSpacing(10)

        hb_origin.addWidget(QtWidgets.QLabel("lat0"))

        hb_origin.addWidget(self.sp_origin_lat0)

        hb_origin.addSpacing(10)

        hb_origin.addWidget(QtWidgets.QLabel("lon0"))

        hb_origin.addWidget(self.sp_origin_lon0)

        hb_origin.addStretch(1)

        pg.addWidget(QtWidgets.QLabel("ENU origin:"), 3, 0)

        pg.addLayout(hb_origin, 3, 1)
        cal_ctrl = QtWidgets.QHBoxLayout()
        self.btn_cal_run = QtWidgets.QPushButton("Compute calibration")
        self.btn_cal_cancel = QtWidgets.QPushButton("Cancel")
        self.btn_cal_cancel.setEnabled(False)
        self.btn_cal_use = QtWidgets.QPushButton("Use calibration in Run tab")
        self.btn_cal_use.setEnabled(False)
        cal_ctrl.addWidget(self.btn_cal_run)
        cal_ctrl.addWidget(self.btn_cal_cancel)
        cal_ctrl.addStretch(1)
        cal_ctrl.addWidget(self.btn_cal_use)
        cal_layout.addLayout(cal_ctrl)

        self.lbl_cal_result = QtWidgets.QLabel("")
        self.lbl_cal_result.setWordWrap(True)
        cal_layout.addWidget(self.lbl_cal_result)

        self.log_cal = QtWidgets.QPlainTextEdit()
        self.log_cal.setReadOnly(True)
        self.log_cal.setLineWrapMode(_qt_no_wrap())
        self.log_cal.setMaximumBlockCount(20000)
        cal_layout.addWidget(QtWidgets.QLabel("Calibration log"))
        cal_layout.addWidget(self.log_cal, 1)

        # ----- Plot tab
        self.tab_plot = QtWidgets.QWidget()
        self.tabs.addTab(self.tab_plot, "Plot")
        plot_layout = QtWidgets.QVBoxLayout(self.tab_plot)
        plot_layout.setSpacing(8)

        self.lbl_plot_status = QtWidgets.QLabel("No plot yet. Create an MCAP or browse one.")
        self.lbl_plot_status.setWordWrap(True)
        plot_layout.addWidget(self.lbl_plot_status)

        hb_plot = QtWidgets.QHBoxLayout()
        self.btn_browse_mcap = QtWidgets.QPushButton("Browse MCAP…")
        self.btn_plot_selected = QtWidgets.QPushButton("Plot selected")
        self.btn_plot_selected.setEnabled(False)
        self.btn_plot_last = QtWidgets.QPushButton("Plot last produced")
        self.btn_plot_last.setEnabled(False)
        hb_plot.addWidget(self.btn_browse_mcap)
        hb_plot.addWidget(self.btn_plot_selected)
        hb_plot.addWidget(self.btn_plot_last)
        hb_plot.addStretch(1)
        plot_layout.addLayout(hb_plot)

        # Plot options
        self.chk_equal_axes = QtWidgets.QCheckBox("Lock X/Y scale (equal axes)")
        self.chk_equal_axes.setChecked(True)
        self.chk_undock_plot = QtWidgets.QCheckBox("Undock plot (floating dock)")
        self.chk_undock_plot.setChecked(False)
        plot_layout.addWidget(self.chk_equal_axes)
        plot_layout.addWidget(self.chk_undock_plot)

        # Altair controls (simplified)
        gb_alt = QtWidgets.QGroupBox("Altair (interactive, browser)")
        plot_layout.addWidget(gb_alt)
        ag = QtWidgets.QGridLayout(gb_alt)
        ag.setColumnStretch(1, 1)

        self.cmb_metric = QtWidgets.QComboBox()
        self.cmb_metric.setEditable(True)
        self.cmb_metric.addItem("vel_y")
        self.sp_obj_id = QtWidgets.QSpinBox()
        self.sp_obj_id.setRange(0, 10**9)
        self.sp_start = QtWidgets.QSpinBox()
        self.sp_start.setRange(0, 10**9)
        self.sp_end = QtWidgets.QSpinBox()
        self.sp_end.setRange(0, 10**9)
        self.sp_end.setValue(400)

        self.chk_altair_large = QtWidgets.QCheckBox("Allow large Altair datasets")
        self.chk_altair_large.setChecked(True)
        self.btn_refresh_metrics = QtWidgets.QPushButton("Refresh metrics")
        self.btn_plot_altair = QtWidgets.QPushButton("Plot Altair (browser)")

        ag.addWidget(QtWidgets.QLabel("Metric:"), 0, 0)
        ag.addWidget(self.cmb_metric, 0, 1)
        ag.addWidget(QtWidgets.QLabel("Object id:"), 0, 2)
        ag.addWidget(self.sp_obj_id, 0, 3)
        ag.addWidget(self.btn_refresh_metrics, 0, 4)

        ag.addWidget(QtWidgets.QLabel("Start frame:"), 1, 0)
        ag.addWidget(self.sp_start, 1, 1)
        ag.addWidget(QtWidgets.QLabel("End frame:"), 1, 2)
        ag.addWidget(self.sp_end, 1, 3)

        ag.addWidget(self.btn_plot_altair, 2, 0, 1, 5)
        ag.addWidget(self.chk_altair_large, 3, 0, 1, 5)

        # Recording info
        gb_info = QtWidgets.QGroupBox("Recording info")
        plot_layout.addWidget(gb_info, 1)
        vi = QtWidgets.QVBoxLayout(gb_info)
        hb_info = QtWidgets.QHBoxLayout()
        self.btn_info_load = QtWidgets.QPushButton("Load info")
        self.btn_info_clear = QtWidgets.QPushButton("Clear")
        hb_info.addWidget(self.btn_info_load)
        hb_info.addWidget(self.btn_info_clear)
        hb_info.addStretch(1)
        vi.addLayout(hb_info)
        self.txt_info = QtWidgets.QPlainTextEdit()
        self.txt_info.setReadOnly(True)
        self.txt_info.setMaximumBlockCount(5000)
        vi.addWidget(self.txt_info, 1)

        # Container for embedded matplotlib plot
        self.plot_container = QtWidgets.QWidget()
        self.plot_container_layout = QtWidgets.QVBoxLayout(self.plot_container)
        self.plot_container_layout.setContentsMargins(0, 0, 0, 0)
        self.plot_container_layout.setSpacing(0)
        plot_layout.addWidget(self.plot_container, 2)

        # ----- Settings tab (simplified)
        self.tab_settings = QtWidgets.QWidget()
        self.tabs.addTab(self.tab_settings, "Settings")
        s = QtWidgets.QFormLayout(self.tab_settings)

        self.chk_autoscroll = QtWidgets.QCheckBox("Auto-scroll logs")
        self.chk_autoscroll.setChecked(True)

        self.ed_runs_base_settings = QtWidgets.QLineEdit()
        self.btn_runs_base_settings = QtWidgets.QToolButton(text="…")

        hb_runs = QtWidgets.QHBoxLayout()
        hb_runs.addWidget(self.ed_runs_base_settings, 1)
        hb_runs.addWidget(self.btn_runs_base_settings)

        s.addRow("Runs base directory (optional):", hb_runs)
        s.addRow("", self.chk_autoscroll)

        self.btn_reset_settings = QtWidgets.QPushButton("Reset settings")
        s.addRow("", self.btn_reset_settings)

        self.status = QtWidgets.QStatusBar()
        self.setStatusBar(self.status)
        self.lbl_status = QtWidgets.QLabel("Ready")
        self.status.addPermanentWidget(self.lbl_status)

    # ---------------- Wiring ----------------

    def _wire_signals(self):
        # file pickers
        self.btn_openlabel.clicked.connect(self._pick_openlabel)
        self.btn_odr.clicked.connect(self._pick_odr)
        self.btn_georef.clicked.connect(self._pick_georef)
        self.btn_calib.clicked.connect(self._pick_calib)

        self.btn_runs_base.clicked.connect(self._pick_runs_base)
        self.btn_runs_base_settings.clicked.connect(self._pick_runs_base_settings)

        # keep settings and run tab in sync for runs base
        self.ed_runs_base.textChanged.connect(self.ed_runs_base_settings.setText)
        self.ed_runs_base_settings.textChanged.connect(self.ed_runs_base.setText)

        # output preview updates
        self.ed_openlabel.textChanged.connect(self._update_output_preview)
        self.chk_csv.stateChanged.connect(lambda _s: self._update_output_preview())
        self.chk_mcap.stateChanged.connect(lambda _s: self._update_output_preview())
        self.ed_runs_base.textChanged.connect(self._update_output_preview)

        # run conversion
        self.btn_run.clicked.connect(self._run_convert)
        self.btn_cancel.clicked.connect(self._cancel_convert)
        self.btn_open_run.clicked.connect(self._open_last_run)

        # calibration inputs
        self.btn_pixel_pairs.clicked.connect(self._pick_pixel_pairs)
        self.btn_visual_markers.clicked.connect(self._pick_visual_markers)
        self.btn_convert_markers.clicked.connect(self._convert_markers_to_odr_local)
        self.btn_cal_odr.clicked.connect(self._pick_cal_odr)
        self.btn_cal_image.clicked.connect(self._pick_cal_image)
        self.btn_cal_openlabel.clicked.connect(self._pick_cal_openlabel)

        # run calibration
        self.btn_cal_run.clicked.connect(self._run_calibrate)
        self.btn_cal_cancel.clicked.connect(self._cancel_calibrate)
        self.btn_cal_use.clicked.connect(self._use_generated_calibration)

        # origin override enable/disable
        self.chk_origin_override.toggled.connect(self.sp_origin_lat0.setEnabled)
        self.chk_origin_override.toggled.connect(self.sp_origin_lon0.setEnabled)

        # plot tab
        self.btn_browse_mcap.clicked.connect(self._browse_mcap)
        self.btn_plot_selected.clicked.connect(self._plot_selected)
        self.btn_plot_last.clicked.connect(self._plot_last)
        self.btn_info_load.clicked.connect(self._load_info)
        self.btn_info_clear.clicked.connect(lambda: self.txt_info.setPlainText(""))
        self.btn_refresh_metrics.clicked.connect(self._refresh_metrics)
        self.btn_plot_altair.clicked.connect(self._plot_altair)

        self.chk_equal_axes.toggled.connect(lambda _b: self._replot_if_needed())
        self.chk_undock_plot.toggled.connect(lambda _b: self._apply_plot_dock())

        # settings
        self.btn_reset_settings.clicked.connect(self._reset_settings)

    # ---------------- Helpers: logging ----------------

    def _log(self, widget: QtWidgets.QPlainTextEdit, line: str):
        widget.appendPlainText(line)
        if self.chk_autoscroll.isChecked():
            sb = widget.verticalScrollBar()
            sb.setValue(sb.maximum())

    def _log_run_line(self, line: str):
        self._log(self.log_run, line)

    def _log_cal_line(self, line: str):
        self._log(self.log_cal, line)

    # ---------------- File pickers ----------------

    def _last_dir(self) -> str:
        self._settings.beginGroup(SETTINGS_GROUP)
        d = self._settings.value("last_dir", str(Path.home()))
        self._settings.endGroup()
        return str(d)

    def _set_last_dir(self, path: str):
        p = Path(path)
        d = str(p.parent if p.is_file() else p)
        self._settings.beginGroup(SETTINGS_GROUP)
        self._settings.setValue("last_dir", d)
        self._settings.endGroup()

    def _pick_file(self, title: str, filt: str) -> str:
        start = self._last_dir()
        fn, _ = QtWidgets.QFileDialog.getOpenFileName(self, title, start, filt)
        if fn:
            self._set_last_dir(fn)
        return fn

    def _pick_dir(self, title: str) -> str:
        start = self._last_dir()
        d = QtWidgets.QFileDialog.getExistingDirectory(self, title, start)
        if d:
            self._set_last_dir(d)
        return d

    def _pick_openlabel(self):
        fn = self._pick_file("Select OpenLABEL JSON", "JSON (*.json);;All files (*.*)")
        if fn:
            self.ed_openlabel.setText(fn)

    def _pick_odr(self):
        fn = self._pick_file("Select OpenDRIVE", "OpenDRIVE (*.xodr *.xml *.txt);;All files (*.*)")
        if fn:
            self.ed_odr.setText(fn)
            # also sync into calibration tab unless already set
            if not self.ed_cal_odr.text().strip():
                self.ed_cal_odr.setText(fn)

    def _pick_georef(self):
        fn = self._pick_file("Select ORBIT georef-data JSON", "JSON (*.json);;All files (*.*)")
        if fn:
            self.ed_georef.setText(fn)
            self.rb_georef.setChecked(True)

    def _pick_calib(self):
        fn = self._pick_file("Select calibration JSON", "JSON (*.json);;All files (*.*)")
        if fn:
            self.ed_calib.setText(fn)
            self.rb_calib.setChecked(True)

    def _pick_runs_base(self):
        d = self._pick_dir("Select runs base directory")
        if d:
            self.ed_runs_base.setText(d)

    def _pick_runs_base_settings(self):
        d = self._pick_dir("Select runs base directory")
        if d:
            self.ed_runs_base_settings.setText(d)

    # calibration pickers
    def _pick_pixel_pairs(self):
        fn = self._pick_file("Select pixel pairs CSV", "CSV (*.csv);;All files (*.*)")
        if fn:
            self.ed_pixel_pairs.setText(fn)

    def _pick_visual_markers(self):
        fn = self._pick_file("Select visual markers CSV", "CSV (*.csv);;All files (*.*)")
        if fn:
            self.ed_visual_markers.setText(fn)

    def _pick_cal_odr(self):
        fn = self._pick_file("Select OpenDRIVE", "OpenDRIVE (*.xodr *.xml *.txt);;All files (*.*)")
        if fn:
            self.ed_cal_odr.setText(fn)

    def _pick_cal_image(self):
        fn = self._pick_file("Select image", "Images (*.png *.jpg *.jpeg *.bmp);;All files (*.*)")
        if fn:
            self.ed_cal_image.setText(fn)

    def _pick_cal_openlabel(self):
        fn = self._pick_file("Select OpenLABEL JSON", "JSON (*.json);;All files (*.*)")
        if fn:
            self.ed_cal_openlabel.setText(fn)

    # ---------------- Run tab: conversion ----------------

    def _update_output_preview(self):
        raw = self.ed_openlabel.text().strip()
        if not raw:
            self.lbl_out_preview.setText("Select an OpenLABEL file to preview outputs.")
            return
        stem = Path(raw).stem.lower().replace(" ", "_")
        files = []
        if self.chk_csv.isChecked():
            files.append(f"{stem}.csv")
        if self.chk_mcap.isChecked():
            files.append(f"{stem}.mcap")
        base = self.ed_runs_base.text().strip()
        base_txt = base if base else "<default runs/>"
        self.lbl_out_preview.setText(
            f"Runs base: {base_txt}\n"
            f"Run folder: <timestamp>_convert_{stem}/\n"
            f"Outputs: outputs/{', '.join(files) if files else '(none)'}"
        )

    def _collect_convert_config(self) -> Optional[ConvertConfig]:
        openlabel = self.ed_openlabel.text().strip()
        if not openlabel:
            QtWidgets.QMessageBox.warning(self, "Missing input", "Please select an OpenLABEL JSON.")
            return None

        odr = self.ed_odr.text().strip() or None
        georef = self.ed_georef.text().strip() or None
        calib = self.ed_calib.text().strip() or None

        if self.rb_georef.isChecked():
            if not georef:
                QtWidgets.QMessageBox.warning(self, "Missing input", "You selected ORBIT georef-data but did not provide a file.")
                return None
            calib = None
        elif self.rb_calib.isChecked():
            if not calib:
                QtWidgets.QMessageBox.warning(self, "Missing input", "You selected Calibration but did not provide a file.")
                return None
            georef = None
        else:
            georef = None
            calib = None

        fps = float(self.sp_fps.value())
        fps_val = fps if fps > 0.0 else None

        runs_base = self.ed_runs_base.text().strip() or None

        cfg = ConvertConfig(
            openlabel=openlabel,
            opendrive=odr,
            georef_data=georef,
            calibration=calib,
            fps=fps_val,
            write_csv=self.chk_csv.isChecked(),
            write_mcap=self.chk_mcap.isChecked(),
            swap_xy=self.chk_swap_xy.isChecked(),
            flip_x=self.chk_flip_x.isChecked(),
            flip_y=self.chk_flip_y.isChecked(),
            xy_offset=(float(self.sp_dx.value()), float(self.sp_dy.value())),
            yaw_offset_deg=float(self.sp_yaw_deg.value()),
            out_dir=runs_base,
            run_name=None,
        )
        if not cfg.write_csv and not cfg.write_mcap:
            QtWidgets.QMessageBox.warning(self, "No outputs", "Select at least one output: CSV and/or MCAP.")
            return None
        return cfg

    def _run_convert(self):
        if self._convert_worker is not None:
            QtWidgets.QMessageBox.information(self, "Busy", "A conversion is already running.")
            return

        cfg = self._collect_convert_config()
        if cfg is None:
            return

        self._save_settings()
        self.log_run.appendPlainText("\n" + "=" * 80)
        self.log_run.appendPlainText("Starting conversion…")

        self.btn_run.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.lbl_status.setText("Running…")

        self._convert_worker = ConvertWorker(cfg)
        self._convert_worker.line.connect(self._log_run_line)
        self._convert_worker.finished.connect(self._on_convert_finished)
        self._convert_worker.start()

    def _cancel_convert(self):
        if self._convert_worker is None:
            return
        self._log_run_line("[GUI] Cancel requested (will stop after current step if supported).")
        self._convert_worker.cancel()
        self.btn_cancel.setEnabled(False)

    def _on_convert_finished(self, obj):
        self.btn_run.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self._convert_worker = None

        if isinstance(obj, Exception):
            self.lbl_status.setText("Failed")
            QtWidgets.QMessageBox.critical(self, "Conversion failed", str(obj))
            return

        result: ConvertResult = obj
        self._last_run_dir = result.run_dir
        self.btn_open_run.setEnabled(True)
        self.lbl_status.setText("Ready")

        self._log_run_line(f"[GUI] Run folder: {result.run_dir}")
        if result.csv_path:
            self._log_run_line(f"[GUI] CSV: {result.csv_path}")
        if result.mcap_path:
            self._log_run_line(f"[GUI] MCAP: {result.mcap_path}")
            self._last_mcap_path = result.mcap_path

        if result.notes:
            for n in result.notes:
                self._log_run_line(f"[NOTE] {n}")

        self._refresh_plot_buttons()

    def _open_last_run(self):
        if not self._last_run_dir:
            return
        _open_in_file_manager(self._last_run_dir)

    # ---------------- Calibration tab ----------------

    def _collect_calibrate_config(self) -> Optional[CalibrateConfig]:
        pixel_pairs = self.ed_pixel_pairs.text().strip()
        visual_markers = self.ed_visual_markers.text().strip()
        odr = self.ed_cal_odr.text().strip() or self.ed_odr.text().strip()

        if not pixel_pairs or not visual_markers or not odr:
            QtWidgets.QMessageBox.warning(self, "Missing input", "Please provide pixel pairs, visual markers, and OpenDRIVE.")
            return None

        cfg = CalibrateConfig(
            pixel_pairs=pixel_pairs,
            visual_markers=visual_markers,
            opendrive=odr,
            image=self.ed_cal_image.text().strip() or None,
            openlabel=self.ed_cal_openlabel.text().strip() or None,
            fps=float(self.sp_cal_fps.value()),
            image_width=int(self.sp_cal_w.value()),
            image_height=int(self.sp_cal_h.value()),
            ransac_thresh_m=float(self.sp_cal_thresh.value()),
            origin_lat0=float(self.sp_origin_lat0.value()) if self.chk_origin_override.isChecked() else None,
            origin_lon0=float(self.sp_origin_lon0.value()) if self.chk_origin_override.isChecked() else None,
            out_dir=self.ed_runs_base.text().strip() or None,  # reuse runs base
            run_name=None,
        )
        return cfg

    def _run_calibrate(self):
        if self._calib_worker is not None:
            QtWidgets.QMessageBox.information(self, "Busy", "A calibration run is already running.")
            return

        cfg = self._collect_calibrate_config()
        if cfg is None:
            return

        self._save_settings()
        self.log_cal.appendPlainText("\n" + "=" * 80)
        self.log_cal.appendPlainText("Starting calibration…")

        self.btn_cal_run.setEnabled(False)
        self.btn_cal_cancel.setEnabled(True)
        self.btn_cal_use.setEnabled(False)
        self.lbl_cal_result.setText("")

        self._calib_worker = CalibrateWorker(cfg)
        self._calib_worker.line.connect(self._log_cal_line)
        self._calib_worker.finished.connect(self._on_calibrate_finished)
        self._calib_worker.start()

    def _cancel_calibrate(self):
        if self._calib_worker is None:
            return
        self._log_cal_line("[GUI] Cancel requested (will stop after current step if supported).")
        self._calib_worker.cancel()
        self.btn_cal_cancel.setEnabled(False)

    def _on_calibrate_finished(self, obj):
        self.btn_cal_run.setEnabled(True)
        self.btn_cal_cancel.setEnabled(False)
        self._calib_worker = None

        if isinstance(obj, Exception):
            self.lbl_cal_result.setStyleSheet("color:#b91c1c; font-weight:600;")
            self.lbl_cal_result.setText("❌ Calibration failed (see log).")
            QtWidgets.QMessageBox.critical(self, "Calibration failed", str(obj))
            return

        result: CalibrateResult = obj
        self._last_run_dir = result.run_dir
        self.btn_open_run.setEnabled(True)

        self.lbl_cal_result.setStyleSheet("color:#065f46; font-weight:600;")
        self.lbl_cal_result.setText(f"✅ Calibration written: {result.calibration_json_path}")
        self.btn_cal_use.setEnabled(True)

        self._log_cal_line(f"[GUI] Run folder: {result.run_dir}")
        self._log_cal_line(f"[GUI] Calibration: {result.calibration_json_path}")
        if result.summary_json_path:
            self._log_cal_line(f"[GUI] Summary: {result.summary_json_path}")
        if result.notes:
            for n in result.notes:
                self._log_cal_line(f"[NOTE] {n}")

    def _use_generated_calibration(self):
        # When using app layer, calibration result path is shown in label; parse it.
        txt = self.lbl_cal_result.text()
        # naive extraction: last token after ": "
        if ": " in txt:
            path = txt.split(": ", 1)[1].strip()
        else:
            return
        if Path(path).is_file():
            self.ed_calib.setText(path)
            self.rb_calib.setChecked(True)
            self.tabs.setCurrentWidget(self.tab_run)

    # ---------------- Plot tab ----------------

    def _refresh_plot_buttons(self):
        has_sel = self._selected_mcap_path is not None and Path(self._selected_mcap_path).is_file()
        self.btn_plot_selected.setEnabled(bool(has_sel))
        has_last = self._last_mcap_path is not None and Path(self._last_mcap_path).is_file()
        self.btn_plot_last.setEnabled(bool(has_last))

    def _browse_mcap(self):
        fn = self._pick_file("Select MCAP file", "MCAP (*.mcap);;All files (*.*)")
        if not fn:
            return
        self._selected_mcap_path = fn
        self.lbl_plot_status.setText(f"Selected: {fn}")
        self._refresh_plot_buttons()

    def _current_mcap(self) -> Optional[str]:
        if self._selected_mcap_path and Path(self._selected_mcap_path).is_file():
            return self._selected_mcap_path
        if self._last_mcap_path and Path(self._last_mcap_path).is_file():
            return self._last_mcap_path
        return None

    def _clear_plot_container(self):
        for i in reversed(range(self.plot_container_layout.count())):
            w = self.plot_container_layout.itemAt(i).widget()
            if w is not None:
                w.setParent(None)

    def _apply_plot_dock(self):
        # Simplified: just keep embedded in the Plot tab for now.
        # You can add a QDockWidget here later if desired.
        # Checkbox is stored as preference only.
        pass

    def _plot_selected(self):
        if not self._selected_mcap_path:
            return
        self._plot_mcap(self._selected_mcap_path)

    def _plot_last(self):
        if not self._last_mcap_path:
            return
        self._plot_mcap(self._last_mcap_path)

    def _plot_mcap(self, mcap_path: str):
        if not self.plotter.is_available():
            QtWidgets.QMessageBox.critical(self, "omega_prime missing", "Install 'omega-prime' to plot MCAP files.")
            return

        rec = self.plotter.load_recording(mcap_path)
        if rec is None:
            QtWidgets.QMessageBox.critical(self, "Failed to load", f"Could not load recording:\n{mcap_path}")
            return

        try:
            fig = self.plotter.embed_plot(rec, equal_axes=self.chk_equal_axes.isChecked())
            canvas, toolbar = self.plotter.make_canvas_and_toolbar(fig, self)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Plot failed", str(e))
            return

        self._clear_plot_container()
        self.plot_container_layout.addWidget(toolbar)
        self.plot_container_layout.addWidget(canvas, 1)

        self.lbl_plot_status.setText(f"Showing: {mcap_path}")
        self._last_mcap_path = mcap_path
        self._refresh_metrics()

    def _replot_if_needed(self):
        cur = self._current_mcap()
        if cur and Path(cur).is_file():
            self._plot_mcap(cur)

    def _load_info(self):
        cur = self._current_mcap()
        if not cur:
            self.txt_info.setPlainText("No MCAP selected. Create one or browse an MCAP first.")
            return
        rec = self.plotter.load_recording(cur)
        if rec is None:
            self.txt_info.setPlainText(f"Failed to load: {cur}")
            return
        info = self.plotter.recording_info(rec)
        self.txt_info.setPlainText(
            f"MCAP: {cur}\n\n"
            f"r.map:\n{info.map_repr}\n\n"
            f"Object IDs:\n{info.object_ids}\n"
        )
        # Adjust object id range
        if info.object_ids:
            self.sp_obj_id.setRange(int(info.object_ids[0]), int(info.object_ids[-1]))
            if int(self.sp_obj_id.value()) not in info.object_ids:
                self.sp_obj_id.setValue(int(info.object_ids[0]))

    def _refresh_metrics(self):
        cur = self._current_mcap()
        if not cur:
            return
        rec = self.plotter.load_recording(cur)
        if rec is None:
            return
        obj_id = int(self.sp_obj_id.value())
        cols = list(self.plotter.metric_columns_for_object(rec, obj_id))
        current = self.cmb_metric.currentText().strip() if self.cmb_metric.currentText() else ""
        self.cmb_metric.blockSignals(True)
        self.cmb_metric.clear()
        self.cmb_metric.addItems(cols)
        if current and current in cols:
            self.cmb_metric.setCurrentText(current)
        elif "vel_y" in cols:
            self.cmb_metric.setCurrentText("vel_y")
        self.cmb_metric.blockSignals(False)

    def _plot_altair(self):
        cur = self._current_mcap()
        if not cur:
            QtWidgets.QMessageBox.information(self, "No MCAP", "No MCAP selected.")
            return
        rec = self.plotter.load_recording(cur)
        if rec is None:
            QtWidgets.QMessageBox.critical(self, "Failed to load", f"Could not load:\n{cur}")
            return
        try:
            self.plotter.plot_altair_browser(
                rec,
                metric=self.cmb_metric.currentText().strip() or "vel_y",
                obj_id=int(self.sp_obj_id.value()),
                start_frame=int(self.sp_start.value()),
                end_frame=int(self.sp_end.value()),
                allow_large=self.chk_altair_large.isChecked(),
            )
            self.lbl_plot_status.setText(f"Altair opened in browser for: {cur}")
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Altair plot failed", str(e))

    # ---------------- Settings persistence ----------------

    def _load_settings(self):
        self._settings.beginGroup(SETTINGS_GROUP)

        self.ed_openlabel.setText(self._settings.value("openlabel", ""))
        self.ed_odr.setText(self._settings.value("odr", ""))
        self.ed_georef.setText(self._settings.value("georef", ""))
        self.ed_calib.setText(self._settings.value("calib", ""))

        runs_base = self._settings.value("runs_base", "")
        self.ed_runs_base.setText(runs_base)
        self.ed_runs_base_settings.setText(runs_base)

        self.chk_autoscroll.setChecked(self._settings.value("autoscroll", True, type=bool))
        self.chk_csv.setChecked(self._settings.value("write_csv", True, type=bool))
        self.chk_mcap.setChecked(self._settings.value("write_mcap", True, type=bool))

        method = str(self._settings.value("alignment_method", "georef")).lower()
        if method == "calibration":
            self.rb_calib.setChecked(True)
        elif method == "none":
            self.rb_none.setChecked(True)
        else:
            self.rb_georef.setChecked(True)

        self.sp_fps.setValue(float(self._settings.value("fps", 0.0)))
        self.chk_swap_xy.setChecked(self._settings.value("swap_xy", False, type=bool))
        self.chk_flip_x.setChecked(self._settings.value("flip_x", False, type=bool))
        self.chk_flip_y.setChecked(self._settings.value("flip_y", False, type=bool))
        self.sp_dx.setValue(float(self._settings.value("dx", 0.0)))
        self.sp_dy.setValue(float(self._settings.value("dy", 0.0)))
        self.sp_yaw_deg.setValue(float(self._settings.value("yaw_deg", 0.0)))

        # Plot settings
        self.chk_equal_axes.setChecked(self._settings.value("plot_equal_axes", True, type=bool))
        self.chk_undock_plot.setChecked(self._settings.value("plot_undock", False, type=bool))
        self.chk_altair_large.setChecked(self._settings.value("altair_large", True, type=bool))

        self._settings.endGroup()

        self._update_output_preview()

    def _save_settings(self):
        self._settings.beginGroup(SETTINGS_GROUP)

        self._settings.setValue("openlabel", self.ed_openlabel.text().strip())
        self._settings.setValue("odr", self.ed_odr.text().strip())
        self._settings.setValue("georef", self.ed_georef.text().strip())
        self._settings.setValue("calib", self.ed_calib.text().strip())

        self._settings.setValue("runs_base", self.ed_runs_base.text().strip())
        self._settings.setValue("autoscroll", self.chk_autoscroll.isChecked())

        method = "georef" if self.rb_georef.isChecked() else ("calibration" if self.rb_calib.isChecked() else "none")
        self._settings.setValue("alignment_method", method)

        self._settings.setValue("write_csv", self.chk_csv.isChecked())
        self._settings.setValue("write_mcap", self.chk_mcap.isChecked())

        self._settings.setValue("fps", float(self.sp_fps.value()))
        self._settings.setValue("swap_xy", self.chk_swap_xy.isChecked())
        self._settings.setValue("flip_x", self.chk_flip_x.isChecked())
        self._settings.setValue("flip_y", self.chk_flip_y.isChecked())
        self._settings.setValue("dx", float(self.sp_dx.value()))
        self._settings.setValue("dy", float(self.sp_dy.value()))
        self._settings.setValue("yaw_deg", float(self.sp_yaw_deg.value()))

        self._settings.setValue("plot_equal_axes", self.chk_equal_axes.isChecked())
        self._settings.setValue("plot_undock", self.chk_undock_plot.isChecked())
        self._settings.setValue("altair_large", self.chk_altair_large.isChecked())

        self._settings.endGroup()
        try:
            self._settings.sync()
        except Exception:
            pass

    def _reset_settings(self):
        res = QtWidgets.QMessageBox.question(self, "Reset settings", "Reset stored GUI settings?")
        if res != QtWidgets.QMessageBox.Yes:
            return
        self._settings.beginGroup(SETTINGS_GROUP)
        self._settings.remove("")
        self._settings.endGroup()
        self._load_settings()

    def closeEvent(self, event: QtGui.QCloseEvent):
        # prevent accidental close while running
        if self._convert_worker is not None or self._calib_worker is not None:
            res = QtWidgets.QMessageBox.question(self, "Quit", "A job is still running. Quit anyway?")
            if res != QtWidgets.QMessageBox.Yes:
                event.ignore()
                return
        self._save_settings()
        super().closeEvent(event)


def main():
    QtCore.QCoreApplication.setOrganizationName(ORG_NAME)
    QtCore.QCoreApplication.setApplicationName(APP_NAME)
    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")
    win = MainWindow()
    win.show()
    exec_fn = getattr(app, "exec", None) or getattr(app, "exec_", None)
    sys.exit(exec_fn())


if __name__ == "__main__":
    main()