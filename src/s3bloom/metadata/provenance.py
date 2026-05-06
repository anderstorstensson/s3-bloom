"""Immutable provenance records embedded in every output file.

A :class:`Provenance` instance is created once per output (per pass *and*
per dataset, or per composite) and travels through the export step,
where it is serialised into:

* TIFF tags in GeoTIFF outputs (prefix ``s3bloom_``);
* global attributes in NetCDF outputs (same prefix);
* the title text of PNG outputs.

The dataclass is ``frozen=True`` to make accidental late-stage mutation
impossible — once a record is created it represents one immutable
description of the output's lineage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(frozen=True)
class Provenance:
    """Immutable record of how one output product was produced.

    Single-pass outputs use the first nine fields; composite outputs
    additionally fill ``source_products``, ``composite_window_days`` and
    ``pass_count``.

    Attributes
    ----------
    source_product : str
        Source ``.SEN3`` directory name (passes) or a synthetic
        ``composite_YYYYMMDD`` identifier (composites).
    satellite : str
        ``"S3A"`` / ``"S3B"`` for passes; for composites the sorted
        hyphen-joined unique set, e.g. ``"S3A-S3B"``.
    sensing_time : datetime
        Pass sensing-start time, or composite centre date.
    dataset : str
        Logical dataset name (``"chl_nn"`` etc.).
    masking_preset : str
        Strictness preset name (``strict``/``moderate``/``relaxed``)
        or ``"custom"`` if explicit flags were used.
    masking_flags : tuple of str
        Exact flags applied — captured at the time of processing so
        the record reflects the actual masking, not just the preset.
    projection : str
        Target CRS as a string (``"EPSG:3035"`` or WKT).
    resolution_m : int
        Target pixel pitch in projected metres.
    pipeline_version : str
        :data:`s3bloom.__version__` at the time of creation.
    created_at : datetime
        UTC timestamp of record creation. Defaults to "now".
    source_products : tuple of str
        For composites, the contributing source product names.
    composite_window_days : int, optional
        Window length for composites; ``None`` for single passes.
    pass_count : int, optional
        Number of passes contributing to a composite; ``None`` for
        single passes.
    """

    source_product: str
    satellite: str
    sensing_time: datetime
    dataset: str
    masking_preset: str
    masking_flags: tuple[str, ...]
    projection: str
    resolution_m: int
    pipeline_version: str
    created_at: datetime = field(
        default_factory=lambda: datetime.now(tz=timezone.utc)
    )
    source_products: tuple[str, ...] = ()
    composite_window_days: int | None = None
    pass_count: int | None = None

    def to_dict(self) -> dict:
        """Flatten to a string-valued dict for tag/attribute writers.

        Datetimes are serialised as ISO-8601, sequences as
        comma-separated strings, and ``None`` becomes ``""``.
        """
        return {
            "source_product": self.source_product,
            "satellite": self.satellite,
            "sensing_time": self.sensing_time.isoformat(),
            "dataset": self.dataset,
            "masking_preset": self.masking_preset,
            "masking_flags": ",".join(self.masking_flags),
            "projection": self.projection,
            "resolution_m": self.resolution_m,
            "pipeline_version": self.pipeline_version,
            "created_at": self.created_at.isoformat(),
            "source_products": ",".join(self.source_products),
            "composite_window_days": (
                str(self.composite_window_days)
                if self.composite_window_days is not None
                else ""
            ),
            "pass_count": (
                str(self.pass_count) if self.pass_count is not None else ""
            ),
        }

    def to_netcdf_attrs(self) -> dict[str, str]:
        """Return attributes suitable for NetCDF global attrs.

        Each non-empty field is prefixed with ``s3bloom_`` to avoid
        clashing with CF/CDM standard attribute names.
        """
        return {
            f"s3bloom_{k}": str(v) for k, v in self.to_dict().items() if v
        }


def create_pass_provenance(
    *,
    source_product: str,
    satellite: str,
    sensing_time: datetime,
    dataset: str,
    masking_preset: str,
    masking_flags: list[str],
    projection: str,
    resolution_m: int,
) -> Provenance:
    """Build a :class:`Provenance` record for a single satellite pass."""
    from s3bloom import __version__

    return Provenance(
        source_product=source_product,
        satellite=satellite,
        sensing_time=sensing_time,
        dataset=dataset,
        masking_preset=masking_preset,
        masking_flags=tuple(masking_flags),
        projection=projection,
        resolution_m=resolution_m,
        pipeline_version=__version__,
    )


def create_composite_provenance(
    *,
    source_products: list[str],
    satellites: list[str],
    center_date: datetime,
    dataset: str,
    masking_preset: str,
    masking_flags: list[str],
    projection: str,
    resolution_m: int,
    composite_window_days: int,
) -> Provenance:
    """Build a :class:`Provenance` record for a multi-day composite.

    The synthetic ``source_product`` is ``composite_YYYYMMDD`` where
    the date is the composite centre; the actual source product names
    are stored in ``source_products``.
    """
    from s3bloom import __version__

    unique_sats = sorted(set(satellites))
    return Provenance(
        source_product=f"composite_{center_date.strftime('%Y%m%d')}",
        satellite="-".join(unique_sats),
        sensing_time=center_date,
        dataset=dataset,
        masking_preset=masking_preset,
        masking_flags=tuple(masking_flags),
        projection=projection,
        resolution_m=resolution_m,
        pipeline_version=__version__,
        source_products=tuple(source_products),
        composite_window_days=composite_window_days,
        pass_count=len(source_products),
    )
