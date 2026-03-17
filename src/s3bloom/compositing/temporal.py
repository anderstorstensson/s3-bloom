"""3-day rolling nanmean composites."""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import xarray as xr

from s3bloom.config import PipelineConfig
from s3bloom.export import export_dataset
from s3bloom.export.naming import composite_output_path
from s3bloom.metadata.provenance import create_composite_provenance
from s3bloom.processing.pipeline import PassResult

logger = logging.getLogger(__name__)


def create_composites(
    pass_results: list[PassResult],
    config: PipelineConfig,
) -> list[Path]:
    """Create rolling temporal composites from processed passes.

    Groups passes by date, then for each date builds a composite
    using a centered window of N days (nanmean).

    Returns list of output file paths.
    """
    window = config.output.composite_window_days

    if not pass_results:
        logger.warning("No pass results to composite")
        return []

    by_dataset = _group_by_dataset(pass_results)
    output_files: list[Path] = []

    for dataset_name, date_entries in by_dataset.items():
        dates = sorted(date_entries.keys())
        logger.info(
            "Compositing %s: %d unique dates, %d-day window",
            dataset_name,
            len(dates),
            window,
        )

        composite_dates = _compute_composite_dates(dates, window)

        for center_date in composite_dates:
            window_start = center_date - timedelta(days=window // 2)
            window_end = center_date + timedelta(days=window // 2)

            window_entries: list[tuple[xr.DataArray, str, str]] = []
            for d in dates:
                if window_start <= d <= window_end:
                    window_entries.extend(date_entries[d])

            if not window_entries:
                continue

            arrays = [entry[0] for entry in window_entries]
            satellites = [entry[1] for entry in window_entries]
            source_products = [entry[2] for entry in window_entries]

            composite = _nanmean_composite(arrays)

            center_dt = datetime(
                center_date.year,
                center_date.month,
                center_date.day,
                tzinfo=timezone.utc,
            )

            prov = create_composite_provenance(
                source_products=source_products,
                satellites=satellites,
                center_date=center_dt,
                dataset=dataset_name,
                masking_preset=config.masking.preset,
                masking_flags=config.masking.flags,
                projection=config.output.projection,
                resolution_m=config.output.resolution_m,
                composite_window_days=window,
            )

            for fmt in config.output.formats:
                out_path = composite_output_path(
                    base_dir=config.output.base_dir,
                    fmt=fmt,
                    dataset=dataset_name,
                    center_date=center_dt,
                    satellites=satellites,
                    window_days=window,
                )
                export_dataset(composite, out_path, fmt, prov, dataset_name)
                output_files.append(out_path)
                logger.info("Composite exported: %s", out_path)

    return output_files


def _group_by_dataset(
    pass_results: list[PassResult],
) -> dict[str, dict[date, list[tuple[xr.DataArray, str, str]]]]:
    """Group pass results by dataset name and date.

    Returns: {dataset_name: {date: [(data, satellite, product_name), ...]}}
    """
    grouped: dict[str, dict[date, list[tuple[xr.DataArray, str, str]]]] = defaultdict(
        lambda: defaultdict(list)
    )

    for pr in pass_results:
        d = pr.sensing_time.date()
        for ds_name, data in pr.datasets.items():
            grouped[ds_name][d].append(
                (data, pr.satellite, pr.product_path.name)
            )

    return grouped


def _compute_composite_dates(
    dates: list[date],
    window: int,
) -> list[date]:
    """Compute the set of dates for which to produce composites.

    Produces a composite for every date that has data within reach
    of the window.
    """
    if not dates:
        return []

    all_dates = set()
    start = min(dates)
    end = max(dates)
    current = start
    while current <= end:
        all_dates.add(current)
        current += timedelta(days=1)

    return sorted(all_dates)


def _nanmean_composite(arrays: list[xr.DataArray]) -> xr.DataArray:
    """Compute nanmean across a list of DataArrays."""
    if len(arrays) == 1:
        return arrays[0].copy()

    stacked = xr.concat(arrays, dim="pass_idx")
    composite = stacked.mean(dim="pass_idx", skipna=True)

    composite.attrs = {**arrays[0].attrs}
    return composite


