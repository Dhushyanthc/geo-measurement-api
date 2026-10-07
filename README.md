# Geospatial File Measurement API

## Overview

A FastAPI service that accepts a KML file or a zipped Shapefile and extracts every feature with
its geometry, properties and source CRS. Each polygon gets an area in square metres and each line a
length in metres, measured after reprojecting into the feature's own UTM zone, never in degrees.
Uploads are processed in the background: clients upload, poll for status, then page through
the measurements.

## Setup and run

Requires Python 3.11 or newer. No system GDAL is needed: `pyogrio` ships GDAL inside its wheels.

macOS / Linux:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Windows (PowerShell):

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

### Configuration

Settings come from environment variables; every one has a default for local runs.

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./app.db` | SQLAlchemy URL. For Postgres: `postgresql+psycopg://user:pass@host:5432/db` (install with `pip install -e ".[postgres]"`) |
| `STORAGE_DIR` | `./storage` | Where uploads wait until they are processed; cleaned after each job |
| `MAX_UPLOAD_MB` | `25` | Largest accepted upload |
| `MAX_UNCOMPRESSED_MB` | `100` | Cap on the total uncompressed size of a zip |
| `MAX_ZIP_ENTRIES` | `100` | Cap on the number of entries in a zip |

Start the API on `http://localhost:8000` (tables are created on startup; interactive docs at
`/docs`):

```bash
uvicorn app.main:app --reload
```

To create the tables up front instead (safe to run again; existing tables are left alone):

```bash
python -m app.db
```

### Docker

```bash
docker compose up --build
```

