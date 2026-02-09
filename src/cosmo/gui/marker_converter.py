# src/cosmo/gui/marker_converter.py
from __future__ import annotations

import csv
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple, List


@dataclass(frozen=True)
class OdrUtmInfo:
    zone: int
    offset_x: float
    offset_y: float


def detect_odr_utm_with_offset(opendrive_path: str) -> Optional[OdrUtmInfo]:
    """Detect +proj=utm +zone=.. in <geoReference> and <offset x= y=> in OpenDRIVE header."""
    p = Path(opendrive_path)
    if not p.is_file():
        return None
    txt = p.read_text(encoding="utf-8", errors="ignore")

    if "+proj=utm" not in txt.lower():
        return None

    m_zone = re.search(r"\+zone=([0-9]+)", txt)
    if not m_zone:
        return None
    zone = int(m_zone.group(1))

    m_off = re.search(r"<offset\s+[^>]*x=\"([0-9\.-]+)\"\s+y=\"([0-9\.-]+)\"", txt)
    if not m_off:
        return None
    offx = float(m_off.group(1))
    offy = float(m_off.group(2))
    return OdrUtmInfo(zone=zone, offset_x=offx, offset_y=offy)


def latlon_to_utm(lat_deg: float, lon_deg: float, zone: int) -> Tuple[float, float]:
    """WGS84 latitude/longitude -> UTM easting/northing for a given zone."""
    a = 6378137.0
    f = 1 / 298.257223563
    e2 = f * (2 - f)
    ep2 = e2 / (1 - e2)
    k0 = 0.9996

    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)
    lon0_deg = (zone - 1) * 6 - 180 + 3
    lon0 = math.radians(lon0_deg)

    N = a / math.sqrt(1 - e2 * math.sin(lat) ** 2)
    T = math.tan(lat) ** 2
    C = ep2 * math.cos(lat) ** 2
    A = math.cos(lat) * (lon - lon0)

    e4 = e2 * e2
    e6 = e4 * e2
    M = a * (
        (1 - e2 / 4 - 3 * e4 / 64 - 5 * e6 / 256) * lat
        - (3 * e2 / 8 + 3 * e4 / 32 + 45 * e6 / 1024) * math.sin(2 * lat)
        + (15 * e4 / 256 + 45 * e6 / 1024) * math.sin(4 * lat)
        - (35 * e6 / 3072) * math.sin(6 * lat)
    )

    easting = (
        k0
        * N
        * (
            A
            + (1 - T + C) * A**3 / 6
            + (5 - 18 * T + T**2 + 72 * C - 58 * ep2) * A**5 / 120
        )
        + 500000.0
    )

    northing = k0 * (
        M
        + N
        * math.tan(lat)
        * (
            A**2 / 2
            + (5 - T + 9 * C + 4 * C**2) * A**4 / 24
            + (61 - 58 * T + T**2 + 600 * C - 330 * ep2) * A**6 / 720
        )
    )

    if lat_deg < 0:
        northing += 10000000.0

    return float(easting), float(northing)


def convert_visual_markers_latlon_to_odr_local(
    visual_markers_csv: str,
    opendrive_path: str,
    out_csv: Optional[str] = None,
) -> str:
    """Convert visual_markers.csv (lat/lon/alt) to OpenDRIVE-local E,N using UTM zone + <offset>."""
    vm_path = Path(visual_markers_csv)
    if not vm_path.is_file():
        raise FileNotFoundError(f"visual_markers not found: {vm_path}")

    info = detect_odr_utm_with_offset(opendrive_path)
    if info is None:
        raise RuntimeError(
            "OpenDRIVE does not look like UTM (+proj=utm +zone=..) with <offset x/y>. Cannot convert markers."
        )

    if out_csv is None:
        out_csv = str(vm_path.with_name(vm_path.stem + "_odr_local.csv"))
    out_path = Path(out_csv)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    rows_out: List[Tuple[str, float, float]] = []
    with open(vm_path, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = str(row.get("point_name", "")).strip()
            if not name:
                continue
            lat = float(row["latitude"])
            lon = float(row["longitude"])
            Eutm, Nutm = latlon_to_utm(lat, lon, info.zone)
            E = Eutm - info.offset_x
            N = Nutm - info.offset_y
            rows_out.append((name, E, N))

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["point_name", "E", "N"])
        for name, E, N in rows_out:
            w.writerow([name, f"{E:.6f}", f"{N:.6f}"])

    return str(out_path.resolve())
