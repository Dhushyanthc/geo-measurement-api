import pytest
import shapely
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.models import File, FileFormat, FileStatus
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
ROAD = from_utm(shapely.LineString([BENGALURU_UTM, (BENGALURU_UTM[0] + 500, BENGALURU_UTM[1])]))
FIVE_PLOTS = kml_document(
    kml_folder("Plots", *(kml_placemark(f"Plot {i}", kml_polygon(PLOT)) for i in range(5)))
).encode()


def upload(client: TestClient, content: bytes, filename: str = "survey.kml") -> str:
    response = client.post("/api/files/", files={"file": (filename, content)})
    assert response.status_code == 202
    return response.json()["id"]


def add_file(session_factory: sessionmaker[Session], status: FileStatus, **values: object) -> str:
    with session_factory() as session:
        file = File(
            filename="survey.kml", format=FileFormat.KML, size_bytes=1, status=status, **values
        )
        session.add(file)
        session.commit()
        return file.id


# GET /api/files/{id}/


def test_file_info_after_processing(client: TestClient) -> None:
    file_id = upload(client, FIVE_PLOTS)

    response = client.get(f"/api/files/{file_id}/")

    assert response.status_code == 200
    assert response.json() == {
        "id": file_id,
        "filename": "survey.kml",
        "feature_count": 5,
        "crs": "EPSG:4326",
        "status": "COMPLETED",
        "error": None,
    }


def test_file_info_unknown_id_is_404(client: TestClient) -> None:
    response = client.get("/api/files/no-such-id/")

    assert response.status_code == 404
    assert response.json() == {"detail": "File not found."}


# GET /api/files/{id}/measurements/


def test_measurements_response_has_geometry_properties_and_measurement(
    client: TestClient,
) -> None:
    line = f"<LineString><coordinates>{kml_coords(ROAD)}</coordinates></LineString>"
    content = kml_document(
        kml_folder(
            "Survey",
            kml_placemark("Plot A", kml_polygon(PLOT)),
            kml_placemark("Road", line),
        )
    ).encode()
    file_id = upload(client, content)

    response = client.get(f"/api/files/{file_id}/measurements/")

    assert response.status_code == 200
    body = response.json()
    assert (body["file_id"], body["total"], body["limit"], body["offset"]) == (file_id, 2, 100, 0)
    plot, road = body["features"]
    assert plot["index"] == 0
    assert plot["layer"] == "Survey"
    assert plot["geometry_type"] == "Polygon"
    assert plot["source_crs"] == "EPSG:4326"
    assert plot["properties"]["Name"] == "Plot A"
    assert plot["geometry"]["type"] == "Polygon"
    assert plot["measurement"]["status"] == "MEASURED"
    assert plot["measurement"]["area_m2"] == pytest.approx(1_000_000, rel=0.0025)
    assert plot["measurement"]["length_m"] is None
    assert plot["measurement"]["crs"] == "EPSG:32643"
    assert plot["measurement"]["reason"] is None
    assert plot["measurement"]["warnings"] == []
    assert road["geometry_type"] == "LineString"
    assert road["measurement"]["length_m"] == pytest.approx(500, rel=0.0025)
    assert road["measurement"]["area_m2"] is None


@pytest.mark.parametrize(
    ("limit", "offset", "expected"),
    [(2, 0, [0, 1]), (2, 3, [3, 4]), (10, 4, [4]), (10, 5, []), (1000, 0, [0, 1, 2, 3, 4])],
)
def test_measurements_pagination(
    client: TestClient, limit: int, offset: int, expected: list[int]
) -> None:
    file_id = upload(client, FIVE_PLOTS)

    response = client.get(
        f"/api/files/{file_id}/measurements/", params={"limit": limit, "offset": offset}
    )

    body = response.json()
    assert [f["index"] for f in body["features"]] == expected
    assert (body["total"], body["limit"], body["offset"]) == (5, limit, offset)


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 1001}, {"offset": -1}])
def test_measurements_bad_pagination_is_422(client: TestClient, params: dict[str, int]) -> None:
    file_id = upload(client, FIVE_PLOTS)

    response = client.get(f"/api/files/{file_id}/measurements/", params=params)

    assert response.status_code == 422


def test_measurements_unknown_id_is_404(client: TestClient) -> None:
    response = client.get("/api/files/no-such-id/measurements/")

    assert response.status_code == 404


@pytest.mark.parametrize("status", [FileStatus.PENDING, FileStatus.PROCESSING])
def test_measurements_while_processing_is_409_with_retry_after(
    client: TestClient, session_factory: sessionmaker[Session], status: FileStatus
) -> None:
    file_id = add_file(session_factory, status)

    response = client.get(f"/api/files/{file_id}/measurements/")

    assert response.status_code == 409
    assert response.json() == {"detail": "File is still processing; retry later."}
    assert response.headers["Retry-After"] == "1"


def test_measurements_of_failed_file_is_409_with_reason(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    file_id = add_file(
        session_factory, FileStatus.FAILED, error_message="The file could not be read as a KML."
    )

    response = client.get(f"/api/files/{file_id}/measurements/")

    assert response.status_code == 409
    assert response.json() == {
        "detail": "File processing failed: The file could not be read as a KML."
    }
    assert "Retry-After" not in response.headers
