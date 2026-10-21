"""Build OSI EnvironmentalConditions from OpenLABEL weather tags and DJI SRT / flight-record metadata.

Every source is optional; a field is only set when its source is available.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import numpy as np

DEFAULT_TIMEZONE = "Europe/Stockholm"

# OSI fog classes by visibility lower bound in metres, descending.
_FOG_BANDS_M: List[Tuple[float, str]] = [
    (40000, "EXCELLENT_VISIBILITY"), (10000, "GOOD_VISIBILITY"), (4000, "MODERATE_VISIBILITY"),
    (2000, "POOR_VISIBILITY"), (1000, "MIST"), (200, "LIGHT"), (50, "THICK"), (0, "DENSE"),
]
# OSI ambient illumination classes by illuminance lower bound in lux, descending.
_ILLUMINATION_BANDS_LX: List[Tuple[float, str]] = [
    (10000, "LEVEL9"), (1000, "LEVEL8"), (400, "LEVEL7"), (20, "LEVEL6"), (10, "LEVEL5"),
    (3, "LEVEL4"), (1, "LEVEL3"), (0.01, "LEVEL2"), (0, "LEVEL1"),
]
# VLM vocabulary (SAVANT markit prompt) -> OSI enum names.
_PRECIPITATION_INTENSITY = {"none": "NONE", "light": "LIGHT", "moderate": "MODERATE", "heavy": "HEAVY"}
# partly_cloudy has no okta count; FOUR_OKTAS assumes the meteorological 3-5 okta range.
_CLOUD_COVER = {"clear": "ZERO_OKTAS", "partly_cloudy": "FOUR_OKTAS", "overcast": "EIGHT_OKTAS"}


# -----------------------------------------------------------------------------
# DJI SRT and flight record
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class SrtFrame:
    """One DJI SRT entry; local_time is naive (camera clock, local timezone)."""
    frame_idx: int
    local_time: datetime
    iso: Optional[float] = None
    shutter_s: Optional[float] = None
    fnum: Optional[float] = None
    ev: float = 0.0
    lat: Optional[float] = None
    lon: Optional[float] = None


_SRT_FRAMECNT = re.compile(r"FrameCnt:\s*(\d+)")
_SRT_TIME = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?)")
_SRT_FIELD = re.compile(r"\[\s*(\w+)\s*:\s*([^\]\s]+)")


def _to_float(text: Optional[str]) -> Optional[float]:
    """Parse a float or a fraction like '1/400.0'; None if absent or malformed."""
    if text is None:
        return None
    try:
        if "/" in text:
            num, den = text.split("/", 1)
            return float(num) / float(den)
        return float(text)
    except (ValueError, ZeroDivisionError):
        return None


def parse_srt(path: str) -> List[SrtFrame]:
    """Parse a DJI SRT file; FrameCnt is 1-based, frame_idx is 0-based to match OpenLABEL frames."""
    with open(path, encoding="utf-8", errors="replace") as f:
        blocks = re.split(r"\n\s*\n", f.read())
    frames: List[SrtFrame] = []
    for block in blocks:
        m_cnt, m_time = _SRT_FRAMECNT.search(block), _SRT_TIME.search(block)
        if not (m_cnt and m_time):
            continue
        fields = dict(_SRT_FIELD.findall(block))
        frames.append(SrtFrame(
            frame_idx=int(m_cnt.group(1)) - 1,
            local_time=datetime.fromisoformat(m_time.group(1)),
            iso=_to_float(fields.get("iso")),
            shutter_s=_to_float(fields.get("shutter")),
            fnum=_to_float(fields.get("fnum")),
            ev=_to_float(fields.get("ev")) or 0.0,
            lat=_to_float(fields.get("latitude")),
            lon=_to_float(fields.get("longitude")),
        ))
    return frames


def read_flight_record_meta(path: str, sequence: int = 0) -> Tuple[Optional[datetime], Optional[float], Optional[float]]:
    """Return (recording start UTC, mean latitude, mean longitude) from a *.video_stats.json."""
    with open(path, encoding="utf-8") as f:
        seq = json.load(f)["sequences"][sequence]
    start = seq.get("time_range", {}).get("start")
    start_utc = datetime.fromisoformat(start.replace("Z", "+00:00")) if start else None
    osd = seq.get("stats", {}).get("osd", {})
    lat = osd.get("latitude", {}).get("mean")
    lon = osd.get("longitude", {}).get("mean")
    return start_utc, lat, lon


# FieldDataLab vidrectify writes this into its provenance output log once its output is frame-aligned;
# older vidrectify output dropped the first frame, so annotated frame k was SRT frame k + 1
FRAME_ALIGNED_NOTE = "Frame-aligned: output frame k is input frame k."
RECTIFY_TOOL = "vidrectify"


def frame_offset_from_provenance(prov_path: Optional[str]) -> Optional[int]:
    """SRT frame offset of the annotated video from its provenance chain, or None if it can't be told."""
    if not prov_path:
        return None
    try:
        with open(prov_path, encoding="utf-8") as f:
            chain = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    logs = [activity.get("dataprov:outputLog", "") for activity in _activities(chain)
            if RECTIFY_TOOL in activity.get("dataprov:arguments", "").split(" ", 1)[0]]
    if not logs:
        return None
    return 0 if FRAME_ALIGNED_NOTE in logs[-1] else 1


