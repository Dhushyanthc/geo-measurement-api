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


def kml_coords(geom: shapely.Geometry) -> str:
    """KML coordinate text (lon,lat[,alt] tuples) for a point, line or ring."""
    return " ".join(",".join(f"{v:.8f}" for v in c) for c in geom.coords)


def kml_polygon(polygon: shapely.Polygon) -> str:
    ring = kml_coords(polygon.exterior)
    return (
        "<Polygon><outerBoundaryIs><LinearRing>"
        f"<coordinates>{ring}</coordinates>"
        "</LinearRing></outerBoundaryIs></Polygon>"
    )


def kml_placemark(name: str, geometry_xml: str = "", extra_xml: str = "") -> str:
    return f"<Placemark><name>{name}</name>{extra_xml}{geometry_xml}</Placemark>"


def kml_document(*children: str) -> str:
    """A complete KML file whose <Document> contains the given elements."""
    body = "\n".join(children)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>\n'
        f"{body}\n"
        "</Document></kml>\n"
    )


def kml_folder(name: str, *placemarks: str) -> str:
    return f"<Folder><name>{name}</name>{''.join(placemarks)}</Folder>"
