"""Coordinate reference system helpers."""
from __future__ import annotations

import re

from pyproj import CRS
from rasterio.crs import CRS as RioCRS

PRESETS = [
    ("EPSG:3826", "TWD97 / TM2 zone 121 (Taiwan main island)"),
    ("EPSG:3825", "TWD97 / TM2 zone 119 (Penghu, Kinmen, Matsu)"),
    ("EPSG:3828", "TWD67 / TM2 zone 121"),
    ("EPSG:3827", "TWD67 / TM2 zone 119"),
    ("EPSG:3824", "TWD97 geographic (lon/lat)"),
    ("EPSG:32651", "WGS 84 / UTM 51N"),
    ("EPSG:32650", "WGS 84 / UTM 50N"),
    ("EPSG:4326", "WGS 84 geographic (lon/lat)"),
]


def preset_labels() -> list[str]:
    return [f"{code}  {name}" for code, name in PRESETS]


def normalize_text(text: str) -> str:
    """Turn 'EPSG:3826  description' into 'EPSG:3826'; bare digits are treated as EPSG codes."""
    t = (text or "").strip()
    m = re.match(r"^(EPSG:\d+)", t, re.I)
    if m:
        return m.group(1).upper()
    if t.isdigit():
        return f"EPSG:{t}"
    return t


def parse_crs(text: str) -> CRS:
    t = normalize_text(text)
    if not t:
        raise ValueError("No coordinate system specified")
    return CRS.from_user_input(t)


def to_rio(crs: CRS) -> RioCRS:
    epsg = crs.to_epsg()
    if epsg:
        return RioCRS.from_epsg(epsg)
    return RioCRS.from_wkt(crs.to_wkt())


def short_name(crs) -> str:
    if crs is None:
        return ""
    if not isinstance(crs, CRS):
        crs = CRS.from_user_input(crs.to_wkt() if hasattr(crs, "to_wkt") else crs)
    epsg = crs.to_epsg()
    return f"EPSG:{epsg}" if epsg else crs.name


def describe(text: str) -> str:
    try:
        c = parse_crs(text)
        unit = c.axis_info[0].unit_name if c.axis_info else ""
        return f"{c.name}  (units: {unit})"
    except Exception:
        return "⚠ Invalid coordinate system"
