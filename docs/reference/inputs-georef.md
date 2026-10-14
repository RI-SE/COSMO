# Input: pixel→ground mapping (ORBIT georef or calibration)

COSMO projects pixel positions to the ground with a 3×3 homography `H`. It comes from one of two files,
in this order:

1. **ORBIT georef** (`--georef-data`, recommended): `*_georef_data.json` exported by
   [ORBIT](https://github.com/RI-SE/ORBIT). Full exports are read with the `orbit-georef` library; for
   partial files `H` is taken from `transformation_matrix`, else `homography`, else the inverse of
   `inverse_matrix`. `transform_method` must be `homography` (or absent), otherwise COSMO stops.
2. **Calibration** (`--calibration`, fallback): `*_calibration.json` from `cosmo calibrate`
   ([how-to](../how-to/calibration.md)); uses `homography`, else `intrinsics` + `extrinsics`.
3. **None** (debug only): pixels × 0.01, with class-default dimensions.

`run_summary.json` records which one was used (`alignment_source`).

## Other fields used
| Field | File | Used for |
|---|---|---|
| `fps` | georef, else calibration | frame period when neither `--srt` nor `--fps` is given (else 30) |
| `default_dimensions_m` | calibration, else georef | per-class height and fallback length/width |
| `proj_string` | georef | OSI `proj_string`; checked against the OpenDRIVE `<geoReference>` |
| `reference_point` (else first `control_points` entry) | georef | country code and sun position fallback |

Use `--georef-prov` to inline the georef file's provenance into `--prov-out`.
The full projection chain is described in the [georef pipeline](../developer_guide/georef-pipeline.md).
