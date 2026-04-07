"""
cosmo.app.convert_app
Tool-facing orchestration for conversion runs.

Responsibilities:
- Create per-run output directories (Option A).
- Derive output base name from OpenLABEL stem (choice B).
- Call the in-package converter implementation (cosmo.converters.openlabel_to_omega).
- Write run metadata (run_inputs.json, run_summary.json).

This module is intentionally "app layer":
it should not contain detailed parsing/geometry logic.
"""
from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, List, Optional, Tuple

LogFn = Callable[[str], None]


# ------------------------------------------------------------------------------------------
# Public results / configuration
# ------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class ConvertConfig:
    openlabel: str
    opendrive: Optional[str] = None
    georef_data: Optional[str] = None
    calibration: Optional[str] = None
    fps: Optional[float] = None
    write_csv: bool = True
    write_mcap: bool = True

    # optional alignment tweaks
    swap_xy: bool = False
    flip_x: bool = False
    flip_y: bool = False
    xy_offset: Tuple[float, float] = (0.0, 0.0)
    yaw_offset_deg: float = 0.0
    strip_xodr_namespace: bool = False

    # oblique correction
    flight_record: Optional[str] = None
    flight_record_sequence: int = 0
    bbox_correction: str = "none"   # "none" | "analytical" | "3d"
    camera_model: str = "mavic3pro-standard"
    hfov_deg: Optional[float] = None
    use_gps_cam_pos: bool = False

    # size stabilization
    stabilize_size: bool = False

    # output control
    out_dir: Optional[str] = None  # If None => <project>/runs/<timestamp>_convert_<stem>/
    run_name: Optional[str] = None  # Optional override for the run folder name


@dataclass(frozen=True)
class ConvertResult:
    run_dir: str
    outputs_dir: str
    base_name: str
    csv_path: Optional[str]
    mcap_path: Optional[str]
    fps_used: Optional[float]
    notes: List[str]


# ------------------------------------------------------------------------------------------
# Helpers: paths, run folders
# ------------------------------------------------------------------------------------------
_PROJECT_MARKERS = (
    "pyproject.toml",
    "environment.yml",
    "environment.yaml",
    ".git",
)


def _find_project_root(start: Path) -> Path:
    start = start.resolve()
    for d in [start] + list(start.parents):
        if any((d / m).exists() for m in _PROJECT_MARKERS):
            return d
    return start


def _timestamp() -> str:
    return time.strftime("%Y-%m-%d_%H%M%S")


