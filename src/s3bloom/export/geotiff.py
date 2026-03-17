"""GeoTIFF export via rioxarray."""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import rioxarray  # noqa: F401 -- registers accessor
import xarray as xr
from pyproj import CRS

from s3bloom.metadata.provenance import Provenance

logger = logging.getLogger(__name__)


def export_geotiff(
    data: xr.DataArray,
    path: Path,
    provenance: Provenance,
) -> Path:
    """Export a DataArray as a GeoTIFF with CRS and provenance tags."""
    path.parent.mkdir(parents=True, exist_ok=True)

    data_2d = _ensure_2d(data)

    crs = CRS.from_user_input(provenance.projection)
    data_2d = data_2d.rio.write_crs(crs)

    if data_2d.rio.nodata is None:
        data_2d = data_2d.rio.write_nodata(np.nan)

    tags = {f"s3bloom_{k}": v for k, v in provenance.to_dict().items() if v}

    data_2d.rio.to_raster(
        str(path),
        driver="GTiff",
        compress="deflate",
        tags=tags,
    )

    logger.info("GeoTIFF exported: %s (%.1f MB)", path, path.stat().st_size / 1e6)
    return path


def _ensure_2d(data: xr.DataArray) -> xr.DataArray:
    """Ensure the DataArray is 2D with (y, x) dimensions."""
    if len(data.dims) == 2:
        return data
    if len(data.dims) == 3 and data.shape[0] == 1:
        return data.isel({data.dims[0]: 0})
    raise ValueError(
        f"Expected 2D array, got shape {data.shape} with dims {data.dims}"
    )
