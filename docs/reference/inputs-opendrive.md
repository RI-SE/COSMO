# Input: OpenDRIVE (optional)

`--odr/--opendrive` takes an OpenDRIVE map (`.xodr`, or XML stored as `.xml`/`.txt`), typically exported by
[ORBIT](https://github.com/RI-SE/ORBIT) together with the georef file. It is not used for positioning.

## What COSMO uses it for
- Embedded in the MCAP as topic `ground_truth_map` (see [OSI/MCAP output](osi-mapping.md#mcap-topics)).
- With `--georef-data`, the header `<geoReference>` must match the georef `proj_string` (ignoring
  `+lat_0/+lon_0`); otherwise, or if either is missing, COSMO stops. Re-export both from ORBIT to fix.
- `--object-frame local`: object positions are shifted by the header `<offset>` into the map-local frame,
  and the offset is recorded in OSI `proj_frame_offset`.
- `--strip-xodr-namespace`: removes XML namespace declarations before embedding (workaround for
  omega-prime's namespace-unaware XPath).

Use `--opendrive-prov` to inline the map's provenance into `--prov-out`.
