OL2OP
=====

OL2OP is set of Python script to transform OpenLABEL files from SAVANT to Omega-Prime files.



Input:
* An OpenLABEL files
* An OpenDRIVE file
* An Calibration.json file to align the OpenLABEL and the OpenDRIVE file

The conversion is done running the file "convert_openlabel_to_omega.py"

`usage: convert_openlabel_to_omega.py [-h] --openlabel OPENLABEL [--odr ODR] --out-prefix OUT_PREFIX`
`                                     [--calibration CALIBRATION] [--fps FPS] [--no-csv] [--no-mcap]`

`OpenLABEL ➜ OSI (MCAP) + Omega-Prime CSV`

`options:`
`  -h, --help            show this help message and exit`
`  --openlabel OPENLABEL`
`                        Path to OpenLABEL JSON (e.g., Saro_roundabout.json)`
`  --odr ODR             Path to OpenDRIVE XML (or .txt containing XML)`
`  --out-prefix OUT_PREFIX`
`                        Output file prefix (no extension), e.g., Saro_roundabout`
`  --calibration CALIBRATION`
`                        Path to calibration.json with homography or camera model`
`  --fps FPS             Override FPS (if not given in calibration)`
`  --no-csv              Skip CSV writing`
`  --no-mcap             Skip MCAP writing`
  
  
  
  
The Calibration.json can be calibrated running either:

`02_compute_calibration.py`
 

or

`compute_calibration.py`

The seconda one offers option validation vs. the OpenLABEL.json.


03_validate_openlabel_with_calibration.py can be used for validation afterwards.

Depending on image size it may be necessary to rescale the point2pixel. That may be done using
01_rescale_pixel_pairs.py



# OL2OP PyQt6 GUI

This is a lightweight PyQt6 front-end for the **OpenLABEL + OpenDRIVE → Omega-Prime** converter.

It is intended to be used alongside the updated converter script **`convert_openlabel_to_omega.py`**.

## Files

- `ol2op_gui.py` — the GUI application.
- `convert_openlabel_to_omega.py` — the converter (run by the GUI as a subprocess).

## Install

```bash
pip install PyQt6
# Optional (only needed if you want MCAP output):
pip install betterosi
```

## Run

```bash
python ol2op_gui.py
```

## Notes

- The GUI stores your last-used paths and settings using `QSettings`.
- The GUI executes the converter using `sys.executable`, so it will use the same
  Python environment you launched the GUI with.
- If you want to embed the GUI into ORBIT, you can:
  - add a menu entry that launches this window, or
  - refactor the worker to import and call `convert_openlabel_to_omega()` directly.


# SAVANTPostProcessing
Temporary repro for experiments for SAVANT postprocessing

ISO 8855 for cooredinate systems is available here for RISE: https://www.sis.se/api/document/get/82643
A local copy for RSIE DTS is stored on our sharepoint: https://risecloud.sharepoint.com/:b:/r/sites/PlitligaTransportsystem/Delade%20dokument/Dokument/Standarder/SS_ISO_8855_2011_EN.pdf?csf=1&web=1&e=hDD4Eh

