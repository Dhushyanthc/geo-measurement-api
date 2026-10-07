"""Where uploads wait between the request and the background job.

Each upload gets its own directory, STORAGE_DIR/{file_id}/, named by the
server-generated id. The client's filename is never used as a path.
"""

import logging
import shutil
from pathlib import Path
from typing import BinaryIO

logger = logging.getLogger(__name__)

_MIB = 1024 * 1024
CHUNK_BYTES = _MIB


class UploadTooLarge(Exception):
    """The upload is over the size limit. The message is safe to show the client."""


def too_large_message(max_bytes: int) -> str:
    return f"The file is larger than the {max_bytes / _MIB:g} MB upload limit."


def upload_dir(storage_dir: Path, file_id: str) -> Path:
    return storage_dir / file_id


def save_stream(src: BinaryIO, dest: Path, max_bytes: int) -> int:
    """Copy `src` to `dest` in chunks and return the byte count.

    Raises UploadTooLarge as soon as more than `max_bytes` have been read, so
    an upload without a Content-Length header still cannot fill the disk.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with dest.open("wb") as out:
        while chunk := src.read(CHUNK_BYTES):
            written += len(chunk)
            if written > max_bytes:
                raise UploadTooLarge(too_large_message(max_bytes))
            out.write(chunk)
    return written


def remove_upload_dir(directory: Path) -> None:
    """Delete an upload directory; a missing one is fine, other errors are logged."""
    try:
        shutil.rmtree(directory)
    except FileNotFoundError:
        pass
    except OSError:
        logger.exception("Could not remove upload directory %s", directory)
