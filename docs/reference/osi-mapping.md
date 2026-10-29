# OSI/MCAP output and OpenLABEL → OSI mapping

`cosmo convert` maps each OpenLABEL frame to one OSI `GroundTruth` message (and one CSV row per object).
Optional inputs add data; a field is only written when its source is available. The CSV columns use the same
type, subtype and role values; see [Outputs](outputs-omega-prime.md) for the CSV schema.

## MCAP topics
MCAP output requires the `mcap` dependency group (`betterosi`). If MCAP is requested but `betterosi` is
missing, COSMO writes CSV only and logs a message. Topics are written without a leading slash:

| Topic | Content |
|---|---|
| `ground_truth_map` | OSI `MapAsamOpenDrive` with the embedded OpenDRIVE map (only with `--odr`), log time 0 |
| `ground_truth` | one OSI `GroundTruth` (interface version 3.7.0) per frame, log time = frame timestamp |

## GroundTruth fields
| Field | Source |
|---|---|
| `timestamp` | frame timestamp, see Geometry and kinematics below |
| `moving_object[*]` | one per annotated object in the frame |
| `proj_string` | georef `proj_string` (`--georef-data`) |
| `country_code` | `--country-code`, else derived from the georef position; omitted when unresolved |
| `proj_frame_offset` | OpenDRIVE header offset, only with `--object-frame local` |
| `environmental_conditions` | see Environmental conditions below |

## Geometry and kinematics
The derivation is explained step by step in the [georef pipeline](../developer_guide/georef-pipeline.md).

| Output | Derived from |
|---|---|
| Position x, y | rbbox centre (pixels) through the pixel→ground homography (`--georef-data`, else `--calibration`), then `--xy-offset/--swap-xy/--flip-*`. Without either: debug fallback `px × 0.01`, z = 0. With `cosmo correct` output: the corrected cuboid position. |
| Heading (yaw) | `−r_image + direction of image +x in the world` at the box position, wrapped to (−π, π] |
| Length, width | rbbox corners projected to the ground, measured along the heading; without a homography: class defaults |
| Height | class default (car 1.5 m, van 2.2 m, truck 3.5 m, bus 3.2 m, pedestrian 1.7 m, other 1.5 m; overridable via `default_dimensions_m` in the georef/calibration file). Trailers use the height of the towing vehicle (`towed-by` relation). |
| Timestamp | frame number × frame period. Period: from `--srt` (fitted to the SRT clock), else `--fps`, else fps from georef / calibration, else 30 fps. Relative to video frame 0. |
| Velocity, acceleration | finite differences between an object's consecutive appearances, divided by the real time step (frame gaps are handled) |

## Object classes
Classes are resolved by `OntologyMapper` (`src/cosmo/converters/ontology_mapper.py`): a built-in table for the
SAVANT ontology 1.3.1, extended at run time with the ontology URLs declared in the OpenLABEL file (needs `rdflib`);
a class missing from the table inherits the mapping of its nearest mapped ancestor (`rdfs:subClassOf`). Unknown
classes become OTHER. Vehicle classification is only written for type VEHICLE.

| OpenLABEL class | OSI MovingObject type | Vehicle classification type | Vehicle role |
|---|---|---|---|
| Car | VEHICLE | CAR | CIVIL |
| Van | VEHICLE | DELIVERY_VAN | CIVIL |
| Truck | VEHICLE | HEAVY_TRUCK | CIVIL |
| Trailer, Caravan | VEHICLE | TRAILER | CIVIL |
| Bus | VEHICLE | BUS | PUBLIC_TRANSPORT ¹ |
| Tram | VEHICLE | TRAM | PUBLIC_TRANSPORT ¹ |
| Train | VEHICLE | TRAIN | PUBLIC_TRANSPORT ¹ |
| Motorbike | VEHICLE | MOTORBIKE | CIVIL |
| Bicycle | VEHICLE | BICYCLE | CIVIL |
| StandupScooter | VEHICLE | STANDUP_SCOOTER | CIVIL |
| WheelChairUser | VEHICLE ² | WHEELCHAIR | CIVIL |
| Ambulance | VEHICLE | OTHER | AMBULANCE |
| Fire | VEHICLE | OTHER | FIRE |
| Police | VEHICLE | OTHER | POLICE |
| EmergencyVehicle, AgriculturalVehicle | VEHICLE | OTHER | OTHER ³ |
| Vehicle, ConstructionVehicle, SlowMovingVehicle | VEHICLE | OTHER | CIVIL |
| Human, Pedestrian | PEDESTRIAN | – | – |
| Animal | ANIMAL | – | – |
| RoadUser, DynamicObject, unknown | OTHER | – | – |

¹ Assumes buses, trams and trains are in public service.
² OSI models wheelchairs only as a vehicle classification type, so wheelchair users are exported as vehicles.
³ OSI has no agricultural or generic emergency role; OTHER means a known but unspecified role.

The CSV `role`/`role_name` columns carry the same role for every object (CIVIL when the class implies none).

## Environmental conditions (`GroundTruth.environmental_conditions`, MCAP only)
| OSI field | Source | Mapping |
|---|---|---|
| `unix_timestamp` | `--srt` clock, else `--flight-record` `time_range.start` + frame period | UTC seconds per frame |
| `time_of_day.seconds_since_midnight` | same as above | local time in `--timezone` (default Europe/Stockholm) |
| `sun.azimuth`, `sun.elevation` | time above + position: SRT GPS per frame, else flight-record mean lat/lon, else georef `reference_point` | NOAA solar position, no refraction. OSI azimuth is counterclockwise from north (west = +π/2). |
| `ambient_illumination` | `--srt` exposure (ISO, shutter, f-number, EV compensation) | lux ≈ 2.5·2^EV100 (reflected-light estimate, about ±1 level) → OSI LEVEL1–9 lux bands |
| `precipitation` | OpenLABEL weather annotation ⁴ | `precipitation=none` → NONE; intensity light/moderate/heavy → LIGHT/MODERATE/HEAVY; otherwise UNKNOWN |
| `fog` | OpenLABEL weather `visibility_km` ⁴ | OSI visibility bands (≥40 km EXCELLENT, 10–40 GOOD, 4–10 MODERATE, 2–4 POOR, 1–2 MIST, 0.2–1 LIGHT, 0.05–0.2 THICK, <0.05 DENSE) |
| `clouds.fractional_cloud_cover` | OpenLABEL weather `cloud_cover` ⁴ | clear → ZERO_OKTAS, partly_cloudy → FOUR_OKTAS (assumed 3–5 oktas), overcast → EIGHT_OKTAS |
| temperature, pressure, humidity, wind | – | not set (no source yet) |

⁴ Weather contexts (per frame interval) take precedence over sequence-level weather tags; both are written by
SAVANT markit's VLM analysis, in either SAVANT format ([inputs](inputs-openlabel.md#optional-weather-annotation)).

Not exported: the road, junction, traffic, structures, notes and VLM-analysis tags. Road and junction layout is in the
embedded OpenDRIVE map; the rest has no OSI field.
