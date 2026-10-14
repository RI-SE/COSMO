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
