import io
from dataclasses import replace
from pathlib import Path

import numpy as np
import pyproj
import pytest
import shapely
from fastapi import UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app import service
from app.config import Settings
from app.core.measure import MeasureStatus
from app.core.readers import RawFeature, ReadResult
from app.core.ziputil import InvalidUpload
from app.models import Feature, File, FileFormat, FileStatus
from app.service import GEOMETRY_NOT_SERIALIZABLE, accept_upload, process_file
from app.storage import UploadTooLarge
from tests.factories import (
    from_utm,
    kml_document,
    kml_folder,
    kml_placemark,
    kml_polygon,
    shapefile_parts,
    shapefile_zip,
    utm_square,
)

PLOT = from_utm(utm_square(1000))
KML = kml_document(
    kml_folder(
        "Plots",
        kml_placemark("Plot A", kml_polygon(PLOT)),
        kml_placemark("Well", "<Point><coordinates>77.59,12.97</coordinates></Point>"),
    )
).encode()


def upload(content: bytes, filename: str | None = "survey.kml") -> UploadFile:
    return UploadFile(file=io.BytesIO(content), filename=filename)


def accept(
    session_factory: sessionmaker[Session], settings: Settings, content: bytes, filename: str
) -> File:
    with session_factory() as session:
        return accept_upload(upload(content, filename), session, settings)


def file_count(session_factory: sessionmaker[Session]) -> int:
    with session_factory() as session:
        return session.scalar(select(func.count()).select_from(File))


def storage_entries(settings: Settings) -> list[Path]:
    if not settings.storage_dir.exists():
        return []
    return list(settings.storage_dir.iterdir())


def load(session_factory: sessionmaker[Session], file_id: str) -> tuple[File, list[Feature]]:
    with session_factory() as session:
        file = session.get_one(File, file_id)
        features = session.scalars(
            select(Feature).where(Feature.file_id == file_id).order_by(Feature.idx)
        ).all()
        return file, list(features)


# accept_upload


def test_accept_kml_stores_upload_and_pending_row(
    session_factory: sessionmaker[Session], settings: Settings
) -> None:
    file = accept(session_factory, settings, KML, "survey.kml")

    assert file.status is FileStatus.PENDING
    assert file.format is FileFormat.KML
    assert file.filename == "survey.kml"
    assert file.size_bytes == len(KML)
    assert (settings.storage_dir / file.id / "upload.kml").read_bytes() == KML
    assert file_count(session_factory) == 1


def test_accept_zip_extracts_shapefile(
    session_factory: sessionmaker[Session], settings: Settings
) -> None:
    content = shapefile_zip(shapefile_parts([PLOT], pyproj.CRS.from_epsg(4326)))

    file = accept(session_factory, settings, content, "PLOTS.ZIP")

    assert file.format is FileFormat.SHAPEFILE
    assert (settings.storage_dir / file.id / "extracted" / "data.shp").exists()


def test_accept_keeps_only_the_basename_of_the_client_filename(
    session_factory: sessionmaker[Session], settings: Settings
) -> None:
    file = accept(session_factory, settings, KML, "C:\\fakepath\\..\\survey.kml")

    assert file.filename == "survey.kml"
    assert [p.name for p in storage_entries(settings)] == [file.id]


@pytest.mark.parametrize("filename", ["survey.geojson", "survey", "", None])
def test_accept_rejects_unsupported_names(
    session_factory: sessionmaker[Session], settings: Settings, filename: str | None
) -> None:
    with session_factory() as session, pytest.raises(InvalidUpload):
        accept_upload(upload(KML, filename), session, settings)

    assert file_count(session_factory) == 0
    assert storage_entries(settings) == []


def test_accept_invalid_zip_leaves_nothing_behind(
    session_factory: sessionmaker[Session], settings: Settings
) -> None:
    with pytest.raises(InvalidUpload):
        accept(session_factory, settings, b"not a zip", "plots.zip")

    assert file_count(session_factory) == 0
    assert storage_entries(settings) == []


