"""CF-1.8 compliant NetCDF exporter.

Goals (in priority order):

1. **Self-describing**: any tool that follows the CF conventions can
   open the file and plot it without out-of-band metadata.
2. **Provenance-rich**: every output traces back to the source product,
   the masking applied, and the pipeline version.
3. **Compact**: zlib level-4 compression strikes a good balance between
   size (~3-4× smaller than uncompressed) and read latency.

CRS handling
------------
rioxarray attaches non-serializable CRS objects as coordinates which
NetCDF4 cannot write. We strip those coordinates and instead store the
CRS as the WKT string ``crs_wkt`` global attribute, which CF readers
(xarray + ``decode_cf``, MetPy, etc.) understand.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import xarray as xr

from s3bloom.defaults import COLORMAP_SETTINGS
from s3bloom.metadata.provenance import Provenance

logger = logging.getLogger(__name__)


def export_netcdf(
    data: xr.DataArray,
    path: Path,
    provenance: Provenance,
    dataset_name: str,
) -> Path:
    """Write *data* as a CF-1.8 compliant NetCDF file.

    Parameters
    ----------
    data : xarray.DataArray
        Resampled array on the target grid.
    path : pathlib.Path
        Output path.
    provenance : Provenance
        Lineage record stored as ``s3bloom_*`` global attributes.
    dataset_name : str
        Logical dataset name; used to look up CF ``standard_name`` and
        ``long_name`` and to set the variable name inside the file.

    Returns
    -------
    pathlib.Path
        Output path.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    ds = _build_dataset(data, dataset_name, provenance)

    ds.to_netcdf(
        str(path),
        engine="netcdf4",
        encoding={
            dataset_name: {
                "dtype": "float32",
                "zlib": True,
                "complevel": 4,
                "_FillValue": np.nan,
            }
        },
    )

    logger.info("NetCDF exported: %s (%.1f MB)", path, path.stat().st_size / 1e6)
    return path


def _build_dataset(
    data: xr.DataArray,
    dataset_name: str,
    provenance: Provenance,
) -> xr.Dataset:
    """Wrap *data* in a CF-1.8 :class:`xarray.Dataset` with metadata.

    Adds ``long_name``, ``standard_name``, ``units``, ``valid_min``/
    ``valid_max``, axis attributes for the projected coordinates, and
    every provenance field as a global attribute.
    """
    color_cfg = COLORMAP_SETTINGS.get(dataset_name, {})

    da = data.copy()

    # rioxarray/satpy attach a CRS object as a non-dim coord; netcdf4
    # can't serialize Python objects. Drop everything except the real
    # spatial coords.
    for coord_name in list(da.coords):
        if coord_name not in da.dims and coord_name not in ("x", "y"):
            da = da.drop_vars(coord_name)

    da.name = dataset_name
    da.attrs = {
        "long_name": _long_name(dataset_name),
        "units": color_cfg.get("units", ""),
        "standard_name": _standard_name(dataset_name),
        "valid_min": float(color_cfg.get("vmin", 0)),
        "valid_max": float(color_cfg.get("vmax", 1000)),
    }

    ds = da.to_dataset()

    ds.attrs = {
        "Conventions": "CF-1.8",
        "title": f"s3bloom {dataset_name} — {provenance.source_product}",
        "institution": "s3bloom pipeline",
        "source": f"Sentinel-3 {provenance.satellite} OLCI L2",
        "history": (
            f"Created {datetime.now(tz=timezone.utc).isoformat()} "
            f"by s3bloom v{provenance.pipeline_version}"
        ),
        "references": "https://sentinels.copernicus.eu/web/sentinel/missions/sentinel-3",
        **provenance.to_netcdf_attrs(),
    }

    if "x" in ds.coords:
        ds.x.attrs = {
            "standard_name": "projection_x_coordinate",
            "units": "m",
            "axis": "X",
        }
    if "y" in ds.coords:
        ds.y.attrs = {
            "standard_name": "projection_y_coordinate",
            "units": "m",
            "axis": "Y",
        }

    # Store CRS as CF grid_mapping
    ds.attrs["crs_wkt"] = provenance.projection

    return ds


def _long_name(dataset_name: str) -> str:
    """CF ``long_name`` (human-readable description) for a dataset."""
    names = {
        "chl_nn": "Chlorophyll-a concentration (neural network)",
        "chl_oc4me": "Chlorophyll-a concentration (OC4Me algorithm)",
        "tsm_nn": "Total suspended matter (neural network)",
    }
    return names.get(dataset_name, dataset_name)


def _standard_name(dataset_name: str) -> str:
    """CF ``standard_name`` from the official table.

    Empty string is returned for datasets not in the lookup; CF allows
    omitting the attribute when no standard name fits.
    """
    names = {
        "chl_nn": "mass_concentration_of_chlorophyll_a_in_sea_water",
        "chl_oc4me": "mass_concentration_of_chlorophyll_a_in_sea_water",
        "tsm_nn": "mass_concentration_of_suspended_matter_in_sea_water",
    }
    return names.get(dataset_name, "")
