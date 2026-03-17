"""File naming conventions for pipeline outputs."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path


def pass_filename(
    *,
    dataset: str,
    sensing_time: datetime,
    satellite: str,
    ext: str,
) -> str:
    """Generate filename for a single-pass product.

    Format: s3bloom_{dataset}_pass_{datetime}_{satellite}.{ext}
    Example: s3bloom_chl_nn_pass_20240315T091500_S3A.tif
    """
    ts = sensing_time.strftime("%Y%m%dT%H%M%S")
    return f"s3bloom_{dataset}_pass_{ts}_{satellite}.{ext}"


def composite_filename(
    *,
    dataset: str,
    center_date: datetime,
    satellites: list[str],
    window_days: int,
    ext: str,
) -> str:
    """Generate filename for a composite product.

    Format: s3bloom_{dataset}_composite{N}d_{date}_{satellites}.{ext}
    Example: s3bloom_chl_nn_composite3d_20240315_S3A-S3B.tif
    """
    ds = center_date.strftime("%Y%m%d")
    sat_str = "-".join(sorted(set(satellites)))
    return f"s3bloom_{dataset}_composite{window_days}d_{ds}_{sat_str}.{ext}"


def pass_output_path(
    *,
    base_dir: Path,
    fmt: str,
    dataset: str,
    sensing_time: datetime,
    satellite: str,
) -> Path:
    """Full path for a single-pass output file."""
    ext_map = {"geotiff": "tif", "netcdf": "nc", "png": "png"}
    ext = ext_map[fmt]
    name = pass_filename(
        dataset=dataset,
        sensing_time=sensing_time,
        satellite=satellite,
        ext=ext,
    )
    return base_dir / "processed" / fmt / name


def composite_output_path(
    *,
    base_dir: Path,
    fmt: str,
    dataset: str,
    center_date: datetime,
    satellites: list[str],
    window_days: int,
) -> Path:
    """Full path for a composite output file."""
    ext_map = {"geotiff": "tif", "netcdf": "nc", "png": "png"}
    ext = ext_map[fmt]
    name = composite_filename(
        dataset=dataset,
        center_date=center_date,
        satellites=satellites,
        window_days=window_days,
        ext=ext,
    )
    return base_dir / "composites" / fmt / name
