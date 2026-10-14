# Workflow: from annotated video to Omega‑Prime

This guide walks through a conversion, in the GUI and with the CLI. Install COSMO first (see the
[README](../../README.md#install)). Every option is listed in the [CLI reference](cli.md).

## 1) Collect the inputs

| Input | Typically produced by | Needed for |
|---|---|---|
| OpenLABEL JSON | SAVANT markit (annotation) | **required**: objects and rotated bboxes per frame |
| `*_georef_data.json` | [ORBIT](https://github.com/RI-SE/ORBIT) | pixel→ground mapping (recommended) |
| `*_calibration.json` | `cosmo calibrate` | pixel→ground mapping when no georef exists ([how-to](../how-to/calibration.md)) |
| OpenDRIVE `.xodr` | ORBIT | map in the MCAP, local object frame |
| DJI `.srt` | the drone (concatenated per sequence) | exact frame timing, time of day, sun, illumination |
| `FlightRecord_*.video_stats.json` | flight-log extraction | oblique bbox correction; start time/position without SRT |
| `DPR_*.json` | upstream tools (dataprov) | provenance of each input |

Without georef or calibration COSMO still runs, but positions are only a debug scale of the pixels.

## 2a) Convert in the GUI

Start with `cosmo gui`. In the **Run** tab:
1. Pick the OpenLABEL file, and the georef (or calibration) file. Optionally the OpenDRIVE map and the DJI SRT.
2. Choose the output: a runs base directory (per-run folder) or names derived from the input; tick CSV and/or MCAP.
3. Optional alignment tweaks: swap/flip X/Y, XY offset, yaw offset, FPS override.
4. **Run conversion**, then **Open run folder**.

The **Calibration** tab computes a calibration file, the **Plot** tab plots trajectories from a produced
or browsed MCAP (matplotlib, or Altair in the browser) with recording info, and **Settings** holds log options.
Flight record, bbox correction, timezone and provenance options are CLI-only.

## 2b) Convert with the CLI

Minimal:
```bash
cosmo convert scenario.json --georef-data site_georef_data.json --odr site.xodr -o runs/
```

All optional inputs, as used for drone campaigns (DJI folder layout):
```bash
cosmo convert An_VIDa_<ts>_D.json \
  --georef-data drone_processed/sequenceN/VIDr_ff_<ts>_georef.json \
  --odr map.xodr \
  --srt drone_concatenated/sequenceN/VIDc_<ts>_D.srt \
  --flight-record drone_concatenated/sequenceN/FlightRecord_<ts>.video_stats.json \
  --srt-prov drone_concatenated/sequenceN/DPR_VIDc_<ts>_D.json \
  --output-prefix out/OmegaPrime_<name> \
  --prov-out out/DPR_OmegaPrime_<name>.json
```
The SRT must belong to the annotated video (OpenLABEL frame N = SRT `FrameCnt` N+1); see
[SRT input](../reference/inputs-srt.md).

## 3) Optional: oblique bbox correction

For a tilted (non-nadir) drone camera, bbox footprints are distorted. Either correct inline:
```bash
cosmo convert scenario.json --georef-data georef.json \
  --flight-record video_stats.json --bbox-correction analytical -o runs/
```
or write a corrected OpenLABEL first with `cosmo correct` (useful to inspect it), then convert that file.
Details: [georef pipeline, step 4](../developer_guide/georef-pipeline.md#step-4--oblique-correction-optional).

## 4) Check the result

- `run_summary.json` in the run folder: `alignment_source` (`georef-data`, `calibration` or `none`),
  `fps_used`, applied offsets/flips and which outputs were produced.
- The log: warnings about SRT coverage, dropped frames or timezone mismatches.
- Visually: `trajectory-explorer --xodr map.xodr --a runs/<run>/outputs/<name>.csv` (compare up to three
  sources with `--b`/`--c`).

If objects look mirrored, rotated or shifted, fix with `--swap-xy`, `--flip-x/--flip-y`,
`--yaw-offset-deg` and `--xy-offset DX DY` (GUI: alignment tweaks); more in
[troubleshooting](../how-to/troubleshooting.md).

What the outputs contain: [CSV and run artifacts](../reference/outputs-omega-prime.md),
[OSI/MCAP and mapping tables](../reference/osi-mapping.md).
