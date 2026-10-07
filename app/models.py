"""Database tables. Only portable column types, so SQLite and Postgres share one schema.

- JSON columns become JSONB on Postgres.
- Enums are stored as plain strings (no Postgres enum type to migrate).
- Datetimes are timezone-aware and always written in UTC.
"""

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, DateTime, Enum, Float, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.measure import MeasureStatus

# none_as_null: a Python None is stored as SQL NULL, not as the JSON literal 'null'.
JsonType = JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql")


class FileStatus(StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class FileFormat(StrEnum):
    KML = "kml"
    SHAPEFILE = "shapefile"


def utcnow() -> datetime:
    return datetime.now(UTC)


def _string_enum(enum_class: type[StrEnum]) -> Enum:
    """A VARCHAR column holding the enum's values (e.g. "kml", not "KML")."""
    return Enum(
        enum_class,
        native_enum=False,
        length=32,
        values_callable=lambda members: [member.value for member in members],
    )


class Base(DeclarativeBase):
    pass


class File(Base):
    __tablename__ = "files"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    filename: Mapped[str] = mapped_column(String(255))
    format: Mapped[FileFormat] = mapped_column(_string_enum(FileFormat))
    status: Mapped[FileStatus] = mapped_column(_string_enum(FileStatus), default=FileStatus.PENDING)
    crs: Mapped[str | None] = mapped_column(Text)
    feature_count: Mapped[int] = mapped_column(default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    size_bytes: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # Bumped on every update, so an operator can spot jobs stuck in PROCESSING.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Feature(Base):
    __tablename__ = "features"
    # The constraint's index leads with file_id, so it also serves lookups by
    # file and the paginated "WHERE file_id = ? ORDER BY idx" query.
    __table_args__ = (UniqueConstraint("file_id", "idx", name="uq_features_file_id_idx"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    file_id: Mapped[str] = mapped_column(ForeignKey("files.id", ondelete="CASCADE"))
    idx: Mapped[int]
    layer: Mapped[str] = mapped_column(Text)
    geometry_type: Mapped[str | None] = mapped_column(String(64))
    source_crs: Mapped[str] = mapped_column(Text)
    geometry: Mapped[dict[str, Any] | None] = mapped_column(JsonType)  # GeoJSON, EPSG:4326
    properties: Mapped[dict[str, Any]] = mapped_column(JsonType)
    status: Mapped[MeasureStatus] = mapped_column(_string_enum(MeasureStatus))
    area_m2: Mapped[float | None] = mapped_column(Float)
    length_m: Mapped[float | None] = mapped_column(Float)
    measurement_crs: Mapped[str | None] = mapped_column(String(32))
    reason: Mapped[str | None] = mapped_column(Text)
    warnings: Mapped[list[str]] = mapped_column(JsonType, default=list)
