"""
cosmo.app.calibrate_app

Tool-facing orchestration for calibration runs.

Responsibilities:
- Create per-run output directories (Option A).
- Derive output base name from inputs (OpenLABEL stem if provided, else pixel_pairs stem).
- Compute calibration in-process via cosmo.calibration.compute (no scripts dependency).
- Write stem-based outputs directly into outputs/ folder.
- Write run metadata (run_inputs.json, run_summary.json).
"""

from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Callable, Optional, Any, List


LogFn = Callable[[str], None]


@dataclass(frozen=True)
class CalibrateConfig:
    pixel_pairs: str
    visual_markers: str
    opendrive: str
    image: Optional[str] = None
    openlabel: Optional[str] = None

    fps: float = 30.0
    image_width: int = 3840
    image_height: int = 2160
    ransac_thresh_m: float = 0.50

    origin_lat0: Optional[float] = None
    origin_lon0: Optional[float] = None
    out_dir: Optional[str] = None
    run_name: Optional[str] = None


@dataclass(frozen=True)
class CalibrateResult:
    run_dir: str
    outputs_dir: str
    base_name: str

    calibration_json_path: str
    summary_json_path: Optional[str]
    residuals_png_path: Optional[str]
    overlay_png_path: Optional[str]

    notes: List[str]


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
    return s or "calib"


def _make_run_dir(command: str, stem: str, out_dir: Optional[str], run_name: Optional[str]) -> Path:
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


def run_calibrate(cfg: CalibrateConfig, log_fn: Optional[LogFn] = None) -> CalibrateResult:
    # Validate input files
    pixel_pairs = Path(cfg.pixel_pairs).expanduser().resolve()
    visual_markers = Path(cfg.visual_markers).expanduser().resolve()
    opendrive = Path(cfg.opendrive).expanduser().resolve()

    for p, label in [(pixel_pairs, "pixel_pairs"), (visual_markers, "visual_markers"), (opendrive, "opendrive")]:
        if not p.is_file():
            raise FileNotFoundError(f"{label} not found: {p}")

    image = Path(cfg.image).expanduser().resolve() if cfg.image else None
    if image and not image.is_file():
        raise FileNotFoundError(f"image not found: {image}")

    openlabel = Path(cfg.openlabel).expanduser().resolve() if cfg.openlabel else None
    if openlabel and not openlabel.is_file():
        raise FileNotFoundError(f"openlabel not found: {openlabel}")

    stem = _safe_stem(openlabel.stem if openlabel else pixel_pairs.stem)

    run_dir = _make_run_dir("calibrate", stem, cfg.out_dir, cfg.run_name)
    outputs_dir = run_dir / "outputs"
    base_name = stem

    notes: List[str] = []

    inputs = asdict(cfg)
    inputs["pixel_pairs"] = str(pixel_pairs)
    inputs["visual_markers"] = str(visual_markers)
    inputs["opendrive"] = str(opendrive)
    if image:
        inputs["image"] = str(image)
    if openlabel:
        inputs["openlabel"] = str(openlabel)

    _write_json(run_dir / "run_inputs.json", inputs)

    if log_fn:
        log_fn("[COSMO] Using calibration: cosmo.calibration.compute")

    # Compute & write outputs directly (no scripts dependency)
    from cosmo.calibration.compute import compute_calibration, write_calibration_outputs

    comp = compute_calibration(
        pixel_pairs_csv=str(pixel_pairs),
        visual_markers_csv=str(visual_markers),
        opendrive_path=str(opendrive),
        ransac_thresh_m=float(cfg.ransac_thresh_m),
        origin_lat0=cfg.origin_lat0,
        origin_lon0=cfg.origin_lon0,
        log_fn=log_fn,
    )

    # Stem-based output filenames (your requirement)
    calib_json = outputs_dir / f"{base_name}_calibration.json"
    summary_json = outputs_dir / f"{base_name}_homography_fit_summary.json"
    resid_png = outputs_dir / f"{base_name}_homography_fit_residuals.png"
    overlay_png = outputs_dir / f"{base_name}_overlay_markers_on_image.png"

    outs = write_calibration_outputs(
        comp,
        calibration_json_path=str(calib_json),
        summary_json_path=str(summary_json),
        fps=float(cfg.fps),
        image_width=int(cfg.image_width),
        image_height=int(cfg.image_height),
        residuals_png_path=str(resid_png),
        overlay_png_path=str(overlay_png) if image else None,
        image_path=str(image) if image else None,
        openlabel_path=str(openlabel) if openlabel else None,
    )

    # Notes: if optional artifacts missing
    if outs.residuals_png_path is None:
        notes.append("Residual plot was not written (matplotlib error or disabled).")
    if image is not None and outs.overlay_png_path is None:
        notes.append("Overlay image was requested but not written (image/plot error).")

    summary = {
        "command": "calibrate",
        "run_dir": str(run_dir),
        "outputs_dir": str(outputs_dir),
        "base_name": base_name,
        "calibration_json_path": outs.calibration_json_path,
        "summary_json_path": outs.summary_json_path,
        "residuals_png_path": outs.residuals_png_path,
        "overlay_png_path": outs.overlay_png_path,
        "openlabel_validation_count": outs.openlabel_validation_count,
        "rmse_m": comp.rmse_m,
        "inliers_count": int(len(comp.inlier_idx)),
        "notes": notes,
        "python": sys.version,
    }
    _write_json(run_dir / "run_summary.json", summary)

    return CalibrateResult(
        run_dir=str(run_dir),
        outputs_dir=str(outputs_dir),
        base_name=base_name,
        calibration_json_path=outs.calibration_json_path,
        summary_json_path=outs.summary_json_path,
        residuals_png_path=outs.residuals_png_path,
        overlay_png_path=outs.overlay_png_path,
        notes=notes,
    )