"""File routes: upload, status and measurements."""

from typing import Annotated

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.ziputil import InvalidUpload
from app.db import get_session
from app.models import Feature, File, FileStatus
from app.schemas import FeatureOut, FileOut, MeasurementsPage
from app.service import accept_upload, process_file
from app.storage import UploadTooLarge

router = APIRouter(prefix="/api/files", tags=["files"])


@router.post("/", status_code=202, response_model=FileOut)
def upload_file(
    file: UploadFile,
    request: Request,
    response: Response,
    background_tasks: BackgroundTasks,
    session: Annotated[Session, Depends(get_session)],
) -> FileOut:
    """Accept a .kml or zipped Shapefile and process it in the background.

    Returns 202 with a Location header; poll that URL until the status is
    COMPLETED or FAILED.
    """
    settings = request.app.state.settings
    try:
        record = accept_upload(file, session, settings)
    except InvalidUpload as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except UploadTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc

    background_tasks.add_task(process_file, record.id, request.app.state.session_factory, settings)
    response.headers["Location"] = f"/api/files/{record.id}/"
    return FileOut.from_model(record)


@router.get("/{file_id}/", response_model=FileOut)
def get_file(file_id: str, session: Annotated[Session, Depends(get_session)]) -> FileOut:
    """Status and summary of an upload. Poll this until COMPLETED or FAILED."""
    return FileOut.from_model(_get_file_or_404(session, file_id))


@router.get("/{file_id}/measurements/", response_model=MeasurementsPage)
def get_measurements(
    file_id: str,
    session: Annotated[Session, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> MeasurementsPage:
    """Per-feature measurements, ordered by feature index. 409 until COMPLETED."""
    file = _get_file_or_404(session, file_id)
    if file.status in (FileStatus.PENDING, FileStatus.PROCESSING):
        raise HTTPException(
            status_code=409,
            detail="File is still processing; retry later.",
            headers={"Retry-After": "1"},
        )
    if file.status is FileStatus.FAILED:
        raise HTTPException(status_code=409, detail=f"File processing failed: {file.error_message}")

    features = session.scalars(
        select(Feature)
        .where(Feature.file_id == file_id)
        .order_by(Feature.idx)
        .limit(limit)
        .offset(offset)
    )
    return MeasurementsPage(
        file_id=file_id,
        total=file.feature_count,
        limit=limit,
        offset=offset,
        features=[FeatureOut.from_model(feature) for feature in features],
    )


def _get_file_or_404(session: Session, file_id: str) -> File:
    file = session.get(File, file_id)
    if file is None:
        raise HTTPException(status_code=404, detail="File not found.")
    return file
