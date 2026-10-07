from collections.abc import Iterator
from dataclasses import replace

import pyproj
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from app import service
from app.config import Settings
from app.main import MULTIPART_OVERHEAD_BYTES, create_app
from app.models import File, FileStatus
from tests.factories import (
    from_utm,
    kml_document,
    kml_placemark,
    kml_polygon,
    shapefile_parts,
    shapefile_zip,
    utm_square,
)

PLOT = from_utm(utm_square(1000))
KML = kml_document(kml_placemark("Plot A", kml_polygon(PLOT))).encode()
ZIP = shapefile_zip(shapefile_parts([PLOT], pyproj.CRS.from_epsg(4326)))


def post(client: TestClient, content: bytes, filename: str) -> object:
    return client.post("/api/files/", files={"file": (filename, content)})


def stored_files(session_factory: sessionmaker[Session]) -> list[File]:
    with session_factory() as session:
        return list(session.scalars(select(File)))


def storage_is_empty(settings: Settings) -> bool:
    return not settings.storage_dir.exists() or not any(settings.storage_dir.iterdir())


@pytest.mark.parametrize(("content", "filename"), [(KML, "survey.kml"), (ZIP, "plots.zip")])
def test_upload_returns_202_and_processes_in_background(
    client: TestClient,
    session_factory: sessionmaker[Session],
    settings: Settings,
    content: bytes,
    filename: str,
) -> None:
    response = post(client, content, filename)

    assert response.status_code == 202
    body = response.json()
    assert body == {
        "id": body["id"],
        "filename": filename,
        "feature_count": 0,
        "crs": None,
        "status": "PENDING",
        "error": None,
    }
    assert response.headers["Location"] == f"/api/files/{body['id']}/"
    # TestClient runs background tasks before returning, so the job is done.
    [file] = stored_files(session_factory)
    assert (file.id, file.status, file.feature_count) == (body["id"], FileStatus.COMPLETED, 1)
    assert storage_is_empty(settings)


@pytest.mark.parametrize(
    ("content", "filename", "message"),
    [
        (KML, "survey.geojson", "Unsupported file type"),
        (b"not a zip", "plots.zip", "not a valid zip"),
        (shapefile_zip(shapefile_parts([PLOT], crs=None)), "plots.zip", "no .prj"),
    ],
)
def test_upload_rejects_invalid_files_with_400_and_leaves_nothing(
    client: TestClient,
    session_factory: sessionmaker[Session],
    settings: Settings,
    content: bytes,
    filename: str,
    message: str,
) -> None:
    response = post(client, content, filename)

    assert response.status_code == 400
    assert message in response.json()["detail"]
    assert str(settings.storage_dir) not in response.json()["detail"]
    assert stored_files(session_factory) == []
    assert storage_is_empty(settings)


def test_upload_without_file_field_is_422(client: TestClient) -> None:
    response = client.post("/api/files/", data={"other": "x"})

    assert response.status_code == 422


@pytest.fixture
def small_client(
    settings: Settings, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TestClient, list[str]]]:
    """A client with a 1 MB limit that records whether accept_upload ran."""
    calls: list[str] = []
    real_accept = service.accept_upload

    def spy(*args: object, **kwargs: object) -> object:
        calls.append("accept_upload")
        return real_accept(*args, **kwargs)

    monkeypatch.setattr("app.api.files.accept_upload", spy)
    app = create_app(replace(settings, max_upload_mb=1))
    with TestClient(app) as client:
        yield client, calls


def test_oversize_content_length_is_rejected_before_the_body_is_read(
    small_client: tuple[TestClient, list[str]], settings: Settings
) -> None:
    client, calls = small_client
    content = b"x" * (1024 * 1024 + MULTIPART_OVERHEAD_BYTES + 1)

    response = post(client, content, "big.kml")

    assert response.status_code == 413
    assert response.json() == {"detail": "The file is larger than the 1 MB upload limit."}
    assert calls == []  # the middleware answered; the route never ran
    assert storage_is_empty(settings)


def test_file_exactly_at_the_limit_is_accepted(
    small_client: tuple[TestClient, list[str]],
) -> None:
    # The multipart wrapping pushes Content-Length past 1 MB; the overhead
    # allowance must keep this file from being rejected.
    client, calls = small_client

    response = post(client, b"x" * 1024 * 1024, "edge.kml")

    assert response.status_code == 202
    assert calls == ["accept_upload"]


def test_size_limit_applies_only_to_the_upload_route(
    small_client: tuple[TestClient, list[str]],
) -> None:
    client, _ = small_client
    big = b"x" * (2 * 1024 * 1024)

    response = client.post("/api/other/", content=big)

    assert response.status_code == 404
