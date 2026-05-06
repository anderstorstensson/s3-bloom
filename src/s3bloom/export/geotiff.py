"""GeoTIFF exporter — CRS-tagged raster with provenance in TIFF tags.

The output is intended to drop directly into QGIS, ArcGIS, Google Earth
Engine, etc. without any additional sidecar files. Provenance fields
are written as TIFF tags prefixed ``s3bloom_`` so a reader that does
not understand them can ignore them safely.
"""

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
    """Write *data* to *path* as a deflate-compressed GeoTIFF.

    Parameters
    ----------
    data : xarray.DataArray
        2-D array (or ``(1, y, x)``) on the target grid. CRS is read
        from ``provenance.projection``.
    path : pathlib.Path
        Output path; parent directories are created if missing.
    provenance : Provenance
        Lineage record. Every truthy field is written as an
        ``s3bloom_<key>`` TIFF tag.

    Returns
    -------
    pathlib.Path
        The path that was written.
    """
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
    """Reduce a DataArray to a 2-D ``(y, x)`` raster.

    GeoTIFF is fundamentally a 2-D raster format. satpy / xarray
    occasionally hand us a ``(1, y, x)`` array (a singleton time or
    band dim from concatenation); we squeeze it. Anything else is an
    error the caller needs to fix.
    """
    if len(data.dims) == 2:
        return data
    if len(data.dims) == 3 and data.shape[0] == 1:
        return data.isel({data.dims[0]: 0})
    raise ValueError(
        f"Expected 2D array, got shape {data.shape} with dims {data.dims}"
    )
