"""Immutable provenance records for tracking data lineage."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(frozen=True)
class Provenance:
    """Immutable record of how a data product was created."""

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
        """Return attributes suitable for NetCDF global attrs."""
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
