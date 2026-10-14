
<p align="center">
  <picture>
    <source srcset="docs/images/cosmo_logo_with_text.drawio.svg" type="image/svg+xml">
    <img src="docs/images/cosmo_logo_with_text.drawio.png" width="700" alt="COSMO logo" />
  </picture>
</p>

# COSMO - OpenLABEL + OpenDRIVE → Omega‑Prime

[![CI](https://github.com/MickOls/COSMO/actions/workflows/ci.yml/badge.svg)](https://github.com/MickOls/COSMO/actions/workflows/ci.yml)
[![License: GPL v3](https://img.shields.io/badge/License-GPL_v3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
![Python](https://img.shields.io/badge/python-3.10%20%E2%80%93%203.14-blue)
[![OpenDrive](https://img.shields.io/badge/OpenDRIVE-1.8-orange)](https://www.asam.net/standards/detail/opendrive/)
[![OpenLABEL](https://img.shields.io/badge/OpenLABEL-1.0-orange)](https://www.asam.net/standards/detail/openlabel/)
[![Omega‑Prime](https://img.shields.io/badge/Omega--Prime-0.2.0-orange)](https://github.com/ika-rwth-aachen/omega-prime)

COSMO converts **ASAM OpenLABEL** annotations into:

- **Omega‑Prime compatible CSV** (moving-object table)
- optionally **MCAP** containing **ASAM OSI GroundTruth**, optionally bundled with an **OpenDRIVE** map.

![COSMO main window](docs/images/screenshot_main_window.png)

> [!NOTE]
> This is a beta version. Bugs and missing features should be expected. Github issues can be added for bug reports or feature requests.

> Maintained by **RISE Research Institutes of Sweden**. Developed in the SYNERGIES project.

---
## Documentation (quick links)
- [Docs index](docs/README.md)
- [Quickstart](docs/getting-started/quickstart.md)
- [CLI](docs/user_guide/cli.md)
- [Outputs (CSV/MCAP)](docs/reference/outputs-omega-prime.md)
- [Troubleshooting](docs/how-to/troubleshooting.md)
---
## Quick start (recommended: ORBIT georef)

Install (editable + dev tools). Recommended with **uv** (the project ships a `uv.lock`):
```bash
uv sync --group dev          # or --all-groups for gui/mcap/plot/etc.
```

With **pip** (≥ 25.1, which understands `[dependency-groups]`):
```bash
python -m pip install -U pip
python -m pip install -e . --group dev
```
> Note: `[dependency-groups]` (dev, gui, mcap, …) are not pip *extras*, so
> `pip install -e ".[dev]"` does **not** work. CI installs the base package plus
> tools explicitly: `pip install -e . pytest ruff` (adding `betterosi mcap` for
> MCAP integration tests).

> Tip 1: The **GUI** is launched with `cosmo` or `cosmo gui`.
> Tip 2: In headless environments, prefer explicit subcommands such as `cosmo convert ...` rather than plain `cosmo`, which may start the GUI.

Convert using [ORBIT](https://github.com/RI-SE/ORBIT) georef as the primary pixel→ground mapping (**Convert** tab in the **GUI**):

```bash
cosmo convert scenario.json \
  --georef-data path/to/*_georef_data.json \
  --odr path/to/map.xodr \
  -o runs/
```

This creates a per-run folder (default base runs/) with:
- outputs/<base_name>.csv
- outputs/<base_name>.mcap (if enabled + betterosi installed)
- run_inputs.json, run_summary.json

> Tip: in some setups, running plain cosmo (no args) may start the GUI; use subcommands in headless environments.

#### Backup workflow: calibration.json (when ORBIT georef is unavailable)
Compute calibration (pixel→ground homography):

```bash
cosmo calibrate --inputs pixel_pairs.csv visual_markers.csv map.xodr -o runs/
```
Calibration outputs are written to outputs/ as:
- <base_name>_calibration.json
- <base_name>_homography_fit_summary.json
- <base_name>_homography_fit_residuals.png
- <base_name>_overlay_markers_on_image.png (only if --image is provided)

Use the calibration file for conversion fallback:

```bash
cosmo convert scenario.json \
  --calibration runs/<calibrate_run>/outputs/<base_name>_calibration.json \
  -o runs/
```
---
## Transformations (OpenLABEL → Omega‑Prime CSV / OSI)

`cosmo convert` maps each OpenLABEL frame to one OSI `GroundTruth` (and CSV rows). Optional inputs
add data; a field is only written when its source is available.

### Geometry and kinematics
| Output | Derived from |
|---|---|
| Position x, y | rbbox centre (pixels) through the pixel→ground homography (`--georef-data`, else `--calibration`), then `--xy-offset/--swap-xy/--flip-*`. Without either: debug fallback `px × 0.01`, z = 0. With `cosmo correct` output: the corrected cuboid position. |
| Heading (yaw) | `−r_image + direction of image +x in the world` at the box position, wrapped to (−π, π] |
| Length, width | rbbox corners projected to the ground, measured along the heading; without a homography: class defaults |
| Height | class default (car 1.5 m, van 2.2 m, truck 3.5 m, bus 3.2 m, pedestrian 1.7 m, other 1.5 m; overridable via `default_dimensions_m` in the georef/calibration file). Trailers use the height of the towing vehicle (`towed-by` relation). |
| Timestamp | frame number × frame period. Period: from `--srt` (fitted to the SRT clock), else fps from georef / calibration / `--fps`, else 30 fps. Relative to video frame 0. |
| Velocity, acceleration | finite differences between an object's consecutive appearances, divided by the real time step (frame gaps are handled) |

### Object classes
Classes are resolved by `OntologyMapper` (`src/cosmo/converters/ontology_mapper.py`): a built-in table for the
SAVANT ontology 1.3.1, extended at run time with the ontology URLs declared in the OpenLABEL file (needs `rdflib`);
a class missing from the table inherits the mapping of its nearest mapped ancestor (`rdfs:subClassOf`). Unknown
classes become OTHER. Vehicle classification is only written for type VEHICLE.

| OpenLABEL class | OSI MovingObject type | Vehicle classification type | Vehicle role |
|---|---|---|---|
| Car | VEHICLE | CAR | CIVIL |
| Van | VEHICLE | DELIVERY_VAN | CIVIL |
| Truck | VEHICLE | HEAVY_TRUCK | CIVIL |
| Trailer, Caravan | VEHICLE | TRAILER | CIVIL |
| Bus | VEHICLE | BUS | PUBLIC_TRANSPORT ¹ |
| Tram | VEHICLE | TRAM | PUBLIC_TRANSPORT ¹ |
| Train | VEHICLE | TRAIN | CIVIL |
| Motorbike | VEHICLE | MOTORBIKE | CIVIL |
| Bicycle | VEHICLE | BICYCLE | CIVIL |
| StandupScooter | VEHICLE | STANDUP_SCOOTER | CIVIL |
| WheelChairUser | VEHICLE ² | WHEELCHAIR | CIVIL |
| Ambulance | VEHICLE | OTHER | AMBULANCE |
| Fire | VEHICLE | OTHER | FIRE |
| Police | VEHICLE | OTHER | POLICE |
| Vehicle, EmergencyVehicle, AgriculturalVehicle, ConstructionVehicle, SlowMovingVehicle | VEHICLE | OTHER | CIVIL |
| Human, Pedestrian | PEDESTRIAN | – | – |
| Animal | ANIMAL | – | – |
| RoadUser, DynamicObject, unknown | OTHER | – | – |

¹ Assumes buses and trams are in public service.
² OSI models wheelchairs only as a vehicle classification type, so wheelchair users are exported as vehicles.

The CSV `role`/`role_name` columns carry the same role for every object (CIVIL when the class implies none).

### Environmental conditions (`GroundTruth.environmental_conditions`, MCAP only)
| OSI field | Source | Mapping |
|---|---|---|
| `unix_timestamp` | `--srt` clock, else `--flight-record` `time_range.start` + frame period | UTC seconds per frame |
| `time_of_day.seconds_since_midnight` | same as above | local time in `--timezone` (default Europe/Stockholm) |
| `sun.azimuth`, `sun.elevation` | time above + position: SRT GPS per frame, else flight-record mean lat/lon, else georef `reference_point` | NOAA solar position, no refraction. OSI azimuth is counterclockwise from north (west = +π/2). |
| `ambient_illumination` | `--srt` exposure (ISO, shutter, f-number, EV compensation) | lux ≈ 2.5·2^EV100 (reflected-light estimate, about ±1 level) → OSI LEVEL1–9 lux bands |
| `precipitation` | OpenLABEL weather annotation ³ | `precipitation=none` → NONE; intensity light/moderate/heavy → LIGHT/MODERATE/HEAVY; otherwise UNKNOWN |
| `fog` | OpenLABEL weather `visibility_km` ³ | OSI visibility bands (≥40 km EXCELLENT, 10–40 GOOD, 4–10 MODERATE, 2–4 POOR, 1–2 MIST, 0.2–1 LIGHT, 0.05–0.2 THICK, <0.05 DENSE) |
| `clouds.fractional_cloud_cover` | OpenLABEL weather `cloud_cover` ³ | clear → ZERO_OKTAS, partly_cloudy → FOUR_OKTAS (assumed 3–5 oktas), overcast → EIGHT_OKTAS |
| temperature, pressure, humidity, wind | – | not set (no source yet) |

³ `WeatherContext` (per frame interval) takes precedence over the sequence-level `WeatherTag`; both are written by
SAVANT markit's VLM analysis.

Not exported: the road, junction, traffic, structures, notes and VLM-analysis tags. Road and junction layout is in the
embedded OpenDRIVE map; the rest has no OSI field.

Example with all optional time/environment inputs (DJI drone folder layout):

```bash
cosmo convert An_VIDa_<ts>_D.json \
  --georef-data drone_processed/sequenceN/VIDr_ff_<ts>_georef.json \
  --odr map.xodr \
  --flight-record drone_concatenated/sequenceN/FlightRecord_<ts>.video_stats.json \
  --srt drone_concatenated/sequenceN/VIDc_<ts>_D.srt \
  --srt-prov drone_concatenated/sequenceN/DPR_VIDc_<ts>_D.json \
  --output-prefix out/OmegaPrime_<name> --prov-out out/DPR_OmegaPrime_<name>.json
```

The SRT must belong to the video that was annotated (frame N of the OpenLABEL file = SRT `FrameCnt` N+1). See
[SRT input](docs/reference/inputs-srt.md).

---
## Optional preprocessing: correct oblique-drone bboxes

For footage from an oblique (tilted) drone camera, object bounding boxes are
geometrically distorted. `cosmo correct` rewrites an OpenLABEL file into a
corrected OpenLABEL file before conversion:

```bash
cosmo correct scenario.json \
  --georef-data path/to/*_georef_data.json \
  --flight-record path/to/FlightRecord_*.video_stats.json \
  -o scenario_corrected.json
```

Then feed the corrected file to `cosmo convert`. Key options:
- `--georef-data` / `--calibration`: pixel→ground mapping (one is required).
- `--flight-record` (required): drone/camera pose per frame.
- `--bbox-correction {analytical,3d}`: correction mode (default: analytical).
- `--output-coords {pixel,geo,both}`: corrected pixel rbbox (default), world cuboid, or both.
- `--stabilize-size`: replace per-frame dimensions with the per-object average.

---
## Trajectory explorer (visual inspection)

`trajectory-explorer` is a standalone Qt viewer for inspecting and comparing
object trajectories over an OpenDRIVE map. It loads up to three sources at once
(CSV, MCAP, or OpenLABEL JSON), so you can compare e.g. raw vs corrected output.

```bash
trajectory-explorer \
  --xodr path/to/map.xodr \
  --a runs/<run>/outputs/<base_name>.csv \
  --b path/to/scenario_corrected.json
```

It needs the GUI dependencies (PyQt5); install its dependency group with
`uv sync --group trajectory-explorer`. Slots `--a/--b/--c` each accept a
`.csv`, `.mcap`, or `.json` file.

---
## Documentation (start here)

* 📌 [Docs index](docs/README.md)
* Getting started:
  - [Quickstart](docs/getting-started/quickstart.md)
* User guide:
  - [CLI reference](docs/user_guide/cli.md)
  - [Workflow overview](docs/user_guide/workflow.md)
* How-to:
  - [Create and use calibration](docs/how-to/calibration.md)
  - [Troubleshooting](docs/how-to/troubleshooting.md)
* Reference:
  - [OpenLABEL input](docs/reference/inputs-openlabel.md)
  - [OpenDRIVE input](docs/reference/inputs-opendrive.md)
  - [DJI SRT input](docs/reference/inputs-srt.md)
  - [Outputs (CSV/MCAP)](docs/reference/outputs-omega-prime.md)
  - [OSI/MCAP topics](docs/reference/osi-mcap.md)
* Developer guide:
  - [Georef pipeline](docs/developer_guide/georef-pipeline.md)
  - [Testing](docs/developer_guide/tests.md)

---

## OSI/MCAP notes

* MCAP output requires betterosi. If MCAP is requested but betterosi is missing, COSMO logs that it will write CSV only.
* MCAP topics written:
  - ground_truth_map (OpenDRIVE, if provided)
  - ground_truth (OSI GroundTruth per frame)

---

## Status and License

- Beta.
- COSMO is licensed under the [GNU General Public License v3.0 (GPL-3.0)](LICENSE).

## Acknowledgement
<br><div align="center">
  <img src="docs/images/synergies.svg" alt="Synergies logo" width="200"/>
</div>

This package is developed as part of the [SYNERGIES](https://synergies-ccam.eu/) project.

<br><div align="center">
  <img src="docs/images/funded_by_eu.svg" alt="Funded by EU" width="200"/>
</div>

Funded by the European Union. Views and opinions expressed are however those of the author(s) only and do not necessarily reflect those of the European Union or European Climate, Infrastructure and Environment Executive Agency (CINEA). Neither the European Union nor the granting authority can be held responsible for them.
