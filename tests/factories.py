"""Builders for test geometries and files. Everything is generated in code."""

import io
import zipfile

import pyproj
import shapefile
import shapely
from pyproj.enums import WktVersion

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


def shapefile_parts(
    geometries: list[shapely.Geometry | None],
    crs: pyproj.CRS | None,
    *,
    esri_prj: bool = False,
    fields: list[tuple] | None = None,
    records: list[tuple] | None = None,
) -> dict[str, bytes]:
    """The .shp/.shx/.dbf (and .prj unless `crs` is None) bytes of a shapefile.

    Geometries must all be Polygons, all LineStrings or all Points; None writes
    a null shape. Without `fields`, each record gets a `name` of "f<index>".
    """
    if fields is None:
        fields = [("name", "C")]
        records = [(f"f{i}",) for i in range(len(geometries))]
    kinds = {g.geom_type for g in geometries if g is not None}
    shape_type = {
        "Polygon": shapefile.POLYGON,
        "LineString": shapefile.POLYLINE,
        "Point": shapefile.POINT,
    }[kinds.pop()]

    shp, shx, dbf = io.BytesIO(), io.BytesIO(), io.BytesIO()
    with shapefile.Writer(shp=shp, shx=shx, dbf=dbf, shapeType=shape_type) as writer:
        for field in fields:
            writer.field(*field)
        for geometry, record in zip(geometries, records, strict=True):
            if geometry is None:
                writer.null()
            elif shape_type == shapefile.POLYGON:
                writer.poly([list(geometry.exterior.coords)])
            elif shape_type == shapefile.POLYLINE:
                writer.line([list(geometry.coords)])
            else:
                writer.point(geometry.x, geometry.y)
            writer.record(*record)

    parts = {".shp": shp.getvalue(), ".shx": shx.getvalue(), ".dbf": dbf.getvalue()}
    if crs is not None:
        version = WktVersion.WKT1_ESRI if esri_prj else WktVersion.WKT1_GDAL
        parts[".prj"] = crs.to_wkt(version).encode()
    return parts


def zip_bytes(members: dict[str, bytes]) -> bytes:
    """A deflate-compressed zip archive holding `members` (name -> content)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def shapefile_zip(parts: dict[str, bytes], stem: str = "plots") -> bytes:
    """Zip the given shapefile parts as `<stem><suffix>` at the archive root."""
    return zip_bytes({f"{stem}{suffix}": content for suffix, content in parts.items()})
