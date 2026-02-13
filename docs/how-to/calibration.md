# Calibration (fallback path)

Use calibration when ORBIT georef is not available or incomplete. ORBIT georef (`--georef-data`) remains the recommended happy path.

---

## Step 1 — Compute calibration

```bash
cosmo calibrate --inputs pixel_pairs.csv visual_markers.csv map.xodr -o runs/
```
COSMO creates a per-run folder with outputs under: `runs/<timestamp>_calibrate_<stem>/outputs/`

## Step 2 — Find the calibration file
The calibration JSON is written as:
`outputs/<base_name>_calibration.json`
Other helpful artifacts:
- <base_name>_homography_fit_summary.json
- <base_name>_homography_fit_residuals.png
- <base_name>_overlay_markers_on_image.png (only if --image was supplied) 

## Step 3 — Convert using calibration

```bash
cosmo convert scenario.json \
  --calibration runs/<calibrate_run>/outputs/<base_name>_calibration.json \
  -o runs/
``
```

## Troubleshooting
If results look wrong, check the calibration run’s run_summary.json for:

- RMSE and inlier count
- notes about missing plots/overlays 
