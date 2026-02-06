# src/cosmo/gui/plotting.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence, List, Any, Tuple

# Optional deps: omega_prime and altair are optional (same as your current GUI) [1](https://risecloud-my.sharepoint.com/personal/anders_thorsen_ri_se/Documents/Microsoft%20Copilot%20Chat%20Files/convert_openlabel_to_omega.py)[2](https://risecloud-my.sharepoint.com/personal/anders_thorsen_ri_se/Documents/Microsoft%20Copilot%20Chat%20Files/pyproject.toml.txt)
try:
    import omega_prime as op
except Exception:  # pragma: no cover
    op = None

try:
    import altair as alt
except Exception:  # pragma: no cover
    alt = None

# Matplotlib embedding (QtAgg backend). Your current GUI uses FigureCanvas + toolbar. [1](https://risecloud-my.sharepoint.com/personal/anders_thorsen_ri_se/Documents/Microsoft%20Copilot%20Chat%20Files/convert_openlabel_to_omega.py)
try:
    from matplotlib.figure import Figure
    try:
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
        from matplotlib.backends.backend_qtagg import NavigationToolbar2QT as NavigationToolbar
    except Exception:  # pragma: no cover
        from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
        from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT as NavigationToolbar
except Exception:  # pragma: no cover
    Figure = None
    FigureCanvas = None
    NavigationToolbar = None


@dataclass
class RecordingInfo:
    map_repr: str
    object_ids: List[int]


class PlotController:
    """
    Handles:
      - loading omega_prime Recording from MCAP
      - matplotlib embedded plotting
      - altair interactive plotting (browser)
      - extracting object IDs & metric columns
    """

    def __init__(self):
        self._cache_path: Optional[str] = None
        self._cache_rec: Any = None

    def is_available(self) -> bool:
        return op is not None

    def load_recording(self, mcap_path: str):
        if op is None:
            return None
        mcap_path = str(Path(mcap_path))
        if self._cache_path == mcap_path and self._cache_rec is not None:
            return self._cache_rec
        rec = op.Recording.from_file(mcap_path)
        self._cache_path = mcap_path
        self._cache_rec = rec
        return rec

    def recording_info(self, rec) -> RecordingInfo:
        # Similar to your current "Recording info" panel. [1](https://risecloud-my.sharepoint.com/personal/anders_thorsen_ri_se/Documents/Microsoft%20Copilot%20Chat%20Files/convert_openlabel_to_omega.py)
        try:
            map_repr = repr(getattr(rec, "map", None))
        except Exception:
            map_repr = "<error>"
        try:
            mo = getattr(rec, "moving_objects", {})
            keys = sorted(list(mo.keys())) if hasattr(mo, "keys") else []
        except Exception:
            keys = []
        return RecordingInfo(map_repr=map_repr, object_ids=[int(k) for k in keys])

    def metric_columns_for_object(self, rec, obj_id: int) -> Sequence[str]:
        """
        Try to discover dataframe columns from omega_prime moving object.
        Fallback to a reasonable default list if not introspectable.
        """
        cols: Optional[List[str]] = None
        mo = getattr(rec, "moving_objects", None)
        if mo is None or not hasattr(mo, "__getitem__"):
            return ["vel_x", "vel_y", "acc_x", "acc_y", "x", "y", "yaw"]

        obj = None
        try:
            if obj_id in mo:
                obj = mo[obj_id]
        except Exception:
            obj = None

        if obj is None:
            try:
                keys = sorted(list(mo.keys()))
                if keys:
                    obj = mo[keys[0]]
            except Exception:
                obj = None

        if obj is not None:
            # Heuristic borrowed from your current GUI approach. [1](https://risecloud-my.sharepoint.com/personal/anders_thorsen_ri_se/Documents/Microsoft%20Copilot%20Chat%20Files/convert_openlabel_to_omega.py)
            for attr in ("df", "dataframe", "states", "state_df", "_df", "_dataframe", "track", "trajectory"):
                cand = getattr(obj, attr, None)
                if cand is None:
                    continue
                if hasattr(cand, "columns"):
                    try:
                        cols = [str(c) for c in list(cand.columns)]
                        break
                    except Exception:
                        cols = None

        if not cols:
            cols = ["vel_x", "vel_y", "speed", "acc_x", "acc_y", "x", "y", "yaw", "yaw_rate"]
        return cols

    def embed_plot(self, rec, *, equal_axes: bool = True):
        """
        Create a matplotlib Figure (and return it) using omega_prime Recording.plot.
        """
        if Figure is None:
            raise RuntimeError("matplotlib is not available")

        fig = Figure(figsize=(9, 6), tight_layout=True)
        ax = fig.add_subplot(111)

        plotted = False
        try:
            rec.plot(ax=ax)
            plotted = True
        except TypeError:
            plotted = False
        except Exception:
            plotted = False

        if not plotted:
            # try omega_prime returning an axis itself (older patterns)
            ax2 = rec.plot()
            fig = ax2.figure

        if equal_axes:
            for ax in getattr(fig, "axes", []) or []:
                try:
                    ax.set_aspect("equal", adjustable="box")
                except Exception:
                    pass

        return fig

    def make_canvas_and_toolbar(self, fig, parent_widget):
        """
        Create FigureCanvas + NavigationToolbar for embedding into Qt.
        """
        if FigureCanvas is None or NavigationToolbar is None:
            raise RuntimeError("Matplotlib Qt backends not available")
        canvas = FigureCanvas(fig)
        toolbar = NavigationToolbar(canvas, parent_widget)
        return canvas, toolbar

    def plot_altair_browser(
        self,
        rec,
        *,
        metric: str,
        obj_id: int,
        start_frame: int,
        end_frame: int,
        allow_large: bool = True,
    ) -> None:
        """
        Create Altair chart via omega_prime Recording.plot_altair and open in browser.
        """
        if alt is None:
            raise RuntimeError("altair is not installed")
        if op is None:
            raise RuntimeError("omega_prime is not installed")

        if allow_large:
            try:
                alt.data_transformers.enable("vegafusion")
            except Exception:
                try:
                    alt.data_transformers.disable_max_rows()
                except Exception:
                    pass
        else:
            try:
                alt.data_transformers.enable("default")
            except Exception:
                pass

        chart = rec.plot_altair(
            start_frame=int(start_frame),
            end_frame=int(end_frame),
            metric_column=str(metric),
            idx=int(obj_id),
        )
        try:
            alt.renderers.enable("browser")
        except Exception:
            pass
        chart.show()