def _activities(node: Any):
    """All provenance activities, including those in nested (inlined) bundles."""
    if isinstance(node, dict):
        if "dataprov:arguments" in node:
            yield node
        for value in node.values():
            yield from _activities(value)
    elif isinstance(node, list):
        for value in node:
            yield from _activities(value)


def shift_srt_frames(frames: List[SrtFrame], frame_offset: int) -> List[SrtFrame]:
    """Re-index SRT frames to annotated-video frames: annotated frame k is SRT frame k + frame_offset."""
    if frame_offset == 0:
        return frames
    return [replace(f, frame_idx=f.frame_idx - frame_offset) for f in frames]


@dataclass(frozen=True)
class FrameClock:
    """Linear frame -> absolute time model: utc(frame) = t0_utc + frame * period_s."""
    t0_utc: datetime
    period_s: float
    max_residual_s: float = 0.0

    def utc(self, frame_idx: int) -> datetime:
        return self.t0_utc + timedelta(seconds=frame_idx * self.period_s)


def clock_from_srt(frames: List[SrtFrame], tz_name: str) -> FrameClock:
    """Fit a line through the SRT times; avoids the ms quantization jitter of the raw stamps."""
    tz = ZoneInfo(tz_name)
    idx = np.array([f.frame_idx for f in frames], dtype=np.float64)
    secs = np.array([f.local_time.replace(tzinfo=tz).timestamp() for f in frames], dtype=np.float64)
    period, t0 = np.polyfit(idx, secs, 1)
    residual = float(np.max(np.abs(secs - (t0 + period * idx))))
    return FrameClock(datetime.fromtimestamp(t0, tz=timezone.utc), float(period), residual)


# -----------------------------------------------------------------------------
# Physical models
# -----------------------------------------------------------------------------

