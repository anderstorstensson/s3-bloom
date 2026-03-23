"""Pydantic configuration models for the pipeline."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from pydantic import BaseModel, Field, field_validator, model_validator

from s3bloom.defaults import (
    BBOX_PRESETS,
    COMPOSITE_WINDOW_DAYS,
    DEFAULT_DATASETS,
    DEFAULT_MASKING,
    DEFAULT_PROJECTION,
    DEFAULT_RESOLUTION,
    MASKING_PRESETS,
    get_masking_flags,
)


class BoundingBox(BaseModel):
    """Geographic bounding box (lon/lat, WGS84)."""

    lon_min: float = Field(ge=-180, le=180)
    lat_min: float = Field(ge=-90, le=90)
    lon_max: float = Field(ge=-180, le=180)
    lat_max: float = Field(ge=-90, le=90)

    @model_validator(mode="after")
    def _validate_bounds(self) -> BoundingBox:
        if self.lon_min >= self.lon_max:
            raise ValueError(
                f"lon_min ({self.lon_min}) must be less than "
                f"lon_max ({self.lon_max})"
            )
        if self.lat_min >= self.lat_max:
            raise ValueError(
                f"lat_min ({self.lat_min}) must be less than "
                f"lat_max ({self.lat_max})"
            )
        return self

    @classmethod
    def from_string(cls, value: str) -> BoundingBox:
        """Parse from 'lon_min,lat_min,lon_max,lat_max' or a preset name."""
        if value in BBOX_PRESETS:
            coords = BBOX_PRESETS[value]
            return cls(
                lon_min=coords[0],
                lat_min=coords[1],
                lon_max=coords[2],
                lat_max=coords[3],
            )
        raw_parts = value.split(",")
        if len(raw_parts) != 4:
            raise ValueError(
                f"Expected 4 comma-separated values or a preset name "
                f"({', '.join(BBOX_PRESETS)}), got: {value!r}"
            )
        try:
            parts = [float(p.strip()) for p in raw_parts]
        except ValueError:
            raise ValueError(
                f"Expected 4 comma-separated values or a preset name "
                f"({', '.join(BBOX_PRESETS)}), got: {value!r}"
            ) from None
        return cls(
            lon_min=parts[0],
            lat_min=parts[1],
            lon_max=parts[2],
            lat_max=parts[3],
        )

    def as_tuple(self) -> tuple[float, float, float, float]:
        return (self.lon_min, self.lat_min, self.lon_max, self.lat_max)


class TimePeriod(BaseModel):
    """Time range for product search."""

    start_date: date
    end_date: date

    @model_validator(mode="after")
    def _validate_dates(self) -> TimePeriod:
        if self.start_date > self.end_date:
            raise ValueError(
                f"start_date ({self.start_date}) must be on or before "
                f"end_date ({self.end_date})"
            )
        return self


class MaskingConfig(BaseModel):
    """Quality/cloud masking configuration."""

    preset: str = DEFAULT_MASKING
    custom_flags: list[str] | None = None

    @field_validator("preset")
    @classmethod
    def _validate_preset(cls, v: str) -> str:
        if v not in MASKING_PRESETS:
            raise ValueError(
                f"Unknown masking preset: {v!r}. "
                f"Choose from: {', '.join(MASKING_PRESETS)}"
            )
        return v

    def flags_for_product(self, product: str) -> list[str]:
        """Return the correct flags for a specific product.

        Combines common flags (strictness-dependent), processing-chain
        flags (BAC only), and per-product algorithm failure flags.
        """
        if self.custom_flags is not None:
            return list(self.custom_flags)
        return get_masking_flags(self.preset, product)

    @property
    def flags(self) -> list[str]:
        """Common flags for the current preset (product-agnostic).

        Prefer ``flags_for_product(name)`` for product-aware masking.
        """
        if self.custom_flags is not None:
            return list(self.custom_flags)
        return list(MASKING_PRESETS[self.preset])


class OutputConfig(BaseModel):
    """Output directory and format settings."""

    base_dir: Path = Field(default_factory=lambda: Path("data"))
    formats: list[str] = Field(default_factory=lambda: ["geotiff", "netcdf", "png"])
    projection: str = DEFAULT_PROJECTION
    resolution_m: int = DEFAULT_RESOLUTION
    composite_window_days: int = COMPOSITE_WINDOW_DAYS

    @field_validator("formats")
    @classmethod
    def _validate_formats(cls, v: list[str]) -> list[str]:
        valid = {"geotiff", "netcdf", "png"}
        for fmt in v:
            if fmt not in valid:
                raise ValueError(
                    f"Unknown format: {fmt!r}. Choose from: {', '.join(valid)}"
                )
        return v

    @property
    def raw_dir(self) -> Path:
        return self.base_dir / "raw"

    @property
    def processed_dir(self) -> Path:
        return self.base_dir / "processed"

    @property
    def composites_dir(self) -> Path:
        return self.base_dir / "composites"


class PipelineConfig(BaseModel):
    """Top-level pipeline configuration."""

    bbox: BoundingBox
    time_period: TimePeriod
    datasets: list[str] = Field(default_factory=lambda: list(DEFAULT_DATASETS))
    masking: MaskingConfig = Field(default_factory=MaskingConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)

    @field_validator("datasets")
    @classmethod
    def _validate_datasets(cls, v: list[str]) -> list[str]:
        pattern = re.compile(r"^[a-z][a-z0-9_]*$")
        for ds in v:
            if not pattern.match(ds):
                raise ValueError(
                    f"Invalid dataset name: {ds!r}. "
                    f"Use lowercase alphanumeric with underscores."
                )
        return v

    def ensure_directories(self) -> None:
        """Create all output directories."""
        for parent in [self.output.processed_dir, self.output.composites_dir]:
            for sub in ["geotiff", "netcdf", "png"]:
                (parent / sub).mkdir(parents=True, exist_ok=True)
        self.output.raw_dir.mkdir(parents=True, exist_ok=True)
