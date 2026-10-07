"""Upload handling in two halves.

`accept_upload` runs inside the request: cheap checks, save to storage, insert
a PENDING row. `process_file` runs afterwards as a background job: read,
reproject, measure, persist. The job takes only ids and factories, never
request objects, so a queue worker could call it unchanged.
"""

import json
import logging
import uuid
from pathlib import Path
from typing import Any

import numpy as np
import shapely
from fastapi import UploadFile
from pyogrio.errors import DataSourceError
from sqlalchemy import insert, update
from sqlalchemy.orm import Session, sessionmaker

from app import storage
from app.config import Settings
from app.core.crs import to_wgs84
from app.core.measure import measure_geometry
from app.core.readers import ReadResult, read_kml, read_shapefile
from app.core.ziputil import EXTRACTED_STEM, InvalidUpload, extract_shapefile
from app.models import Feature, File, FileFormat, FileStatus

logger = logging.getLogger(__name__)

GEOMETRY_NOT_SERIALIZABLE = "GEOMETRY_NOT_SERIALIZABLE"

FORMATS_BY_EXTENSION = {".kml": FileFormat.KML, ".zip": FileFormat.SHAPEFILE}
MAX_FILENAME_LENGTH = 255


def accept_upload(upload: UploadFile, session: Session, settings: Settings) -> File:
    """Validate and store an upload, then record it as PENDING.

    Raises InvalidUpload (bad type or zip) or UploadTooLarge. On any failure
    the storage directory is removed and no row is left behind.
    """
    filename = client_filename(upload.filename)
    extension = Path(filename).suffix.lower()
    file_format = FORMATS_BY_EXTENSION.get(extension)
    if file_format is None:
        raise InvalidUpload(
            "Unsupported file type: upload a .kml file or a zipped Shapefile (.zip)."
        )

    file_id = str(uuid.uuid4())
    directory = storage.upload_dir(settings.storage_dir, file_id)
    try:
        upload_path = directory / f"upload{extension}"
        size = storage.save_stream(upload.file, upload_path, settings.max_upload_bytes)
        if file_format is FileFormat.SHAPEFILE:
            extract_shapefile(
                upload_path,
                directory / "extracted",
                max_entries=settings.max_zip_entries,
                max_uncompressed=settings.max_uncompressed_bytes,
            )
        file = File(id=file_id, filename=filename, format=file_format, size_bytes=size)
        session.add(file)
        session.commit()
    except Exception:
        session.rollback()
        storage.remove_upload_dir(directory)
        raise
    logger.info("Accepted upload %s (%s, %d bytes)", file_id, file_format, size)
    return file


def client_filename(raw: str | None) -> str:
    """The basename of the client's filename, for display only (never a path).

    Browsers may send a full path such as C:\\fakepath\\survey.kml.
    """
    name = (raw or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not name:
        raise InvalidUpload("The upload has no filename.")
    return name[:MAX_FILENAME_LENGTH]


def process_file(file_id: str, session_factory: sessionmaker[Session], settings: Settings) -> None:
    """Background job: read, reproject, measure and persist one PENDING file.

    A failure while reading or measuring is recorded on the row as FAILED
    rather than raised. The upload directory is deleted afterwards either way.
    """
    with session_factory() as session:
        if not _claim(session, file_id):
            logger.warning("Skipping file %s: unknown or not PENDING", file_id)
            return

        directory = storage.upload_dir(settings.storage_dir, file_id)
        try:
            file = session.get_one(File, file_id)
            result = _read(file.format, directory)
            rows = _feature_rows(file_id, result)
            if rows:
                session.execute(insert(Feature), rows)
            file.status = FileStatus.COMPLETED
            file.crs = result.file_crs
            file.feature_count = len(rows)
            session.commit()
            logger.info("Processed file %s: %d features", file_id, len(rows))
        except Exception as exc:
            logger.exception("Processing failed for file %s", file_id)
            session.rollback()
            file = session.get_one(File, file_id)
            file.status = FileStatus.FAILED
            file.error_message = _client_error(exc, file.format)
            session.commit()
        finally:
            storage.remove_upload_dir(directory)


def _claim(session: Session, file_id: str) -> bool:
    """Atomically move PENDING -> PROCESSING. False if another job got there first."""
    claimed = session.execute(
        update(File)
        .where(File.id == file_id, File.status == FileStatus.PENDING)
        .values(status=FileStatus.PROCESSING)
    )
    session.commit()
    return claimed.rowcount == 1


def _read(file_format: FileFormat, directory: Path) -> ReadResult:
    if file_format is FileFormat.KML:
        return read_kml(directory / "upload.kml")
    return read_shapefile(directory / "extracted" / f"{EXTRACTED_STEM}.shp")


def _feature_rows(file_id: str, result: ReadResult) -> list[dict[str, Any]]:
    """Reproject all geometries in one call, then measure each feature."""
    source = np.array([feature.geometry for feature in result.features], dtype=object)
    in_wgs84 = to_wgs84(source, result.crs_definition)

    rows = []
    for feature, geometry in zip(result.features, in_wgs84, strict=True):
        measurement = measure_geometry(geometry)
        warnings = list(measurement.warnings)
        geojson = to_geojson(geometry)
        if geometry is not None and geojson is None:
            warnings.append(GEOMETRY_NOT_SERIALIZABLE)
        rows.append(
            {
                "file_id": file_id,
                "idx": feature.index,
                "layer": feature.layer,
                "geometry_type": None if feature.geometry is None else feature.geometry.geom_type,
                "source_crs": result.file_crs,
                "geometry": geojson,
                "properties": feature.properties,
                "status": measurement.status,
                "area_m2": measurement.area_m2,
                "length_m": measurement.length_m,
                "measurement_crs": measurement.crs,
                "reason": measurement.reason,
                "warnings": warnings,
            }
        )
    return rows


def to_geojson(geometry: shapely.Geometry | None) -> dict[str, Any] | None:
    """GeoJSON for a geometry, or None if it has no valid representation.

    shapely writes non-finite coordinates as null, which is not valid GeoJSON.
    """
    if geometry is None:
        return None
    coordinates = shapely.get_coordinates(geometry, include_z=shapely.has_z(geometry))
    if not np.isfinite(coordinates).all():
        return None
    return json.loads(shapely.to_geojson(geometry))


def _client_error(exc: Exception, file_format: FileFormat) -> str:
    """A short message for the client. Never the raw exception text for unknown
    errors: GDAL messages, for one, include server file paths."""
    if isinstance(exc, InvalidUpload):
        return str(exc)
    if isinstance(exc, DataSourceError):
        kind = "KML" if file_format is FileFormat.KML else "Shapefile"
        return f"The file could not be read as a {kind}."
    return "Unexpected error while processing the file."
