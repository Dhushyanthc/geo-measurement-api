"""Response bodies of the API."""

from pydantic import BaseModel

from app.models import File, FileStatus


class FileOut(BaseModel):
    id: str
    filename: str
    feature_count: int
    crs: str | None
    status: FileStatus
    error: str | None

    @classmethod
    def from_model(cls, file: File) -> "FileOut":
        return cls(
            id=file.id,
            filename=file.filename,
            feature_count=file.feature_count,
            crs=file.crs,
            status=file.status,
            error=file.error_message,
        )
