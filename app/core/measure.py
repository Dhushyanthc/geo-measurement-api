"""Per-feature measurement: area for polygons, length for lines, in metres.

Every geometry arrives in EPSG:4326 and is reprojected to the UTM zone at the
centre of its bounding box before measuring. Nothing is measured in degrees.
"""

from dataclasses import dataclass
from enum import StrEnum

import numpy as np
import shapely
from shapely.validation import explain_validity

from app.core.crs import OutsideUtmRange, central_meridian, to_utm, utm_epsg_for

Z_IGNORED = "Z_IGNORED"
NORMALIZED_FROM_GEOMETRYCOLLECTION = "NORMALIZED_FROM_GEOMETRYCOLLECTION"
FAR_FROM_CENTRAL_MERIDIAN = "FAR_FROM_CENTRAL_MERIDIAN"

# UTM scale error grows with distance from the zone's central meridian; zones
# are 6° wide, so anything reaching past 4° spills well into a neighbour zone.
FAR_FROM_CENTRAL_MERIDIAN_DEG = 4.0

_POLYGONAL = {"Polygon", "MultiPolygon"}
_LINEAL = {"LineString", "MultiLineString"}
_PUNTAL = {"Point", "MultiPoint"}


class MeasureStatus(StrEnum):
    MEASURED = "MEASURED"
    NO_MEASUREMENT_REQUIRED = "NO_MEASUREMENT_REQUIRED"
    UNSUPPORTED = "UNSUPPORTED"
    INVALID = "INVALID"


@dataclass(frozen=True)
class Measurement:
    status: MeasureStatus
    area_m2: float | None = None
    length_m: float | None = None
    crs: str | None = None
    reason: str | None = None
    warnings: tuple[str, ...] = ()


class _CannotMeasure(Exception):
    """Internal signal: the geometry is fine but cannot be projected safely."""


def measure_geometry(geom_wgs84: shapely.Geometry | None) -> Measurement:
    """Measure one EPSG:4326 geometry. Never raises for bad or odd geometries."""
    if geom_wgs84 is None or geom_wgs84.is_empty:
        return Measurement(MeasureStatus.UNSUPPORTED, reason="null or empty geometry")

    warnings: list[str] = []
    geom = geom_wgs84
    if shapely.has_z(geom):
        geom = shapely.force_2d(geom)
        warnings.append(Z_IGNORED)

    try:
        return _measure_by_type(geom, warnings)
    except _CannotMeasure as exc:
        return Measurement(MeasureStatus.UNSUPPORTED, reason=str(exc), warnings=tuple(warnings))


def _measure_by_type(geom: shapely.Geometry, warnings: list[str]) -> Measurement:
    geom_type = geom.geom_type

    if geom_type == "GeometryCollection":
        kinds = {part.geom_type for part in shapely.get_parts(geom)}
        if kinds <= _POLYGONAL:
            warnings.append(NORMALIZED_FROM_GEOMETRYCOLLECTION)
            return _measure_area(geom, warnings)
        if kinds <= _LINEAL:
            warnings.append(NORMALIZED_FROM_GEOMETRYCOLLECTION)
            return _measure_length(shapely.MultiLineString(_flatten(geom)), warnings)
        if kinds <= _PUNTAL:
            warnings.append(NORMALIZED_FROM_GEOMETRYCOLLECTION)
            return _no_measurement(warnings)
        names = ", ".join(sorted(kinds))
        raise _CannotMeasure(f"mixed or nested GeometryCollection: {names}")

    if geom_type in _PUNTAL:
        return _no_measurement(warnings)
    if geom_type in _POLYGONAL:
        return _measure_area(geom, warnings)
    if geom_type in _LINEAL:
        return _measure_length(geom, warnings)
    raise _CannotMeasure(f"geometry type {geom_type} is not supported for measurement")


def _measure_area(geom: shapely.Geometry, warnings: list[str]) -> Measurement:
    # Each polygon is validated on its own: shapely treats a MultiPolygon whose
    # parts share an edge (adjacent fields) as invalid, which is wrong here.
    polygons = _flatten(geom)
    for i, polygon in enumerate(polygons):
        if not polygon.is_valid:
            reason = explain_validity(polygon)
            if len(polygons) > 1:
                reason = f"part {i}: {reason}"
            return Measurement(MeasureStatus.INVALID, reason=reason, warnings=tuple(warnings))

    epsg = _choose_utm_zone(geom, warnings)
    area = sum(_project(polygon, epsg).area for polygon in polygons)
    return Measurement(
        MeasureStatus.MEASURED, area_m2=area, crs=f"EPSG:{epsg}", warnings=tuple(warnings)
    )


def _measure_length(geom: shapely.Geometry, warnings: list[str]) -> Measurement:
    epsg = _choose_utm_zone(geom, warnings)
    length = _project(geom, epsg).length
    return Measurement(
        MeasureStatus.MEASURED, length_m=length, crs=f"EPSG:{epsg}", warnings=tuple(warnings)
    )


def _no_measurement(warnings: list[str]) -> Measurement:
    return Measurement(MeasureStatus.NO_MEASUREMENT_REQUIRED, warnings=tuple(warnings))


def _flatten(geom: shapely.Geometry) -> list[shapely.Geometry]:
    """Split a geometry into single parts, one level of collection deep."""
    return list(shapely.get_parts(shapely.get_parts(geom)))


def _choose_utm_zone(geom: shapely.Geometry, warnings: list[str]) -> int:
    min_lon, min_lat, max_lon, max_lat = geom.bounds
    if max_lon - min_lon > 180:
        raise _CannotMeasure("crosses antimeridian")
    try:
        epsg = utm_epsg_for((min_lon + max_lon) / 2, (min_lat + max_lat) / 2)
    except OutsideUtmRange as exc:
        raise _CannotMeasure(str(exc)) from exc

    meridian = central_meridian(epsg)
    if max(abs(min_lon - meridian), abs(max_lon - meridian)) > FAR_FROM_CENTRAL_MERIDIAN_DEG:
        warnings.append(FAR_FROM_CENTRAL_MERIDIAN)
    return epsg


def _project(geom: shapely.Geometry, epsg: int) -> shapely.Geometry:
    projected = to_utm(geom, epsg)
    if not np.isfinite(shapely.get_coordinates(projected)).all():
        raise _CannotMeasure("projection failed")
    return projected
