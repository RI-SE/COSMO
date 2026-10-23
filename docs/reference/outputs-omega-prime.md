# Outputs: Omega‑Prime CSV + OSI/MCAP

## Convert run artifacts

By default every run gets its own folder under `-o` (default `runs/`):

```text
runs/<timestamp>_convert_<base_name>/
  outputs/
    <base_name>.csv      (unless --no-csv)
    <base_name>.mcap     (unless --no-mcap; needs betterosi)
  run_inputs.json        resolved configuration and input paths
  run_summary.json       produced outputs, fps used, alignment source, applied offsets/flips
```

`<base_name>` is the OpenLABEL file stem (sanitized and lowercased). `--run-name` overrides the folder name.

With `--output-prefix PREFIX` the run folder is skipped and only `PREFIX.csv` / `PREFIX.mcap` are written
(used by pipelines). `--prov-out PATH` additionally writes a W3C-PROV provenance record (dataprov) listing
all inputs, with upstream provenance from the `--*-prov` options inlined.

## Uncertainty: companion OpenLABEL and SensorData

OSI GroundTruth has no uncertainty fields, so COSMO writes it next to the MCAP
([scenario-uncertainty](https://github.com/das-rise/scenario-uncertainty) `world` profile, SPEC P5).

**Inputs.** Box σ (`sigma_px`), `origin` and `p_exist` from the SAVANT OpenLABEL; the georef error from
orbit-georef (`position_covariance`: image and datum parts); the per-frame stabilisation error from
`--video-quality`. Missing inputs fall back to the SPEC P7 defaults, listed in `metadata.quality.defaults_used`
and as `[su:default_used]` in provenance; a missing value is "unknown", never "exact".

**Error levels** are kept apart, because they cancel differently:
- `datum` (one per recording): `metadata.quality.datum`; add it only for absolute positions.
- `position_cov_rel` per object and frame: box pixel error, stabilisation, georef image error and, with
  `--bbox-correction`, the vehicle-height assumption (σ = 15 % of h). Use it for positions relative to a map
  drawn on the same image, distances between vehicles and lane positions.
- The smoother averages only the frame-to-frame noise (pixel, stabilisation); height and georef errors are
  added back unreduced.

**`<prefix>.companion.json`** (default; `--no-companion` to skip):
- Variant A: `metadata.quality` (spec, datum and image covariance, input files with sha256, defaults used,
  shares of interpolated and human-made boxes, kinematics and oblique settings), `coordinate_systems.world`,
  objects with the same uids as the OSI ids plus `osi_type`, and per-object `num`: `n_frames`,
  `size_mean_l/w`, `size_std_l/w` (over measured, not interpolated, frames), `path_residual_rms` (scatter
  around the smoothed track), `share_interpolated`, `p_exist_mean`, and with `--estimate-height` `h_eff` and
  `h_eff_sigma` (m; sigma from the two halves of the track, at least 0.1 m; experimental and probably not
  reliable yet, see the [CLI warning](../user_guide/cli.md#uncertainty)). Tags, contexts, relations, actions,
  events and streams are copied from the input.
- Variant B (`--companion-frames`) adds `frames`: per object a world `cuboid` and `vec` entries `velocity`,
  `acceleration`, `position_cov_rel` (`[EE, EN, NE, NN]`, m²), `sigma_heading` (rad), `sigma_size`
  (`[σL, σW]`, m), `size_deviation`, `oblique` (`[method, shift_m, h_used, fit_residual]`: correction method, centre shift by the
  correction, silhouette height used (cars 1.15 m, other classes their vehicle height), and the 3d fit's final loss in px² — also when it fell back to `analytical`; for input
  corrected by `cosmo correct` copied from its per-box `oblique` entry), `origin`, `p_exist`, and with
  `--smooth-kinematics` `velocity_cov`/`acceleration_cov`. `frame_properties.timestamp` is the OSI time in
  seconds, the join key with the MCAP.

**`--sensor-data`**: topic `sensor_data`, one `osi3.SensorData` per frame. Each `DetectedMovingObject` has
`header.ground_truth_id` (= the GroundTruth object id), `measurement_state` (MEASURED for detected and human
boxes, PREDICTED for interpolated/tracked ones), `existence_probability` only when the box has `p_exist`, and
`base_rmse` (position RMSE including the datum term, dimension, yaw, and velocity/acceleration when smoothed).

## CSV schema (Omega‑Prime moving objects)

One row per object and frame:

```text
total_nanos, idx, x, y, z,
vel_x, vel_y, vel_z,
acc_x, acc_y, acc_z,
length, width, height,
roll, pitch, yaw,
type, subtype, role,
type_name, subtype_name, role_name
```

- `total_nanos`: frame timestamp relative to video frame 0.
- `idx`: stable integer ID, assigned by sorting the OpenLABEL object IDs and numbering from 1.
- `vel_*`, `acc_*`: finite differences between an object's consecutive appearances, divided by the real time step.
- `type`/`subtype`/`role` (OSI codes) and their `*_name` columns: see [object classes](osi-mapping.md#object-classes).
  `role` is CIVIL when the class implies no other role.

How each column is derived, and the OSI/MCAP content, is described in
[OSI/MCAP output and mapping](osi-mapping.md).
