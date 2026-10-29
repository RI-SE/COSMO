from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

import pytest

from cosmo.converters.environment import (
    FRAME_ALIGNED_NOTE,
    EnvironmentSources,
    FrameClock,
    clock_from_srt,
    environment_fields,
    exposure_to_lux,
    fog_level,
    frame_offset_from_provenance,
    illumination_level,
    load_weather,
    parse_srt,
    precipitation_level,
    read_flight_record_meta,
    shift_srt_frames,
    solar_position,
)

SRT_TEXT = """1
00:00:00,000 --> 00:00:00,033
<font size="28">FrameCnt: 1, DiffTime: 33ms
2025-10-16 13:40:59.816
[iso: 100] [shutter: 1/400.0] [fnum: 2.8] [ev: 0] [latitude: 57.48494] [longitude: 12.00940] [rel_alt: 119.600 abs_alt: 153.255] [ct: 5435] </font>

2
00:00:00,033 --> 00:00:00,066
<font size="28">FrameCnt: 2, DiffTime: 33ms
2025-10-16 13:40:59.850
[iso: 100] [shutter: 1/400.0] [fnum: 2.8] [ev: 0] [latitude: 57.48494] [longitude: 12.00940] [rel_alt: 119.600 abs_alt: 153.255] [ct: 5435] </font>

3
00:00:00,066 --> 00:00:00,099
<font size="28">FrameCnt: 3, DiffTime: 33ms
2025-10-16 13:40:59.883
[iso: 100] [shutter: 1/400.0] [fnum: 2.8] [ev: 0] [latitude: 57.48494] [longitude: 12.00940] [rel_alt: 119.600 abs_alt: 153.255] [ct: 5435] </font>
"""


def _weather_root() -> dict:
    return {
        "tags": {"0": {"type": "WeatherTag", "tag_data": {
            "text": [{"name": "precipitation", "val": "none"}, {"name": "cloud_cover", "val": "overcast"}],
            "num": [{"name": "visibility_km", "val": 10.2}],
        }}},
        "contexts": {"0": {"type": "WeatherContext", "frame_intervals": [{"frame_start": 5, "frame_end": 9}],
                           "context_data": {"text": [{"name": "precipitation", "val": "rain"},
                                                     {"name": "precipitation_intensity", "val": "heavy"}]}}},
    }


def test_parse_srt_and_clock(tmp_path: Path):
    srt = tmp_path / "v.srt"
    srt.write_text(SRT_TEXT, encoding="utf-8")
    frames = parse_srt(str(srt))
    assert [f.frame_idx for f in frames] == [0, 1, 2]
    assert frames[0].shutter_s == pytest.approx(1 / 400)
    assert frames[0].lat == pytest.approx(57.48494)

    clock = clock_from_srt(frames, "Europe/Stockholm")
    expected_t0 = datetime(2025, 10, 16, 11, 40, 59, 816000, tzinfo=timezone.utc)  # 13:40 CEST
    assert abs((clock.t0_utc - expected_t0).total_seconds()) < 0.002
    assert clock.period_s == pytest.approx(0.0335, abs=0.001)


def test_read_flight_record_meta(tmp_path: Path):
    fr = tmp_path / "fr.video_stats.json"
    fr.write_text(json.dumps({"sequences": [{
        "time_range": {"start": "2025-10-16T11:41:00.967Z"},
        "stats": {"osd": {"latitude": {"mean": 57.48}, "longitude": {"mean": 12.0}}},
    }]}), encoding="utf-8")
    start, lat, lon = read_flight_record_meta(str(fr))
    assert start == datetime(2025, 10, 16, 11, 41, 0, 967000, tzinfo=timezone.utc)
    assert (lat, lon) == (57.48, 12.0)


def test_solar_position_reference_values():
    # Equinox, equator, around solar noon: sun near zenith.
    _, el = solar_position(0.0, 0.0, datetime(2025, 3, 20, 12, 7, tzinfo=timezone.utc))
    assert math.degrees(el) > 89.0
    # Gothenburg 2025-10-16 11:41 UTC: elevation ~22.8 deg, compass azimuth ~191.7 deg (SSW).
    az, el = solar_position(57.48494, 12.0094, datetime(2025, 10, 16, 11, 41, tzinfo=timezone.utc))
    assert math.degrees(el) == pytest.approx(22.8, abs=0.3)
    # OSI azimuth is counterclockwise from north: compass 191.7 deg -> 168.3 deg.
    assert math.degrees(az) == pytest.approx(168.3, abs=0.5)