def solar_position(lat_deg: float, lon_deg: float, when_utc: datetime) -> Tuple[float, float]:
    """Return (azimuth, elevation) in rad, NOAA algorithm without refraction; azimuth in OSI convention.

    OSI azimuth is counterclockwise from north (west = +pi/2), unlike the compass convention.
    """
    jc = (when_utc.timestamp() / 86400.0 + 2440587.5 - 2451545.0) / 36525.0
    mean_long = (280.46646 + jc * (36000.76983 + jc * 0.0003032)) % 360
    mean_anom = math.radians(357.52911 + jc * (35999.05029 - 0.0001537 * jc))
    ecc = 0.016708634 - jc * (0.000042037 + 0.0000001267 * jc)
    center = (math.sin(mean_anom) * (1.914602 - jc * (0.004817 + 0.000014 * jc))
              + math.sin(2 * mean_anom) * (0.019993 - 0.000101 * jc)
              + math.sin(3 * mean_anom) * 0.000289)
    omega = math.radians(125.04 - 1934.136 * jc)
    app_long = math.radians(mean_long + center - 0.00569 - 0.00478 * math.sin(omega))
    mean_obliq = 23 + (26 + (21.448 - jc * (46.815 + jc * (0.00059 - jc * 0.001813))) / 60) / 60
    obliq = math.radians(mean_obliq + 0.00256 * math.cos(omega))
    decl = math.asin(math.sin(obliq) * math.sin(app_long))

    y = math.tan(obliq / 2) ** 2
    l0 = math.radians(mean_long)
    eq_time_min = 4 * math.degrees(
        y * math.sin(2 * l0) - 2 * ecc * math.sin(mean_anom)
        + 4 * ecc * y * math.sin(mean_anom) * math.cos(2 * l0)
        - 0.5 * y * y * math.sin(4 * l0) - 1.25 * ecc * ecc * math.sin(2 * mean_anom)
    )
    utc = when_utc.astimezone(timezone.utc)
    minutes = utc.hour * 60 + utc.minute + (utc.second + utc.microsecond / 1e6) / 60
    hour_angle = math.radians(((minutes + eq_time_min + 4 * lon_deg) % 1440) / 4 - 180)

    lat = math.radians(lat_deg)
    cos_zen = math.sin(lat) * math.sin(decl) + math.cos(lat) * math.cos(decl) * math.cos(hour_angle)
    zenith = math.acos(max(-1.0, min(1.0, cos_zen)))
    # Compass azimuth (clockwise from north) from the standard spherical formula.
    az_cw = math.atan2(math.sin(hour_angle),
                       math.cos(hour_angle) * math.sin(lat) - math.tan(decl) * math.cos(lat)) + math.pi
    return (2 * math.pi - az_cw) % (2 * math.pi), math.pi / 2 - zenith


def exposure_to_lux(iso: float, shutter_s: float, fnum: float, ev_comp: float = 0.0) -> float:
    """Estimate scene illuminance from camera exposure (reflected-light meter, calibration C=250)."""
    ev100 = math.log2(fnum * fnum / shutter_s) - math.log2(iso / 100.0) + ev_comp
    return 2.5 * 2 ** ev100


def _band(value: float, bands: List[Tuple[float, str]]) -> str:
    """Return the name of the first band whose lower bound value reaches."""
    for lower, name in bands:
        if value >= lower:
            return name
    return bands[-1][1]


def illumination_level(lux: float) -> str:
    return _band(lux, _ILLUMINATION_BANDS_LX)


def fog_level(visibility_km: float) -> str:
    return _band(visibility_km * 1000.0, _FOG_BANDS_M)


# -----------------------------------------------------------------------------
# OpenLABEL weather
# -----------------------------------------------------------------------------

def _named_values(data: Dict[str, Any]) -> Dict[str, Any]:
    """Flatten OpenLABEL text/num/boolean entries into {name: val}."""
    return {e["name"]: e.get("val") for kind in ("text", "num", "boolean")
            for e in data.get(kind, []) or [] if "name" in e}


@dataclass(frozen=True)
class WeatherAnnotations:
    """Sequence-level WeatherTag values plus frame-interval WeatherContext values (contexts win)."""
    global_values: Dict[str, Any]
    intervals: List[Tuple[int, int, Dict[str, Any]]]

    def for_frame(self, frame_idx: int) -> Dict[str, Any]:
        for start, end, values in self.intervals:
            if start <= frame_idx <= end:
                return values
        return self.global_values


def load_weather(root: Dict[str, Any]) -> WeatherAnnotations:
    global_values: Dict[str, Any] = {}
    for tag in (root.get("tags") or {}).values():
        if tag.get("type") == "WeatherTag":
            global_values = _named_values(tag.get("tag_data") or {})
    intervals = []
    for ctx in (root.get("contexts") or {}).values():
        if ctx.get("type") == "WeatherContext":
            values = _named_values(ctx.get("context_data") or {})
            for fi in ctx.get("frame_intervals") or []:
                intervals.append((int(fi["frame_start"]), int(fi["frame_end"]), values))
    return WeatherAnnotations(global_values, intervals)


