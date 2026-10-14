# Calibration (fallback when no ORBIT georef is available)

`cosmo calibrate` fits a pixel→ground homography from matched points and writes a calibration JSON that
`cosmo convert --calibration` uses instead of an ORBIT georef. Prefer ORBIT georef when you have it.

## Step 1 — Prepare inputs
- `pixel_pairs.csv`: `point_name,u,v` (pixel coordinates of named points in the video frame).
- `visual_markers.csv`: `point_name` plus either `lat,lon,alt` or `E,N` (world coordinates of the same points).
- OpenDRIVE map: its `<geoReference>` is used to project lat/lon markers.

In the GUI (**Calibration** tab) you can pick the files, edit pixel pairs on an image and convert lat/lon
markers to OpenDRIVE-local E/N.

## Step 2 — Compute calibration
```bash
cosmo calibrate --inputs pixel_pairs.csv visual_markers.csv map.xodr -o runs/
```
Optional: `--image frame.png` (overlay plot), `--openlabel scenario.json` (validation),
`--ransac-thresh-m` (RANSAC inlier threshold, default 0.50 m). All options: [CLI reference](../user_guide/cli.md#cosmo-calibrate).

Outputs in `runs/<timestamp>_calibrate_<stem>/outputs/`:
- `<base_name>_calibration.json` — the file to use for conversion
- `<base_name>_homography_fit_summary.json` — RMSE and inlier count
- `<base_name>_homography_fit_residuals.png`
- `<base_name>_overlay_markers_on_image.png` (only with `--image`)

## Step 3 — Convert using the calibration
```bash
cosmo convert scenario.json \
  --calibration runs/<calibrate_run>/outputs/<base_name>_calibration.json \
  -o runs/
```
In the GUI, **Use calibration in Run tab** fills it in for you.

## Troubleshooting
If positions look wrong, check RMSE and inlier count in the fit summary and the residuals plot. A few
badly matched points dominate the fit; fix or remove them.
