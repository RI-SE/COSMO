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
`openlabel.contexts[*]` of type `WeatherContext` (per frame interval) and `openlabel.tags[*]` of type
`WeatherTag` (whole sequence) fill OSI precipitation, fog and cloud cover. Used fields:
`precipitation`, `precipitation_intensity`, `visibility_km`, `cloud_cover`. Other tags (road, junction,
traffic, structures, notes) are not exported. Mapping: [environmental conditions](osi-mapping.md#environmental-conditions-groundtruthenvironmental_conditions-mcap-only).

## Confidence
COSMO can read confidence from:
`object_data.vec.confidence.val` (if present).