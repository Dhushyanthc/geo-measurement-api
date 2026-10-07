FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv

# pyogrio and psycopg ship binary wheels (GDAL and libpq included), so no
# system packages are needed.
COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install ".[postgres]"

# Run as an unprivileged user. /data holds uploads waiting to be processed and,
# when DATABASE_URL is left at the default below, a standalone SQLite file.
RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p /data/storage \
    && chown -R appuser:appuser /data
USER appuser

ENV DATABASE_URL=sqlite:////data/app.db \
    STORAGE_DIR=/data/storage

EXPOSE 8000

# The slim image has no curl, so the probe uses Python's standard library.
# urlopen raises on a 503, which marks the container unhealthy.
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=2)"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
