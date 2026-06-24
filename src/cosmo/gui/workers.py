# src/cosmo/gui/workers.py
from __future__ import annotations

from typing import Callable

# Qt binding selection (same preference order as your current GUI)
try:
    from PyQt5 import QtCore
    _Signal = QtCore.pyqtSignal
except ImportError:  # pragma: no cover
    try:
        from PySide6 import QtCore
        _Signal = QtCore.Signal
    except ImportError:  # pragma: no cover
        from PyQt6 import QtCore
        _Signal = QtCore.pyqtSignal

from cosmo.app.calibrate_app import CalibrateConfig, CalibrateResult, run_calibrate
from cosmo.app.convert_app import ConvertConfig, ConvertResult, run_convert

LogFn = Callable[[str], None]


class ConvertWorker(QtCore.QThread):
    """
    Runs conversion in a background thread.
    Emits log lines and returns either ConvertResult or Exception.
    """
    line = _Signal(str)
    finished = _Signal(object)  # ConvertResult | Exception

    def __init__(self, cfg: ConvertConfig):
        super().__init__()
        self.cfg = cfg
        self._cancel_requested = False

    def cancel(self) -> None:
        # Cancellation token is reserved for future use in core loops
        self._cancel_requested = True

    def run(self) -> None:
        try:
            def log_fn(msg: str) -> None:
                self.line.emit(msg)

            result: ConvertResult = run_convert(self.cfg, log_fn=log_fn)
            self.finished.emit(result)
        except Exception as e:
            self.finished.emit(e)


class CalibrateWorker(QtCore.QThread):
    """
    Runs calibration in a background thread.
    Emits log lines and returns either CalibrateResult or Exception.
    """
    line = _Signal(str)
    finished = _Signal(object)  # CalibrateResult | Exception

    def __init__(self, cfg: CalibrateConfig):
        super().__init__()
        self.cfg = cfg
        self._cancel_requested = False

    def cancel(self) -> None:
        self._cancel_requested = True

    def run(self) -> None:
        try:
            def log_fn(msg: str) -> None:
                self.line.emit(msg)

            result: CalibrateResult = run_calibrate(self.cfg, log_fn=log_fn)
            self.finished.emit(result)
        except Exception as e:
            self.finished.emit(e)

