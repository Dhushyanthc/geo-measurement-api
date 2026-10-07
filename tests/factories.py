"""Builders for test geometries and files. Everything is generated in code."""

import shapely

from app.core.crs import WGS84, get_transformer

# Zone 43N easting/northing near Bengaluru (77.59°E, 12.97°N).
BENGALURU_UTM = (781_000.0, 1_435_000.0)


def from_utm(geom: shapely.Geometry, epsg: int = 32643) -> shapely.Geometry:
    """Convert a geometry built in a UTM zone (metres) to EPSG:4326."""
    transformer = get_transformer(f"EPSG:{epsg}", WGS84)
    return shapely.transform(geom, transformer.transform, interleaved=False)


def utm_square(size_m: float, origin: tuple[float, float] = BENGALURU_UTM) -> shapely.Polygon:
    """A square of exactly `size_m` x `size_m` metres in zone 43N."""
    x, y = origin
    return shapely.box(x, y, x + size_m, y + size_m)
