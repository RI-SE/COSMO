# Troubleshooting

## 1) Check what COSMO used (fastest fix)
Open `<run_dir>/run_summary.json` to see:
- `alignment_source` (`georef-data`, `calibration`, or `none`)
- applied `xy_offset`, `yaw_offset_deg`, and flip/swap flags
- which outputs were actually produced [1](https://risecloud-my.sharepoint.com/personal/anders_thorsen_ri_se/Documents/Microsoft%20Copilot%20Chat%20Files/calibrate_app.py)[1](https://risecloud-my.sharepoint.com/personal/anders_thorsen_ri_se/Documents/Microsoft%20Copilot%20Chat%20Files/calibrate_app.py)

## 2) MCAP missing?
If MCAP was requested but missing:
- install `betterosi` (MCAP requires it) [1](https://risecloud-my.sharepoint.com/personal/anders_thorsen_ri_se/Documents/Microsoft%20Copilot%20Chat%20Files/calibrate_app.py)[1](https://risecloud-my.sharepoint.com/personal/anders_thorsen_ri_se/Documents/Microsoft%20Copilot%20Chat%20Files/calibrate_app.py)
- rerun the conversion

## 3) Alignment looks mirrored/rotated/shifted?
Use post-projection options:
- `--flip-x`, `--flip-y`, `--swap-xy`
- `--yaw-offset-deg`, `--xy-offset DX DY` [1](https://risecloud-my.sharepoint.com/personal/anders_thorsen_ri_se/Documents/Microsoft%20Copilot%20Chat%20Files/calibrate_app.py)[1](https://risecloud-my.sharepoint.com/personal/anders_thorsen_ri_se/Documents/Microsoft%20Copilot%20Chat%20Files/calibrate_app.py)

## 4) ORBIT georef rejected?
If ORBIT georef includes `transform_method` not equal to `homography`, COSMO will error (it expects a homography-style pixel→ground transform). [1](https://risecloud-my.sharepoint.com/personal/anders_thorsen_ri_se/Documents/Microsoft%20Copilot%20Chat%20Files/calibrate_app.py)