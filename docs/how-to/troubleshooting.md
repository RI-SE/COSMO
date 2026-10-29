# Troubleshooting

## 1) Check what COSMO used (fastest fix)
Open `<run_dir>/run_summary.json` to see:
- `alignment_source` (`georef-data`, `calibration`, or `none`)
- applied `xy_offset`, `yaw_offset_deg`, and flip/swap flags
- which outputs were actually produced

## 2) MCAP missing?
MCAP needs `betterosi`: install the `mcap` dependency group (`uv sync --group mcap`, included in
`cosmo-cli`/`cosmo-gui`) and rerun.

## 3) Alignment looks mirrored/rotated/shifted?
Use post-projection options:
- `--flip-x`, `--flip-y`, `--swap-xy`
- `--yaw-offset-deg`, `--xy-offset DX DY`

## 4) ORBIT georef rejected?
If ORBIT georef includes `transform_method` not equal to `homography`, COSMO will error (it expects a homography-style pixel→ground transform).

## 5) Projection mismatch between georef and XODR?
The georef `proj_string` and the OpenDRIVE `<geoReference>` must describe the same projection. Re-export
both from ORBIT with the same projection.

## 6) Timestamps drift or start time looks wrong?
- Without `--srt`, the frame period comes from `--fps`, else the georef/calibration `fps`, else 30 fps; DJI video is
  usually 29.97 fps. Pass the SRT of the annotated video ([SRT input](../reference/inputs-srt.md)).
- A start-time warning between SRT and flight record usually means a wrong `--timezone`.
- A one-frame (33 ms) time offset against the SRT: FieldDataLab `vidrectify` output made before 2026-10-21 dropped
  the first frame. `--frame-offset auto` (default) detects it from `--prov-in`. A warning says when it can't (e.g.
  annotations edited in SAVANT edit, which records no provenance yet); then pass `--frame-offset 1` for such data.
