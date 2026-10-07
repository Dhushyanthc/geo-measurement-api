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
