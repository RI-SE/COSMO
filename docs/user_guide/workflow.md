# Workflow: OpenLABEL → Omega‑Prime (GUI-first)

This guide describes the recommended COSMO workflow for typical users: **start in the GUI**, convert using **ORBIT georef** (happy path), and only use calibration if ORBIT georef is not available.

> Power users: every GUI step below has a **CLI equivalent** so you can automate runs and make them reproducible. COSMO records `run_inputs.json` and `run_summary.json` for every run.

---

## 0) Install COSMO

Recommended with **uv** (the project ships a `uv.lock`):

```bash
uv sync --group dev          # or --all-groups for gui/mcap/plot/etc.
```

With **pip** (≥ 25.1):

```bash
python -m pip install -U pip
python -m pip install -e . --group dev
```

> `[dependency-groups]` are not pip *extras*, so `pip install -e ".[dev]"` does
> not work. CI installs the base package plus tools explicitly:
> `pip install -e . pytest ruff`.

## 1) Start the GUI (recommended starting point)

COSMO supports launching the GUI in two equivalent ways:

```bash
cosmo
# or
cosmo gui
```

> Headless note: calling plain cosmo (no args) attempts to start the GUI by default—avoid this on servers/CI and use subcommands like cosmo convert … instead.

### Development shortcut (no editable install required)
During development (when you don’t want to install with `uv sync --group dev` / `pip install -e . --group dev`), you can run the GUI directly:
```bash
python run_gui.py
```
or with the cosmo equivalent:
```bash
python run_cosmo.py
```
Running Windows it you can also from a CMD terminal run
```bash
cosmo.cmd
```
or from a PowerShell terminal run
```bash
cosmo.ps1
```
> Calling `python run_cosmo.py`, `cosmo.cmd`, or `cosmo.ps1` show same behaviour as calling `cosmo`. E.g., `python run_cosmo.py --help` or `cosmo.cmd convert`


## 2) Happy path: Convert using ORBIT georef (recommended)

#### What you need

* **OpenLABEL JSON** (your annotation file)
* **ORBIT georef expor**t *_georef_data.json (pixel→ground mapping)
* (Optional but recommended) **OpenDRIVE map** (.xodr/.xml/.txt)

#### In the GUI

1) Select **OpenLABEL input**
2) Select **ORBIT georef** (*_georef_data.json)
3) (Optional) Select **OpenDRIVE map**
4) Set output base directory to **runs/**
5) Run conversion

COSMO treats ORBIT georef as the primary alignment source and uses it to project OpenLABEL pixel coordinates into the ground/map frame.

#### What COSMO writes

A conversion run is written into a per-run folder (default base: runs/).

```bash
runs/<timestamp>_convert_<openlabel_stem>/
  outputs/
    <base_name>.csv
    <base_name>.mcap
  run_inputs.json
  run_summary.json
```

* <base_name> is derived from the OpenLABEL filename stem (sanitized & lowercased).
* run_inputs.json captures the fully resolved inputs/config.
* run_summary.json records produced outputs, alignment source, and applied transforms.

> MCAP output is optional and requires betterosi. If MCAP is requested but betterosi is missing, COSMO writes CSV only and logs a message.

#### CLI equivalent (same as GUI action)

```bash
cosmo convert scenario.json \
  --georef-data path/to/*_georef_data.json \
    --odr path/to/map.xodr \
      -o runs/
```

## 3) Optional: Fix alignment issues (GUI or CLI)

If objects look mirrored/rotated/shifted after conversion, COSMO supports post-projection alignment options:

* swap X/Y, flip X, flip Y
* apply yaw offset (deg)
* apply XY translation offset (meters)

#### CLI examples (use these to reproduce GUI tweaks)

Rotate and swap:

```bash
cosmo convert scenario.json \
  --georef-data path/to/*_georef_data.json \
    --swap-xy --yaw-offset-deg 90 \
      -o runs/
```

Shift XY:

```bash
cosmo convert scenario.json \
  --georef-data path/to/*_georef_data.json \
    --xy-offset 1.2 -0.5 \
      -o runs/
```

## 4) Backup path: Create calibration (when ORBIT georef is missing)

If you don’t have ORBIT georef exports, use COSMO’s calibration workflow to compute a *_calibration.json homography (pixel→ground).

#### What you need

* pixel_pairs.csv (pixel coordinates for named points)
* visual_markers.csv (world coordinates for the same named points)
* OpenDRIVE map (map.xodr) to provide geo/projection context when needed

#### Run calibration

```bash
cosmo calibrate --inputs pixel_pairs.csv visual_markers.csv map.xodr -o runs/
```

#### Calibration outputs (exact filenames)

Calibration writes stem-based files under the run’s outputs/ folder:

* <base_name>_calibration.json
* <base_name>_homography_fit_summary.json
* <base_name>_homography_fit_residuals.png
* <base_name>_overlay_markers_on_image.png (only if an image is provided)

#### Use the calibration file for conversion

```bash
cosmo convert scenario.json \
  --calibration runs/<calibrate_run>/outputs/<base_name>_calibration.json \
    -o runs/
```

## 5) Verify results quickly

### 1) Check run summary (fastest)

Open:

* runs/.../run_summary.json

It tells you:

* alignment_source (georef-data, calibration, or none)
* which outputs were actually produced
* applied transform flags and offsets

### 2) Check produced files

* CSV: outputs/<base_name>.csv
* MCAP: outputs/<base_name>.mcap (if enabled + deps installed)

### 3) MCAP topics (if you inspect MCAP)
COSMO writes:

* ground_truth_map (OpenDRIVE, if provided)
* ground_truth (OSI GroundTruth messages per frame)
