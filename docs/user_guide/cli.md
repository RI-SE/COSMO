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

# CLI reference

COSMO provides a `cosmo` command with subcommands like `convert` and `calibrate`.

---

## `cosmo convert`

Convert OpenLABEL → Omega‑Prime CSV and optionally OSI/MCAP. Outputs are written under a per-run folder.

### Input styles
```bash
cosmo convert INPUT.json [options]
cosmo convert --input INPUT.json [options]
cosmo convert --openlabel INPUT.json [options]
```

`--input` and `--openlabel` are aliases.
### Recommended (ORBIT georef happy path)
```bash
cosmo convert scenario.json \
  --georef-data path/to/*_georef_data.json \
  --odr path/to/map.xodr \
  -o runs/
```
### Fallback (calibration)
```bash
cosmo convert scenario.json \
  --calibration path/to/<base_name>_calibration.json \
  -o runs/
```
### Output toggles
Defaults are `--csv` and `--mcap` enabled. Use:
```bash
cosmo convert scenario.json --no-mcap -o runs/
cosmo convert scenario.json --no-csv  -o runs/
```

### Post-projection alignment fixes
```bash
cosmo convert scenario.json \
  --georef-data path/to/*_georef_data.json \
  --swap-xy --yaw-offset-deg 90 \
  --xy-offset 1.2 -0.5 \
  -o runs/
```
Available flags: `--swap-xy`, `--flip-x`, `--flip-y`, `--xy-offset DX DY`, `--yaw-offset-deg DEG`. 
### Run naming & JSON output
```bash
cosmo convert scenario.json --run-name saro_roundabout -o runs/
cosmo convert scenario.json --json -o runs/
```

---

## `cosmo correct`

Pre-process an oblique-drone OpenLABEL file before conversion. Corrects perspective-distorted
bboxes and optionally outputs world-frame cuboids. Use this when you want a corrected OpenLabel
as an intermediate artefact (e.g. for inspection in the trajectory explorer).

```bash
cosmo correct input.json -o corrected.json \
  --georef-data path/to/*_georef_data.json \
  --flight-record path/to/FlightRecord_*.video_stats.json
```

### Output coordinate format (`--output-coords`)
| Value | Effect |
|---|---|
| `pixel` (default) | Updates rbbox with corrected pixel coords |
| `geo` | Replaces rbbox with world-frame cuboid (uses proj_string from georef) |
| `both` | Updates rbbox AND adds cuboid |

### Size stabilization
`--stabilize-size` replaces per-frame L/W/H with the per-object mean across all frames.
Also adds `size_std` and `size_deviation` vec entries to the OpenLabel output, which the
trajectory explorer surfaces as a ΔL×W×H column and a size σ tooltip.

### Correction modes
- `--correction analytical` (default): fast homography-based
- `--correction 3d`: ray-casting using the flight record camera model; requires `--flight-record`

### Typical two-step workflow
```bash
cosmo correct input.json -o corrected.json \
  --georef-data georef.json \
  --flight-record video_stats.json \
  --output-coords geo \
  --stabilize-size

cosmo convert corrected.json --georef-data georef.json -o runs/
```

> **Inline alternative:** `cosmo convert` also supports `--bbox-correction analytical|3d`
> (with `--flight-record`) for single-step correction without producing an intermediate file.

---

### cosmo calibrate
Compute a calibration JSON (pixel→ground homography) into a per-run folder.
#### Choose one input style (do not mix)
##### Style A — --inputs (recommended)
```bash
cosmo calibrate --inputs pixel_pairs.csv visual_markers.csv map.xodr -o runs/
```
##### Style B — positional
```bash
cosmo calibrate pixel_pairs.csv visual_markers.csv map.xodr -o runs/
```
##### Style C — explicit flags
```bash
cosmo calibrate \
  --pixel-pairs pixel_pairs.csv \
  --visual-markers visual_markers.csv \
  --opendrive map.xodr \
  -o runs/
```

##### Optional validation & visualization
```bash
cosmo calibrate --inputs pixel_pairs.csv visual_markers.csv map.xodr \
  --image frame.png \
  --openlabel scenario.json \
  -o runs/
```

##### Useful knobs
- --ransac-thresh-m (default 0.50) — RANSAC distance threshold in meters --fps,
- --image-width, --image-height 

##### Calibration outputs
Calibration writes stem-based files under outputs/:

- <base_name>_calibration.json
- <base_name>_homography_fit_summary.json
- <base_name>_homography_fit_residuals.png
- <base_name>_overlay_markers_on_image.png (only if --image is provided) 

