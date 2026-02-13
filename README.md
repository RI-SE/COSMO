
<p align="center">
  <picture>
    <source srcset="logo/cosmo_logo.svg" type="image/svg+xml">
    <img src="logo/cosmo_logo.png" width="260" alt="COSMO logo" />
  </picture>
</p>

# COSMO — OpenLABEL → Omega‑Prime (CSV) + optional OSI/MCAP

[![CI](https://github.com/MickOls/COSMO/actions/workflows/ci.yml/badge.svg)](https://github.com/MickOls/COSMO/actions/workflows/ci.yml)
[![License: GPL v3](https://img.shields.io/badge/License-GPL_v3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
![Python](https://img.shields.io/badge/python-3.10%20%E2%80%93%203.13-blue)
[![OpenDrive](https://img.shields.io/badge/OpenDRIVE-1.8-orange)](https://www.asam.net/standards/detail/opendrive/)
[![OpenLABEL](https://img.shields.io/badge/OpenLABEL-1.0-orange)](https://www.asam.net/standards/detail/openlabel/)
[![Omega‑Prime](https://img.shields.io/badge/Omega--Prime-0.2.0-orange)](https://github.com/ika-rwth-aachen/omega-prime)

COSMO converts **ASAM OpenLABEL** annotations into:

- **Omega‑Prime compatible CSV** (moving-object table)
- optionally **MCAP** containing **ASAM OSI GroundTruth**, optionally bundled with an **OpenDRIVE** map.

![COSMO main window](docs/images/screenshot_main_window.png)

 [!NOTE]
> This is a beta version. Bugs and missing features should be expected. Github issues can be added for bug reports or feature requests.

> Maintained by **RISE Research Institutes of Sweden**. Developed in the SYNERGIES project.

---
## Documentation (quick links)
- [Docs index](docs/README.md)
- [Quickstart](docs/getting-started/quickstart.md)
- [CLI](docs/user-guide/cli.md)
- [Outputs (CSV/MCAP)](docs/reference/outputs-omega-prime.md)
- [Troubleshooting](docs/how-to/troubleshooting.md)
---
## Quick start (recommended: ORBIT georef)

Install (editable + dev tools):
```bash
python -m pip install -U pip
python -m pip install -e ".[dev]"
```
(CI installs COSMO using this method.)

> Tip 1: The **GUI** is launced with `cosmo` or `cosmo gui`.
> Tip 2: During development with downloaded repro, all `cosmo` commands can be replaced with `python run_cosmo.py ...`, `cosmo.cmd ...` or `cosmo.ps1 ...`. `python run_gui.py` always starts the **GUI**.

Convert using ORBIT georef as the primary pixel→ground mapping (**Convert** tab in the **GUI**):

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
## Documentation (start here)

* 📌 Docs index: docs/README.md
* Getting started:
  - docs/getting-started/installation.md
  - docs/getting-started/quickstart.md
* User guide:
  - docs/user-guide/cli.md
  - docs/user-guide/workflow.md
* How-to:
  - docs/how-to/orbit-georef.md
  - docs/how-to/calibration.md
  - docs/how-to/troubleshooting.md
* Reference:

  - docs/reference/inputs-openlabel.md
  - docs/reference/inputs-opendrive.md
  - docs/reference/outputs-omega-prime.md
  - docs/reference/osi-mcap.md

---

## OSI/MCAP notes

* MCAP output requires betterosi. If MCAP is requested but betterosi is missing, COSMO logs that it will write CSV only.
* MCAP topics written:
  - ground_truth_map (OpenDRIVE, if provided)
  - ground_truth (OSI GroundTruth per frame) [risecloud-...epoint.com]

---

## Status and License

- Beta.
- ORBIT is licensed under the [GNU General Public License v3.0 (GPL-3.0)](LICENSE).

## Acknowledgement
<br><div align="center">
  <img src="docs/synergies.svg" alt="Synergies logo" width="200"/>
</div>

This package is developed as part of the [SYNERGIES](https://synergies-ccam.eu/) project.

<br><div align="center">
  <img src="docs/funded_by_eu.svg" alt="Funded by EU" width="200"/>
</div>

Funded by the European Union. Views and opinions expressed are however those of the author(s) only and do not necessarily reflect those of the European Union or European Climate, Infrastructure and Environment Executive Agency (CINEA). Neither the European Union nor the granting authority can be held responsible for them.
