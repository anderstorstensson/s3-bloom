"""Reproject OLCI swath data onto a regular projected grid via pyresample.

OLCI L2 products store data in a *swath* geometry: each pixel carries
its own ``(lat, lon)`` derived from the satellite's viewing angle, and
the array indices have no direct geographic meaning. This module turns
that into a regular grid with constant pixel spacing in a chosen
projection so the result is straightforward to overlay, composite and
write to GeoTIFF/NetCDF.

Why nearest-neighbour resampling
--------------------------------
Ocean-colour retrievals are discrete measurements, not samples of a
continuous field. Bilinear/cubic interpolation would create plausible-
looking but synthetic values across cloud edges and along the
coastline. Nearest-neighbour preserves the original pixel population at
the cost of a slightly blocky appearance.

Why a 1500 m radius of influence
--------------------------------
~5× the native ~300 m pixel pitch — large enough to fill the target
grid even when swath pixels are stretched at the edge of the field of
view, small enough that we never paint over a genuine NaN gap.
"""

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
    """Build a pyresample :class:`AreaDefinition` for the output grid.

    Parameters
    ----------
    bbox : BoundingBox
        AOI in WGS84 lon/lat. The corners are reprojected into the
        target CRS to derive the area extent in projected metres.
    output_config : OutputConfig
        Provides the target ``projection`` (CRS string) and
        ``resolution_m`` (pixel pitch in projected units, typically
        metres).

    Returns
    -------
    pyresample.geometry.AreaDefinition
        Target grid; its width/height are derived from extent ÷
        resolution. For the default ``swedish_west_coast`` AOI in
        EPSG:3035 at 300 m this is ~576 × 939 pixels.
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
    """Resample each requested dataset in *scene* onto *target_area*.

    Parameters
    ----------
    scene : satpy.Scene
        Loaded scene; masking has typically already been applied.
    target_area : pyresample.geometry.AreaDefinition
        Output grid built by :func:`create_target_area`.
    datasets : list of str
        Names of datasets to resample. Datasets missing from the scene
        after resampling are warned and skipped.

    Returns
    -------
    dict[str, xarray.DataArray]
        ``{dataset_name: resampled_dataarray}``. Each output carries
        ``x``/``y`` coordinate arrays in projected metres and a
        ``crs`` attribute set to the WKT of *target_area*.
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
    """Reproject a WGS84 bounding box into ``(x_min, y_min, x_max, y_max)``.

    The two diagonally opposite corners are transformed and then sorted,
    which handles projections in which transformed coordinates may run
    in either direction (``min``/``max`` order can flip).

    Returns
    -------
    tuple[float, float, float, float]
        Area extent in the target CRS, suitable for pyresample's
        ``area_extent`` argument.
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
    """Attach projected ``x``/``y`` coordinate arrays and CRS metadata.

    The y axis is built from ``area_extent[3]`` (north) down to
    ``area_extent[1]`` (south), matching the row-major top-down ordering
    that GeoTIFF and other raster formats expect.
    """
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
