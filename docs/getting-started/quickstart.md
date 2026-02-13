# Quickstart

COSMO writes outputs into **per-run folders**. All examples use `-o runs/` as the default output base directory.

## GUI launch

```bash
cosmo
# or
cosmo gui
```

Development shortcut (either of):

```bash
python run_gui.py # Start GUI
python run_cosmo.py gui # Same behaviour as cosmo (when installed)
cosmo.cmd gui # Same behaviour in Windows cmd termial as cosmo (when installed)
cosmo.ps1 gui # Same behaviour in Windows PowerShell termial as cosmo (when installed)
```
---
## CLI
> During development `cosmo ...` can be replaced with `python run_cosmo ...`, `cosmo.exe ...` or `cosmo.ps1 ...` with need to install cosmo running, e.g., `pip install -e .` or `pip install -e .[dev]`.
---
### Convert (recommended: ORBIT georef)

```bash
cosmo convert scenario.json \
  --georef-data path/to/*_georef_data.json \
  --odr path/to/map.xodr \
  -o runs/
```


This creates:

```bash
runs/<timestamp>_convert_<scenario_stem>/
  outputs/
    <scenario_stem>.csv
    <scenario_stem>.mcap
  run_inputs.json
  run_summary.json

```
- <scenario_stem> is derived from the OpenLABEL filename stem (sanitized & lowercased).

#### Backup: Create calibration (when ORBIT georef is unavailable)

```bash
cosmo calibrate --inputs pixel_pairs.csv visual_markers.csv map.xodr -o runs/

```
Calibration outputs are written to:
```bash

runs/<timestamp>_calibrate_<stem>/outputs/
  <base_name>_calibration.json
  <base_name>_homography_fit_summary.json
  <base_name>_homography_fit_residuals.png
  <base_name>_overlay_markers_on_image.png   (only if --image is provided)
```

#### Convert using calibration (fallback)

```bash
cosmo convert scenario.json \
--calibration runs/<calibrate_run>/outputs/<base_name>_calibration.json \
-o runs/
```

