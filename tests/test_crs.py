import numpy as np
import pyproj
import pytest
import shapely
from pyproj.enums import WktVersion

from app.core.crs import (
    WGS84,
    OutsideUtmRange,
    central_meridian,
    describe_crs,
    get_transformer,
    to_utm,
    to_wgs84,
    utm_epsg_for,
)


@pytest.mark.parametrize(
    ("lon", "lat", "expected"),
    [
        (77.59, 12.97, 32643),  # Bengaluru
        (-122.42, 37.77, 32610),  # San Francisco
        (151.2, -33.87, 32756),  # Sydney
        (18.42, -33.92, 32734),  # Cape Town
        (0.0, 0.0, 32631),  # the equator counts as north
    ],
)
def test_utm_epsg_for_known_places(lon: float, lat: float, expected: int) -> None:
    assert utm_epsg_for(lon, lat) == expected


@pytest.mark.parametrize(
    ("lon", "expected"),
    [
        (-180.0, 32601),
        (180.0, 32601),  # same meridian as -180
        (179.99, 32660),
        (-179.99, 32601),
        (77.59 + 360, 32643),  # out-of-range longitudes are wrapped
    ],
)
def test_utm_epsg_for_longitude_edges(lon: float, expected: int) -> None:
    assert utm_epsg_for(lon, 10.0) == expected


def test_utm_epsg_for_accepts_band_limits() -> None:
    assert utm_epsg_for(10.0, 84.0) == 32632
    assert utm_epsg_for(10.0, -80.0) == 32732


@pytest.mark.parametrize("lat", [84.01, -80.01, 90.0, -90.0])
def test_utm_epsg_for_rejects_polar_latitudes(lat: float) -> None:
    with pytest.raises(OutsideUtmRange):
        utm_epsg_for(10.0, lat)


@pytest.mark.parametrize(
    ("epsg", "expected"),
    [(32643, 75.0), (32610, -123.0), (32756, 153.0), (32601, -177.0), (32660, 177.0)],
)
def test_central_meridian(epsg: int, expected: float) -> None:
    assert central_meridian(epsg) == expected


@pytest.mark.parametrize("epsg", [4326, 3857, 32600, 32661, 32700])
def test_central_meridian_rejects_non_utm_codes(epsg: int) -> None:
    with pytest.raises(ValueError):
        central_meridian(epsg)


def test_get_transformer_is_cached() -> None:
    assert get_transformer(WGS84, "EPSG:32643") is get_transformer(WGS84, "EPSG:32643")


def test_transformer_uses_lon_lat_order() -> None:
    # EPSG:4326 officially orders axes lat, lon. With always_xy the first
    # argument must be treated as longitude, giving a zone-43N easting near
    # the false easting of 500 km and a northing of about 1,434 km.
    easting, northing = get_transformer(WGS84, "EPSG:32643").transform(77.59, 12.97)
    assert 650_000 < easting < 800_000
    assert 1_400_000 < northing < 1_470_000


def test_to_wgs84_returns_input_unchanged_for_wgs84() -> None:
    geoms = np.array([shapely.Point(77.59, 12.97)])
    assert to_wgs84(geoms, WGS84) is geoms


def test_to_wgs84_vectorized_matches_one_by_one() -> None:
    mercator = pyproj.Transformer.from_crs(WGS84, "EPSG:3857", always_xy=True)
    x, y = mercator.transform(77.59, 12.97)
    geoms = np.array(
        [
            shapely.Point(x, y),
            shapely.LineString([(x, y), (x + 1000, y + 1000)]),
            shapely.box(x, y, x + 1000, y + 1000),
        ]
    )

    together = to_wgs84(geoms, "EPSG:3857")

    for geom, result in zip(geoms, together, strict=True):
        alone = to_wgs84(np.array([geom]), "EPSG:3857")[0]
        assert shapely.equals_exact(result, alone, tolerance=1e-12)
    assert together[0].x == pytest.approx(77.59)
    assert together[0].y == pytest.approx(12.97)


def test_to_wgs84_keeps_nulls_and_z() -> None:
    geoms = np.array([None, shapely.Point(0, 0, 900.0), shapely.Point(0, 0)], dtype=object)

    result = to_wgs84(geoms, "EPSG:3857")

    assert result[0] is None
    assert shapely.has_z(result[1]) and result[1].z == pytest.approx(900.0)
    assert not shapely.has_z(result[2])


def test_to_wgs84_accepts_wkt_definition() -> None:
    wkt = pyproj.CRS.from_proj4("+proj=tmerc +lon_0=77 +k=1 +datum=WGS84 +units=m").to_wkt()

    result = to_wgs84(np.array([shapely.Point(0, 0)]), wkt)

    assert result[0].x == pytest.approx(77.0)
    assert result[0].y == pytest.approx(0.0, abs=1e-9)


def test_to_utm_round_trips() -> None:
    square = shapely.box(700_000, 1_430_000, 701_000, 1_431_000)
    back_to_degrees = get_transformer("EPSG:32643", WGS84)
    in_degrees = shapely.transform(square, back_to_degrees.transform, interleaved=False)

    result = to_utm(in_degrees, 32643)

    assert shapely.equals_exact(result, square, tolerance=1e-6)


@pytest.mark.parametrize("code", [4326, 32643, 3857])
def test_describe_crs_identifies_epsg(code: int) -> None:
    assert describe_crs(pyproj.CRS.from_epsg(code)) == f"EPSG:{code}"


@pytest.mark.parametrize("code", [4326, 32643])
def test_describe_crs_identifies_esri_wkt(code: int) -> None:
    # ArcGIS writes .prj files in ESRI WKT, e.g. GEOGCS["GCS_WGS_1984", ...].
    esri_wkt = pyproj.CRS.from_epsg(code).to_wkt(WktVersion.WKT1_ESRI)

    assert describe_crs(pyproj.CRS.from_user_input(esri_wkt)) == f"EPSG:{code}"


def test_describe_crs_falls_back_to_wkt_name() -> None:
    custom = pyproj.CRS.from_proj4("+proj=tmerc +lon_0=77 +k=1 +datum=WGS84 +units=m")

    assert describe_crs(custom).startswith("WKT:")
