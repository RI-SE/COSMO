# Outputs: Omega‑Prime CSV + OSI/MCAP

COSMO writes all outputs inside a per-run folder under `runs/` by default.

---

## Convert run artifacts

A convert run produces:
- `run_inputs.json` — resolved configuration and input paths
- `run_summary.json` — produced outputs, alignment source, applied offsets
- `outputs/<base_name>.csv` (if enabled)
- `outputs/<base_name>.mcap` (if enabled and `betterosi` installed)

`<base_name>` is derived from the OpenLABEL file stem (sanitized and lowercased).

---

## CSV schema (Omega‑Prime moving objects)

COSMO writes the following columns:

```text
total_nanos, idx, x, y, z,
vel_x, vel_y, vel_z,
acc_x, acc_y, acc_z,
length, width, height,
roll, pitch, yaw,
type, subtype, role,
type_name, subtype_name, role_name
```

Notes:

* idx is a stable integer ID assigned by sorting object IDs and numbering from 1.
* Kinematics (vel_*, acc_*) are estimated from differences between an object's consecutive appearances,
  divided by the real time step (frame period from `--srt`, else FPS).
* role/role_name: OSI vehicle role implied by the class (e.g. PUBLIC_TRANSPORT for Bus), else CIVIL.

Each `ground_truth` message also carries `environmental_conditions` when sources are available
(time, sun, illumination, precipitation, fog, clouds); see the Transformations section in the main README.


## MCAP / OSI (optional)
MCAP writing requires betterosi. If MCAP is requested but betterosi is missing, COSMO logs that it will write CSV only.
## Topics
COSMO writes topics without a leading slash:

* ground_truth_map — embedded OpenDRIVE (if provided)
* ground_truth — OSI GroundTruth messages per frame


## Alignment source (important)
COSMO chooses pixel→ground mapping in this order:

* ORBIT georef (--georef-data): prefers transformation_matrix as homography
* calibration (--calibration)
* none (debug fallback)