# Input: OpenDRIVE

COSMO can embed an OpenDRIVE map into MCAP when you pass `--odr/--opendrive`.

## Accepted inputs
The CLI describes OpenDRIVE input as `.xodr/.xml/.txt` (useful if XML is stored with a `.txt` extension).

## Usage
```bash
cosmo convert scenario.json \
  --georef-data path/to/*_georef_data.json \
  --odr path/to/map.xodr \
  -o runs/
```