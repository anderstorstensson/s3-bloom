"""Multi-day rolling-window ``nanmean`` composites.

Single OLCI passes typically lose 60-90 % of their ocean pixels to
clouds with strict masking. This module fills the gaps by averaging
multiple passes over a centred N-day window. ``nanmean`` is used so
that a pixel which is cloudy in some passes but clear in others
contributes only its valid observations.

Why nanmean
-----------
* Simple and reproducible — no weights, no priors.
* Cloud-robust by construction.
* Composes naturally over S3A + S3B passes on the same day.

The window length comes from ``config.output.composite_window_days``.
A composite is produced for every calendar day between the earliest
and latest pass, even days without their own pass — neighbouring-day
passes keep coverage continuous.
"""

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
    """Build rolling-window composites for every dataset in *pass_results*.

    For each dataset and each calendar day between the first and last
    pass, the function:

    1. Selects every pass within ``±window/2`` days of the centre date.
    2. Stacks them along a new ``pass_idx`` dimension.
    3. Computes ``nanmean`` across that dimension.
    4. Writes the composite in every requested output format.

    Parameters
    ----------
    pass_results : list of PassResult
        Output of :func:`s3bloom.processing.pipeline.process_single_pass`
        for every successfully-processed pass.
    config : PipelineConfig
        Pipeline configuration; supplies the window size, output
        directories, masking metadata for provenance, and target CRS.

    Returns
    -------
    list[pathlib.Path]
        Every composite file written. May be empty if no passes were
        provided, but the function never raises in that case.
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

            center_dt = datetime(
                center_date.year,
                center_date.month,
                center_date.day,
                tzinfo=timezone.utc,
            )

            if not config.output.overwrite:
                expected = [
                    composite_output_path(
                        base_dir=config.output.base_dir,
                        fmt=fmt,
                        dataset=dataset_name,
                        center_date=center_dt,
                        satellites=satellites,
                        window_days=window,
                    )
                    for fmt in config.output.formats
                ]
                if expected and all(p.exists() for p in expected):
                    logger.info(
                        "Skipping composite %s %s (outputs exist)",
                        dataset_name,
                        center_date,
                    )
                    continue

            composite = _nanmean_composite(arrays)

            prov = create_composite_provenance(
                source_products=source_products,
                satellites=satellites,
                center_date=center_dt,
                dataset=dataset_name,
                masking_preset=config.masking.preset,
                masking_flags=config.masking.flags_for_product(dataset_name),
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
    """Reshape pass results by dataset and sensing date.

    Returns a nested dict::

        {
            dataset_name: {
                date: [(data_array, satellite, source_product_name), ...]
            }
        }

    Two passes from the same day on different spacecraft (S3A + S3B)
    naturally end up in the same date bucket and contribute equally to
    every composite that covers that day.
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
    """Return one centre date per calendar day in the observed range.

    Even days with no pass of their own get an entry — passes from
    neighbouring days within the rolling window can still produce a
    valid composite.

    The ``window`` parameter is currently unused (the date grid is
    independent of the window length); it is kept on the signature so
    a future change can compute a different stride without breaking
    callers.
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
    """Average a list of DataArrays along a new dim, ignoring NaN.

    Single-array input is short-circuited to a copy so the trivial
    one-pass-window case stays cheap. The result inherits the first
    array's attributes (CRS, units, …); these are identical across
    inputs because every array came from the same target grid.
    """
    if len(arrays) == 1:
        return arrays[0].copy()

    stacked = xr.concat(arrays, dim="pass_idx")
    composite = stacked.mean(dim="pass_idx", skipna=True)

    composite.attrs = {**arrays[0].attrs}
    return composite


