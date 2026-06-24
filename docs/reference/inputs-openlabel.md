# Input: OpenLABEL (expected structure)

COSMO reads OpenLABEL JSON and extracts object metadata and per-frame rotated bounding boxes (rbbox).

## Required content
COSMO expects, per frame, objects that contain an `rbbox` with at least five values:

`[cx, cy, w_px, h_px, yaw]` in pixel coordinates.

COSMO looks for rbbox values under:
- `openlabel.frames[*].objects[*].object_data.rbbox.val`, or
- `...rbbox.shape.val` (supported variant)

## Optional object metadata
If present under `openlabel.objects`, COSMO uses:
- `type` (e.g., car, truck, pedestrian)
- optional `subtype` and `role` (mapped to OSI-like naming/codes when possible)
## Confidence
COSMO can read confidence from:
`object_data.vec.confidence.val` (if present).