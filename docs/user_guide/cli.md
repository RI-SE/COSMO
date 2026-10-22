# CLI reference

| Command | Purpose |
|---|---|
| `cosmo convert` | OpenLABEL → Omega‑Prime CSV and OSI/MCAP |
| `cosmo correct` | oblique-drone bbox correction, OpenLABEL → corrected OpenLABEL |
| `cosmo calibrate` | compute a pixel→ground calibration (fallback for ORBIT georef) |
| `trajectory-explorer` | viewer for CSV/MCAP/OpenLABEL trajectories on an OpenDRIVE map |
| `cosmo` / `cosmo gui` | start the GUI (plain `cosmo` without a subcommand starts the GUI; use subcommands on headless machines) |

`cosmo-convert`, `cosmo-correct`, `cosmo-calibrate` and `cosmo-gui` are equivalent standalone scripts.
For a walkthrough see the [workflow guide](workflow.md).

---

## `cosmo convert`

```bash
cosmo convert INPUT.json [options]          # or --input / --openlabel INPUT.json
```

### Inputs
| Option | Description |
|---|---|
| `--georef-data PATH` | ORBIT `*_georef_data.json` (recommended pixel→ground mapping); see [georef input](../reference/inputs-georef.md) |
| `--calibration PATH` | calibration JSON from `cosmo calibrate` (fallback) |
| `--odr`, `--opendrive PATH` | OpenDRIVE map, embedded in the MCAP; see [OpenDRIVE input](../reference/inputs-opendrive.md) |
| `--srt PATH` | DJI SRT of the annotated video: frame timing and environmental conditions; see [SRT input](../reference/inputs-srt.md) |
| `--timezone IANA` | timezone of the SRT clock and for local time of day (default `Europe/Stockholm`) |
| `--flight-record PATH` | `FlightRecord_*.video_stats.json`: bbox correction, and start time/position without `--srt`; see [flight record input](../reference/inputs-flight-record.md) |
| `--flight-record-sequence N` | sequence index in the flight record (default 0) |
| `--fps FPS` | frame rate; overrides georef/calibration `fps`, but not `--srt` (default: from those files, else 30) |

### Outputs
| Option | Description |
|---|---|
| `-o`, `--output`, `--out DIR` | base directory for per-run folders (default `runs/`), or an explicit run directory |
| `--output-prefix PREFIX` | write `PREFIX.csv` / `PREFIX.mcap` directly, without a run folder |
| `--run-name NAME` | run folder name override |
| `--csv/--no-csv`, `--mcap/--no-mcap` | enable/disable each output (both on by default) |
| `--object-frame {global,local}` | `global` (default): positions in the georef CRS; `local`: shifted by the OpenDRIVE header offset (needs `--odr`), recorded in OSI `proj_frame_offset` |
| `--country-code N` | ISO 3166-1 numeric code for OSI `country_code` (e.g. 752 = Sweden); derived from the georef position if omitted |
| `--strip-xodr-namespace` | remove XML namespaces from the embedded OpenDRIVE (omega-prime workaround) |
| `--json` | print the run result as JSON |

### Alignment fixes (applied after projection)
`--swap-xy`, `--flip-x`, `--flip-y`, `--xy-offset DX DY` (metres), `--yaw-offset-deg DEG` (CCW).

### Inline bbox correction
| Option | Description |
|---|---|
| `--bbox-correction {none,analytical,3d}` | correct oblique-camera bboxes during conversion (default `none`; needs `--flight-record`) |
| `--camera-model KEY` | camera model for HFOV/resolution (default `mavic3pro-standard`) |
| `--hfov-deg FLOAT` | override horizontal FOV |
| `--use-gps-cam-pos` | use the drone GPS position as camera position instead of the homography-derived one |
| `--stabilize-size` | use per-object mean dimensions instead of per-frame ones |

