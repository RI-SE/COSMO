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
from typing import Any, Callable, Dict, List, Optional, Tuple

from cosmo.converters.companion import COMPANION_SUFFIX
from cosmo.converters.kinematics import DEFAULT_JERK_PSD

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

    # environmental conditions / timing: DJI SRT gives per-frame time, GPS and exposure;
    # the flight record (above) gives start time and position when no SRT is given
    srt: Optional[str] = None
    timezone: str = "Europe/Stockholm"  # IANA zone of the SRT clock and for local time of day
    # Annotated frame k is SRT frame k + frame_offset; None = detect from the provenance chain (prov_in)
    frame_offset: Optional[int] = None
    prov_in: Optional[str] = None

    # size stabilization
    stabilize_size: bool = False

    # uncertainty (scenario-uncertainty `world` profile): FieldDataLab video sidecar, companion OpenLABEL
    # (<prefix>.companion.json; with per-frame data when companion_frames), smoothed kinematics, SensorData
    video_quality: Optional[str] = None
    companion: bool = True
    companion_frames: bool = False
    smooth_kinematics: bool = False
    jerk_psd: float = DEFAULT_JERK_PSD
    sensor_data: bool = False
    camera_uncertainty: bool = False  # camera height/pitch/azimuth error in the oblique terms
    estimate_height: bool = False  # per-object effective height from the track (needs bbox_correction)

    # ISO 3166-1 numeric country code; auto-derived from georef lat/lon if None
    country_code: Optional[int] = None

    # object output frame: "global" (absolute proj_string CRS) or "local"
    # (map-local; offset recorded in OSI proj_frame_offset)
    object_frame: str = "global"

    # output control
    out_dir: Optional[str] = None  # If None => <project>/runs/<timestamp>_convert_<stem>/
    run_name: Optional[str] = None  # Optional override for the run folder name
    output_prefix: Optional[str] = None  # Write directly to <prefix>.csv/.mcap (bypasses run-folder)


@dataclass(frozen=True)
class ConvertResult:
    run_dir: str
    outputs_dir: str
    base_name: str
    csv_path: Optional[str]
    mcap_path: Optional[str]
    fps_used: Optional[float]
    notes: List[str]
    companion_path: Optional[str] = None
    quality: Optional[Dict[str, Any]] = None


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
def _resolve_frame_offset(cfg: ConvertConfig, log_fn: Optional[LogFn]) -> Tuple[int, str]:
    """Offset between annotated-video frames and SRT frames: explicit, from provenance, or assumed 0."""
    from cosmo.converters.environment import frame_offset_from_provenance

    if cfg.frame_offset is not None:
        return cfg.frame_offset, "explicit"
    detected = frame_offset_from_provenance(cfg.prov_in)
    if detected is not None:
        if detected and log_fn:
            log_fn(f"[COSMO] Warning: provenance shows pre-2026-10 vidrectify output (first frame dropped); "
                   f"annotated frame k is SRT frame k+{detected}")
        return detected, "provenance"
    if cfg.srt and log_fn:
        log_fn("[COSMO] Warning: frame alignment with the SRT is unknown (no vidrectify step in --prov-in); "
               "assuming 0. FieldDataLab output made before 2026-10-21 needs --frame-offset 1")
    return 0, "assumed"


