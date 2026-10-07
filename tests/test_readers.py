import json
import math
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pyproj
import pytest
import shapely

from app.core.crs import WGS84, to_wgs84
from app.core.measure import MeasureStatus, measure_geometry
from app.core.readers import KML_DISPLAY_FIELDS, read_kml, read_shapefile, to_json_safe
from app.core.ziputil import extract_shapefile
from tests.factories import (
    BENGALURU_UTM,
    from_utm,
    kml_coords,
    kml_document,
    kml_folder,
    kml_placemark,
    kml_polygon,
    shapefile_parts,
    shapefile_zip,
    utm_square,
)

PLOT = from_utm(utm_square(1000))
ROAD = shapely.LineString([(77.59, 12.97), (77.60, 12.98)])


def write(tmp_path: Path, text: str, name: str = "survey.kml") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_kml_reads_every_folder_with_continuous_indices(tmp_path: Path) -> None:
    text = kml_document(
        kml_folder(
            "Plots",
            kml_placemark("Plot A", kml_polygon(PLOT)),
            kml_placemark("Plot B", kml_polygon(PLOT)),
        ),
        kml_folder(
            "Roads",
            kml_placemark(
                "Road 1", f"<LineString><coordinates>{kml_coords(ROAD)}</coordinates></LineString>"
            ),
        ),
    )

    result = read_kml(write(tmp_path, text))

    assert [f.index for f in result.features] == [0, 1, 2]
    assert [f.layer for f in result.features] == ["Plots", "Plots", "Roads"]
    assert [f.properties["Name"] for f in result.features] == ["Plot A", "Plot B", "Road 1"]
    assert [f.geometry.geom_type for f in result.features] == ["Polygon", "Polygon", "LineString"]


def test_kml_crs_is_always_wgs84(tmp_path: Path) -> None:
    result = read_kml(write(tmp_path, kml_document(kml_placemark("P", kml_polygon(PLOT)))))

    assert result.file_crs == "EPSG:4326"
    assert result.crs_definition == "EPSG:4326"


def test_kml_reads_placemarks_outside_folders(tmp_path: Path) -> None:
    text = kml_document(
        kml_placemark("Loose", "<Point><coordinates>77.59,12.97</coordinates></Point>"),
        kml_placemark("Plot", kml_polygon(PLOT)),
    )

    result = read_kml(write(tmp_path, text, name="loose.kml"))

    assert len(result.features) == 2
    assert {f.layer for f in result.features} == {"loose"}


def test_kml_untyped_data_becomes_properties(tmp_path: Path) -> None:
    # Google Earth style: free-form <Data name="..."> pairs.
    data = (
        "<ExtendedData>"
        '<Data name="owner"><value>Asha</value></Data>'
        '<Data name="crop"><value>rice</value></Data>'
        "</ExtendedData>"
    )
    text = kml_document(kml_placemark("A", kml_polygon(PLOT), extra_xml=data))

    properties = read_kml(write(tmp_path, text)).features[0].properties

    assert properties["owner"] == "Asha"
    assert properties["crop"] == "rice"


def test_kml_schema_data_becomes_properties(tmp_path: Path) -> None:
    # QGIS / ArcGIS style: a declared <Schema> with typed <SimpleData> values.
    schema_data = (
        '<ExtendedData><SchemaData schemaUrl="#plots">'
        '<SimpleData name="survey_no">42</SimpleData>'
        "</SchemaData></ExtendedData>"
    )
    text = kml_document(
        '<Schema name="plots" id="plots"><SimpleField name="survey_no" type="string"/></Schema>',
        kml_placemark("B", kml_polygon(PLOT), extra_xml=schema_data),
    )

    properties = read_kml(write(tmp_path, text)).features[0].properties

    assert properties["survey_no"] == "42"


def test_kml_drops_libkml_display_fields(tmp_path: Path) -> None:
    result = read_kml(write(tmp_path, kml_document(kml_placemark("P", kml_polygon(PLOT)))))

    properties = result.features[0].properties
    assert KML_DISPLAY_FIELDS.isdisjoint(properties)
    assert {"Name", "description"} <= properties.keys()


def test_kml_properties_are_json_serializable(tmp_path: Path) -> None:
    timestamp = "<TimeStamp><when>2024-05-01T10:30:00Z</when></TimeStamp>"
    text = kml_document(kml_placemark("P", kml_polygon(PLOT), extra_xml=timestamp))

    properties = read_kml(write(tmp_path, text)).features[0].properties

    json.dumps(properties, allow_nan=False)
    assert properties["timestamp"].startswith("2024-05-01T10:30:00")


def test_kml_multigeometry_of_polygons_is_measured(tmp_path: Path) -> None:
    x, y = BENGALURU_UTM
    neighbour = from_utm(utm_square(1000, origin=(x + 1000, y)))
    multi = f"<MultiGeometry>{kml_polygon(PLOT)}{kml_polygon(neighbour)}</MultiGeometry>"

    feature = read_kml(write(tmp_path, kml_document(kml_placemark("Twin", multi)))).features[0]
    measurement = measure_geometry(feature.geometry)

    assert measurement.status is MeasureStatus.MEASURED
    assert measurement.area_m2 == pytest.approx(2_000_000, rel=0.0025)


def test_kml_keeps_altitude_as_z(tmp_path: Path) -> None:
    text = kml_document(
        kml_placemark("Tower", "<Point><coordinates>77.59,12.97,900</coordinates></Point>")
    )

    geometry = read_kml(write(tmp_path, text)).features[0].geometry

    assert shapely.has_z(geometry)


