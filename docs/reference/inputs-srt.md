# Input: DJI SRT (optional)

DJI drones write an `.SRT` subtitle file next to each video with one entry per frame:

```text
1
00:00:00,000 --> 00:00:00,033
<font size="28">FrameCnt: 1, DiffTime: 33ms
2025-10-16 13:40:59.816
[iso: 100] [shutter: 1/400.0] [fnum: 2.8] [ev: 0] ... [latitude: 57.48494] [longitude: 12.00940] ...
```

## What COSMO uses
| SRT field | Used for |
|---|---|
| `FrameCnt` | frame index (OpenLABEL frame N = `FrameCnt` N+1) |
| timestamp (local camera clock) | frame period and absolute time. A straight line is fitted through all entries because the millisecond stamps jitter by ±1 ms. |
| `iso`, `shutter`, `fnum`, `ev` | ambient illumination estimate |
| `latitude`, `longitude` | sun position |

The timestamp has no timezone; `--timezone` (default `Europe/Stockholm`) is applied. If a flight record is
also given, COSMO warns when the two start times differ by more than 5 s (a likely timezone error).

## Which file to use
The SRT must match the annotated video frame for frame. In the drone folder layout:
- `drone_concatenated/sequenceN/VIDc_*.srt`: one SRT for the concatenated video, with continuous `FrameCnt`.
  Use this one, with `DPR_VIDc_*.json` as `--srt-prov`. Rectified/masked/annotated videos (VIDr/VIDp/VIDa)
  keep the VIDc frame numbering.
- `drone_raw/sequenceN/DJI_*_D.SRT`: one per raw clip, numbering restarts per clip. Not directly usable.

COSMO warns when the last OpenLABEL frame has no SRT entry, and when the SRT clock deviates from a constant
frame rate by more than one frame (dropped frames).