@pytest.mark.parametrize("lux, level", [(0.005, "LEVEL1"), (0.5, "LEVEL2"), (500, "LEVEL7"),
                                        (7840, "LEVEL8"), (50000, "LEVEL9")])
def test_illumination_level(lux, level):
    assert illumination_level(lux) == level


def test_exposure_to_lux_bright_day():
    assert exposure_to_lux(100, 1 / 400, 2.8) == pytest.approx(7840, rel=0.01)


@pytest.mark.parametrize("km, level", [(50, "EXCELLENT_VISIBILITY"), (10.2, "GOOD_VISIBILITY"),
                                       (1.5, "MIST"), (0.5, "LIGHT"), (0.01, "DENSE")])
def test_fog_level(km, level):
    assert fog_level(km) == level


@pytest.mark.parametrize("weather, level", [
    ({"precipitation": "none"}, "NONE"),
    ({"precipitation": "rain", "precipitation_intensity": "moderate"}, "MODERATE"),
    ({"precipitation": "snow", "precipitation_intensity": "unknown"}, "UNKNOWN"),
    ({}, None),
])
def test_precipitation_level(weather, level):
    assert precipitation_level(weather) == level


def test_weather_context_overrides_tag_for_its_frames():
    weather = load_weather(_weather_root())
    assert weather.for_frame(0)["precipitation"] == "none"
    assert weather.for_frame(7)["precipitation_intensity"] == "heavy"


def test_environment_fields_only_set_available_sources():
    weather = load_weather(_weather_root())
    assert environment_fields(0, EnvironmentSources(weather=weather)) == {
        "precipitation": "NONE", "fog": "GOOD_VISIBILITY", "cloud_cover": "EIGHT_OKTAS"}

    clock = FrameClock(datetime(2025, 10, 16, 11, 41, tzinfo=timezone.utc), 0.1)
    fields = environment_fields(10, EnvironmentSources(weather=load_weather({}), clock=clock,
                                                       lat_lon=(57.48, 12.0)))
    assert fields["unix_timestamp"] == int(datetime(2025, 10, 16, 11, 41, 1, tzinfo=timezone.utc).timestamp())
    assert fields["seconds_since_midnight"] == 13 * 3600 + 41 * 60 + 1  # CEST
    assert "sun_elevation" in fields and "ambient_illumination" not in fields


def test_shift_srt_frames_maps_annotated_frame_to_later_srt_frame(tmp_path: Path):
    srt = tmp_path / "v.srt"
    srt.write_text(SRT_TEXT, encoding="utf-8")
    frames = parse_srt(str(srt))
    shifted = shift_srt_frames(frames, 1)
    # Annotated frame 0 now carries the time of SRT frame 1
    assert [f.frame_idx for f in shifted] == [-1, 0, 1]
    assert shifted[1].local_time == frames[1].local_time
    shifted_clock = clock_from_srt(shifted, "Europe/Stockholm")
    original_clock = clock_from_srt(frames, "Europe/Stockholm")
    assert abs((shifted_clock.utc(0) - original_clock.utc(1)).total_seconds()) < 0.002


def _write_prov(tmp_path: Path, rectify_log: str | None, nested: bool = False) -> str:
    activities = {"activity:step_1": {"dataprov:arguments": "/x/vidtools/vidconcat.py --output o",
                                      "dataprov:outputLog": ""}}
    if rectify_log is not None:
        activities["activity:step_2"] = {
            "dataprov:arguments": "/x/vidtools/vidrectify.py in.mp4 out.mp4",
            "dataprov:outputLog": rectify_log,
        }
    # An orchestrator step that merely mentions vidrectify must not be taken for the tool itself
    activities["activity:step_3"] = {"dataprov:arguments": "orchestrator --rectify",
                                     "dataprov:outputLog": "vidrectify"}
    if nested:
        chain = {"activity": {}, "bundle": {"bundle:nested_0": {"activity": activities}}}
    else:
        chain = {"activity": activities}
    path = tmp_path / "prov.json"
    path.write_text(json.dumps(chain), encoding="utf-8")
    return str(path)


