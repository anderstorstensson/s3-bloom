"""Output exporters for the three supported formats.

This package fans a single resampled :class:`xarray.DataArray` out into
its on-disk representations:

* :mod:`s3bloom.export.geotiff` — CRS-tagged GeoTIFF for GIS tools.
* :mod:`s3bloom.export.netcdf`  — CF-1.8 compliant NetCDF for analysis.
* :mod:`s3bloom.export.png`     — Cartopy-rendered map for quick look.

The unified entry point is :func:`export_dataset`; the format-specific
modules are imported lazily inside it so that callers requesting only
one format do not pay the import cost of the others (matplotlib +
cartopy in particular are slow to import).
"""

from __future__ import annotations

from pathlib import Path

import xarray as xr

from s3bloom.metadata.provenance import Provenance

# Mapping from logical format name (CLI/Config) to the file extension
# the corresponding exporter writes. Used by `export.naming` to derive
# output paths from format identifiers.
FORMAT_EXTENSIONS: dict[str, str] = {
    "geotiff": "tif",
    "netcdf": "nc",
    "png": "png",
}


def export_dataset(
    data: xr.DataArray,
    path: Path,
    fmt: str,
    provenance: Provenance,
    dataset_name: str,
) -> None:
    """Dispatch to the format-specific exporter.

    Parameters
    ----------
    data : xarray.DataArray
        Resampled, masked DataArray on the target grid.
    path : pathlib.Path
        Output file path. Parent directories are created if missing.
    fmt : {"geotiff", "netcdf", "png"}
        Format identifier (must be a key of :data:`FORMAT_EXTENSIONS`).
    provenance : Provenance
        Lineage metadata embedded into the output file.
    dataset_name : str
        Logical dataset name (e.g. ``"chl_nn"``); used by NetCDF and
        PNG exporters to look up CF metadata and colormap settings.

    Raises
    ------
    ValueError
        If ``fmt`` is not a recognised format.
    """
    from s3bloom.export.geotiff import export_geotiff
    from s3bloom.export.netcdf import export_netcdf
    from s3bloom.export.png import export_png

    path.parent.mkdir(parents=True, exist_ok=True)

    if fmt == "geotiff":
        export_geotiff(data, path, provenance)
    elif fmt == "netcdf":
        export_netcdf(data, path, provenance, dataset_name)
    elif fmt == "png":
        export_png(data, path, provenance, dataset_name)
    else:
        raise ValueError(f"Unknown export format: {fmt}")
