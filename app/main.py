"""Application factory. `uvicorn app.main:app` serves the app built from the environment."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.api import files, health
from app.config import Settings
from app.db import init_db, make_engine, make_session_factory
from app.storage import too_large_message

UPLOAD_PATH = "/api/files/"

# Content-Length counts the whole multipart body: boundaries and part headers
# as well as the file. This allowance keeps a file just under the cap from
# being rejected; the exact file-size cap is enforced while saving.
MULTIPART_OVERHEAD_BYTES = 64 * 1024


class UploadSizeLimit:
    """Reject oversized uploads with 413 from the Content-Length header alone.

    Starlette parses the whole multipart body before the route runs, so a
    check inside the route would come after a huge upload was already read.
    This middleware answers before the body is touched. Requests without
    Content-Length fall through to the byte count in storage.save_stream.
    """

    def __init__(self, app: ASGIApp, max_upload_bytes: int) -> None:
        self.app = app
        self.max_upload_bytes = max_upload_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if self._too_large(scope):
            response = JSONResponse(
                {"detail": too_large_message(self.max_upload_bytes)},
                status_code=413,
                # The unread body is still on the socket, so don't reuse the connection.
                headers={"Connection": "close"},
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)

    def _too_large(self, scope: Scope) -> bool:
        if scope["type"] != "http" or scope["method"] != "POST" or scope["path"] != UPLOAD_PATH:
            return False
        headers = dict(scope["headers"])
        length = headers.get(b"content-length", b"")
        return length.isdigit() and int(length) > self.max_upload_bytes + MULTIPART_OVERHEAD_BYTES


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    init_db(app.state.engine)
    yield
    app.state.engine.dispose()


def create_app(settings: Settings) -> FastAPI:
    """Build the app with its own engine and session factory (no module globals)."""
    app = FastAPI(title="Geospatial File Measurement API", lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = make_engine(settings.database_url)
    app.state.session_factory = make_session_factory(app.state.engine)
    app.add_middleware(UploadSizeLimit, max_upload_bytes=settings.max_upload_bytes)
    app.include_router(files.router)
    app.include_router(health.router)
    return app


logging.basicConfig(level=logging.INFO)
app = create_app(Settings.from_env())