### Uncertainty
See [outputs: companion OpenLABEL and SensorData](../reference/outputs-omega-prime.md#uncertainty-companion-openlabel-and-sensordata).

| Option | Description |
|---|---|
| `--video-quality PATH` | FieldDataLab `<video>.quality.json` of the annotated video: per-frame stabilisation error |
| `--no-companion` | do not write `<prefix>.companion.json` (written by default when a homography is known) |
| `--companion-frames` | companion with per-frame world cuboids, covariance and σ (variant B; about 3× the CSV size) |
| `--smooth-kinematics` | Kalman/RTS-smoothed position, velocity and acceleration in all outputs instead of raw differences |
| `--jerk-psd Q` | smoother process noise, white-jerk PSD in m²/s⁵ (default 2.0) |
| `--camera-uncertainty` | add camera height, gimbal pitch and azimuth error (flight-record stddev combined with assumed sensor error: 1 m, 0.5°, 1°) to the oblique-correction uncertainty |
| `--estimate-height` | **Experimental, not recommended yet.** Estimate each vehicle's effective height from how its corrected size changes along the track (needs `--bbox-correction`, a moving vehicle and ≥ 20 frames; others keep the class default). Changes positions and sizes of those objects. See the warning below |
| `--sensor-data` | also write an OSI `SensorData` topic (`sensor_data`) with existence probability and RMS errors |

> **Warning: `--estimate-height` probably does not produce good results at the moment.** It recovers the height
> on synthetic data, but on real annotations (GbgSaroRound 2025-10-16, human-edited boxes) it gave implausibly low
> heights (median 0.9 m for cars) and did not make sizes more consistent; the other distance-dependent size
> errors of image-drawn boxes are absorbed into the height. Use it only for experiments, not for data products.

### Provenance
| Option | Description |
|---|---|
| `--prov-out PATH` | write a W3C-PROV provenance record (dataprov) of this run |
| `--prov-in PATH` | continue an existing provenance chain |
| `--opendrive-prov`, `--georef-prov`, `--flight-record-prov`, `--srt-prov PATH` | provenance of each input, inlined into `--prov-out` |

---

## `cosmo correct`

Pre-process an oblique-drone OpenLABEL file before conversion. Use it when you want the corrected
OpenLABEL as an intermediate file (e.g. to inspect it in the trajectory explorer); otherwise use
`cosmo convert --bbox-correction`.

```bash
cosmo correct input.json -o corrected.json \
  --georef-data georef.json --flight-record video_stats.json \
  --output-coords geo --stabilize-size
cosmo convert corrected.json --georef-data georef.json -o runs/
```

| Option | Description |
|---|---|
| `-o`, `--output PATH` | corrected OpenLABEL (required) |
| `--georef-data` / `--calibration PATH` | pixel→ground mapping (one required) |
| `--flight-record PATH` (required), `--flight-record-sequence N` | drone/camera pose |
| `--bbox-correction {analytical,3d}` | `analytical` (default): homography-based, fast; `3d`: ray casting with the flight-record camera model |
| `--output-coords {pixel,geo,both}` | `pixel` (default): corrected rbbox; `geo`: replace rbbox with a world-frame cuboid (georef `proj_string`); `both` |
| `--stabilize-size` | per-object mean dimensions; adds `size_mean`, `size_std`, `size_deviation` (shown by the trajectory explorer) |
| `--camera-model`, `--hfov-deg`, `--use-gps-cam-pos` | as for `convert` |
| `--workers N` | parallel worker processes (default 1; more is usually slower) |
| `--prov-out`, `--prov-in`, `--georef-prov`, `--flight-record-prov` | provenance, as for `convert` |

---

## `cosmo calibrate`

Compute a calibration JSON (pixel→ground homography) into a per-run folder; see the
[calibration how-to](../how-to/calibration.md).

```bash
cosmo calibrate --inputs pixel_pairs.csv visual_markers.csv map.xodr -o runs/
cosmo calibrate pixel_pairs.csv visual_markers.csv map.xodr -o runs/          # positional
cosmo calibrate --pixel-pairs P.csv --visual-markers V.csv --opendrive map.xodr -o runs/
```
Use one input style, do not mix.

| Option | Description |
|---|---|
| `--image PATH` | frame image for the marker overlay plot |
| `--openlabel PATH` | OpenLABEL for validation |
| `--ransac-thresh-m M` | RANSAC inlier threshold on the ground (default 0.50 m) |
| `--fps`, `--image-width`, `--image-height` | written to the calibration file (defaults 30, 3840 × 2160); `convert` uses this fps when there is no `--srt`, `--fps` or georef fps |
| `-o`, `--run-name`, `--json`, `--prov-out`, `--prov-in` | as for `convert` |

---

## `trajectory-explorer`

```bash
trajectory-explorer --xodr map.xodr --a runs/<run>/outputs/<name>.csv --b corrected.json
```

| Option | Description |
|---|---|
| `--xodr PATH` | OpenDRIVE map |
| `--a`, `--b`, `--c PATH` | up to three sources to compare: `.csv`, `.mcap` or OpenLABEL `.json` |
| `--smooth-window N`, `--smooth-sigma S` | Gaussian smoothing in frames (default 5, sigma = window/2) |
| `--no-live-table` | skip table updates during playback (faster with many objects) |

Needs the `trajectory-explorer` dependency group.