def test_accept_byte_count_backstop_rejects_oversize_upload(
    session_factory: sessionmaker[Session], settings: Settings
) -> None:
    # TestClient always sends Content-Length, so the middleware would stop this
    # first; calling accept_upload directly exercises the chunked byte count.
    small = replace(settings, max_upload_mb=1)
    content = b"x" * (small.max_upload_bytes + 1)

    with pytest.raises(UploadTooLarge):
        accept(session_factory, small, content, "big.kml")

    assert file_count(session_factory) == 0
    assert storage_entries(small) == []


def test_accept_file_exactly_at_the_limit_is_kept(
    session_factory: sessionmaker[Session], settings: Settings
) -> None:
    small = replace(settings, max_upload_mb=1)

    file = accept(session_factory, small, b"x" * small.max_upload_bytes, "edge.kml")

    assert file.size_bytes == small.max_upload_bytes


def test_accept_commit_failure_propagates_and_cleans_storage(
    session_factory: sessionmaker[Session], settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_commit(self: Session) -> None:
        raise RuntimeError("database went away")

    with session_factory() as session:
        monkeypatch.setattr(Session, "commit", fail_commit)
        with pytest.raises(RuntimeError, match="database went away"):
            accept_upload(upload(KML), session, settings)
        monkeypatch.undo()

    assert file_count(session_factory) == 0
    assert storage_entries(settings) == []


# process_file


def test_process_kml_completes_and_persists_features(
    session_factory: sessionmaker[Session], settings: Settings
) -> None:
    accepted = accept(session_factory, settings, KML, "survey.kml")

    process_file(accepted.id, session_factory, settings)

    file, features = load(session_factory, accepted.id)
    assert file.status is FileStatus.COMPLETED
    assert file.updated_at > file.created_at
    assert file.crs == "EPSG:4326"
    assert file.feature_count == 2
    assert file.error_message is None
    assert [f.idx for f in features] == [0, 1]
    plot, well = features
    assert plot.layer == "Plots"
    assert plot.geometry_type == "Polygon"
    assert plot.source_crs == "EPSG:4326"
    assert plot.geometry["type"] == "Polygon"
    assert plot.properties["Name"] == "Plot A"
    assert plot.status is MeasureStatus.MEASURED
    assert plot.area_m2 == pytest.approx(1_000_000, rel=0.0025)
    assert plot.measurement_crs == "EPSG:32643"
    assert well.status is MeasureStatus.NO_MEASUREMENT_REQUIRED
    assert well.area_m2 is None and well.length_m is None
    assert storage_entries(settings) == []


def test_process_projected_shapefile_reports_source_crs(
    session_factory: sessionmaker[Session], settings: Settings
) -> None:
    mercator = pyproj.CRS.from_epsg(3857)
    to_mercator = pyproj.Transformer.from_crs("EPSG:4326", mercator, always_xy=True)
    plot_3857 = shapely.transform(PLOT, to_mercator.transform, interleaved=False)
    content = shapefile_zip(shapefile_parts([plot_3857], mercator))
    accepted = accept(session_factory, settings, content, "plots.zip")

    process_file(accepted.id, session_factory, settings)

    file, [feature] = load(session_factory, accepted.id)
    assert file.crs == "EPSG:3857"
    assert feature.source_crs == "EPSG:3857"
    # Output geometry is GeoJSON in EPSG:4326 (lon/lat), whatever the source CRS.
    lon, lat = feature.geometry["coordinates"][0][0][:2]
    assert 77 < lon < 78 and 12 < lat < 14
    assert feature.area_m2 == pytest.approx(1_000_000, rel=0.0025)


def test_process_empty_kml_completes_with_no_features(
    session_factory: sessionmaker[Session], settings: Settings
) -> None:
    accepted = accept(session_factory, settings, kml_document().encode(), "empty.kml")

    process_file(accepted.id, session_factory, settings)

    file, features = load(session_factory, accepted.id)
    assert (file.status, file.feature_count, features) == (FileStatus.COMPLETED, 0, [])


@pytest.mark.parametrize("status", [FileStatus.PROCESSING, FileStatus.COMPLETED])
def test_process_skips_file_that_is_not_pending(
    session_factory: sessionmaker[Session], settings: Settings, status: FileStatus
) -> None:
    accepted = accept(session_factory, settings, KML, "survey.kml")
    with session_factory() as session:
        session.get_one(File, accepted.id).status = status
        session.commit()

    process_file(accepted.id, session_factory, settings)

    file, features = load(session_factory, accepted.id)
    assert file.status is status
    assert features == []
    # Another job may own this upload, so its files must be left alone.
    assert (settings.storage_dir / accepted.id / "upload.kml").exists()


def test_process_unknown_file_is_a_no_op(
    session_factory: sessionmaker[Session], settings: Settings
) -> None:
    process_file("no-such-id", session_factory, settings)

    assert file_count(session_factory) == 0


def test_process_read_failure_marks_failed_without_leaking_paths(
    session_factory: sessionmaker[Session], settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    accepted = accept(session_factory, settings, KML, "survey.kml")

    def broken_reader(path: Path) -> ReadResult:
        raise OSError(f"cannot open {path}")

    monkeypatch.setattr(service, "read_kml", broken_reader)

    process_file(accepted.id, session_factory, settings)

    file, features = load(session_factory, accepted.id)
    assert file.status is FileStatus.FAILED
    assert file.error_message == "Unexpected error while processing the file."
    assert features == []
    assert storage_entries(settings) == []


def test_process_unparseable_kml_fails_with_safe_message(
    session_factory: sessionmaker[Session], settings: Settings
) -> None:
    accepted = accept(session_factory, settings, b"this is not kml", "broken.kml")

    process_file(accepted.id, session_factory, settings)

    file, _ = load(session_factory, accepted.id)
    assert file.status is FileStatus.FAILED
    # GDAL's own message includes the server path; the client gets a fixed one.
    assert file.error_message == "The file could not be read as a KML."


def test_process_unreadable_prj_fails_with_its_message(
    session_factory: sessionmaker[Session], settings: Settings
) -> None:
    parts = shapefile_parts([PLOT], pyproj.CRS.from_epsg(4326))
    parts[".prj"] = b"not a coordinate system"
    accepted = accept(session_factory, settings, shapefile_zip(parts), "plots.zip")

    process_file(accepted.id, session_factory, settings)

    file, _ = load(session_factory, accepted.id)
    assert file.status is FileStatus.FAILED
    assert "readable coordinate system" in file.error_message


def test_process_keeps_feature_whose_geometry_cannot_be_serialized(
    session_factory: sessionmaker[Session], settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    accepted = accept(session_factory, settings, KML, "survey.kml")
    with np.errstate(invalid="ignore"):  # shapely warns about the NaN on purpose here
        bad = shapely.LineString([(77.59, 12.97), (float("nan"), 12.98)])
    result = ReadResult(
        file_crs="EPSG:4326",
        crs_definition="EPSG:4326",
        features=[
            RawFeature(index=0, layer="L", geometry=bad, properties={}),
            RawFeature(index=1, layer="L", geometry=PLOT, properties={}),
        ],
    )
    monkeypatch.setattr(service, "read_kml", lambda path: result)

    process_file(accepted.id, session_factory, settings)

    file, (broken, fine) = load(session_factory, accepted.id)
    assert file.status is FileStatus.COMPLETED
    assert broken.geometry is None
    assert GEOMETRY_NOT_SERIALIZABLE in broken.warnings
    assert broken.status is not MeasureStatus.MEASURED
    assert fine.status is MeasureStatus.MEASURED


def test_to_geojson_handles_2d_3d_and_missing_geometries() -> None:
    with np.errstate(invalid="ignore"):
        non_finite = shapely.Point(float("nan"), 1.0)

    assert service.to_geojson(shapely.Point(77.59, 12.97)) == {
        "type": "Point",
        "coordinates": [77.59, 12.97],
    }
    assert service.to_geojson(shapely.Point(77.59, 12.97, 900.0))["coordinates"] == [
        77.59,
        12.97,
        900.0,
    ]
    assert service.to_geojson(non_finite) is None
    assert service.to_geojson(None) is None
