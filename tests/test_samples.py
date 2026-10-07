"""The committed samples back the README examples, so they are tested too."""

import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SAMPLES = PROJECT_ROOT / "samples"


def process(client: TestClient, name: str) -> tuple[dict, list[dict]]:
    response = client.post("/api/files/", files={"file": (name, (SAMPLES / name).read_bytes())})
    file_id = response.json()["id"]
    info = client.get(f"/api/files/{file_id}/").json()
    features = client.get(f"/api/files/{file_id}/measurements/").json()["features"]
    return info, features


def test_sample_kml(client: TestClient) -> None:
    info, features = process(client, "sample.kml")

    assert (info["status"], info["crs"], info["feature_count"]) == ("COMPLETED", "EPSG:4326", 4)
    summary = [(f["layer"], f["properties"]["Name"], f["measurement"]["status"]) for f in features]
    assert summary == [
        ("Plots", "Plot A", "MEASURED"),
        ("Plots", "Plots B and C", "MEASURED"),
        ("Infrastructure", "Access road", "MEASURED"),
        ("Infrastructure", "Well", "NO_MEASUREMENT_REQUIRED"),
    ]
    plot_a, plots_b_c, road, _ = (f["measurement"] for f in features)
    # Laid out in UTM 43N, so the true sizes are known exactly.
    assert plot_a["area_m2"] == pytest.approx(8_000, rel=0.001)
    assert plots_b_c["area_m2"] == pytest.approx(5_000, rel=0.001)
    assert road["length_m"] == pytest.approx(210, rel=0.001)
    assert features[0]["properties"]["owner"] == "Asha"


def test_sample_shapefile(client: TestClient) -> None:
    info, features = process(client, "sample_shapefile.zip")

    assert (info["status"], info["crs"], info["feature_count"]) == ("COMPLETED", "EPSG:32643", 3)
    field_1, field_2, field_3 = (f["measurement"] for f in features)
    assert field_1["area_m2"] == pytest.approx(30_000, rel=0.001)
    assert field_2["area_m2"] == pytest.approx(9_600, rel=0.001)  # 100 x 100 minus a 20 x 20 pond
    assert field_3["status"] == "INVALID"
    assert features[0]["properties"] == {
        "name": "Field 1",
        "crop": "sugarcane",
        "surveyed": "2024-05-01",
    }


def test_samples_match_the_generator(tmp_path: Path) -> None:
    subprocess.run(
        [sys.executable, "scripts/make_samples.py", "--out", str(tmp_path)],
        cwd=PROJECT_ROOT,
        check=True,
        timeout=60,
    )

    assert (tmp_path / "sample.kml").read_bytes() == (SAMPLES / "sample.kml").read_bytes()
    with (
        zipfile.ZipFile(tmp_path / "sample_shapefile.zip") as fresh,
        zipfile.ZipFile(SAMPLES / "sample_shapefile.zip") as committed,
    ):
        assert fresh.namelist() == committed.namelist()
        for name in fresh.namelist():
            # The .dbf header stores the date it was written, so it differs by day.
            if not name.endswith(".dbf"):
                assert fresh.read(name) == committed.read(name), name