def test_kml_placemark_without_geometry_is_kept(tmp_path: Path) -> None:
    text = kml_document(kml_placemark("Note"), kml_placemark("Plot", kml_polygon(PLOT)))

    features = read_kml(write(tmp_path, text)).features

    assert [f.properties["Name"] for f in features] == ["Note", "Plot"]
    assert features[0].geometry is None
    assert [f.index for f in features] == [0, 1]


def read_zipped(tmp_path: Path, parts: dict[str, bytes]):
    """Zip the parts, extract them as an upload would be, and read the result."""
    zip_path = tmp_path / "upload.zip"
    zip_path.write_bytes(shapefile_zip(parts))
    shp = extract_shapefile(
        zip_path, tmp_path / "extracted", max_entries=10, max_uncompressed=10_000_000
    )
    return read_shapefile(shp)


def in_crs(geom: shapely.Geometry, crs: pyproj.CRS) -> shapely.Geometry:
    """Express an EPSG:4326 geometry in `crs`, as a GIS would write it to a shapefile."""
    transformer = pyproj.Transformer.from_crs(WGS84, crs, always_xy=True)
    return shapely.transform(geom, transformer.transform, interleaved=False)


@pytest.mark.parametrize("epsg", [4326, 32643, 3857])
@pytest.mark.parametrize("esri_prj", [False, True], ids=["gdal_prj", "esri_prj"])
def test_shapefile_crs_comes_from_prj(tmp_path: Path, epsg: int, esri_prj: bool) -> None:
    crs = pyproj.CRS.from_epsg(epsg)
    parts = shapefile_parts([in_crs(PLOT, crs)], crs, esri_prj=esri_prj)

    result = read_zipped(tmp_path, parts)

    assert result.file_crs == f"EPSG:{epsg}"
    assert result.crs_definition == f"EPSG:{epsg}"
    assert [(f.index, f.layer) for f in result.features] == [(0, "data")]
    assert result.features[0].properties == {"name": "f0"}


def test_shapefile_in_web_mercator_measures_true_area(tmp_path: Path) -> None:
    # A true 1 km² square delivered in EPSG:3857. Measured in Mercator metres it
    # would come out about 5% high at 13°N; the pipeline goes via EPSG:4326 to UTM.
    mercator = pyproj.CRS.from_epsg(3857)
    parts = shapefile_parts([in_crs(PLOT, mercator)], mercator)
    result = read_zipped(tmp_path, parts)

    geoms = to_wgs84(np.array([f.geometry for f in result.features]), result.crs_definition)
    measurement = measure_geometry(geoms[0])

    # Same tolerance and reasoning as test_measure.TOLERANCE (UTM is not equal-area).
    assert measurement.area_m2 == pytest.approx(1_000_000, rel=0.0025)


def test_shapefile_with_unidentified_crs_keeps_full_wkt(tmp_path: Path) -> None:
    custom = pyproj.CRS.from_proj4("+proj=tmerc +lat_0=0 +lon_0=78 +k=1 +x_0=0 +y_0=0 +ellps=WGS84")
    parts = shapefile_parts([in_crs(PLOT, custom)], custom)

    result = read_zipped(tmp_path, parts)

    assert result.file_crs.startswith("WKT:")
    assert pyproj.CRS.from_user_input(result.crs_definition).equals(custom)
    geoms = to_wgs84(np.array([f.geometry for f in result.features]), result.crs_definition)
    assert measure_geometry(geoms[0]).area_m2 == pytest.approx(1_000_000, rel=0.0025)


def test_shapefile_with_unreadable_prj_is_rejected(tmp_path: Path) -> None:
    parts = shapefile_parts([PLOT], pyproj.CRS.from_epsg(4326))
    parts[".prj"] = b"not a coordinate system"

    with pytest.raises(ValueError, match="readable coordinate system"):
        read_zipped(tmp_path, parts)


def test_shapefile_null_geometry_kept_and_properties_json_safe(tmp_path: Path) -> None:
    fields = [("name", "C"), ("yield", "N", 10, 2), ("surveyed", "D")]
    records = [("A", 1.5, date(2024, 5, 1)), ("B", None, None)]
    parts = shapefile_parts(
        [PLOT, None], pyproj.CRS.from_epsg(4326), fields=fields, records=records
    )

    features = read_zipped(tmp_path, parts).features

    assert [f.index for f in features] == [0, 1]
    assert features[1].geometry is None
    assert features[0].properties == {"name": "A", "yield": 1.5, "surveyed": "2024-05-01"}
    assert features[1].properties == {"name": "B", "yield": None, "surveyed": None}
    json.dumps([f.properties for f in features], allow_nan=False)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        (np.int32(7), 7),
        (np.float64(2.5), 2.5),
        (np.float64("nan"), None),
        (math.inf, None),
        (np.bool_(True), True),
        (np.datetime64("2024-05-01T10:30:00.000"), "2024-05-01T10:30:00.000"),
        (np.datetime64("NaT", "ms"), None),
        (b"caf\xc3\xa9", "café"),
        (b"\xff", "�"),
        (date(2024, 5, 1), "2024-05-01"),
        (datetime(2024, 5, 1, 10, 30), "2024-05-01T10:30:00"),
        ("plain", "plain"),
    ],
)
def test_to_json_safe(value: object, expected: object) -> None:
    assert to_json_safe(value) == expected
