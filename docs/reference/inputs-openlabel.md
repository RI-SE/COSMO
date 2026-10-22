# Input: OpenLABEL (expected structure)

COSMO reads OpenLABEL JSON and extracts object metadata and per-frame rotated bounding boxes (rbbox).

## Required content
COSMO expects, per frame, objects that contain an `rbbox` with at least five values:

`[cx, cy, w_px, h_px, yaw]` in pixel coordinates.

COSMO looks for rbbox values under:
- `openlabel.frames[*].objects[*].object_data.rbbox.val`, or
- `...rbbox.shape.val` (supported variant)

## Optional object metadata
If present under `openlabel.objects`, COSMO uses `type` (an ontology class such as Car, Truck,
Pedestrian). It is mapped to OSI type, vehicle classification and role; see the [object class table](osi-mapping.md#object-classes). Ontology URLs in `openlabel.ontologies` extend the mapping (needs `rdflib`).

## Optional weather annotation
Weather from SAVANT markit's VLM analysis fills OSI precipitation, fog and cloud cover. Used fields:
`precipitation`, `precipitation_intensity`, `visibility_km` (newer files: `environmentVisibilityValue`),
`cloud_cover`. Both SAVANT formats are read, by field name whatever the tag type:
- before SAVANT 2.5: one `WeatherTag` (whole sequence) and `WeatherContext`s (frame intervals), with explicit
  `"none"` values;
- SAVANT ≥ 2.5: one tag per value typed by its scenario-ontology class (`WeatherRain`, `CloudinessOvercast`,
  `EnvironmentVisibility`, …) and contexts only where it rains or snows. Values without a class (`none`,
  `unknown`) are not written, so outside those contexts precipitation is left unset (unknown), not "none".

Contexts override tags for their frames. When a field appears in several tags, a person's edit wins, then the
higher VLM agreement, then the higher confidence. Other tags (road, junction, traffic, structures, notes) are not
exported to OSI; they are copied to the companion OpenLABEL. Mapping: [environmental conditions](osi-mapping.md#environmental-conditions-groundtruthenvironmental_conditions-mcap-only).

## Confidence
COSMO reads the per-box confidence from the `confidence` entry of `object_data.vec`, a list of
`{"name": ..., "val": [...]}` entries with the newest value first, as SAVANT writes it. It is kept
for future use and does not affect the output yet. A missing entry means unknown, not 1.0.