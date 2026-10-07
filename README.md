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
