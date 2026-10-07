# Geospatial File Measurement API

## Overview

This is a small FastAPI service for measuring survey data. You upload a KML file or a zipped
Shapefile, and it pulls out every feature along with its geometry, attributes and CRS. Polygons get
an area in square metres and lines get a length in metres. Every measurement is taken after
reprojecting the feature into its local UTM zone, so nothing is ever measured in degrees.

Processing happens in the background. The upload returns right away, you poll the file until
it's done, and then page through the measurements.

## Setup and run

You need Python 3.11 or newer. You don't need to install GDAL yourself, because `pyogrio` ships
it inside its wheels.

On macOS or Linux:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

On Windows (PowerShell):

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

### Configuration

Everything is configured through environment variables, and each one has a default that works
for local runs.

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./app.db` | SQLAlchemy URL. For Postgres: `postgresql+psycopg://user:pass@host:5432/db` (install with `pip install -e ".[postgres]"`) |
| `STORAGE_DIR` | `./storage` | Where uploads wait until they are processed; cleaned after each job |
| `MAX_UPLOAD_MB` | `25` | Largest accepted upload |
| `MAX_UNCOMPRESSED_MB` | `100` | Cap on the total uncompressed size of a zip |
| `MAX_ZIP_ENTRIES` | `100` | Cap on the number of entries in a zip |

Start the API on `http://localhost:8000`. The tables are created on startup, and the interactive
docs are at `/docs`.

```bash
uvicorn app.main:app --reload
```

If you'd rather create the tables up front, run this. It's safe to run more than once, since
existing tables are left alone.

```bash
python -m app.db
```

### Docker

```bash
docker compose up --build
```