def precipitation_level(weather: Dict[str, Any]) -> Optional[str]:
    """Map VLM precipitation/intensity to an OSI precipitation name; None if not annotated."""
    kind, intensity = weather.get("precipitation"), weather.get("precipitation_intensity")
    if kind == "none":
        return "NONE"
    if intensity in _PRECIPITATION_INTENSITY:
        return _PRECIPITATION_INTENSITY[intensity]
    return "UNKNOWN" if kind or intensity else None


# -----------------------------------------------------------------------------
# OSI message
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class EnvironmentSources:
    """Optional inputs for per-frame environmental conditions."""
    weather: WeatherAnnotations
    clock: Optional[FrameClock] = None
    srt_by_frame: Optional[Dict[int, SrtFrame]] = None
    lat_lon: Optional[Tuple[float, float]] = None
    tz_name: str = DEFAULT_TIMEZONE


def environment_fields(frame_idx: int, src: EnvironmentSources) -> Dict[str, Any]:
    """Return OSI EnvironmentalConditions field values for one frame (enum values as names)."""
    out: Dict[str, Any] = {}
    srt = (src.srt_by_frame or {}).get(frame_idx)
    if src.clock is not None:
        utc = src.clock.utc(frame_idx)
        local = utc.astimezone(ZoneInfo(src.tz_name))
        out["unix_timestamp"] = int(utc.timestamp())
        out["seconds_since_midnight"] = local.hour * 3600 + local.minute * 60 + local.second
        lat_lon = (srt.lat, srt.lon) if srt and srt.lat is not None and srt.lon is not None else src.lat_lon
        if lat_lon is not None:
            out["sun_azimuth"], out["sun_elevation"] = solar_position(lat_lon[0], lat_lon[1], utc)
    if srt and srt.iso and srt.shutter_s and srt.fnum:
        out["ambient_illumination"] = illumination_level(
            exposure_to_lux(srt.iso, srt.shutter_s, srt.fnum, srt.ev))

    weather = src.weather.for_frame(frame_idx)
    precipitation = precipitation_level(weather)
    if precipitation:
        out["precipitation"] = precipitation
    if isinstance(weather.get("visibility_km"), (int, float)):
        out["fog"] = fog_level(float(weather["visibility_km"]))
    if weather.get("cloud_cover") in _CLOUD_COVER:
        out["cloud_cover"] = _CLOUD_COVER[weather["cloud_cover"]]
    return out


def build_environmental_conditions(osi: Any, fields: Dict[str, Any]) -> Optional[Any]:
    """Build an osi EnvironmentalConditions from environment_fields(); osi is the betterosi module."""
    if not fields:
        return None
    ec = osi.EnvironmentalConditions()
    if "unix_timestamp" in fields:
        ec.unix_timestamp = fields["unix_timestamp"]
        ec.time_of_day = osi.EnvironmentalConditionsTimeOfDay(
            seconds_since_midnight=fields["seconds_since_midnight"])
    if "sun_azimuth" in fields:
        ec.sun = osi.EnvironmentalConditionsSun(
            azimuth=fields["sun_azimuth"], elevation=fields["sun_elevation"])
    if "ambient_illumination" in fields:
        ec.ambient_illumination = getattr(osi.EnvironmentalConditionsAmbientIllumination, fields["ambient_illumination"])
    if "precipitation" in fields:
        ec.precipitation = getattr(osi.EnvironmentalConditionsPrecipitation, fields["precipitation"])
    if "fog" in fields:
        ec.fog = getattr(osi.EnvironmentalConditionsFog, fields["fog"])
    if "cloud_cover" in fields:
        ec.clouds = osi.EnvironmentalConditionsCloudLayer(
            fractional_cloud_cover=getattr(osi.EnvironmentalConditionsCloudLayerFractionalCloudCover, fields["cloud_cover"]))
    return ec
