"""Write the sample files used by the README examples.

    python scripts/make_samples.py [--out samples]

samples/sample.kml
    Two folders near Bengaluru. "Plots": a polygon with ExtendedData and a
    MultiGeometry of two fields sharing an edge. "Infrastructure": a road
    (LineString) and a well (Point).

samples/sample_shapefile.zip
    A polygon Shapefile in EPSG:32643 (WGS 84 / UTM 43N) with an ESRI-style
    .prj, as ArcGIS writes it: a plain field, a field with a pond (hole), and
    a self-intersecting field to show INVALID handling.

Shapes are laid out in UTM metres so their true sizes are known, then
converted to lon/lat for the KML. Needs the dev extras (pyshp).
"""

import argparse
import io
import logging
import zipfile
from datetime import date
from pathlib import Path

import pyproj
import shapefile
from pyproj.enums import WktVersion

logger = logging.getLogger(__name__)

UTM_43N = pyproj.CRS.from_epsg(32643)
TO_LON_LAT = pyproj.Transformer.from_crs(UTM_43N, "EPSG:4326", always_xy=True)

# South-west corner of the survey area, in UTM 43N metres (about 77.59°E, 12.97°N).
X0, Y0 = 781_000.0, 1_435_000.0

Ring = list[tuple[float, float]]


def rectangle(x: float, y: float, width: float, height: float) -> Ring:
    """A closed ring, offset (x, y) metres from the survey origin."""
    x, y = X0 + x, Y0 + y
    return [(x, y), (x, y + height), (x + width, y + height), (x + width, y), (x, y)]


def kml_coordinates(points: Ring) -> str:
    lon_lat = (TO_LON_LAT.transform(x, y) for x, y in points)
    return " ".join(f"{lon:.7f},{lat:.7f}" for lon, lat in lon_lat)


def kml_polygon(ring: Ring) -> str:
    return (
        "<Polygon><outerBoundaryIs><LinearRing>"
        f"<coordinates>{kml_coordinates(ring)}</coordinates>"
        "</LinearRing></outerBoundaryIs></Polygon>"
    )


def build_kml() -> str:
    plot_a = rectangle(0, 0, 100, 80)  # 8,000 m²
    plot_b = rectangle(150, 0, 50, 50)  # 2,500 m²
    plot_c = rectangle(200, 0, 50, 50)  # 2,500 m², shares an edge with B
    road = [(X0, Y0 - 20), (X0 + 120, Y0 - 20), (X0 + 120, Y0 - 110)]  # 120 m + 90 m
    well = [(X0 + 50, Y0 + 40)]
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
<Document>
  <name>Sample survey</name>
  <Folder>
    <name>Plots</name>
    <Placemark>
      <name>Plot A</name>
      <ExtendedData>
        <Data name="owner"><value>Asha</value></Data>
        <Data name="crop"><value>rice</value></Data>
      </ExtendedData>
      {kml_polygon(plot_a)}
    </Placemark>
    <Placemark>
      <name>Plots B and C</name>
      <MultiGeometry>{kml_polygon(plot_b)}{kml_polygon(plot_c)}</MultiGeometry>
    </Placemark>
  </Folder>
  <Folder>
    <name>Infrastructure</name>
    <Placemark>
      <name>Access road</name>
      <LineString><coordinates>{kml_coordinates(road)}</coordinates></LineString>
    </Placemark>
    <Placemark>
      <name>Well</name>
      <Point><coordinates>{kml_coordinates(well)}</coordinates></Point>
    </Placemark>
  </Folder>
</Document>
</kml>
"""


def build_shapefile_zip() -> bytes:
    shp, shx, dbf = io.BytesIO(), io.BytesIO(), io.BytesIO()
    with shapefile.Writer(shp=shp, shx=shx, dbf=dbf, shapeType=shapefile.POLYGON) as writer:
        writer.field("name", "C", size=40)
        writer.field("crop", "C", size=20)
        writer.field("surveyed", "D")

        writer.poly([rectangle(0, 200, 200, 150)])  # 30,000 m²
        writer.record("Field 1", "sugarcane", date(2024, 5, 1))

        pond = rectangle(330, 230, 20, 20)[::-1]  # hole: opposite winding to the outer ring
        writer.poly([rectangle(300, 200, 100, 100), pond])  # 10,000 - 400 = 9,600 m²
        writer.record("Field 2 with pond", "rice", date(2024, 5, 2))

        x, y = X0 + 450, Y0 + 200
        bowtie = [(x, y), (x + 100, y + 100), (x + 100, y), (x, y + 100), (x, y)]
        writer.poly([bowtie])  # a digitizing error: the boundary crosses itself
        writer.record("Field 3 (self-intersecting)", "millet", None)

    parts = {
        "survey_fields.shp": shp.getvalue(),
        "survey_fields.shx": shx.getvalue(),
        "survey_fields.dbf": dbf.getvalue(),
        "survey_fields.prj": UTM_43N.to_wkt(WktVersion.WKT1_ESRI).encode(),
        "survey_fields.cpg": b"UTF-8",
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in parts.items():
            # A fixed timestamp keeps the archive stable across regenerations.
            archive.writestr(zipfile.ZipInfo(name, date_time=(2024, 5, 1, 0, 0, 0)), content)
    return buffer.getvalue()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--out", type=Path, default=Path(__file__).resolve().parent.parent / "samples"
    )
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "sample.kml").write_text(build_kml(), encoding="utf-8", newline="\n")
    (args.out / "sample_shapefile.zip").write_bytes(build_shapefile_zip())
    logger.info("Wrote sample.kml and sample_shapefile.zip to %s", args.out)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    main()