This brings up Postgres 16 and the API on `http://localhost:8000`, running two uvicorn workers.
The `api` container waits until Postgres is healthy, creates the tables once with
`python -m app.db`, and only then starts the workers, so they can't race each other to create
tables. Uploads wait in a named volume (`storage`) until they're processed. The image runs as a
non-root user, and its `HEALTHCHECK` hits `GET /health`. The [API](#api) examples work at the
same address.

## Run tests

```bash
pytest
ruff check .
ruff format --check .
```

By default the tests use a throwaway SQLite database. To run the same suite against Postgres,
point `TEST_DATABASE_URL` at an empty database. Its tables are dropped and recreated before every
test.

```bash
TEST_DATABASE_URL=postgresql+psycopg://user:pass@localhost:5432/geo_test pytest
```

CI (`.github/workflows/ci.yml`) runs ruff, then the test suite on Ubuntu and Windows with Python
3.11 and 3.12, then the suite again against Postgres 16. A final Docker job builds the image,
starts the Compose stack, and runs the upload, poll and measurements flow against it with both
sample files.

## API

Lengths are in metres (`length_m`) and areas in square metres (`area_m2`).

The examples use the two files in [`samples/`](samples/). Running
`python scripts/make_samples.py` regenerates them.

- `sample.kml` has two folders of features near Bengaluru. *Plots* contains an 8,000 m² plot
  with `<ExtendedData>` attributes, plus a MultiGeometry of two 2,500 m² fields that share an
  edge. *Infrastructure* contains a 210 m road and a well.
- `sample_shapefile.zip` has three fields in EPSG:32643 (UTM 43N) with an ESRI-style `.prj`.
  One field has a 20 m x 20 m pond cut out of it, and one has a boundary that crosses itself.

Here's the whole flow in one go. It's bash, and it grabs the id from the upload response:

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

Send the file in a multipart field called `file`. It can be a `.kml`, or a `.zip` containing one
Shapefile. The server checks and stores the upload, then answers straight away with
**202 Accepted** and a status of `PENDING`. The actual reading and measuring happen afterwards,
and the `Location` header tells you where to poll.

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

Keep calling the `Location` URL until `status` turns into `COMPLETED` or `FAILED`. Before that
you'll see `PENDING` and then `PROCESSING`. `crs` is the CRS of the uploaded file, and `error`
explains what went wrong if it failed.

```bash
curl http://localhost:8000/api/files/2446a85c-11d5-458c-9e9c-380c12c6e9ff/
```

```json
{"id": "2446a85c-11d5-458c-9e9c-380c12c6e9ff", "filename": "sample.kml", "feature_count": 4,
 "crs": "EPSG:4326", "status": "COMPLETED", "error": null}
```

For the shapefile you'd get `"crs": "EPSG:32643"`, worked out from the ESRI WKT in its `.prj`.

### Measurements: `GET /api/files/{id}/measurements/?limit=100&offset=0`

You get one entry per feature, ordered by `index`. `limit` can be anything from 1 to 1000
(default 100), `offset` starts at 0, and `total` is the number of features in the file.
`geometry` always comes back as GeoJSON in EPSG:4326, whatever the source CRS was, and
`source_crs` tells you what the file originally used. `measurement.crs` is the UTM zone the
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

The self-intersecting field in the shapefile (`?offset=2&limit=1`) is reported, but it isn't
measured:

```json
"measurement": {"status": "INVALID", "area_m2": null, "length_m": null, "crs": null,
                "reason": "Self-intersection[77.5947582942158 12.970004698885]", "warnings": []}
```

Every feature ends up with one of these statuses:

| Status | Meaning |
|---|---|
| `MEASURED` | Polygon: `area_m2` set. Line: `length_m` set. |
| `NO_MEASUREMENT_REQUIRED` | Point or MultiPoint; both numbers are `null`. |
| `INVALID` | Self-intersecting or otherwise invalid polygon; `reason` says why, numbers are `null`. |
| `UNSUPPORTED` | Cannot be measured (no geometry, mixed GeometryCollection, polar, crosses the antimeridian, ...); `reason` says why. |

### Health: `GET /health`

This runs `SELECT 1` against the database. It returns **200** `{"status": "ok"}` when the
database answers and **503** `{"status": "unavailable"}` when it doesn't. Docker and load
balancers use it as their health check.

### Errors

Errors come back in FastAPI's usual `{"detail": "..."}` shape. The messages are written for
clients, so they never include server paths or stack traces.

| Situation | Code |
|---|---|
| Wrong extension, corrupt zip, no or several `.shp`, missing `.shx`/`.dbf`/`.prj`, unsafe zip | 400 |
| Unknown file id | 404 |
| Measurements requested while `PENDING`/`PROCESSING` (with `Retry-After: 1`) or after `FAILED` | 409 |
| Upload larger than `MAX_UPLOAD_MB` | 413 |
| Missing `file` field, bad `limit`/`offset` | 422 |
| Database unreachable (`/health` only) | 503 |

Oversized uploads are turned away based on the `Content-Length` header, before the body is
read. Requests that don't send that header are still capped, because the server counts bytes
as it saves. In production I'd also limit the body size at the reverse proxy (for example
nginx's `client_max_body_size`).

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

I kept `core/` free of any web or database code. That way the geospatial logic, which is the
part most likely to have subtle bugs, can be tested on its own without starting anything.

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

A few details about reading the files:

- In a KML file, every `<Folder>` becomes a layer. Placemarks that sit outside any folder end up
  in one extra layer, named after the stored file (`upload`). GDAL opens KML with its LIBKML
  driver, which turns `<ExtendedData>` (both `<Data>` and `<SchemaData>`) into properties. LIBKML
  also adds display settings such as `tessellate`, `visibility` and `icon` to every feature. I
  drop those, since they describe how Google Earth draws the shape rather than anything about
  the feature. The KML spec says coordinates are always EPSG:4326, so that's what the CRS is set
  to.
- A Shapefile zip has to contain exactly one `.shp`, with its `.shx`, `.dbf` and `.prj` next to
  it in the same folder (a `.cpg` is optional). The CRS is read from the `.prj`, including the
  ESRI-flavoured WKT that ArcGIS writes. If the `.prj` is missing or can't be read, the upload
  is rejected. I didn't want to guess a CRS and return confident numbers that are wrong.
- Zips are treated as untrusted. There's a cap on the number of entries and on the total
  uncompressed size, and entries with `..`, absolute paths or symlinks are rejected. The
  Shapefile parts are then copied out under fixed names (`data.shp` and so on), counting the
  real bytes as they go. Nothing inside the archive gets to decide where files land on disk,
  and a member that lies about its size gets caught.
- Features are numbered from 0 across the whole file, in layer order and then feature order. A
  placemark without a geometry is kept, and its status is `UNSUPPORTED`. That way the indices
  always line up with the source file.
- Attribute values are cleaned up so they're safe to store as JSON. Numpy numbers become plain
  Python numbers, NaN becomes `null`, dates and timestamps become ISO 8601 strings, and bytes
  are decoded. A value that can't be converted is never a reason to fail the file.

### Measurement flow

`measure_geometry` in `app/core/measure.py` takes a single EPSG:4326 geometry and decides what
to do with it. Bad input never makes it raise. It works through these rules and stops at the
first one that matches:

1. If there's no geometry, or it's empty, the feature is `UNSUPPORTED`.
2. Z values are dropped before measuring, and the feature gets a `Z_IGNORED` warning. The
   GeoJSON output still keeps them.
3. A `GeometryCollection` is what GDAL produces from a KML `<MultiGeometry>` with mixed members.
   If every member is a polygon, or every member is a line, or every member is a point, it's
   treated as the matching multi-geometry and gets the warning
   `NORMALIZED_FROM_GEOMETRYCOLLECTION`. A mixed or nested collection is `UNSUPPORTED`, and the
   reason names the member types.
4. Points and MultiPoints are `NO_MEASUREMENT_REQUIRED`.
5. For a Polygon or MultiPolygon, each part is validated on its own. If any part is invalid
   (self-intersecting, for example), the whole feature is `INVALID`. The reason comes from GEOS,
   and no numbers are returned. Otherwise the area is the sum of the parts' areas, with holes
   subtracted.
6. LineStrings and MultiLineStrings get a length.
7. Anything else is `UNSUPPORTED`, and the reason names the type.

For polygons and lines, the UTM zone is chosen from the centre of the geometry's bounding box.
Some features can't be measured this way. If the bounding box is more than 180° wide, the
feature crosses the antimeridian. If it falls outside 80°S to 84°N, UTM isn't defined there.
Both cases are `UNSUPPORTED`. If a feature stretches more than 4° away from its zone's central
meridian, it gets a `FAR_FROM_CENTRAL_MERIDIAN` warning, because the distortion grows towards
the zone's edges. Polygons fill in `area_m2` (m²) and lines fill in `length_m` (m); the other
field stays `null`.

### CRS handling

The one rule I didn't want to break is that nothing gets measured in degrees. A degree of
longitude is about 108.5 km wide at 13°N (Bengaluru) but only about 55.7 km at 60°N, so an
"area in square degrees" doesn't mean anything on its own. Every geometry goes down the same
path:

```
source CRS --(one vectorized call per file)--> EPSG:4326 --(per feature)--> WGS 84 / UTM zone
```

KML is always EPSG:4326. For a Shapefile, the CRS comes from the `.prj`. ESRI WKT names such as
`GCS_WGS_1984` and `WGS_1984_UTM_Zone_43N` are recognised as `EPSG:4326` and `EPSG:32643`. If a
CRS has no EPSG code at all, it's reported as `WKT:<name>` and transformed using its full WKT.

The zone number is `floor((lon + 180) / 6) + 1`. The EPSG code is `32600 + zone` north of the
equator and `32700 + zone` south of it. Bengaluru (77.59°E, 12.97°N) is in zone 43N, which is
`EPSG:32643`. The same longitude at 12.97°S would be `EPSG:32743`.

Axis order is an easy thing to get wrong. Officially EPSG:4326 lists latitude first, so a naive
transform quietly swaps the coordinates. Every `pyproj.Transformer` here is created with
`always_xy=True`, so coordinates are always (longitude, latitude). Transformers are cached
rather than rebuilt for each feature.

Shapefiles that are already "in metres" go through the same reprojection. They aren't measured
in their own CRS, and there's no special case for them. Web Mercator (EPSG:3857), which is
common in data exported from web maps, inflates areas by roughly 1/cos²(latitude). That's about
5% at 13°N and four times the real area at 60°N. One of the tests delivers a true 1 km² square
as an EPSG:3857 Shapefile and checks that the result lands within 0.25% of 1,000,000 m².

UTM keeps angles correct, but it doesn't preserve area exactly. Its scale error is about 0.1%
at 2.6° from the central meridian. The tests allow 0.25% to cover this, and they cross-check
the results against geodesic areas from `pyproj.Geod`.

### Scaling

The upload request only does the cheap checks and then returns 202. Reading and measuring
happen in a background task, so a slow file doesn't tie up an HTTP connection. The endpoints
and the job are plain `def` functions rather than `async def`. That way FastAPI runs the
blocking work in its threadpool instead of on the event loop.

`process_file(file_id, session_factory, settings)` doesn't take any request objects. It only
reads from storage and the database. That's deliberate: a Celery, RQ or arq worker could call
exactly the same function, so moving to a real queue would only change how the job is
dispatched, not the job itself.

The API processes don't keep any state between requests, so you can run several of them behind
a load balancer against one Postgres database. Docker Compose already runs two workers this way.
Each process handles the uploads it received itself, which is why `STORAGE_DIR` can live on that
process's own machine.

Limiting upload size is subtler than it looks. Starlette parses the whole
multipart body before the route runs, so a size check inside the route would only happen after
a 5 GB upload had already been read. Instead, a small ASGI middleware rejects the request from
its `Content-Length` header. It allows an extra 64 KiB for the multipart framing. The byte count
during saving then enforces the exact limit, and it also covers requests that don't send
`Content-Length`.

On the database side, all of a file's features are written in one batched `INSERT`, and the
measurements query pages through the `(file_id, idx)` unique index.

## Design decisions and alternatives

**Background tasks instead of processing in the request or using a full queue.** Processing
inside the request ties up the connection and times out on big files. A proper queue like Celery
or RQ with Redis would need a broker and a separate worker deployment, which felt like too much
for this scope. FastAPI's `BackgroundTasks` sits in between, and since `process_file` is already
shaped like a queue job, switching later is a small change.

**UTM per feature instead of geodesic or equal-area measurement.** The task asks for measurement
in a projected CRS, and UTM is what surveyors normally use. Its EPSG codes are also easy to
sanity-check by hand. `pyproj.Geod` gives very accurate geodesic areas, so I use it as the
reference in the tests rather than in the service. A per-feature Lambert azimuthal equal-area
projection would remove the area error completely, but it has no standard code you could report
per feature. Choosing the zone is a single function, so swapping the method later is a small
job.

**SQLite by default, Postgres when it matters.** SQLite needs no setup, which keeps local runs
and tests simple. Running several API processes needs Postgres, because SQLite only allows one
writer at a time and lives on a single machine. The models only use types that work on both, and
CI runs the full test suite against each.

**`pyogrio` instead of `fiona` or `geopandas`.** `pyogrio` bundles GDAL in its wheels, so there's
nothing to install system-wide on Linux, macOS or Windows. Its raw API hands back WKB and plain
arrays without pulling in pandas, which is all this service needs.

**One Shapefile per zip.** A single Shapefile means a single CRS and an unambiguous feature
numbering. Supporting several Shapefiles per zip is in the future scope.

**`INVALID` instead of `make_valid`.** `make_valid` quietly changes the shape. A bowtie, for
example, becomes two triangles, so the area you get back belongs to a shape nobody actually drew.
I'd rather return no number and a clear reason than a confident wrong one.

**Normalizing only collections where every member is the same kind.** Google Earth exports use
`<MultiGeometry>` a lot. A collection of only polygons, or only lines, has one obvious
measurement. A polygon plus a line doesn't add up to any single meaningful number, so those are
reported as unsupported.

**Validating MultiPolygon parts one at a time.** Shapely considers a MultiPolygon invalid when
two of its parts share an edge. In survey data that's completely normal, because neighbouring
fields share a boundary, so each part is checked separately.

**GeoJSON output in EPSG:4326.** GeoJSON (RFC 7946) is defined as WGS 84 longitude/latitude,
and every web map can read it. `source_crs` sits alongside it so the original CRS isn't lost.

**A Content-Length middleware for the size limit.** The route only runs once the whole body has
already been parsed into a temporary file, so checking there is too late (see Scaling).

**`create_all` instead of Alembic.** There are two tables and no production data to migrate yet.
Migrations are in the future scope.

## Known limitations

- UTM doesn't preserve area exactly. The error is about 0.1% at 2.6° from a zone's central
  meridian, and it grows towards the zone edges, which is what `FAR_FROM_CENTRAL_MERIDIAN` flags.
  A feature that spans several zones is still measured in the one zone at its centre.
- Features that cross the antimeridian, or that lie outside 80°S to 84°N where UTM isn't defined,
  are `UNSUPPORTED`.
- The Norway and Svalbard exceptions to the UTM grid are ignored. The plain 6° zones are used
  everywhere.
- Mixed GeometryCollections, such as a polygon and a line in the same `<MultiGeometry>`, are
  `UNSUPPORTED`.
- If parts of a MultiPolygon or polygon collection overlap, the overlap is counted twice,
  because the area is the sum of the parts.
- KML attributes rely on the LIBKML driver that comes with the `pyogrio` wheels. When a file
  declares a `<Schema>`, LIBKML drops any untyped `<Data>` values on that layer.
- Integer attributes with missing values come back as floats (`3` becomes `3.0`), because GDAL
  reads the column as floating point so it can represent the gaps.
- `BackgroundTasks` isn't durable. If a process dies in the middle of a job, that file stays
  `PROCESSING` forever (`updated_at` shows when it stalled), and nothing retries it.
- Uploads are stored on the machine of the process that received them, so the job has to run in
  that same process.
- Tables are created with `create_all`, and there are no migrations.
- There's no authentication or rate limiting.

## Learning

**Coordinates aren't distances.** Lat/lon looks like an x/y grid, but it isn't one. A degree of
longitude is about 108 km wide in Bengaluru and about 56 km at 60°N, so any area computed
straight from degrees is meaningless. Being "in metres" isn't enough either. Web Mercator data
looks projected, yet it overstates area by about 5% even at Bengaluru's latitude. That's why
every input goes through the same path to a local UTM zone, and why the tests check against a
square whose true area I already know.

**Axis order bites quietly.** EPSG:4326 officially lists latitude first, and KML writes
longitude first. A swapped transform doesn't crash; it just puts Bengaluru somewhere in the
Arctic. Using `always_xy=True` everywhere, and writing a test that checks the chosen zone is
43N, turned a silent bug into a loud one.

**GDAL doesn't always do what you'd expect.** A KML `<MultiGeometry>` is usually described as
arriving as a GeometryCollection, but for polygons LIBKML returns a MultiPolygon. That's why
validity is checked per part: shapely calls two fields sharing an edge an invalid MultiPolygon.
I also found that LIBKML adds display settings to every feature as if they were attributes, and
that it drops untyped `<Data>` when a file also declares a `<Schema>`. Testing against small
hand-written files was the only way to find these out.

**An uploaded zip is untrusted input.** Zip-slip paths, symlinks and zip bombs are all real
attacks on an upload endpoint. The simplest defence turned out to be not trusting the archive's
names at all: copy the few files I need to fixed names and count the bytes while copying. Tests
also caught a case that's easy to miss: damaged compressed data raises `zlib.error`, not
`BadZipFile`, so it needed its own handling to come back as a clean 400.

**Where the request body actually gets read.** The obvious place to enforce the upload limit is
the route, but Starlette reads the whole multipart body before the route runs, so the check has
to happen earlier, from the `Content-Length` header in middleware. Moving the processing into a
background job also taught me what that costs. `BackgroundTasks` isn't durable, two dispatches
of the same job need an atomic claim so they don't both run, and error messages need care,
because GDAL's include the server's file paths.

## Future scope

- A durable job queue with retries (Celery, RQ or arq), plus something that picks up jobs stuck
  in `PROCESSING`.
- Shared object storage such as S3, so separate worker machines can process uploads.
- PostGIS geometry columns with spatial indexes, and Alembic migrations.
- Streaming or batched reading with `pyogrio`'s Arrow API for files bigger than the size cap.
  Grouping features by UTM zone would also allow reprojecting each group in one vectorized call.
- More input formats: KMZ, GeoJSON, GeoPackage, several Shapefiles per zip, and mixed
  GeometryCollections.
- A choice of measurement method (UTM, equal-area or geodesic), and perimeters for polygons.
- Filtering, for example by status, and cursor-based pagination.
- Authentication and rate limiting, and optionally keeping the original uploads.
