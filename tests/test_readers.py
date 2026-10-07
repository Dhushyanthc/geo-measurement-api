import json
import math
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pytest
import shapely

from app.core.measure import MeasureStatus, measure_geometry
from app.core.readers import KML_DISPLAY_FIELDS, read_kml, to_json_safe
from tests.factories import (
    BENGALURU_UTM,
    from_utm,
    kml_coords,
    kml_document,
    kml_folder,
    kml_placemark,
    kml_polygon,
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
