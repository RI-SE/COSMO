# Georef Pipeline: Pixel → World Position and Heading

How COSMO maps detected objects from image coordinates to world (UTM) coordinates and computes their headings.

---

## Inputs

| File | Used for |
|------|----------|
| `*_georef.json` | Homography matrix `H` (pixel → UTM) |
| `*.xodr` | Projection string validation + map rendering only |
| `FlightRecord_*.video_stats.json` | Oblique bbox correction only (optional) |

---

## Step 1 — The H matrix

`H` is a 3×3 projective (homography) matrix stored as `transformation_matrix` in the georef file.
It is computed by ORBIT, not COSMO. ORBIT derives it from **control points**: identifiable ground
features the user marks in both the drone image and the map, solving a least-squares fit to find the
transform that maps those pixel positions to their known UTM coordinates.

**COSMO only consumes the resulting matrix.** The control points themselves are not loaded or used.

---

## Step 2 — Position: pixel → world

Each detected object has a center pixel `(cx, cy)` from its OpenLabel rbbox. COSMO applies:

```
[X, Y, w]  =  H @ [cx, cy, 1]
X_world    =  X / w
Y_world    =  Y / w
```

This gives a UTM coordinate directly. No explicit altitude math is needed for position — `H` already
encodes the perspective projection onto the ground plane, because ORBIT's control points were placed
on the ground.

---

## Step 3 — Heading: image angle → world angle

The rbbox `yaw` is measured from the image **+x axis** (rightward in pixels). In world space, image
+x does not generally point East — the drone faces an arbitrary direction and perspective distortion
means the mapping varies across the image.

`_h_rotation_angle(H, cx, cy)` resolves this locally for each object:

1. Map `(cx, cy)` → world point `P0`
2. Map `(cx + 1, cy)` → world point `P1`  (one pixel to the right)
3. `h_rot = atan2(P1.Y − P0.Y, P1.X − P0.X)` — the compass angle that image +x corresponds to at that position

Then (the `yaw_img` term is negated because image y points down, opposite to the world frame):

```
heading_world = angle_wrap(-yaw_img + h_rot)
```

### Why per-object, not once at image center?

`H` is a projective (not affine) transform. The local rotation it induces varies continuously across
the image — the angular error from using a single center-pixel approximation is 1–3° near the image
edges. Computing `h_rot` at each object's own `(cx, cy)` corrects this.

---

## Step 4 — Oblique correction (optional)

When the gimbal is tilted, objects not directly below the drone are displaced in the image relative
to their true ground position. COSMO offers two paths to correct this.

### Inline path — `cosmo convert --bbox-correction`

Pass `--bbox-correction analytical` or `--bbox-correction 3d` to apply correction during conversion.
No intermediate file is produced.

```bash
cosmo convert input.json --georef-data georef.json \
  --bbox-correction 3d \
  --flight-record path/to/FlightRecord_*.video_stats.json \
  -o runs/
```

### Pre-processing path — `cosmo correct`

`cosmo correct` applies correction before conversion and writes a corrected OpenLabel file.
Use this when you want the corrected file as an intermediate artefact (e.g. for inspection in
the trajectory explorer).

```bash
cosmo correct input.json -o corrected.json \
  --georef-data georef.json \
  --flight-record path/to/FlightRecord_*.video_stats.json \
  --output-coords both \
  --stabilize-size

cosmo convert corrected.json --georef-data georef.json -o runs/
```

`--output-coords` controls what `cosmo correct` writes back to the OpenLabel:

| Value | Effect |
|---|---|
| `pixel` (default) | Updates rbbox with corrected pixel coords |
| `geo` | Replaces rbbox with world-frame cuboid |
| `both` | Updates rbbox AND adds cuboid |

`--stabilize-size` (available in both commands) replaces per-frame L/W/H with the per-object mean.
In `cosmo correct` it also writes `size_std` and `size_deviation` vec entries to the output.

### What the flight record provides

The `FlightRecord_*.video_stats.json` file supplies:

- `drone_height` (AGL, metres)
- `gimbal_pitch` (e.g. −83° → nearly nadir but slightly oblique)
- `drone_yaw + gimbal_yaw` → `camera_azimuth`

