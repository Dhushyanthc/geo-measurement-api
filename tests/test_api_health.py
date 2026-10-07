from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.db import make_engine, make_session_factory


def test_health_ok(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_unavailable_when_database_is_unreachable(
    app: FastAPI, client: TestClient, tmp_path: Path
) -> None:
    # SQLite cannot open a database file inside a directory that does not exist.
    unreachable = make_engine(f"sqlite:///{(tmp_path / 'missing' / 'db.sqlite').as_posix()}")
    app.state.session_factory = make_session_factory(unreachable)

    response = client.get("/health")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
