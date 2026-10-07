"""CRS helpers: UTM zone selection, cached transformers and CRS labels."""

from functools import lru_cache

import numpy as np
import pyproj
import shapely

WGS84 = "EPSG:4326"

# UTM is only defined between 80°S and 84°N; the poles use UPS instead.
_UTM_MIN_LAT = -80.0
_UTM_MAX_LAT = 84.0
_UTM_NORTH_BASE = 32600
_UTM_SOUTH_BASE = 32700


class OutsideUtmRange(ValueError):
    """Raised when a latitude is outside the UTM grid."""


def utm_epsg_for(lon: float, lat: float) -> int:
    """Return the EPSG code of the WGS 84 / UTM zone containing (lon, lat).

    Norway/Svalbard zone exceptions are deliberately ignored.
    """
    if lat < _UTM_MIN_LAT or lat > _UTM_MAX_LAT:
        raise OutsideUtmRange(f"latitude {lat} is outside the UTM range (-80 to 84)")
    lon = ((lon + 180) % 360) - 180
    zone = int((lon + 180) // 6) + 1
    base = _UTM_NORTH_BASE if lat >= 0 else _UTM_SOUTH_BASE
    return base + zone


def central_meridian(epsg: int) -> float:
    """Return the central meridian (degrees longitude) of a WGS 84 / UTM EPSG code."""
    zone = epsg % 100
    if epsg - zone not in (_UTM_NORTH_BASE, _UTM_SOUTH_BASE) or not 1 <= zone <= 60:
        raise ValueError(f"EPSG:{epsg} is not a WGS 84 / UTM zone")
    return float(zone * 6 - 183)


@lru_cache(maxsize=256)
def get_transformer(src: str, dst: str) -> pyproj.Transformer:
    """Return a cached transformer that takes and returns (x, y) = (lon, lat) order.

    pyproj keeps per-thread state inside Transformer, so sharing cached
    instances across the request threadpool is safe.
    """
    return pyproj.Transformer.from_crs(src, dst, always_xy=True)


def to_wgs84(geoms: np.ndarray, src_crs: str) -> np.ndarray:
    """Reproject an array of geometries to EPSG:4326 in one vectorized call.

    `src_crs` is any definition pyproj accepts ("EPSG:xxxx" or WKT). Null
    geometries pass through as None, and Z values are kept where present.
    """
    if src_crs == WGS84:
        return geoms
    transformer = get_transformer(src_crs, WGS84)
    return shapely.transform(geoms, transformer.transform, interleaved=False, include_z=None)


def to_utm(geom_wgs84: shapely.Geometry, epsg: int) -> shapely.Geometry:
    """Reproject a 2D EPSG:4326 geometry to the given UTM zone (metres)."""
    transformer = get_transformer(WGS84, f"EPSG:{epsg}")
    return shapely.transform(geom_wgs84, transformer.transform, interleaved=False)


def describe_crs(crs: pyproj.CRS) -> str:
    """Return "EPSG:xxxx" when pyproj can identify the CRS, else "WKT:<name>"."""
    epsg = crs.to_epsg(min_confidence=70)
    if epsg is not None:
        return f"EPSG:{epsg}"
    return f"WKT:{crs.name}"
