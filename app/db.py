"""Engine and session setup. `python -m app.db` creates the tables and exits."""

import logging
from collections.abc import Iterator
from typing import Any

from fastapi import Request
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.models import Base

logger = logging.getLogger(__name__)


def make_engine(database_url: str) -> Engine:
    """Create an engine tuned for the database behind `database_url`."""
    if database_url.startswith("sqlite"):
        # The background job writes from a different thread than the request
        # that created the connection, so the same-thread check must be off.
        engine = create_engine(database_url, connect_args={"check_same_thread": False})
        event.listen(engine, "connect", _configure_sqlite)
        return engine
    # Drops connections that died while idle in the pool (DB restart, failover).
    return create_engine(database_url, pool_pre_ping=True)


def _configure_sqlite(dbapi_connection: Any, _connection_record: Any) -> None:
    cursor = dbapi_connection.cursor()
    # WAL lets GET requests read while a background job is writing.
    cursor.execute("PRAGMA journal_mode=WAL")
    # SQLite ignores foreign keys unless asked; Postgres always enforces them.
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Sessions whose objects stay readable after commit (e.g. to build a response)."""
    return sessionmaker(engine, expire_on_commit=False)


def init_db(engine: Engine) -> None:
    """Create any missing tables. Safe to run repeatedly."""
    Base.metadata.create_all(engine)


def get_session(request: Request) -> Iterator[Session]:
    """FastAPI dependency: one session per request, from the app's own factory."""
    with request.app.state.session_factory() as session:
        yield session


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    settings = Settings.from_env()
    engine = make_engine(settings.database_url)
    init_db(engine)
    logger.info("Tables created on %s", engine.url.render_as_string(hide_password=True))
