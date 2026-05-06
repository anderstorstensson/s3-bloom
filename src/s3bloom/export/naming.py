"""Filesystem layout and filename conventions for pipeline outputs.

Filenames embed enough information to identify a file in isolation
(dataset, sensing time, satellite, composite window) so that downstream
tools can parse them without needing the provenance metadata. The
top-level layout is::

    {base_dir}/processed/{format}/    — single-pass exports
    {base_dir}/composites/{format}/   — multi-day composite exports

If you change a filename pattern, update the corresponding example in
``README.md`` and ``docs/workflow.md``.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from s3bloom.export import FORMAT_EXTENSIONS


def pass_filename(
    *,
    dataset: str,
    sensing_time: datetime,
    satellite: str,
    ext: str,
) -> str:
    """Single-pass output filename.

    Pattern
    -------
    ``s3bloom_{dataset}_pass_{YYYYMMDDTHHMMSS}_{satellite}.{ext}``

    Example: ``s3bloom_chl_nn_pass_20240315T091500_S3A.tif``.
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
    """Composite-output filename.

    Pattern
    -------
    ``s3bloom_{dataset}_composite{N}d_{YYYYMMDD}_{satellites}.{ext}``

    The satellites segment is the sorted, hyphen-joined unique set of
    spacecraft contributing to the composite (``S3A``, ``S3B``, or
    ``S3A-S3B``).

    Example: ``s3bloom_chl_nn_composite3d_20240315_S3A-S3B.tif``.
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
    """Compose the full output path for a single-pass file.

    The path is ``{base_dir}/processed/{fmt}/{filename}``.
    """
    ext = FORMAT_EXTENSIONS[fmt]
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
    """Compose the full output path for a composite file.

    The path is ``{base_dir}/composites/{fmt}/{filename}``.
    """
    ext = FORMAT_EXTENSIONS[fmt]
    name = composite_filename(
        dataset=dataset,
        center_date=center_date,
        satellites=satellites,
        window_days=window_days,
        ext=ext,
    )
    return base_dir / "composites" / fmt / name