@pytest.mark.parametrize("nested", [False, True])
def test_frame_offset_from_provenance_old_vidrectify_output(tmp_path: Path, nested):
    log = "Video stabilization completed. Successfully stabilized 10/10 frames (100.0%)."
    assert frame_offset_from_provenance(_write_prov(tmp_path, log, nested)) == 1


def test_frame_offset_from_provenance_frame_aligned_output(tmp_path: Path):
    log = f"Video stabilization completed. {FRAME_ALIGNED_NOTE}"
    assert frame_offset_from_provenance(_write_prov(tmp_path, log)) == 0


def test_frame_offset_unknown_without_vidrectify_step(tmp_path: Path):
    assert frame_offset_from_provenance(_write_prov(tmp_path, None)) is None
    assert frame_offset_from_provenance(None) is None
    assert frame_offset_from_provenance(str(tmp_path / "missing.json")) is None


def _new_format_root() -> dict:
    """SAVANT >= 2.5: one tag per value typed by class; contexts only where it rains/snows."""
    def tag(tag_type, name, val, kind="text", agreement=None, annotator="markit_vlm"):
        data = {kind: [{"name": name, "val": val}],
                "vec": [{"name": f"{name}_annotator", "val": [annotator]}, {"name": f"{name}_confidence", "val": [0.9]}]}
        if agreement is not None:
            data["num"] = data.get("num", []) + [{"name": f"{name}_agreement", "val": agreement}]
        return {"type": tag_type, "tag_data": data}
    return {
        "tags": {"0": tag("CloudinessOvercast", "cloud_cover", "overcast", agreement=0.6),
                 "1": tag("EnvironmentVisibility", "environmentVisibilityValue", 8.0, kind="num"),
                 "2": tag("PrecipitationLight", "precipitation_intensity", "light"),
                 "3": tag("WeatherRain", "precipitation", "rain"),
                 # A person changed an older WeatherTag in edit: it wins over the VLM
                 "4": tag("CloudinessClear", "cloud_cover", "clear", annotator="alice")},
        "contexts": {"0": {"type": "WeatherRain", "frame_intervals": [{"frame_start": 5, "frame_end": 9}],
                           "context_data": {"text": [{"name": "precipitation", "val": "rain"}]}}},
    }


def test_new_format_weather_by_field_name():
    weather = load_weather(_new_format_root())
    inside, outside = weather.for_frame(7), weather.for_frame(0)
    assert inside["precipitation"] == "rain" and inside["precipitation_intensity"] == "light"
    # Outside the rain context precipitation is unknown (none or not determinable), not the whole-video vote
    assert "precipitation" not in outside and "precipitation_intensity" not in outside
    assert outside["visibility_km"] == 8.0 and outside["cloud_cover"] == "clear"
    assert environment_fields(7, EnvironmentSources(weather=weather)) == {
        "precipitation": "LIGHT", "fog": "MODERATE_VISIBILITY", "cloud_cover": "ZERO_OKTAS"}


def test_old_context_keeps_tag_values_it_does_not_mention():
    weather = load_weather(_weather_root())
    assert weather.for_frame(7)["cloud_cover"] == "overcast"


def test_new_format_explicit_no_precipitation():
    root = {"contexts": {
        "0": {"type": "WeatherNoPrecipitation", "frame_intervals": [{"frame_start": 0, "frame_end": 4}],
              "context_data": {"text": [{"name": "precipitation", "val": "none"}]}},
        "1": {"type": "WeatherRain", "frame_intervals": [{"frame_start": 5, "frame_end": 9}],
              "context_data": {"text": [{"name": "precipitation", "val": "rain"}]}}}}
    weather = load_weather(root)
    assert environment_fields(2, EnvironmentSources(weather=weather)) == {"precipitation": "NONE"}
    assert environment_fields(7, EnvironmentSources(weather=weather)) == {"precipitation": "UNKNOWN"}
    assert environment_fields(20, EnvironmentSources(weather=weather)) == {}
