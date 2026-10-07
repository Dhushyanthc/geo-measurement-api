"""Shared fixtures. Each test gets a fresh database.

By default that is a temporary SQLite file. When TEST_DATABASE_URL is set (the
CI Postgres job), the same tests run against that database instead, with the
tables dropped and recreated per test.
"""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db import init_db, make_engine, make_session_factory
from app.main import create_app
from app.models import Base


@pytest.fixture
def database_url(tmp_path: Path) -> str:
    return os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{(tmp_path / 'test.db').as_posix()}"


@pytest.fixture
def engine(database_url: str) -> Iterator[Engine]:
    engine = make_engine(database_url)
    Base.metadata.drop_all(engine)
    init_db(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def session_factory(engine: Engine) -> sessionmaker[Session]:
    return make_session_factory(engine)


@pytest.fixture
def settings(database_url: str, tmp_path: Path) -> Settings:
    return Settings(database_url=database_url, storage_dir=tmp_path / "storage")


@pytest.fixture
def app(settings: Settings, engine: Engine) -> FastAPI:
    # Depends on `engine` so the test database is reset before the app starts.
    return create_app(settings)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    # The context manager runs the app's lifespan (init_db on startup).
    with TestClient(app) as client:
        yield client
