# Geospatial File Measurement API

## Overview

A FastAPI service for measuring survey data. Upload a KML or zipped Shapefile and it extracts every
feature's geometry, attributes and CRS, then measures polygon areas (m²) and line lengths (m) in each
feature's local UTM zone, never in degrees. You upload, poll until it's done, then page through results.

## Setup and run

Python 3.11+ is all you need. `pyogrio` ships GDAL inside its wheels, so there's nothing else to install.

```bash
python -m venv .venv
source .venv/bin/activate        # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"
uvicorn app.main:app --reload    # http://localhost:8000, docs at /docs, tables created on startup
```

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./app.db` | Postgres: `postgresql+psycopg://user:pass@host:5432/db` (needs `pip install -e ".[postgres]"`) |
| `STORAGE_DIR` | `./storage` | Where uploads wait until processed; cleaned after each job |
| `MAX_UPLOAD_MB` / `MAX_UNCOMPRESSED_MB` / `MAX_ZIP_ENTRIES` | `25` / `100` / `100` | Upload size, unzipped size and zip entry limits |

**Docker:** `docker compose up --build` runs Postgres 16 and the API (two workers) on port 8000. Tables
are created once before the workers start, so they never race; the image is non-root with a `/health` check.

## Run tests

```bash
pytest && ruff check . && ruff format --check .
TEST_DATABASE_URL=postgresql+psycopg://user:pass@localhost:5432/geo_test pytest   # same suite on Postgres
```

CI runs lint, the suite on Ubuntu and Windows with Python 3.11 and 3.12, the suite on Postgres 16, and a
Docker job that starts the Compose stack and runs the upload-poll-measure flow against both samples.

## API

The examples use [`samples/`](samples/) (`python scripts/make_samples.py` rebuilds them): `sample.kml`
has an 8,000 m² plot, two adjacent 2,500 m² fields, a 210 m road and a well near Bengaluru;
`sample_shapefile.zip` has three fields in EPSG:32643, one with a pond and one that self-intersects.

```bash
ID=$(curl -s -F "file=@samples/sample.kml" http://localhost:8000/api/files/ \
  | python -c "import json, sys; print(json.load(sys.stdin)['id'])")
until curl -s http://localhost:8000/api/files/$ID/ | grep -qE '"status":"(COMPLETED|FAILED)"'; do
  sleep 1
done
curl -s "http://localhost:8000/api/files/$ID/measurements/?limit=100&offset=0"
```

| Endpoint | What it does |
|---|---|
| `POST /api/files/` | Multipart field `file` (`.kml` or `.zip`). Returns **202**, status `PENDING`, and a `Location` header to poll. |
| `GET /api/files/{id}/` | `id`, `filename`, `feature_count`, `crs`, `status` (`PENDING` → `PROCESSING` → `COMPLETED`/`FAILED`), `error`. |
| `GET /api/files/{id}/measurements/` | Features ordered by index, paged with `limit` (1–1000, default 100) and `offset`. |
| `GET /health` | `SELECT 1` on the database: 200 `{"status": "ok"}` or 503 `{"status": "unavailable"}`. |

One feature from the measurements response (geometry is always GeoJSON in EPSG:4326):

```json
{"index": 0, "layer": "Plots", "geometry_type": "Polygon", "source_crs": "EPSG:4326",
 "properties": {"Name": "Plot A", "owner": "Asha", "crop": "rice", "...": null},
 "geometry": {"type": "Polygon", "coordinates": [[[77.5901294, 12.9677922], "..."]]},
 "measurement": {"status": "MEASURED", "area_m2": 7999.79070555905, "length_m": null,
                 "crs": "EPSG:32643", "reason": null, "warnings": []}}
```

Each feature gets a status: `MEASURED` (area or length set), `NO_MEASUREMENT_REQUIRED` (points),
`INVALID` (for example a self-intersecting polygon; `reason` says why, no numbers) or `UNSUPPORTED`
(no geometry, mixed GeometryCollection, polar, crosses the antimeridian).

Errors use FastAPI's `{"detail": "..."}` and never include server paths: **400** bad extension or
zip (no/several `.shp`, missing `.shx`/`.dbf`/`.prj`, unsafe entries), **404** unknown id, **409**
measurements before `COMPLETED` (with `Retry-After: 1` while processing), **413** too large,
**422** bad `limit`/`offset` or missing `file`, **503** database down (`/health`).

## Architecture

```mermaid
flowchart LR
    C([Client]) -->|POST /api/files/| MW[Size middleware<br/>checks Content-Length]
    MW --> UP[Upload route<br/>accept_upload]
    UP -->|save upload| ST[(STORAGE_DIR)]
    UP -->|File row: PENDING| DB[(SQLite / Postgres)]
    UP -->|202 + Location| C
    UP -.->|BackgroundTasks| JOB[process_file]
    ST --> JOB
    JOB --> RD[Read layers<br/>pyogrio / GDAL]
    RD --> RP[Reproject<br/>source CRS → EPSG:4326 → UTM]
    RP --> MS[Measure per feature<br/>shapely]
    MS -->|features + COMPLETED / FAILED| DB
    C -->|GET status / measurements| RT[Read routes] --> DB
```

