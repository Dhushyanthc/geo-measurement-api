import numpy as np
import pytest
import shapely
from pyproj import Geod

from app.core.crs import WGS84, get_transformer, to_wgs84
from app.core.measure import (
    FAR_FROM_CENTRAL_MERIDIAN,
    NORMALIZED_FROM_GEOMETRYCOLLECTION,
    Z_IGNORED,
    MeasureStatus,
    measure_geometry,
)
from tests.factories import BENGALURU_UTM, from_utm, utm_square

# UTM is conformal, not equal-area. Its scale factor is 0.9996 on the central
# meridian and grows away from it: Bengaluru sits about 2.6° east of zone 43N's
# meridian, where areas come out roughly 0.1% high. 0.25% leaves headroom for
# that while still catching real mistakes (degrees, Web Mercator, wrong zone),
# which are off by whole percent or by orders of magnitude.
TOLERANCE = 0.0025

GEOD = Geod(ellps="WGS84")


def geodesic_area(geom: shapely.Geometry) -> float:
    return abs(GEOD.geometry_area_perimeter(geom)[0])


def test_known_square_area_matches_truth_and_geodesic() -> None:
    square = from_utm(utm_square(1000))

    result = measure_geometry(square)

    assert result.status is MeasureStatus.MEASURED
    assert result.area_m2 == pytest.approx(1_000_000, rel=TOLERANCE)
    assert result.area_m2 == pytest.approx(geodesic_area(square), rel=TOLERANCE)
    assert result.length_m is None
    assert result.crs == "EPSG:32643"
    assert result.warnings == ()


def test_web_mercator_input_is_not_measured_in_mercator_metres() -> None:
    to_mercator = get_transformer(WGS84, "EPSG:3857")
    square_3857 = shapely.transform(
        from_utm(utm_square(1000)), to_mercator.transform, interleaved=False
    )
    # The trap: at 13°N, Web Mercator "metres" inflate area by about 1/cos²(lat).
    assert square_3857.area > 1_040_000

    in_degrees = to_wgs84(np.array([square_3857]), "EPSG:3857")[0]
    result = measure_geometry(in_degrees)

    assert result.area_m2 == pytest.approx(1_000_000, rel=TOLERANCE)


def test_lon_lat_input_selects_zone_43n_for_bengaluru() -> None:
    # KML order is lon,lat. If the axes were swapped, (12.97, 77.59) would land
    # in zone 33N at 77°N instead.
    plot = shapely.box(77.59, 12.97, 77.60, 12.98)

    assert measure_geometry(plot).crs == "EPSG:32643"


def test_polygon_with_hole_subtracts_hole() -> None:
    x, y = BENGALURU_UTM
    outer = utm_square(1000).exterior.coords
    hole = shapely.box(x + 400, y + 400, x + 600, y + 600).exterior.coords
    polygon = from_utm(shapely.Polygon(outer, [hole]))

    result = measure_geometry(polygon)

    assert result.area_m2 == pytest.approx(1_000_000 - 40_000, rel=TOLERANCE)


def test_multipolygon_sums_parts() -> None:
    x, y = BENGALURU_UTM
    parts = [utm_square(1000), utm_square(500, origin=(x + 5000, y))]

    result = measure_geometry(from_utm(shapely.MultiPolygon(parts)))

    assert result.status is MeasureStatus.MEASURED
    assert result.area_m2 == pytest.approx(1_250_000, rel=TOLERANCE)


def test_multipolygon_with_shared_edge_is_measured_not_invalid() -> None:
    # Adjacent fields: shapely calls this MultiPolygon invalid, but each part
    # is a valid polygon, so the area is still well defined.
    x, y = BENGALURU_UTM
    parts = [utm_square(1000), utm_square(1000, origin=(x + 1000, y))]
    multipolygon = from_utm(shapely.MultiPolygon(parts))
    assert not multipolygon.is_valid

    result = measure_geometry(multipolygon)

    assert result.status is MeasureStatus.MEASURED
    assert result.area_m2 == pytest.approx(2_000_000, rel=TOLERANCE)


def test_bowtie_polygon_is_invalid_with_reason_and_no_numbers() -> None:
    bowtie = shapely.Polygon([(77.59, 12.97), (77.60, 12.98), (77.60, 12.97), (77.59, 12.98)])

    result = measure_geometry(bowtie)

    assert result.status is MeasureStatus.INVALID
    assert "Self-intersection" in result.reason
    assert result.area_m2 is None and result.length_m is None and result.crs is None


def test_multipolygon_with_one_bad_part_names_that_part() -> None:
    good = shapely.box(77.50, 12.90, 77.51, 12.91)
    bowtie = shapely.Polygon([(77.59, 12.97), (77.60, 12.98), (77.60, 12.97), (77.59, 12.98)])

    result = measure_geometry(shapely.MultiPolygon([good, bowtie]))

    assert result.status is MeasureStatus.INVALID
    assert result.reason.startswith("part 1:")


def test_linestring_length_matches_truth_and_geodesic() -> None:
    x, y = BENGALURU_UTM
    line = from_utm(shapely.LineString([(x, y), (x + 1000, y)]))

    result = measure_geometry(line)

    assert result.status is MeasureStatus.MEASURED
    assert result.length_m == pytest.approx(1000, rel=TOLERANCE)
    assert result.length_m == pytest.approx(GEOD.geometry_length(line), rel=TOLERANCE)
    assert result.area_m2 is None


