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

Then:

```
heading_world = angle_wrap(yaw_img + h_rot)
```

### Why per-object, not once at image center?

`H` is a projective (not affine) transform. The local rotation it induces varies continuously across
the image — the angular error from using a single center-pixel approximation is 1–3° near the image
edges. Computing `h_rot` at each object's own `(cx, cy)` corrects this.

---

## Step 4 — The flight record (oblique correction, optional)

The `FlightRecord_*.video_stats.json` file is **only used when `--bbox-correction 3d` is passed**.
It provides:

- `drone_height` (AGL, metres)
- `gimbal_pitch` (e.g. −83° → nearly nadir but slightly oblique)
- `drone_yaw + gimbal_yaw` → `camera_azimuth`

From these, a full 3D camera model (`DroneCamera`) is built with rotation matrix R and intrinsics K.
When the gimbal is tilted, objects not directly below the drone are displaced in the image relative
to their true ground position. The corrector ray-casts each detected bbox to the ground plane and
returns a corrected `(X, Y)` and heading.

Without `--bbox-correction 3d`, the flight record is ignored entirely. `H` handles everything,
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
        ├─── apply_homography(H, cx, cy) ──────────────────► (X_world, Y_world)
        │
        └─── _h_rotation_angle(H, cx, cy) → h_rot
             angle_wrap(yaw_img + h_rot) ─────────────────► heading_world
                                                                    │
                                              [optional] BboxCorrector (flight record)
                                                                    │
                                              [optional] yaw_offset_rad
                                                                    │
                                                             ► final (X, Y, yaw)
```