From these, a full 3D camera model (`DroneCamera`) is built with rotation matrix R and intrinsics K.
The corrector ray-casts each detected bbox to the ground plane and returns a corrected `(X, Y)` and
heading. The `analytical` mode uses a faster homography-based approximation; `3d` requires the
flight record for the full ray-casting model.

Without any correction flag, the flight record is ignored entirely. `H` handles everything,
implicitly assuming the camera was approximately nadir when ORBIT's control points were set.

---

## Step 5 — The xodr map

The `.xodr` file is **not involved in positioning**. COSMO uses it for:

1. **Projection string validation** — checks that `georef.proj_string` matches the XODR
   `<geoReference>` so UTM zones cannot be silently mixed.
2. **Trajectory explorer rendering** — draws the road network as a visual backdrop.

---

## Step 6 — `yaw_offset_rad` (manual residual correction)

If headings are still off by a fixed amount after all of the above (e.g. systematic north-offset in
ORBIT's control point placement), `--yaw-offset-deg` can compensate. It rotates both positions
(around the world origin) and headings by the same angle.

---

## Data flow summary

```
OpenLabel rbbox (cx, cy, yaw_img)
        │
        │   ┌─── [pre-processing path] ──────────────────────────────────────────┐
        │   │  cosmo correct                                                      │
        │   │    BboxCorrector (analytical|3d + flight record)                   │
        │   │    --output-coords pixel|geo|both                                  │
        │   │    --stabilize-size → per-object mean L/W/H + size_std entries     │
        │   └──► corrected OpenLabel ──────────────────────────────────────────► │
        │                                                                         │
        │   ┌─── [inline path] ──────────────────────────────────┐               │
        │   │  cosmo convert --bbox-correction analytical|3d      │               │
        │   │    BboxCorrector (same models, no intermediate file)│               │
        │   └────────────────────────────────────────────────────┘               │
        │                                                                         │
        ├─── apply_homography(H, cx, cy) ──────────────────► (X_world, Y_world)  │
        │                                                                         │
        └─── _h_rotation_angle(H, cx, cy) → h_rot                               │
             angle_wrap(yaw_img + h_rot) ─────────────────► heading_world        │
                                                                    │             │
                                              [optional] yaw_offset_rad          │
                                                                    │             │
                                                             ► final (X, Y, yaw) │
```

---

## Size estimation variance

When `--stabilize-size` is used, the corrected OpenLabel contains per-object `size_std` and
per-frame `size_deviation`. Length typically shows higher variance than width, **both in absolute
metres and as a percentage of the mean**. This is expected behaviour arising from three compounding
factors:

### 1. Heading angle sensitivity (dominant cause)
The corrector decomposes each bbox into `along`/`across` components using the vehicle heading.
A heading estimation error of a few degrees rotates these axes and swaps some length into width.
Because L >> W (e.g. 4.5 m vs 2.0 m), the same angular jitter produces a proportionally larger
length error. A 3° error on a 4.5 m vehicle shifts ~0.24 m into/out of length vs ~0.10 m for width.

### 2. Camera geometry (depth vs lateral)
For a roadside/oblique camera, vehicles travel roughly parallel to the road. Width is mostly in
the image's lateral direction (well-resolved pixels). Length aligns more with the depth axis, where
homography reprojection is inherently less precise — small pixel errors in the front/rear keypoints
translate to larger metric errors.

### 3. Annotation consistency
Front and rear ends of vehicles are harder to annotate consistently than side profiles: partial
occlusion, similar appearance frame-to-frame, and cropping at frame edges all affect length more
than width.

### Guidance for annotators
To reduce length variance:
- **Annotate the full visible extent**, including partially occluded fronts/rears — do not shrink
  the bbox to only the clearly visible part.
- **Be consistent about which surface to anchor to**: always the outermost visible edge, not the
  closest clearly-visible feature (e.g. windscreen vs bumper).
- **Annotate length carefully when the vehicle heading is near 0° or 180°** relative to the camera
  (i.e. head-on or tail-on views) — this is where heading error has the largest effect on length.
- If the annotation tool supports it, prefer fitting to the vehicle's geometric body rather than
  the detection region.

**Typical ranges:** `size_std` of ~10% for length and ~4% for width on a car is normal. Values
above ~20% may indicate noisy heading estimates or poor homography coverage in that image region.
