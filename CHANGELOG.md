# Changelog

All notable changes to COSMO are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [Semantic Versioning](https://semver.org/).

> **Note:** entries for 0.1.0 – 0.5.0 were reconstructed after the fact (2026-10) from the git history
> (commits where `version` changed in `pyproject.toml`, and the commits between them). Dates are those
> commit dates. They are best-effort summaries and may be incomplete.

## [0.6.1] – unreleased

### Added
- Uncertainty of the converted objects (scenario-uncertainty `world` profile): box σ, origin and `p_exist`
  from SAVANT, georef image/datum error from orbit-georef, per-frame stabilisation error from
  `--video-quality`; SPEC defaults when missing (listed, and warned in provenance).
- Companion OpenLABEL `<prefix>.companion.json`, written by default: run and object quality, tags/contexts;
  `--companion-frames` adds per-frame world cuboids with `position_cov_rel` and σ. Listed in provenance with
  `su` properties and `[su:*]` warnings.
- `--smooth-kinematics` (Kalman/RTS, constant acceleration, `--jerk-psd`): smoothed position, v and a with σ.
  Off by default; raw finite differences are unchanged.
- `--sensor-data`: OSI SensorData topic with existence probability, measurement state and RMS errors.
- `--camera-uncertainty` (opt-in): camera height, gimbal pitch and azimuth error in the oblique-correction
  uncertainty (`BboxCorrector.with_camera`; `DroneCamera.stddev` from the flight record).
- `cosmo correct` writes a per-box `oblique` entry `[method, shift_m, h_used, fit_residual]` (3d fit loss, also
  for fallbacks to analytical); `cosmo convert` copies it into the companion for pre-corrected input.
- `--estimate-height` (opt-in, experimental): per-object effective vehicle height from the track
  (`cosmo.corrections.height_estimate`), written as `h_eff`/`h_eff_sigma` in the companion. Probably does not
  give good results yet (implausibly low heights on real annotations); not for data products.

### Changed
- `convert_openlabel_to_omega` computes all samples first, then writes MCAP/CSV (same output as before).
- Input corrected by `cosmo correct` is recognised: the companion records `cosmo_correct`, and with
  `--flight-record` the vehicle-height uncertainty is included (positions unchanged).

### Fixed
- **Oblique correction placed the camera on the wrong side.** The camera azimuth was `drone_yaw + gimbal_yaw`,
  but DJI flight records log the gimbal yaw as an absolute heading, so the heading was doubled (off by ~57° at
  BorasEkas, ~92° at BorasKraklanda, ~180° at GbgSaroRound). The corrector now takes the viewing azimuth from the
  georeference H (like the camera position) and warns when the flight record differs by more than 20°; the
  flight-record loader uses the gimbal yaw alone. Affects `cosmo correct` and `cosmo convert --bbox-correction`.
- **Cars are corrected with a silhouette height of 1.15 m** (`SILHOUETTE_HEIGHTS`), not the 1.5 m vehicle height:
  a car's roof is narrower than its body, so the far outline seen obliquely is the shoulder. On human-edited
  GbgSaroRound boxes (3d mode) cars were 1.61 m wide with 1.5 m, ≈ 1.8 m with the silhouette height. The output
  height stays 1.5 m; the `oblique` entry records the height used. Other classes are unchanged.
- **Analytical correction model:** replaced the move of "far" corners along camera rays (which skewed the box and
  widened cars beside the camera) by removing the roof stretch δ = (P − C)·h/(H_c − h): centre − δ/2, each
  dimension − |δ| along its axis. The 3d mode starts from this result. Tested against a synthetic pinhole camera.
- Weather from SAVANT ≥ 2.5 (class-typed tags such as `WeatherRain`) was ignored; both SAVANT weather formats are
  now read by field name. Old-format weather contexts no longer hide tag fields they don't mention.
- Vehicle roles: Train → PUBLIC_TRANSPORT (was CIVIL); AgriculturalVehicle and EmergencyVehicle → OTHER
  (was CIVIL), since OSI has no agricultural or generic emergency role.
- Documentation restructured: README covers inputs, install and usage; mapping tables moved to
  `docs/reference/osi-mapping.md`; new input references for georef/calibration and the flight record;
  CLI reference covers all commands; quickstart merged into README/workflow; stale references removed.

### Fixed
- `--fps` (GUI: FPS override) did not override an `fps` in the georef or calibration file. Order is now
  `--srt`, `--fps`, georef, calibration, 30 fps.
- A georef file with `"fps": null` crashed convert; null now falls through to the next source.
- `run_summary.json` `fps_used` only echoed `--fps` (null otherwise); it now reports the frame rate actually
  used (`convert_openlabel_to_omega` returns it).

## [0.6.0] – 2026-10-14

### Added
- OSI `GroundTruth.environmental_conditions` in the MCAP output, filled per frame from optional sources:
  - `unix_timestamp` and local `time_of_day` from the DJI SRT (`--srt`), else the flight record start time.
  - Sun azimuth/elevation (in-house NOAA algorithm; OSI convention: counterclockwise from north).
  - `ambient_illumination` estimated from the SRT camera exposure (ISO, shutter, f-number, EV).
  - `precipitation`, `fog` (from visibility) and cloud cover from the OpenLABEL `WeatherContext`/`WeatherTag`.
- `cosmo convert` options `--srt`, `--srt-prov` (recorded in dataprov) and `--timezone` (default `Europe/Stockholm`).
- SRT file picker in the GUI convert tab.
- OSI vehicle roles from the object class: Ambulance/Fire/Police → AMBULANCE/FIRE/POLICE, Bus/Tram →
  PUBLIC_TRANSPORT; all other vehicles CIVIL. Subclasses inherit roles via the ontology.
- `OntologyMapper.from_openlabel()`; README "Transformations" section; `docs/reference/inputs-srt.md`;
  `development.md` with open items.

### Changed
- With `--srt`, the frame period is fitted to the SRT clock (e.g. 29.97 fps) instead of fps from
  georef/calibration/default 30 fps. Removes timestamp drift (~1.3 s over 22 min at 29.97 vs 30 fps).
- WheelChairUser is exported as VEHICLE / WHEELCHAIR (OSI models wheelchairs as a vehicle classification),
  previously PEDESTRIAN.
- CSV `role_name` is now the OSI role (CIVIL etc.) instead of the movement state MOVING.
- The trajectory explorer uses `OntologyMapper`, so its type/subtype names match the CSV/MCAP output.
- The flight record (`--flight-record`) is also used by convert for start time and position, not only for
  bbox correction.
- CI uses uv for dependency installation.

### Fixed
- StandupScooter was not mapped (typo in the built-in table) and became VEHICLE/OTHER or OTHER/OTHER.
- Velocity/acceleration across frame gaps in an object's track used a single-frame time step,
  overestimating speed by the gap length.

### Removed
- Unused mapping code: `classify_openlabel_type`, `VEHICLE_SUBTYPE_MAP`, `normalize_subtype`,
  `VEHICLE_ROLE_MAP`/`normalize_role` (movement states that never matched OSI roles), and the trajectory
  explorer's own `_OL_TYPE_MAP`.
- Reading of the non-standard per-object OpenLABEL `subtype`/`role` fields.

## [0.5.0] – 2026-06-29 (reconstructed)

### Changed
- Pixel→ground georeferencing uses the shared `orbit-georef` library instead of COSMO's own code.
- OpenDRIVE handling (convert and trajectory explorer, including parking spaces) uses the shared
  `opendrive-map` library.

### Added
- `--object-frame {global,local}`: local map-frame output with the offset recorded in OSI `proj_frame_offset`.

### Fixed
- Trajectory explorer clear bug.
- Removed internal references from docs; fixed docs links and CI triggers; updated `environment.yml` and
  contributing instructions.

## [0.4.1] – 2026-06-22 (reconstructed)

### Added
- OSI GroundTruth `proj_string` (from the georef file) and `country_code` (`--country-code`, else derived
  from the georef position).
- Documentation updates: installation instructions, trajectory explorer, links to ORBIT, license info.

## [0.4.0] – 2026-06-22 (reconstructed)

### Changed
- README update (version bump without code changes in `src/`).

## [0.3.3] – 2026-06-18 (reconstructed)

### Fixed
- Conversion of OpenLABEL files with objects that appear in frames but not in the objects section
  (orphan frame objects).

## [0.3.2] – 2026-06-18 (reconstructed)

### Added
- `--opendrive-prov` provenance input for the OpenDRIVE map.
- Loading dialog in the trajectory explorer.

### Fixed
- MCAP loading bug in the trajectory explorer.
- Performance optimizations for bbox correction.

## [0.3.1] – 2026-05-08 (reconstructed)

### Added
- `--output-prefix` to write `<prefix>.csv/.mcap` directly, bypassing the run folder.
- `--georef-prov` and `--flight-record-prov`: upstream provenance of these inputs is inlined into the output DPR.

## [0.3.0] – 2026-05-06 (reconstructed)

### Added
- `cosmo correct`: oblique-drone bbox correction (analytical and 3D modes) using the flight record
  (`corrections/drone_camera.py`, `corrections/oblique.py`), with `--stabilize-size` and `--use-gps-cam-pos`.
- Provenance recording with the dataprov library (`--prov-in`, `--prov-out`).
- Trajectory explorer with up to three inputs (CSV, MCAP, OpenLABEL).

## [0.2.0] – 2026-04-03 (reconstructed)

### Added
- Package structure with app/CLI/GUI layers: `cosmo convert`, `cosmo calibrate`, Qt GUI (calibration
  editor, conversion, Altair/matplotlib plots).
- `OntologyMapper`: OpenLABEL class → OSI type/subtype via the SAVANT ontology.
- MCAP input for the trajectory explorer; UTM output in geopos files; `--strip-xodr-namespace`.
- CI tests and documentation.

### Fixed
- Type/subtype conversion from SAVANT OpenLABEL to MCAP.

## [0.1.0] – 2026-02-05 (reconstructed)

### Added
- First packaged version (`pyproject.toml`): `calibrate` and `convert` CLI scripts, a GUI entry point and
  OpenLABEL/SAVANT ontology schemas. Grew out of the SAVANTPostProcessing prototype (from 2025-10).
