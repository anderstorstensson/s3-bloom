"""Swath-to-grid resampling via pyresample."""

from __future__ import annotations

import logging

import numpy as np
import xarray as xr
from pyresample import create_area_def
from pyresample.geometry import AreaDefinition
from satpy import Scene

from s3bloom.config import BoundingBox, OutputConfig

logger = logging.getLogger(__name__)


def create_target_area(
    bbox: BoundingBox,
    output_config: OutputConfig,
) -> AreaDefinition:
    """Create a pyresample AreaDefinition for the target grid.

    Uses the configured projection and resolution, bounded by the
    given bounding box.
    """
    area = create_area_def(
        area_id="s3bloom_target",
        projection=output_config.projection,
        area_extent=_bbox_to_extent(bbox, output_config.projection),
        resolution=output_config.resolution_m,
        description="s3bloom target area",
    )

    logger.info(
        "Target area: %dx%d pixels, resolution=%dm, projection=%s",
        area.width,
        area.height,
        output_config.resolution_m,
        output_config.projection,
    )
    return area


def resample_scene(
    scene: Scene,
    target_area: AreaDefinition,
    datasets: list[str],
) -> dict[str, xr.DataArray]:
    """Resample swath datasets to the target grid.

    Uses nearest-neighbor resampling which is appropriate for
    the discrete nature of ocean color products.

    Returns:
        Dict mapping dataset name -> resampled xr.DataArray with CRS info.
    """
    logger.info("Resampling %d datasets to target grid", len(datasets))

    resampled_scene = scene.resample(
        target_area,
        resampler="nearest",
        radius_of_influence=1500,
        fill_value=np.nan,
    )

    results = {}
    for ds_name in datasets:
        if ds_name in resampled_scene:
            da = resampled_scene[ds_name]
            results[ds_name] = _add_spatial_coords(da, target_area)
            logger.debug("Resampled %s: shape=%s", ds_name, da.shape)
        else:
            logger.warning("Dataset %s not found after resampling", ds_name)

    return results


def _bbox_to_extent(
    bbox: BoundingBox,
    projection: str,
) -> tuple[float, float, float, float]:
    """Convert lon/lat bounding box to projected coordinates.

    For EPSG:3035 and similar projections, we transform the
    corner points to get the area extent.
    """
    from pyproj import Transformer

    transformer = Transformer.from_crs("EPSG:4326", projection, always_xy=True)

    x_min, y_min = transformer.transform(bbox.lon_min, bbox.lat_min)
    x_max, y_max = transformer.transform(bbox.lon_max, bbox.lat_max)

    return (
        min(x_min, x_max),
        min(y_min, y_max),
        max(x_min, x_max),
        max(y_min, y_max),
    )


def _add_spatial_coords(
    data: xr.DataArray,
    area: AreaDefinition,
) -> xr.DataArray:
    """Add x/y coordinate arrays and CRS info to a DataArray."""
    if "y" not in data.dims or "x" not in data.dims:
        if len(data.dims) == 2:
            data = data.rename({data.dims[0]: "y", data.dims[1]: "x"})

    x_coords = np.linspace(
        area.area_extent[0],
        area.area_extent[2],
        area.width,
    )
    y_coords = np.linspace(
        area.area_extent[3],
        area.area_extent[1],
        area.height,
    )

    data = data.assign_coords(x=("x", x_coords), y=("y", y_coords))
    data.attrs["crs"] = area.crs.to_wkt()
    data.attrs["area_id"] = area.area_id

    return data
