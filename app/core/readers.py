"""Read KML and Shapefile data into plain features with JSON-safe properties."""

import math
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

import numpy as np
import pyogrio
import shapely
from pyogrio import raw

from app.core.crs import WGS84

# LIBKML exposes these per-feature display settings as fields. They describe
# how Google Earth draws a placemark, not attributes of the feature itself.
KML_DISPLAY_FIELDS = frozenset(
    {"altitudeMode", "tessellate", "extrude", "visibility", "drawOrder", "icon"}
)


@dataclass(frozen=True)
class RawFeature:
    index: int  # global across layers, 0-based: layer order, then feature order
    layer: str
    geometry: shapely.Geometry | None
    properties: dict[str, Any]


@dataclass(frozen=True)
class ReadResult:
    file_crs: str  # display label, e.g. "EPSG:3857" or "WKT:<name>"
    crs_definition: str  # what transformers are built from: "EPSG:xxxx" or full WKT
    features: list[RawFeature]


def read_kml(path: Path) -> ReadResult:
    """Read every layer (KML Folder) of a KML file.

    KML coordinates are always WGS 84 lon/lat by specification, so the CRS is
    fixed to EPSG:4326 whatever the file's metadata says.
    """
    features = _read_layers(path, drop_fields=KML_DISPLAY_FIELDS)
    return ReadResult(file_crs=WGS84, crs_definition=WGS84, features=features)


def _read_layers(path: Path, drop_fields: frozenset[str] = frozenset()) -> list[RawFeature]:
    features: list[RawFeature] = []
    for layer_name, _geometry_type in pyogrio.list_layers(path):
        meta, _fids, wkb, field_data = raw.read(path, layer=layer_name)
        geometries = shapely.from_wkb(wkb)
        for row, geometry in enumerate(geometries):
            properties = {
                name: to_json_safe(values[row])
                for name, values in zip(meta["fields"], field_data, strict=True)
                if name not in drop_fields
            }
            features.append(
                RawFeature(
                    index=len(features),
                    layer=str(layer_name),
                    geometry=geometry,
                    properties=properties,
                )
            )
    return features


def to_json_safe(value: Any) -> Any:
    """Convert a field value from GDAL/numpy into something json.dumps accepts.

    NaN, infinities and missing timestamps become None; dates become ISO
    strings; bytes are decoded; anything unexpected falls back to str().
    """
    if value is None:
        return None
    if isinstance(value, np.datetime64):
        return None if np.isnat(value) else str(np.datetime_as_string(value))
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, bool | int | str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    return str(value)
