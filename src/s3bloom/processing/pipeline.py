"""Per-pass processing orchestrator: load → mask → resample → export.

This is the inner loop of the pipeline. Each ``.SEN3`` product passes
through this module exactly once. Failures here are caught at the CLI
level and recorded; processing of subsequent passes continues.

The :class:`PassResult` returned by :func:`process_single_pass` is the
hand-off to the compositing stage — it carries the resampled DataArrays
in memory (cheap: ~500k floats per pass) so we don't have to re-read
them from disk later.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import xarray as xr

from s3bloom.config import PipelineConfig
from s3bloom.export import export_dataset
from s3bloom.export.naming import pass_output_path
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
    """Outputs and metadata for one fully-processed satellite pass.

    Attributes
    ----------
    product_path : pathlib.Path
        Source ``.SEN3`` directory.
    satellite : str
        ``"S3A"``/``"S3B"``/``"S3X"``.
    sensing_time : datetime
        UTC sensing-start time (from the product directory name).
    datasets : dict[str, xarray.DataArray]
        Resampled DataArrays keyed by dataset name. Held in memory so
        the compositing stage can reuse them without re-loading.
    provenance : dict[str, Provenance]
        Per-dataset provenance records describing how each output was
        produced.
    output_files : list of pathlib.Path
        Every file written for this pass, across all formats.
    """

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
    """Run the full per-pass pipeline on one ``.SEN3`` product.

    Steps:

    1. Read sensing-time and satellite from the directory name.
    2. Load the scene with satpy (:mod:`s3bloom.processing.reader`).
    3. For every requested dataset, build a product-aware quality mask
       from WQSF and set bad pixels to NaN.
    4. Resample masked swaths onto the configured target grid.
    5. Build provenance records and export each resampled dataset to
       every requested format.

    Parameters
    ----------
    product_path : pathlib.Path
        Path to the extracted ``.SEN3`` directory.
    config : PipelineConfig
        Validated pipeline configuration.

    Returns
    -------
    PassResult
        In-memory results plus a list of paths that were written.
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

    # Mask is built per-dataset because the flag list is product-aware
    # (BAC vs AAC, OC4ME_FAIL vs OCNN_FAIL, etc.). Building it once per
    # product keeps memory usage flat.
    for ds_name in config.datasets:
        if ds_name in scene:
            mask = build_quality_mask(scene, config.masking, product=ds_name)
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
            masking_flags=config.masking.flags_for_product(ds_name),
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

            export_dataset(data_array, out_path, fmt, prov, ds_name)
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
