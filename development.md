# Pending development tasks

Working notes, kept here so they can be picked back up later. Not a changelog — delete/update
entries as they're resolved.

## Open

- **Meteorological fields in OSI `environmental_conditions` are not set.** `temperature`,
  `atmospheric_pressure`, `relative_humidity` and `wind` have no source in the drone data. Planned
  source: the project weather-db (fetch by time + position, record the query/result in dataprov as an
  extra input). Hook point: `environment_fields()` in `src/cosmo/converters/environment.py`.

- **Non-OSI OpenLABEL tags are not exported.** Road, junction, traffic, structures, notes and
  VLM-analysis tags (written by SAVANT markit) are dropped by convert. Undecided how/whether they go into
  the MCAP — needs coordination with the project on MCAP contents. Options considered: an MCAP metadata
  record, or a JSON sidecar next to the MCAP.

- **betterosi has no MCAP metadata/attachment API.** `betterosi.Writer` only exposes `add()`; the
  underlying `mcap` writer's `add_metadata()` is only reachable via private attributes
  (`writer.mcap_writer._writer`). Relevant if the item above is resolved as MCAP metadata.

- **FieldDataLab must re-lock COSMO after dev-fixes is merged.** Its orchestrator (branch dev-fall)
  passes `--srt` / `--srt-prov`; `pyproject.toml` points to `RI-SE/COSMO` (default branch), but `uv.lock`
  is at main `1799ffa`, which lacks these flags. After the merge: `uv lock --upgrade-package cosmo && uv sync`
  in FieldDataLab. Existing omegaprime
  outputs were produced without the SRT (30 fps assumed instead of 29.97, no environmental conditions) and
  must be deleted to regenerate (the orchestrator skips existing outputs).

- **GUI has no flight-record or provenance inputs.** The convert tab got an SRT picker; flight record,
  timezone and `*-prov` inputs are CLI-only.

- **`convert_openlabel_to_omega` is ~450 lines with the MCAP and CSV-only frame loops duplicated.**
  Candidate for splitting into per-object row building + writers.