def test_multilinestring_sums_parts() -> None:
    x, y = BENGALURU_UTM
    lines = shapely.MultiLineString([[(x, y), (x + 1000, y)], [(x, y + 5000), (x, y + 5500)]])

    result = measure_geometry(from_utm(lines))

    assert result.length_m == pytest.approx(1500, rel=TOLERANCE)


@pytest.mark.parametrize(
    "geom",
    [shapely.Point(77.59, 12.97), shapely.MultiPoint([(77.59, 12.97), (77.6, 12.98)])],
)
def test_points_need_no_measurement(geom: shapely.Geometry) -> None:
    result = measure_geometry(geom)

    assert result.status is MeasureStatus.NO_MEASUREMENT_REQUIRED
    assert result.area_m2 is None and result.length_m is None and result.crs is None


def test_z_coordinates_are_ignored_with_warning() -> None:
    flat = from_utm(utm_square(1000))
    with_z = shapely.force_3d(flat, 900.0)

    result = measure_geometry(with_z)

    assert result.status is MeasureStatus.MEASURED
    assert result.area_m2 == pytest.approx(measure_geometry(flat).area_m2)
    assert result.warnings == (Z_IGNORED,)


@pytest.mark.parametrize("geom", [None, shapely.Polygon(), shapely.GeometryCollection()])
def test_null_or_empty_geometry_is_unsupported(geom: shapely.Geometry | None) -> None:
    result = measure_geometry(geom)

    assert result.status is MeasureStatus.UNSUPPORTED
    assert result.reason == "null or empty geometry"


def test_geometrycollection_of_polygons_is_normalized_and_summed() -> None:
    x, y = BENGALURU_UTM
    collection = shapely.GeometryCollection(
        [utm_square(1000), shapely.MultiPolygon([utm_square(500, origin=(x + 5000, y))])]
    )

    result = measure_geometry(from_utm(collection))

    assert result.status is MeasureStatus.MEASURED
    assert result.area_m2 == pytest.approx(1_250_000, rel=TOLERANCE)
    assert result.warnings == (NORMALIZED_FROM_GEOMETRYCOLLECTION,)


def test_geometrycollection_of_lines_is_normalized_and_summed() -> None:
    x, y = BENGALURU_UTM
    collection = shapely.GeometryCollection(
        [shapely.LineString([(x, y), (x + 1000, y)]), shapely.LineString([(x, y), (x, y + 500)])]
    )

    result = measure_geometry(from_utm(collection))

    assert result.length_m == pytest.approx(1500, rel=TOLERANCE)
    assert result.warnings == (NORMALIZED_FROM_GEOMETRYCOLLECTION,)


def test_geometrycollection_of_points_needs_no_measurement() -> None:
    collection = shapely.GeometryCollection([shapely.Point(77.59, 12.97)])

    result = measure_geometry(collection)

    assert result.status is MeasureStatus.NO_MEASUREMENT_REQUIRED
    assert result.warnings == (NORMALIZED_FROM_GEOMETRYCOLLECTION,)


def test_mixed_geometrycollection_is_unsupported_naming_types() -> None:
    collection = shapely.GeometryCollection(
        [shapely.box(77.59, 12.97, 77.60, 12.98), shapely.LineString([(77.5, 12.9), (77.6, 13)])]
    )

    result = measure_geometry(collection)

    assert result.status is MeasureStatus.UNSUPPORTED
    assert "LineString" in result.reason and "Polygon" in result.reason
    assert result.warnings == ()


def test_nested_geometrycollection_is_unsupported() -> None:
    inner = shapely.GeometryCollection([shapely.box(77.59, 12.97, 77.60, 12.98)])

    result = measure_geometry(shapely.GeometryCollection([inner]))

    assert result.status is MeasureStatus.UNSUPPORTED


def test_linearring_is_unsupported_naming_type() -> None:
    ring = shapely.LinearRing([(77.59, 12.97), (77.60, 12.97), (77.60, 12.98)])

    result = measure_geometry(ring)

    assert result.status is MeasureStatus.UNSUPPORTED
    assert "LinearRing" in result.reason


def test_polar_geometry_is_unsupported() -> None:
    result = measure_geometry(shapely.box(10.0, 85.0, 10.1, 85.1))

    assert result.status is MeasureStatus.UNSUPPORTED
    assert "UTM range" in result.reason


def test_antimeridian_crossing_is_unsupported() -> None:
    result = measure_geometry(shapely.LineString([(179.5, 10.0), (-179.5, 10.0)]))

    assert result.status is MeasureStatus.UNSUPPORTED
    assert result.reason == "crosses antimeridian"


def test_far_from_central_meridian_warns_but_still_measures() -> None:
    # Centre 76.75°E is in zone 43N (meridian 75°E); the east end is 5.5° away.
    line = shapely.LineString([(73.0, 13.0), (80.5, 13.0)])

    result = measure_geometry(line)

    assert result.status is MeasureStatus.MEASURED
    assert result.crs == "EPSG:32643"
    assert FAR_FROM_CENTRAL_MERIDIAN in result.warnings


def test_small_feature_near_meridian_has_no_warning() -> None:
    assert measure_geometry(shapely.box(75.0, 13.0, 75.01, 13.01)).warnings == ()
