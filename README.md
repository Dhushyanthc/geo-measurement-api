# Geospatial File Measurement API

A FastAPI service that accepts a KML file or a zipped Shapefile, extracts its features, and returns
per-feature measurements: area for polygons and length for lines, computed in a projected CRS
rather than in degrees.

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

### File processing

1. **Read.** `pyogrio` (GDAL) lists the layers in the file and reads each one as raw WKB
   geometries plus attribute columns; `shapely` decodes the geometries.
   - KML: every `<Folder>` is a layer, and placemarks outside any folder form a layer named
     after the file. GDAL opens KML with its LIBKML driver, so `<ExtendedData>` values
     (`<Data>` and `<SchemaData>`) become properties. LIBKML's display settings (`tessellate`,
     `visibility`, `icon`, ...) are dropped because they are not attributes.
   - The CRS of a KML file is always EPSG:4326, as the KML specification requires.
   - Shapefile: the zip must hold exactly one `.shp` with its `.shx`, `.dbf` and `.prj`
     (`.cpg` optional) in the same folder. The CRS comes from the `.prj`, including the ESRI
     WKT that ArcGIS writes; a missing or unreadable `.prj` is rejected rather than guessed.
   - Zip safety: entry count and total uncompressed size are capped, paths with `..`, absolute
     paths and symlinks are rejected, and the shapefile parts are copied to fixed names
     (`data.shp`, ...) while counting the real bytes written, so nothing in the archive
     chooses where files land.
2. **Index.** Features are numbered globally from 0, in layer order and then feature order.
   A placemark without geometry is kept, so indices always match the source file.
3. **Clean properties.** Attribute values are made JSON-safe: numpy scalars become Python
   numbers, NaN becomes `null`, timestamps become ISO 8601 strings, bytes are decoded.
4. **Measure.** Each geometry is measured on its own (see `app/core/measure.py`): polygons
   get an area in m², lines get a length in m, points need no measurement. A geometry that
   cannot be measured gets a status and a reason instead of failing the file.