The upload request only does cheap checks: extension, size, and safe zip extraction. It then stores the
file, inserts a `PENDING` row and returns 202. Any failure rolls everything back, so no row or file is
left behind. The background job claims the row with one atomic `UPDATE ... WHERE status = 'PENDING'`
(so a duplicate dispatch can't run it twice), reads every layer, reprojects the whole file to EPSG:4326
in one vectorized call, measures each feature, bulk-inserts the results and marks the file `COMPLETED`.
If reading fails, the file is marked `FAILED` with a safe message. The upload folder is deleted either way.

`app/core/` (CRS, readers, zip handling, measurement) has no web or database imports, so it's tested
on its own; `app/service.py` holds the upload and job logic and `app/api/` the thin routes.

**CRS handling.** A degree of longitude is about 108.5 km wide at 13°N but 55.7 km at 60°N, so
nothing is measured in degrees. KML is always EPSG:4326. A Shapefile's CRS comes from its `.prj`,
including ArcGIS's ESRI WKT, and a missing `.prj` is rejected rather than guessed. Each feature is
measured in the UTM zone at the centre of its bounding box: `zone = floor((lon + 180) / 6) + 1`,
EPSG `32600 + zone` north and `32700 + zone` south (Bengaluru is `EPSG:32643`). Every transformer
uses `always_xy=True`, because EPSG:4326 officially lists latitude first. Data already "in metres"
is reprojected too: Web Mercator overstates area by about 1/cos²(latitude), around 5% at 13°N, and a
test feeds a true 1 km² square in EPSG:3857 to prove it comes back right. UTM's own error is about
0.1% near Bengaluru, and the tests cross-check results against geodesic areas from `pyproj.Geod`.

**Measurement rules.** Z is ignored (`Z_IGNORED`). Polygon parts are validated one by one, so fields
sharing an edge stay valid, and holes are subtracted. Collections of only polygons, lines or points
are normalized; mixed ones are `UNSUPPORTED`. Going 4°+ past the central meridian adds a warning.

**Scaling.** The API is stateless, so several processes can share one Postgres. `process_file`
takes only an id and factories, so a Celery/RQ worker could call it unchanged. Oversized uploads
are rejected from `Content-Length` in middleware, because Starlette reads the whole body before
the route runs. Counting bytes while saving enforces the exact limit.

## Design decisions

- **Background tasks, not in-request processing or a full queue.** In-request processing times out
  on large files, and Celery plus Redis is more infrastructure than this needs yet.
- **UTM, not geodesic or equal-area.** The task asks for a projected CRS, UTM is the survey
  convention, and its EPSG codes are easy to check. `pyproj.Geod` is used as the test reference instead.
- **`INVALID`, not `make_valid`.** `make_valid` turns a bowtie into two triangles, so the area
  describes a shape nobody drew. I'd rather return no number than a wrong one.
- **SQLite by default, Postgres supported.** Zero setup locally; Postgres for several processes. CI runs both.
- **`pyogrio`, not `geopandas`.** It bundles GDAL in its wheels and returns plain arrays without pandas.
- **One Shapefile per zip, GeoJSON in EPSG:4326, `create_all` instead of Alembic.** Each keeps scope
  small; the alternatives are listed under future scope.

## Known limitations

- UTM error grows towards zone edges, and a feature spanning several zones is measured in its centre zone.
- Antimeridian-crossing and polar (outside 80°S–84°N) features are `UNSUPPORTED`; Norway/Svalbard
  zone exceptions are ignored. Overlapping parts of a multipolygon are counted twice.
- LIBKML drops untyped `<Data>` when a KML declares a `<Schema>`. Integers with gaps come back as floats.
- `BackgroundTasks` isn't durable: a crash mid-job leaves the file `PROCESSING`.
- Uploads live on the receiving machine. There are no migrations, authentication or rate limiting.

## Learning

**Coordinates aren't distances.** Lat/lon looks like an x/y grid but isn't, and even data "in metres"
can lie: Web Mercator overstates area by about 5% at Bengaluru, so everything goes through UTM.

**Axis order bites quietly.** A swapped transform doesn't crash; it puts Bengaluru in the Arctic.
`always_xy=True` plus a test asserting zone 43N turn that silent bug into a loud one.

**GDAL has surprises.** LIBKML returns a polygon `<MultiGeometry>` as a MultiPolygon, adds display
settings as attributes, and drops untyped `<Data>` next to a `<Schema>`. Small test files found all three.

**A zip is untrusted input.** Ignoring the archive's names (fixed output names, bytes counted while
copying) stops zip-slip and zip bombs. Tests showed damaged data raises `zlib.error`, not `BadZipFile`.

**Where the body gets read.** Starlette reads the body before the route, so the size limit lives in
middleware. Background jobs aren't durable, need an atomic claim, and must hide GDAL's server paths.

## Future scope

A durable queue with retries and a stuck-job reaper; S3 storage for separate workers; PostGIS and
Alembic; streaming reads (`pyogrio` Arrow API); KMZ, GeoJSON, GeoPackage and multi-Shapefile zips;
a choice of measurement method and perimeters; status filters, cursor pagination, auth and rate limits.
