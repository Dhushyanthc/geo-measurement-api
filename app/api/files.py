"""File routes: upload, and (later) status and measurements."""

from typing import Annotated

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from sqlalchemy.orm import Session

from app.core.ziputil import InvalidUpload
from app.db import get_session
from app.schemas import FileOut
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
