# OSI + MCAP output

COSMO can write MCAP containing OSI `GroundTruth` messages (one per frame), and optionally a map message embedding OpenDRIVE.

## Dependencies
MCAP output requires `betterosi`. If `--mcap` is enabled but `betterosi` is missing, COSMO falls back to CSV only and logs a message.

## Topics written
COSMO writes:
- `ground_truth_map` (OpenDRIVE embedded, if provided)
- `ground_truth` (OSI GroundTruth per frame)

## Install optional deps
```bash
python -m pip install betterosi mcap
```

(CI installs these for MCAP integration tests.)
