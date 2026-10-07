import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine, create_engine, insert, inspect, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.schema import CreateTable

from app.core.measure import MeasureStatus
from app.db import init_db
from app.models import Feature, File, FileFormat, FileStatus

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def add_file(session_factory: sessionmaker[Session], **overrides: object) -> File:
    values = {"filename": "survey.kml", "format": FileFormat.KML, "size_bytes": 10, **overrides}
    with session_factory() as session:
        file = File(**values)
        session.add(file)
        session.commit()
    return file


def feature_row(file_id: str, idx: int, **overrides: object) -> dict[str, object]:
    return {
        "file_id": file_id,
        "idx": idx,
        "layer": "Plots",
        "geometry_type": "Polygon",
        "source_crs": "EPSG:4326",
        "geometry": {"type": "Point", "coordinates": [77.59, 12.97]},
        "properties": {"Name": f"Plot {idx}", "area_ha": 1.5, "tags": ["a", "b"]},
        "status": MeasureStatus.MEASURED,
        "area_m2": 1_000_000.0,
        "length_m": None,
        "measurement_crs": "EPSG:32643",
        "reason": None,
        "warnings": [],
        **overrides,
    }


def test_new_file_gets_defaults(session_factory: sessionmaker[Session]) -> None:
    file = add_file(session_factory)

    assert len(file.id) == 36
    assert file.status is FileStatus.PENDING
    assert file.feature_count == 0
    assert file.crs is None
    assert isinstance(file.created_at, datetime)
    assert isinstance(file.updated_at, datetime)


def test_enums_are_stored_as_their_string_values(
    engine: Engine, session_factory: sessionmaker[Session]
) -> None:
    add_file(session_factory, format=FileFormat.SHAPEFILE)

    with engine.connect() as conn:
        row = conn.execute(text("SELECT format, status FROM files")).one()

    assert tuple(row) == ("shapefile", "PENDING")


def test_status_change_bumps_updated_at(session_factory: sessionmaker[Session]) -> None:
    file = add_file(session_factory)

    with session_factory() as session:
        stored = session.get(File, file.id)
        stored.status = FileStatus.PROCESSING
        session.commit()
        assert stored.updated_at > file.updated_at


def test_bulk_insert_round_trips_json_and_nulls(session_factory: sessionmaker[Session]) -> None:
    file = add_file(session_factory)
    rows = [
        feature_row(file.id, 0),
        feature_row(
            file.id,
            1,
            geometry=None,
            geometry_type=None,
            status=MeasureStatus.UNSUPPORTED,
            area_m2=None,
            measurement_crs=None,
            reason="null or empty geometry",
            warnings=["Z_IGNORED"],
        ),
    ]

    with session_factory() as session:
        session.execute(insert(Feature), rows)
        session.commit()

    with session_factory() as session:
        features = session.scalars(select(Feature).order_by(Feature.idx)).all()
        null_geometries = session.scalar(select(Feature.idx).where(Feature.geometry.is_(None)))

    assert features[0].geometry == {"type": "Point", "coordinates": [77.59, 12.97]}
    assert features[0].properties == {"Name": "Plot 0", "area_ha": 1.5, "tags": ["a", "b"]}
    assert features[0].status is MeasureStatus.MEASURED
    assert features[1].geometry is None
    assert features[1].warnings == ["Z_IGNORED"]
    # None is stored as SQL NULL, not the JSON literal 'null'.
    assert null_geometries == 1


def test_feature_index_is_unique_per_file(session_factory: sessionmaker[Session]) -> None:
    file = add_file(session_factory)

    with session_factory() as session:
        session.execute(insert(Feature), [feature_row(file.id, 0)])
        with pytest.raises(IntegrityError):
            session.execute(insert(Feature), [feature_row(file.id, 0)])


def test_feature_requires_existing_file(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session, pytest.raises(IntegrityError):
        session.execute(insert(Feature), [feature_row("no-such-file", 0)])


def test_init_db_is_idempotent(engine: Engine) -> None:
    init_db(engine)
    init_db(engine)

    assert {"files", "features"} <= set(inspect(engine).get_table_names())


def test_sqlite_uses_wal(engine: Engine) -> None:
    if engine.dialect.name != "sqlite":
        pytest.skip("SQLite-only setting")

    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA journal_mode")).scalar() == "wal"


def test_postgres_schema_uses_jsonb_and_no_native_enums() -> None:
    # Compiled without a server, so this runs on every machine; the CI
    # Postgres job runs the rest of this file against a real database.
    ddl = str(CreateTable(Feature.__table__).compile(dialect=postgresql.dialect()))

    assert "properties JSONB" in ddl
    assert "status VARCHAR(32)" in ddl
    assert "CREATE TYPE" not in ddl


def test_module_entry_point_creates_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "cli.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{db_path.as_posix()}"}

    subprocess.run(
        [sys.executable, "-m", "app.db"], cwd=PROJECT_ROOT, env=env, check=True, timeout=60
    )

    engine = create_engine(env["DATABASE_URL"])
    try:
        assert {"files", "features"} <= set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