This starts Postgres 16 and the API on `http://localhost:8000` with two uvicorn workers. The
`api` container waits for Postgres to be healthy, creates the tables once with
`python -m app.db`, then starts the workers, so they never race to create tables. Uploads wait
in a named volume (`storage`) until processed. The image runs as a non-root user, and its
`HEALTHCHECK` calls `GET /health`. The [API](#api) examples use the same address.

## Run tests

```bash
pytest
ruff check .
ruff format --check .
```

Tests use a temporary SQLite database by default. To run the same suite against Postgres, point
`TEST_DATABASE_URL` at an empty database (its tables are dropped and recreated for every test):

```bash
TEST_DATABASE_URL=postgresql+psycopg://user:pass@localhost:5432/geo_test pytest
```

CI (`.github/workflows/ci.yml`) runs ruff; the suite on Ubuntu and Windows with Python 3.11 and
3.12; the suite against Postgres 16; and a Docker job that builds the image, starts the Compose
stack and runs the upload, poll and measurements flow against it with both samples.

## API

Measurements are in metres (`length_m`) and square metres (`area_m2`).

The examples below use the files in [`samples/`](samples/), which
`python scripts/make_samples.py` regenerates:

- `sample.kml`: two folders near Bengaluru. *Plots* holds an 8,000 m² plot with
  `<ExtendedData>` and a MultiGeometry of two 2,500 m² fields sharing an edge; *Infrastructure*
  holds a 210 m road and a well.
- `sample_shapefile.zip`: three fields in EPSG:32643 (UTM 43N) with an ESRI-style `.prj`. One has
  a 20 m x 20 m pond (a hole); one has a self-intersecting boundary.

The whole flow, copy-pasteable (bash; the id is captured from the upload response):

```bash
ID=$(curl -s -F "file=@samples/sample.kml" http://localhost:8000/api/files/ \
  | python -c "import json, sys; print(json.load(sys.stdin)['id'])")
# Processing runs in the background: poll until the file is COMPLETED or FAILED.
until curl -s http://localhost:8000/api/files/$ID/ | grep -qE '"status":"(COMPLETED|FAILED)"'; do
  sleep 1
done
curl -s http://localhost:8000/api/files/$ID/
curl -s "http://localhost:8000/api/files/$ID/measurements/?limit=100&offset=0"
```

### Upload: `POST /api/files/`

Send the file as multipart field `file`: a `.kml`, or a `.zip` holding one Shapefile.
The upload is checked and stored, and the response comes back straight away with **202
Accepted** and status `PENDING`. Reading and measuring happen in the background; the
`Location` header is the URL to poll.

```bash
curl -i -F "file=@samples/sample.kml" http://localhost:8000/api/files/
curl -i -F "file=@samples/sample_shapefile.zip" http://localhost:8000/api/files/
```

```http
HTTP/1.1 202 Accepted
location: /api/files/2446a85c-11d5-458c-9e9c-380c12c6e9ff/

{"id": "2446a85c-11d5-458c-9e9c-380c12c6e9ff", "filename": "sample.kml", "feature_count": 0,
 "crs": null, "status": "PENDING", "error": null}
```

### Poll: `GET /api/files/{id}/`

Poll the `Location` URL until `status` is `COMPLETED` or `FAILED` (`PENDING` and `PROCESSING`
come first). `crs` is the source file's CRS; `error` explains a `FAILED` file.

```bash
curl http://localhost:8000/api/files/2446a85c-11d5-458c-9e9c-380c12c6e9ff/
```

```json
{"id": "2446a85c-11d5-458c-9e9c-380c12c6e9ff", "filename": "sample.kml", "feature_count": 4,
 "crs": "EPSG:4326", "status": "COMPLETED", "error": null}
```

For the shapefile, `crs` is `"EPSG:32643"`, identified from the ESRI WKT in its `.prj`.

### Measurements: `GET /api/files/{id}/measurements/?limit=100&offset=0`

One entry per feature, ordered by `index`. `limit` is 1 to 1000 (default 100) and `offset` is 0
or more; `total` is the file's feature count. `geometry` is GeoJSON in EPSG:4326 whatever the
source CRS, and `source_crs` records what the file used. `measurement.crs` is the UTM zone the
feature was measured in.

```bash
curl "http://localhost:8000/api/files/2446a85c-11d5-458c-9e9c-380c12c6e9ff/measurements/?limit=1"
```

```json
{
  "file_id": "2446a85c-11d5-458c-9e9c-380c12c6e9ff",
  "total": 4, "limit": 1, "offset": 0,
  "features": [{
    "index": 0, "layer": "Plots", "geometry_type": "Polygon", "source_crs": "EPSG:4326",
    "properties": {"id": null, "Name": "Plot A", "description": null, "timestamp": null,
                   "begin": null, "end": null, "owner": "Asha", "crop": "rice"},
    "geometry": {"type": "Polygon", "coordinates": [[[77.5901294, 12.9677922],
                 [77.5901369, 12.9685149], [77.591058, 12.9685057],
                 [77.5910505, 12.9677831], [77.5901294, 12.9677922]]]},
    "measurement": {"status": "MEASURED", "area_m2": 7999.79070555905, "length_m": null,
                    "crs": "EPSG:32643", "reason": null, "warnings": []}
  }]
}
```

The self-intersecting field in the shapefile (`?offset=2&limit=1`) is reported, not measured:

```json
"measurement": {"status": "INVALID", "area_m2": null, "length_m": null, "crs": null,
                "reason": "Self-intersection[77.5947582942158 12.970004698885]", "warnings": []}
```

`measurement.status` is one of:

| Status | Meaning |
|---|---|
| `MEASURED` | Polygon: `area_m2` set. Line: `length_m` set. |
| `NO_MEASUREMENT_REQUIRED` | Point or MultiPoint; both numbers are `null`. |
| `INVALID` | Self-intersecting or otherwise invalid polygon; `reason` says why, numbers are `null`. |
| `UNSUPPORTED` | Cannot be measured (no geometry, mixed GeometryCollection, polar, crosses the antimeridian, ...); `reason` says why. |

### Health: `GET /health`

Runs `SELECT 1` against the database: **200** `{"status": "ok"}`, or **503**
`{"status": "unavailable"}` when the database cannot be reached. Used by Docker and load
balancer health checks.

### Errors

Errors use FastAPI's `{"detail": "..."}` body. Messages never include server paths.

| Situation | Code |
|---|---|
| Wrong extension, corrupt zip, no or several `.shp`, missing `.shx`/`.dbf`/`.prj`, unsafe zip | 400 |
| Unknown file id | 404 |
| Measurements requested while `PENDING`/`PROCESSING` (with `Retry-After: 1`) or after `FAILED` | 409 |
| Upload larger than `MAX_UPLOAD_MB` | 413 |
| Missing `file` field, bad `limit`/`offset` | 422 |
| Database unreachable (`/health` only) | 503 |

Oversized uploads are rejected from the `Content-Length` header before the body is read; a
byte count while saving is the backstop for requests without that header. In production,
also cap the body size at the reverse proxy (for example nginx `client_max_body_size`).

## Architecture

### Folder structure

```
app/
├── main.py         create_app(settings): engine on app.state, lifespan creates tables,
│                   upload-size middleware, routers
├── config.py       Settings from environment variables
├── db.py           engine and session factory, get_session; `python -m app.db` creates tables
├── models.py       File and Feature tables (portable across SQLite and Postgres)
├── schemas.py      response bodies
├── storage.py      STORAGE_DIR/{file_id}/: chunked save with a byte cap, cleanup
├── service.py      accept_upload (in the request) and process_file (background job)
├── api/
│   ├── files.py    POST /api/files/, GET /api/files/{id}/, GET .../measurements/
│   └── health.py   GET /health
└── core/           pure geospatial logic: no FastAPI or database imports
    ├── crs.py      UTM zone selection, cached transformers, CRS labels
    ├── measure.py  per-feature area/length with statuses
    ├── readers.py  KML and Shapefile reading, JSON-safe properties
    └── ziputil.py  safe Shapefile extraction from a zip
tests/              unit tests for core/, service tests, API tests; fixtures built in code
samples/            sample.kml and sample_shapefile.zip (regenerate: scripts/make_samples.py)
```

`core/` can be imported and tested with no web server or database, so the geospatial logic is
tested on its own.

### File-processing flow

```
POST /api/files/
  middleware   Content-Length over the limit?            -> 413, body never read
  route        accept_upload (in the request):
                 extension is .kml or .zip?                -> else 400
                 save to STORAGE_DIR/{id}/ in 1 MiB chunks -> over the limit: 413
                 zip: extract the Shapefile safely         -> bad zip: 400
                 insert File row as PENDING
               (any failure: roll back, delete the directory, no row left behind)
               schedule process_file(id) as a background task
               <- 202 Accepted, Location: /api/files/{id}/

process_file(id) (background job, its own database session):
  claim     UPDATE ... SET PROCESSING WHERE status = PENDING (atomic; skip if not claimed)
  read      pyogrio reads every layer: WKB geometries + attribute columns
  reproject all geometries of the file from the source CRS to EPSG:4326 in one call
  measure   each feature on its own (see below) -> status, area/length, warnings
  persist   bulk insert of all features; File -> COMPLETED with crs and feature_count
  (read error: File -> FAILED with a fixed message; the full traceback is logged server-side)
  finally   delete STORAGE_DIR/{id}

GET /api/files/{id}/               poll until COMPLETED or FAILED
GET /api/files/{id}/measurements/  409 until COMPLETED, then paginated features
```

Reading details:

- **KML:** every `<Folder>` is a layer, and placemarks outside any folder form one more layer,
  named after the stored file (`upload`). GDAL opens KML with its LIBKML driver, so
  `<ExtendedData>` values (`<Data>` and `<SchemaData>`) become properties. LIBKML's display
  settings (`tessellate`, `visibility`, `icon`, ...) are dropped because they are not
  attributes. The CRS of a KML file is always EPSG:4326, as the KML specification requires.
- **Shapefile:** the zip must hold exactly one `.shp` with its `.shx`, `.dbf` and `.prj`
  (`.cpg` optional) in the same folder. The CRS comes from the `.prj`, including the ESRI WKT
  that ArcGIS writes; a missing or unreadable `.prj` is rejected rather than guessed.
- **Zip safety:** entry count and declared uncompressed size are capped; entries with `..`,
  absolute paths or symlink attributes are rejected; the Shapefile parts are copied to fixed
  names (`data.shp`, ...) while counting the real bytes written. Nothing in the archive chooses
  where files land, and a member that lies about its size is rejected.
- **Indices:** features are numbered globally from 0, in layer order and then feature order. A
  placemark without geometry is kept (it becomes `UNSUPPORTED`), so indices always match the
  source file.
- **Properties:** made JSON-safe: numpy scalars become Python numbers, NaN becomes `null`,
  dates and timestamps become ISO 8601 strings, bytes are decoded. A value that cannot be
  converted never fails the file.

### Measurement flow

`measure_geometry` in `app/core/measure.py` takes one EPSG:4326 geometry and never raises for
bad input. The first matching rule wins:

1. No geometry, or an empty one: `UNSUPPORTED`.
2. Z values are dropped for measuring (warning `Z_IGNORED`); the GeoJSON output keeps them.
3. `GeometryCollection` (what GDAL makes of a KML `<MultiGeometry>` with mixed members): all
   polygons, all lines or all points are measured as the matching multi-geometry, with warning
   `NORMALIZED_FROM_GEOMETRYCOLLECTION`. Mixed or nested collections are `UNSUPPORTED`, with a
   reason naming the member types.
4. Point / MultiPoint: `NO_MEASUREMENT_REQUIRED`.
5. Polygon / MultiPolygon: each part is validated **on its own**. If any part is invalid (for
   example self-intersecting), the feature is `INVALID` with the reason from GEOS and no
   numbers. Otherwise the area is the sum of the parts' areas, holes subtracted.
6. LineString / MultiLineString: length.
7. Anything else: `UNSUPPORTED`, naming the type.

Before measuring (rules 5 and 6), the UTM zone is chosen from the centre of the geometry's
bounding box. A bounding box wider than 180° of longitude (crosses the antimeridian) or a
latitude outside 80°S to 84°N is `UNSUPPORTED`. If the geometry reaches more than 4° from the
zone's central meridian, warning `FAR_FROM_CENTRAL_MERIDIAN` is added. Polygons report
`area_m2` (m²) and lines `length_m` (m); the other field is `null`.

### CRS handling

**Never measure in degrees.** A degree of longitude is about 108.5 km wide at 13°N
(Bengaluru) but only about 55.7 km at 60°N, so an "area in square degrees" has no fixed
meaning. Every geometry takes the same path:

```
source CRS --(one vectorized call per file)--> EPSG:4326 --(per feature)--> WGS 84 / UTM zone
```

- **Source CRS:** KML is always EPSG:4326. A Shapefile's CRS comes from its `.prj`; ESRI WKT
  such as `GCS_WGS_1984` or `WGS_1984_UTM_Zone_43N` is identified as `EPSG:4326` /
  `EPSG:32643`. A CRS with no EPSG code is reported as `WKT:<name>` and transformed from its
  full WKT.
- **Zone:** `zone = floor((lon + 180) / 6) + 1`; the EPSG code is `32600 + zone` in the
  northern hemisphere and `32700 + zone` in the southern. Bengaluru (77.59°E, 12.97°N) is zone
  43N, `EPSG:32643`; the same longitude at 12.97°S would be `EPSG:32743`.
- **Axis order:** every `pyproj.Transformer` is built with `always_xy=True`, so coordinates are
  always (longitude, latitude). Without it, EPSG:4326's official latitude-first axis order
  silently swaps the axes. Transformers are cached, not rebuilt per feature.
- **Projected sources are reprojected too.** A Shapefile already "in metres" is not measured
  in its own CRS: Web Mercator (EPSG:3857), common in web-exported data, inflates areas by
  about 1/cos²(latitude), around 5% at 13°N and 4x at 60°N. Every input goes through the same
  path, with no special cases. A test delivers a true 1 km² square as an EPSG:3857 Shapefile
  and checks the result is within 0.25% of 1,000,000 m².
- **Accuracy:** UTM is conformal, not equal-area. Its scale error is about 0.1% at 2.6° from
  the central meridian, which the tests allow for (0.25%). Results are cross-checked against
  geodesic areas from `pyproj.Geod` in the tests.

### Scaling

- **Processing outside the request.** The upload request only does cheap checks and returns
  202; reading and measuring run in a background task, so slow files don't hold HTTP
  connections. Endpoints and the job are plain `def`, so blocking work runs in the threadpool,
  not on the event loop.
- **Seam to a job queue.** `process_file(file_id, session_factory, settings)` takes no request
  objects; it reads only from storage and the database. A Celery/RQ/arq worker could call the
  same function: moving to a queue changes the dispatcher, not the job.
- **Stateless API, shared database.** API processes keep no state between requests, so several
  can run behind a load balancer against one Postgres database; Docker Compose runs two
  workers this way. Each process handles the uploads it received, so `STORAGE_DIR` can be
  local to the process's machine.
- **Upload limits before the body is read.** Starlette parses the whole multipart body before
  the route runs, so a check in the route would come after a 5 GB upload had already been
  read. An ASGI middleware rejects the request from its `Content-Length` header instead (with
  a 64 KiB allowance for multipart framing); the byte count while saving is the exact cap and
  covers requests without that header.
- **Database work in bulk.** Features are inserted in one batched `INSERT`; the measurements
  query pages through the `(file_id, idx)` unique index.

## Design decisions and alternatives

| Decision | Alternatives considered | Why |
|---|---|---|
| Process in a FastAPI `BackgroundTasks` job, return 202 | Process inside the request; a full queue (Celery/RQ + Redis) | In-request processing ties up the connection and times out on large files. A queue adds a broker and worker deployment this scope does not need yet; `process_file` is already shaped so a queue worker can call it unchanged. |
| Per-feature UTM zone | Geodesic area with `pyproj.Geod`; per-feature Lambert azimuthal equal-area | The task asks for projected measurement; UTM is the survey convention and its EPSG code is easy to check by hand. Geodesic measurement serves as the test oracle. An equal-area projection removes area distortion but has no standard code per feature. The zone choice is one function, so swapping is small. |
| SQLite by default, Postgres supported | Postgres only | SQLite needs no setup for local runs and tests. Several API processes need Postgres (SQLite allows one writer at a time and lives on one machine). The same models run on both, and CI runs the suite on both. |
| `pyogrio` raw reads | `fiona`, `geopandas` | `pyogrio` ships GDAL in its wheels (no system install on Linux, macOS or Windows), and its raw API returns WKB and plain arrays without pulling in pandas. |
| Exactly one Shapefile per zip | Accept several and merge | One Shapefile gives one CRS and one clear feature numbering. Several Shapefiles per zip is future scope. |
| `INVALID` with a reason | `make_valid`, then measure the result | `make_valid` silently changes the shape (a bowtie becomes two triangles), so the area would describe a shape nobody drew. A wrong number is worse than none. |
| Normalize homogeneous GeometryCollections only | Reject every collection; measure mixed ones partially | Google Earth exports use `<MultiGeometry>` often, and all-polygon or all-line collections have one obvious measurement. A polygon plus a line has no single meaningful number. |
| Validate MultiPolygon parts one by one | `MultiPolygon.is_valid` | Shapely treats parts that share an edge as invalid, but adjacent fields are normal in survey data. |
| GeoJSON output in EPSG:4326 | Geometry in the source CRS | RFC 7946 GeoJSON is lon/lat WGS 84, which every web map reads; `source_crs` keeps the original CRS visible. |
| Content-Length middleware for the size limit | Count bytes in the route | The route runs only after the whole body has been parsed to a temp file. |
| `create_all` at startup and in `python -m app.db` | Alembic migrations | Two tables and no production data to migrate yet. Alembic is future scope. |

## Known limitations

- UTM is not equal-area: about 0.1% error 2.6° from a zone's central meridian, more toward the
  zone edges (flagged with `FAR_FROM_CENTRAL_MERIDIAN`). A feature spanning several zones is
  still measured in the single zone at its centre.
- Features crossing the antimeridian, and features outside 80°S to 84°N (where UTM is not
  defined), are `UNSUPPORTED`.
- The Norway and Svalbard UTM zone exceptions are ignored; the plain 6° grid is used.
- Mixed GeometryCollections (for example a polygon and a line in one `<MultiGeometry>`) are
  `UNSUPPORTED`.
- Overlapping parts of a MultiPolygon or polygon collection are counted twice, because the
  area is the sum of the parts.
- KML attributes depend on the LIBKML driver bundled in the `pyogrio` wheels. When a file
  declares a `<Schema>`, LIBKML drops untyped `<Data>` values on that layer.
- Integer attributes with missing values come back as floats (`3` becomes `3.0`), because GDAL
  reads the column as floating point to represent the gaps.
- `BackgroundTasks` is not durable: if a process dies mid-job, its file stays `PROCESSING`
  (`updated_at` shows when it stalled), and there is no retry.
- Uploads are stored on the machine of the process that received them, so the job must run in
  that same process.
- Tables are created with `create_all`; there are no migrations.
- There is no authentication or rate limiting.

## Future scope

- A durable job queue with retries (Celery, RQ or arq) and a reaper for jobs stuck in
  `PROCESSING`.
- Shared object storage (S3 or similar) so separate worker machines can process uploads.
- PostGIS geometry columns and spatial indexes; Alembic migrations.
- Streaming or batched reading (`pyogrio`'s Arrow API) for files beyond the size cap, and
  grouping features by UTM zone to reproject each group in one vectorized call.
- More inputs: KMZ, GeoJSON, GeoPackage, several Shapefiles per zip, mixed
  GeometryCollections.
- A selectable measurement method (UTM, equal-area or geodesic), and perimeter for polygons.
- Filtering (for example by status) and cursor pagination.
- Authentication and rate limiting; optional retention of original uploads.
