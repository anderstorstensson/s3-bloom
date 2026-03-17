"""CF-compliant NetCDF export via xarray."""

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
    """Export a DataArray as a CF-compliant NetCDF file."""
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
    """Build a CF-compliant xr.Dataset from a DataArray."""
    color_cfg = COLORMAP_SETTINGS.get(dataset_name, {})

    da = data.copy()

    # Drop non-serializable coords added by rioxarray/satpy
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
    names = {
        "chl_nn": "Chlorophyll-a concentration (neural network)",
        "chl_oc4me": "Chlorophyll-a concentration (OC4Me algorithm)",
        "tsm_nn": "Total suspended matter (neural network)",
    }
    return names.get(dataset_name, dataset_name)


def _standard_name(dataset_name: str) -> str:
    names = {
        "chl_nn": "mass_concentration_of_chlorophyll_a_in_sea_water",
        "chl_oc4me": "mass_concentration_of_chlorophyll_a_in_sea_water",
        "tsm_nn": "mass_concentration_of_suspended_matter_in_sea_water",
    }
    return names.get(dataset_name, "")