def _build_corrector(cfg: ConvertConfig, mode: str, log_fn: Optional[LogFn]):
    """BboxCorrector for the flight-record camera and the alignment homography, or None."""
    if not cfg.flight_record:
        return None
    try:
        from cosmo.converters.openlabel_to_omega import load_alignment
        from cosmo.corrections import BboxCorrector, load_camera_from_flight_record
        georef_path = str(Path(cfg.georef_data).expanduser().resolve()) if cfg.georef_data else None
        calib_path = str(Path(cfg.calibration).expanduser().resolve()) if cfg.calibration else None
        _, H_corr, _ = load_alignment(calib_path, georef_path, cfg.fps)
        if H_corr is None:
            if log_fn:
                log_fn("[COSMO] Oblique correction skipped: no homography available")
            return None
        cam = load_camera_from_flight_record(cfg.flight_record, cfg.flight_record_sequence,
                                             cfg.camera_model, cfg.hfov_deg)
        proj_string = None
        if georef_path:
            with open(georef_path, encoding="utf-8") as f:
                proj_string = json.load(f).get("proj_string")
        corrector = BboxCorrector(cam, H_corr, mode=mode, proj_string=proj_string,
                                  use_gps_cam_pos=cfg.use_gps_cam_pos)
        if log_fn and corrector.azimuth_warning():
            log_fn(f"[COSMO] Warning: {corrector.azimuth_warning()}")
        if log_fn:
            log_fn(f"[COSMO] Oblique correction: mode={mode}, "
                   f"height={cam.drone_height:.1f}m, el={cam.elevation_angle_deg:.1f}° from nadir")
        return corrector
    except Exception as exc:
        if log_fn:
            log_fn(f"[COSMO] Oblique correction setup failed: {exc}")
        return None


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

    if cfg.output_prefix:
        # Direct-output mode: write <prefix>.csv/.mcap without a run folder
        out_prefix = Path(cfg.output_prefix).expanduser().resolve()
        out_prefix.parent.mkdir(parents=True, exist_ok=True)
        run_dir = out_prefix.parent
        outputs_dir = run_dir
        base_name = out_prefix.name
    else:
        run_dir = _make_run_dir("convert", stem, cfg.out_dir, cfg.run_name)
        outputs_dir = run_dir / "outputs"
        base_name = stem
        out_prefix = outputs_dir / base_name

    notes: List[str] = []

    # record inputs (only in run-folder mode)
    if not cfg.output_prefix:
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

    frame_offset, frame_offset_source = _resolve_frame_offset(cfg, log_fn)

    # Oblique corrector if requested; else, with a flight record, an analytical one used only for the
    # vehicle-height uncertainty of input already corrected by `cosmo correct`
    corrector = _build_corrector(cfg, cfg.bbox_correction, log_fn) if cfg.bbox_correction != "none" else None
    height_model = _build_corrector(cfg, "analytical", None) if corrector is None else None
    if log_fn and height_model is not None and height_model.azimuth_warning():
        log_fn(f"[COSMO] Warning: {height_model.azimuth_warning()}")

    # Call converter (pass log_fn through); quality_report is filled with the uncertainty summary
    quality_report: Dict[str, Any] = {}
    fps_used = converter_fn(
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
        height_model=height_model,
        stabilize_size=cfg.stabilize_size,
        country_code=cfg.country_code,
        object_frame=cfg.object_frame,
        srt_path=str(Path(cfg.srt).expanduser().resolve()) if cfg.srt else None,
        timezone_name=cfg.timezone,
        frame_offset=frame_offset,
        flight_record_path=str(Path(cfg.flight_record).expanduser().resolve()) if cfg.flight_record else None,
        flight_record_sequence=cfg.flight_record_sequence,
        video_quality_path=str(Path(cfg.video_quality).expanduser().resolve()) if cfg.video_quality else None,
        companion_path=str(out_prefix) + COMPANION_SUFFIX if cfg.companion else None,
        companion_frames=cfg.companion_frames,
        smooth_kinematics=cfg.smooth_kinematics,
        jerk_psd=cfg.jerk_psd,
        sensor_data=cfg.sensor_data,
        quality_report=quality_report,
        camera_uncertainty=cfg.camera_uncertainty,
        estimate_height=cfg.estimate_height,
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

    if not cfg.output_prefix:
        summary = {
            "command": "convert",
            "run_dir": str(run_dir),
            "outputs_dir": str(outputs_dir),
            "base_name": base_name,
            "csv_path": csv_path if csv_path and Path(csv_path).is_file() else None,
            "mcap_path": mcap_path if mcap_path and Path(mcap_path).is_file() else None,
            "fps_used": fps_used,
            "frame_offset_vs_srt": frame_offset,
            "frame_offset_source": frame_offset_source,
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

    csv_path_out = csv_path if csv_path and Path(csv_path).is_file() else None
    mcap_path_out = mcap_path if mcap_path and Path(mcap_path).is_file() else None

    return ConvertResult(
        run_dir=str(run_dir),
        outputs_dir=str(outputs_dir),
        base_name=base_name,
        csv_path=csv_path_out,
        mcap_path=mcap_path_out,
        fps_used=fps_used,
        notes=notes,
        companion_path=quality_report.get("companion_path"),
        quality=quality_report.get("run"),
    )


def quality_properties(run_quality: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """dataprov custom properties (su namespace) of a conversion's uncertainty summary."""
    if not run_quality:
        return {}
    from scenario_uncertainty.provenance import custom_properties

    return custom_properties({key: run_quality[key] for key in ("datum", "image", "share_interpolated",
                                                                "share_reviewed") if run_quality.get(key)})


def quality_warnings(run_quality: Optional[Dict[str, Any]]) -> List[str]:
    """[su:*] warning lines of a conversion."""
    if not run_quality:
        return []
    from scenario_uncertainty.provenance import WarningCode, warning_line

    lines = []
    if run_quality.get("defaults_used"):
        lines.append(warning_line(WarningCode.DEFAULT_USED,
                                  f"defaults used: {', '.join(run_quality['defaults_used'])}"))
    if run_quality.get("oblique_correction") == "none":
        lines.append(warning_line("oblique_uncorrected",
                                  "no oblique correction: sizes inflated, centres biased away from the camera"))
    return lines
