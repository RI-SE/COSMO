# Input: drone flight record (optional)

`--flight-record` takes a `FlightRecord_*.video_stats.json` file: per-video statistics extracted from the
DJI flight log. `--flight-record-sequence N` selects the video sequence in the file (default 0).

## What COSMO uses
| Field (`sequences[N]`) | Used for |
|---|---|
| `stats.osd.height_agl.mean` | camera height for bbox correction |
| `stats.osd.yaw.mean`, `stats.gimbal.yaw.mean`, `stats.gimbal.pitch.mean` | camera orientation for bbox correction |
| `stats.osd.latitude.mean`, `stats.osd.longitude.mean` | camera position with `--use-gps-cam-pos`; sun position when no `--srt` is given |
| `time_range.start` (UTC) | absolute time and time of day when no `--srt` is given |

Bbox correction (`--bbox-correction`, or `cosmo correct`) needs the flight record; see the
[georef pipeline](../developer_guide/georef-pipeline.md#step-4--oblique-correction-optional).
For environmental conditions an SRT is more precise (per-frame time and position); the flight record
start time is the fallback. With both, COSMO warns if their start times differ by more than 5 s.

Use `--flight-record-prov` to inline the flight record's provenance into `--prov-out`.