def _safe_stem(name: str) -> str:
    s = name.strip().lower().replace(" ", "_")
    s = re.sub(r"[^a-z0-9_\-\.]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "run"


def _make_run_dir(command: str, stem: str, out_dir: Optional[str], run_name: Optional[str]) -> Path:
    """
    If out_dir is:
    - None: use <project_root>/runs/<timestamp>_<command>_<stem>/
    - an existing directory: create a run folder inside it
    - a non-existing path: treat it as the run folder itself
    """
    if out_dir:
        base = Path(out_dir).expanduser()
        if base.exists() and base.is_dir():
            run_folder = run_name or f"{_timestamp()}_{command}_{stem}"
            run_dir = base / run_folder
        else:
            run_dir = base
    else:
        project_root = _find_project_root(Path.cwd())
        run_dir = project_root / "runs" / (run_name or f"{_timestamp()}_{command}_{stem}")

    (run_dir / "outputs").mkdir(parents=True, exist_ok=True)
    return run_dir


def _write_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


# ------------------------------------------------------------------------------------------
# Converter import (NO scripts fallback)
# ------------------------------------------------------------------------------------------
def _get_converter_or_raise():
    """
    Import the in-package converter implementation only.
    Raises a clear error if missing, because we do not rely on scripts/ at runtime.
    """
    try:
        from cosmo.converters.openlabel_to_omega import convert_openlabel_to_omega  # type: ignore

        return convert_openlabel_to_omega
    except Exception as e:
        raise RuntimeError(
            "COSMO converter implementation not found.\n\n"
            "Expected Python module:\n"
            "  cosmo.converters.openlabel_to_omega\n"
            "with callable:\n"
            "  convert_openlabel_to_omega(...)\n\n"
            "How to fix:\n"
            "  1) Ensure the file exists at: src/cosmo/converters/openlabel_to_omega.py\n"
            "  2) Ensure repo root/src is on PYTHONPATH (Windows no-install runner does this)\n\n"
            f"Original import error: {type(e).__name__}: {e}"
        ) from e


# ------------------------------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------------------------------
def run_convert(cfg: ConvertConfig, log_fn: Optional[LogFn] = None) -> ConvertResult:
    """
    Run a conversion under a per-run folder and return structured output paths.

    Output naming:
      base_name := stem(openlabel) (lowercased & sanitized)

    Outputs:
      outputs/<base_name>.csv
      outputs/<base_name>.mcap
    """
    openlabel_path = Path(cfg.openlabel).expanduser().resolve()
    if not openlabel_path.is_file():
        raise FileNotFoundError(f"OpenLABEL not found: {openlabel_path}")

    stem = _safe_stem(openlabel_path.stem)
    run_dir = _make_run_dir("convert", stem, cfg.out_dir, cfg.run_name)
    outputs_dir = run_dir / "outputs"
    base_name = stem
    out_prefix = outputs_dir / base_name

    notes: List[str] = []

    # record inputs
    inputs = asdict(cfg)
    inputs["openlabel"] = str(openlabel_path)
    if cfg.opendrive:
        inputs["opendrive"] = str(Path(cfg.opendrive).expanduser().resolve())
    if cfg.georef_data:
        inputs["georef_data"] = str(Path(cfg.georef_data).expanduser().resolve())
    if cfg.calibration:
        inputs["calibration"] = str(Path(cfg.calibration).expanduser().resolve())

    _write_json(run_dir / "run_inputs.json", inputs)

    # import converter
    converter_fn = _get_converter_or_raise()

    # --- Logging: converter + alignment info ---
    if log_fn:
        log_fn("[COSMO] Using converter: cosmo.converters.openlabel_to_omega")

        alignment_source = "none"
        if cfg.georef_data:
            alignment_source = "georef-data"
        elif cfg.calibration:
            alignment_source = "calibration"

        log_fn(f"[COSMO] Alignment source: {alignment_source} / georef_data={bool(cfg.georef_data)} / calibration={bool(cfg.calibration)}")
        log_fn(
            f"[COSMO] Applied xy_offset={cfg.xy_offset}, yaw_offset_deg={cfg.yaw_offset_deg}, "
            f"swap_xy={cfg.swap_xy}, flip_x={cfg.flip_x}, flip_y={cfg.flip_y}"
        )
        log_fn(f"[COSMO] OpenDRIVE embedded: {'yes' if cfg.opendrive else 'no'}")

    # Build oblique corrector if requested
    corrector = None
    if cfg.flight_record and cfg.bbox_correction != "none":
        try:
            from cosmo.converters.openlabel_to_omega import load_alignment
            from cosmo.corrections import BboxCorrector, load_camera_from_flight_record
            georef_path = str(Path(cfg.georef_data).expanduser().resolve()) if cfg.georef_data else None
            calib_path = str(Path(cfg.calibration).expanduser().resolve()) if cfg.calibration else None
            _, H_corr, _ = load_alignment(calib_path, georef_path, cfg.fps)
            if H_corr is not None:
                cam = load_camera_from_flight_record(
                    cfg.flight_record, cfg.flight_record_sequence,
                    cfg.camera_model, cfg.hfov_deg,
                )
                proj_string = None
                if georef_path:
                    import json as _json
                    with open(georef_path, encoding="utf-8") as _f:
                        proj_string = _json.load(_f).get("proj_string")
                corrector = BboxCorrector(cam, H_corr, mode=cfg.bbox_correction,
                                          proj_string=proj_string,
                                          use_gps_cam_pos=cfg.use_gps_cam_pos)
                if log_fn:
                    log_fn(f"[COSMO] Oblique correction: mode={cfg.bbox_correction}, "
                           f"height={cam.drone_height:.1f}m, el={cam.elevation_angle_deg:.1f}° from nadir")
            else:
                if log_fn:
                    log_fn("[COSMO] Oblique correction skipped: no homography available")
        except Exception as exc:
            if log_fn:
                log_fn(f"[COSMO] Oblique correction setup failed: {exc}")

    # Call converter (pass log_fn through)
    converter_fn(
        openlabel_path=str(openlabel_path),
        odr_path=str(Path(cfg.opendrive).expanduser().resolve()) if cfg.opendrive else None,
        out_prefix=str(out_prefix),
        calibration_path=str(Path(cfg.calibration).expanduser().resolve()) if cfg.calibration else None,
        georef_data_path=str(Path(cfg.georef_data).expanduser().resolve()) if cfg.georef_data else None,
        fps_arg=cfg.fps,
        write_csv=cfg.write_csv,
        write_mcap=cfg.write_mcap,
        swap_xy=cfg.swap_xy,
        flip_x=cfg.flip_x,
        flip_y=cfg.flip_y,
        xy_offset=cfg.xy_offset,
        yaw_offset_rad=(cfg.yaw_offset_deg * 3.141592653589793 / 180.0),
        strip_xodr_namespace=cfg.strip_xodr_namespace,
        log_fn=log_fn,
        corrector=corrector,
        stabilize_size=cfg.stabilize_size,
    )

    # determine produced outputs
    csv_path = str(out_prefix) + ".csv" if cfg.write_csv else None
    mcap_path = str(out_prefix) + ".mcap" if cfg.write_mcap else None

    if log_fn:
        if csv_path:
            log_fn(f"[COSMO] CSV output: {csv_path}")
        if mcap_path:
            log_fn(f"[COSMO] MCAP output: {mcap_path}")

    if csv_path and not Path(csv_path).is_file():
        notes.append("CSV was requested but not found after conversion.")
    if mcap_path and not Path(mcap_path).is_file():
        notes.append("MCAP was requested but not found (missing betterosi or conversion disabled).")

    # Best-effort fps_used: converter decides fps from georef/calibration/default if cfg.fps is None
    fps_used = cfg.fps

    summary = {
        "command": "convert",
        "run_dir": str(run_dir),
        "outputs_dir": str(outputs_dir),
        "base_name": base_name,
        "csv_path": csv_path if csv_path and Path(csv_path).is_file() else None,
        "mcap_path": mcap_path if mcap_path and Path(mcap_path).is_file() else None,
        "fps_used": fps_used,
        "alignment_source": ("georef-data" if cfg.georef_data else ("calibration" if cfg.calibration else "none")),
        "applied_xy_offset": list(cfg.xy_offset),
        "applied_yaw_offset_deg": cfg.yaw_offset_deg,
        "applied_swap_xy": cfg.swap_xy,
        "applied_flip_x": cfg.flip_x,
        "applied_flip_y": cfg.flip_y,
        "opendrive_embedded": bool(cfg.opendrive),
        "notes": notes,
        "python": sys.version,
    }
    _write_json(run_dir / "run_summary.json", summary)

    return ConvertResult(
        run_dir=str(run_dir),
        outputs_dir=str(outputs_dir),
        base_name=base_name,
        csv_path=summary["csv_path"],
        mcap_path=summary["mcap_path"],
        fps_used=fps_used,
        notes=notes,
    )
