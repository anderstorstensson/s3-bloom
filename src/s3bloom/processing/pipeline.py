"""Single-pass processing orchestrator."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import xarray as xr

from s3bloom.config import PipelineConfig
from s3bloom.export.geotiff import export_geotiff
from s3bloom.export.naming import pass_output_path
from s3bloom.export.netcdf import export_netcdf
from s3bloom.export.png import export_png
from s3bloom.metadata.provenance import Provenance, create_pass_provenance
from s3bloom.processing.masking import apply_mask, build_quality_mask
from s3bloom.processing.reader import (
    extract_satellite,
    extract_sensing_time,
    load_scene,
)
from s3bloom.processing.resampling import create_target_area, resample_scene

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PassResult:
    """Result of processing a single satellite pass."""

    product_path: Path
    satellite: str
    sensing_time: datetime
    datasets: dict[str, xr.DataArray]
    provenance: dict[str, Provenance]
    output_files: list[Path]


def process_single_pass(
    product_path: Path,
    config: PipelineConfig,
) -> PassResult:
    """Process a single .SEN3 product through the full pipeline.

    Steps:
        1. Load scene with satpy
        2. Build quality mask from WQSF flags
        3. Apply mask (bad pixels -> NaN)
        4. Resample swath to regular grid
        5. Export to configured formats
    """
    satellite = extract_satellite(product_path)
    sensing_time = extract_sensing_time(product_path)

    logger.info(
        "Processing %s (%s, %s)",
        product_path.name,
        satellite,
        sensing_time.strftime("%Y-%m-%d %H:%M"),
    )

    scene = load_scene(product_path, config.datasets)

    mask = build_quality_mask(scene, config.masking)

    for ds_name in config.datasets:
        if ds_name in scene:
            scene[ds_name] = apply_mask(scene[ds_name], mask)

    target_area = create_target_area(config.bbox, config.output)
    resampled = resample_scene(scene, target_area, config.datasets)

    output_files: list[Path] = []
    provenance_records: dict[str, Provenance] = {}

    for ds_name, data_array in resampled.items():
        prov = create_pass_provenance(
            source_product=product_path.name,
            satellite=satellite,
            sensing_time=sensing_time,
            dataset=ds_name,
            masking_preset=config.masking.preset,
            masking_flags=config.masking.flags,
            projection=config.output.projection,
            resolution_m=config.output.resolution_m,
        )
        provenance_records[ds_name] = prov

        for fmt in config.output.formats:
            out_path = pass_output_path(
                base_dir=config.output.base_dir,
                fmt=fmt,
                dataset=ds_name,
                sensing_time=sensing_time,
                satellite=satellite,
            )

            _export_dataset(data_array, out_path, fmt, prov, ds_name)
            output_files.append(out_path)
            logger.info("Exported: %s", out_path)

    return PassResult(
        product_path=product_path,
        satellite=satellite,
        sensing_time=sensing_time,
        datasets=resampled,
        provenance=provenance_records,
        output_files=output_files,
    )


def _export_dataset(
    data: xr.DataArray,
    path: Path,
    fmt: str,
    provenance: Provenance,
    dataset_name: str,
) -> None:
    """Export a single dataset in the specified format."""
    path.parent.mkdir(parents=True, exist_ok=True)

    if fmt == "geotiff":
        export_geotiff(data, path, provenance)
    elif fmt == "netcdf":
        export_netcdf(data, path, provenance, dataset_name)
    elif fmt == "png":
        export_png(data, path, provenance, dataset_name)
    else:
        raise ValueError(f"Unknown export format: {fmt}")
