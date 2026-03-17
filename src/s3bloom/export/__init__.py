"""Export to GeoTIFF, NetCDF, and PNG formats."""

from __future__ import annotations

from pathlib import Path

import xarray as xr

from s3bloom.metadata.provenance import Provenance

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
    """Export a dataset in the specified format."""
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
