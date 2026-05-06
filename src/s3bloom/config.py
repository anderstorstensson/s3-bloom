"""Pydantic configuration models for the pipeline.

Every CLI invocation builds a :class:`PipelineConfig` (the top-level model)
which is then passed through every stage of the pipeline. Validation
happens once, at config-construction time, so downstream code can trust the
shape and value ranges of every field.

The model hierarchy is::

    PipelineConfig
    ├── BoundingBox       — geographic AOI in WGS84 lon/lat
    ├── TimePeriod        — sensing-time range
    ├── MaskingConfig     — strictness preset + optional custom flag list
    └── OutputConfig      — directories, formats, target grid, composite window

Adding a new tunable
--------------------
1. Add a default to :mod:`s3bloom.defaults`.
2. Add a field (with validators if needed) to the relevant model here.
3. Surface it on the CLI (:mod:`s3bloom.cli`) and wire it through
   ``_build_config``.
"""

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
    """Geographic bounding box in WGS84 (EPSG:4326) lon/lat degrees.

    All coordinates are in decimal degrees with the standard sign
    convention: east and north positive. The pipeline reprojects this to
    the target CRS in :mod:`s3bloom.processing.resampling`.
    """

    lon_min: float = Field(ge=-180, le=180)
    lat_min: float = Field(ge=-90, le=90)
    lon_max: float = Field(ge=-180, le=180)
    lat_max: float = Field(ge=-90, le=90)

    @model_validator(mode="after")
    def _validate_bounds(self) -> BoundingBox:
        """Reject zero-area or inverted bounding boxes."""
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
        """Parse a CLI-style bounding-box string.

        Parameters
        ----------
        value : str
            Either a preset name registered in
            :data:`s3bloom.defaults.BBOX_PRESETS`, or a 4-tuple in the
            form ``"lon_min,lat_min,lon_max,lat_max"``.

        Returns
        -------
        BoundingBox
            Validated bounding box.

        Raises
        ------
        ValueError
            If the string is neither a known preset nor parseable as four
            comma-separated floats.
        """
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
        """Return ``(lon_min, lat_min, lon_max, lat_max)``."""
        return (self.lon_min, self.lat_min, self.lon_max, self.lat_max)


class TimePeriod(BaseModel):
    """Inclusive sensing-time date range used for product search.

    Both bounds are interpreted in UTC; the discovery layer expands them
    to ``00:00:00.000Z`` (start) and ``23:59:59.999Z`` (end) when building
    the OData query.
    """

    start_date: date
    end_date: date

    @model_validator(mode="after")
    def _validate_dates(self) -> TimePeriod:
        """Reject inverted ranges."""
        if self.start_date > self.end_date:
            raise ValueError(
                f"start_date ({self.start_date}) must be on or before "
                f"end_date ({self.end_date})"
            )
        return self


class MaskingConfig(BaseModel):
    """How WQSF quality flags are turned into a per-pixel mask.

    Either a strictness preset (``strict`` / ``moderate`` / ``relaxed``) or
    a fully-explicit ``custom_flags`` list. When ``custom_flags`` is set it
    overrides the preset entirely and is applied uniformly to every
    requested dataset (no automatic BAC/AAC or per-product additions).
    """

    preset: str = DEFAULT_MASKING
    custom_flags: list[str] | None = None

    @field_validator("preset")
    @classmethod
    def _validate_preset(cls, v: str) -> str:
        """Reject unknown preset names with a helpful message."""
        if v not in MASKING_PRESETS:
            raise ValueError(
                f"Unknown masking preset: {v!r}. "
                f"Choose from: {', '.join(MASKING_PRESETS)}"
            )
        return v

    def flags_for_product(self, product: str) -> list[str]:
        """Return the flag list to apply to *product*.

        Combines common flags (strictness-dependent), BAC processing-chain
        flags (only for Open Water products), and the product's
        algorithm-failure flag. If ``custom_flags`` is set, that list is
        returned verbatim.

        Parameters
        ----------
        product : str
            Dataset name, e.g. ``"chl_nn"``, ``"chl_oc4me"``.

        Returns
        -------
        list of str
            Flag names to mask.
        """
        if self.custom_flags is not None:
            return list(self.custom_flags)
        return get_masking_flags(self.preset, product)

    @property
    def flags(self) -> list[str]:
        """Common flags for the current preset, ignoring product context.

        Provided mainly for diagnostics and tests. Prefer
        :meth:`flags_for_product` for actual masking — it adds the
        BAC/AAC and per-product flags that the EUMETSAT protocol requires.
        """
        if self.custom_flags is not None:
            return list(self.custom_flags)
        return list(MASKING_PRESETS[self.preset])


class OutputConfig(BaseModel):
    """Where outputs are written and in which formats / projection.

    The directory layout is always ``base_dir/{raw,processed,composites}``
    with one subdirectory per requested format. ``projection`` is any CRS
    that pyproj/pyresample understands (typically ``"EPSG:nnnn"``).
    """

    base_dir: Path = Field(default_factory=lambda: Path("data"))
    formats: list[str] = Field(default_factory=lambda: ["geotiff", "netcdf", "png"])
    projection: str = DEFAULT_PROJECTION
    resolution_m: int = DEFAULT_RESOLUTION
    composite_window_days: int = COMPOSITE_WINDOW_DAYS

    @field_validator("formats")
    @classmethod
    def _validate_formats(cls, v: list[str]) -> list[str]:
        """Reject unknown export formats early, before any I/O happens."""
        valid = {"geotiff", "netcdf", "png"}
        for fmt in v:
            if fmt not in valid:
                raise ValueError(
                    f"Unknown format: {fmt!r}. Choose from: {', '.join(valid)}"
                )
        return v

    @property
    def raw_dir(self) -> Path:
        """Directory holding the downloaded ``.SEN3`` products."""
        return self.base_dir / "raw"

    @property
    def processed_dir(self) -> Path:
        """Directory holding per-pass exports."""
        return self.base_dir / "processed"

    @property
    def composites_dir(self) -> Path:
        """Directory holding multi-day composite exports."""
        return self.base_dir / "composites"


class PipelineConfig(BaseModel):
    """Top-level, fully-validated pipeline configuration.

    Construct one with :func:`s3bloom.cli._build_config` from CLI flags or
    instantiate it directly when calling the pipeline as a library.
    """

    bbox: BoundingBox
    time_period: TimePeriod
    datasets: list[str] = Field(default_factory=lambda: list(DEFAULT_DATASETS))
    masking: MaskingConfig = Field(default_factory=MaskingConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)

    @field_validator("datasets")
    @classmethod
    def _validate_datasets(cls, v: list[str]) -> list[str]:
        """Sanity-check dataset names — the satpy reader expects this shape."""
        pattern = re.compile(r"^[a-z][a-z0-9_]*$")
        for ds in v:
            if not pattern.match(ds):
                raise ValueError(
                    f"Invalid dataset name: {ds!r}. "
                    f"Use lowercase alphanumeric with underscores."
                )
        return v

    def ensure_directories(self) -> None:
        """Create the full output directory tree.

        Idempotent — safe to call on every pipeline run. Creates
        ``raw/``, ``processed/{geotiff,netcdf,png}/`` and
        ``composites/{geotiff,netcdf,png}/`` even for formats that are not
        currently requested, so subsequent runs with different ``--formats``
        do not need to re-create directories.
        """
        for parent in [self.output.processed_dir, self.output.composites_dir]:
            for sub in ["geotiff", "netcdf", "png"]:
                (parent / sub).mkdir(parents=True, exist_ok=True)
        self.output.raw_dir.mkdir(parents=True, exist_ok=True)
