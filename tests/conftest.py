"""Shared fixtures. Each test gets a fresh database.

By default that is a temporary SQLite file. When TEST_DATABASE_URL is set (the
CI Postgres job), the same tests run against that database instead, with the
tables dropped and recreated per test.
"""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.db import init_db, make_engine, make_session_factory
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
