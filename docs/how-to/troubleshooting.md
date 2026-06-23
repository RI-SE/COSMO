# Troubleshooting

## 1) Check what COSMO used (fastest fix)
Open `<run_dir>/run_summary.json` to see:
- `alignment_source` (`georef-data`, `calibration`, or `none`)
- applied `xy_offset`, `yaw_offset_deg`, and flip/swap flags
- which outputs were actually produced

## 2) MCAP missing?
If MCAP was requested but missing:
- install `betterosi` (MCAP requires it)
- rerun the conversion

## 3) Alignment looks mirrored/rotated/shifted?
Use post-projection options:
- `--flip-x`, `--flip-y`, `--swap-xy`
- `--yaw-offset-deg`, `--xy-offset DX DY`

## 4) ORBIT georef rejected?
If ORBIT georef includes `transform_method` not equal to `homography`, COSMO will error (it expects a homography-style pixel→ground transform).