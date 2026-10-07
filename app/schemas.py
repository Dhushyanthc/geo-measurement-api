"""Response bodies of the API."""

from typing import Any

from pydantic import BaseModel

from app.core.measure import MeasureStatus
from app.models import Feature, File, FileStatus


class FileOut(BaseModel):
    id: str
    filename: str
    feature_count: int
    crs: str | None
    status: FileStatus
    error: str | None

    @classmethod
    def from_model(cls, file: File) -> "FileOut":
        return cls(
            id=file.id,
            filename=file.filename,
            feature_count=file.feature_count,
            crs=file.crs,
            status=file.status,
            error=file.error_message,
        )


class MeasurementOut(BaseModel):
    status: MeasureStatus
    area_m2: float | None
    length_m: float | None
    crs: str | None  # the UTM zone used, e.g. "EPSG:32643"
    reason: str | None
    warnings: list[str]


class FeatureOut(BaseModel):
    index: int
    layer: str
    geometry_type: str | None
    source_crs: str
    properties: dict[str, Any]
    geometry: dict[str, Any] | None  # GeoJSON in EPSG:4326
    measurement: MeasurementOut

    @classmethod
    def from_model(cls, feature: Feature) -> "FeatureOut":
        return cls(
            index=feature.idx,
            layer=feature.layer,
            geometry_type=feature.geometry_type,
            source_crs=feature.source_crs,
            properties=feature.properties,
            geometry=feature.geometry,
            measurement=MeasurementOut(
                status=feature.status,
                area_m2=feature.area_m2,
                length_m=feature.length_m,
                crs=feature.measurement_crs,
                reason=feature.reason,
                warnings=feature.warnings,
            ),
        )


class MeasurementsPage(BaseModel):
    file_id: str
    total: int
    limit: int
    offset: int
    features: list[FeatureOut]
