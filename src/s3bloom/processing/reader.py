"""Load Sentinel-3 OLCI L2 products into satpy ``Scene`` objects.

A ``.SEN3`` product is a directory of NetCDF files plus a manifest XML.
This module passes the ``.nc`` files directly to satpy's ``olci_l2``
reader; the manifest is not used because satpy reconstructs the geometry
from the per-file metadata.

The satellite identifier (S3A/S3B) and sensing time are parsed from the
directory name rather than from file metadata. This is safe because
EUMETSAT guarantees the naming convention; doing it this way avoids
having to load the heavy NetCDFs just to read two scalars.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from satpy import Scene
from s3bloom._winpath import win_path

logger = logging.getLogger(__name__)

READER_NAME = "olci_l2"


def load_scene(
    product_path: Path,
    datasets: list[str],
) -> Scene:
    """Load a ``.SEN3`` product directory into a satpy :class:`~satpy.Scene`.

    Parameters
    ----------
    product_path : pathlib.Path
        Path to the extracted ``.SEN3`` directory.
    datasets : list of str
        Names of OLCI L2 datasets to load (e.g. ``["chl_nn"]``). Names
        not present in the product are warned about and skipped, matching
        EUMETSAT's recommendation to be permissive when collections evolve.
        The WQSF flag layer is always loaded in addition to the requested
        datasets — it is needed by the masking step.

    Returns
    -------
    satpy.Scene
        Scene with the requested datasets and ``wqsf`` available via
        ``scene[name]``.

    Raises
    ------
    ValueError
        If none of the requested datasets are present in the product.
    FileNotFoundError
        If the directory contains no ``.nc`` files.
    """
    logger.info("Loading scene from %s", product_path)

    filenames = _collect_filenames(product_path)
    scn = Scene(filenames=filenames, reader=READER_NAME)

    available = scn.available_dataset_names()
    to_load = []
    for ds in datasets:
        if ds in available:
            to_load.append(ds)
        else:
            logger.warning(
                "Dataset %r not available in %s. Available: %s",
                ds,
                product_path.name,
                ", ".join(sorted(available)[:20]),
            )

    if not to_load:
        raise ValueError(
            f"None of the requested datasets {datasets} are available "
            f"in {product_path.name}"
        )

    # WQSF is required for downstream quality masking, regardless of
    # which scientific datasets the caller asked for.
    to_load.append("wqsf")
    scn.load(to_load)

    logger.info("Loaded datasets: %s", list(scn.keys()))
    return scn


def extract_sensing_time(product_path: Path) -> datetime:
    """Parse sensing-start time from the ``.SEN3`` directory name.

    The EUMETSAT naming convention places a 15-character timestamp
    ``YYYYMMDDTHHMMSS`` as the fifth underscore-separated field, e.g.
    ``S3A_OL_2_WFR____20240315T091500_..._..._..._.SEN3``.

    Parameters
    ----------
    product_path : pathlib.Path
        Path whose ``.name`` is the standard product directory name.

    Returns
    -------
    datetime
        UTC-aware datetime of the sensing start.

    Raises
    ------
    ValueError
        If no token of the expected shape is present in the name.
    """
    name = product_path.name
    parts = name.split("_")

    for part in parts:
        if len(part) == 15 and part[0] == "2" and "T" in part:
            try:
                return datetime.strptime(part, "%Y%m%dT%H%M%S").replace(
                    tzinfo=timezone.utc
                )
            except ValueError:
                continue

    raise ValueError(f"Could not parse sensing time from product name: {name}")


def extract_satellite(product_path: Path) -> str:
    """Return the spacecraft tag (``"S3A"``/``"S3B"``) from the product name.

    Falls back to ``"S3X"`` for unrecognised prefixes so callers do not
    have to handle ``None``.
    """
    name = product_path.name
    if name.startswith("S3A"):
        return "S3A"
    if name.startswith("S3B"):
        return "S3B"
    return "S3X"


def _collect_filenames(product_path: Path) -> list[str]:
    """Return every ``*.nc`` file in *product_path* as a list of strings.

    satpy expects a flat file list and discovers which dataset is in
    which file from the file metadata. Files that satpy doesn't
    recognise (tie points, instrument data, etc.) produce harmless
    warnings, so we feed it the entire directory.
    """
    extended = win_path(product_path)
    nc_files = sorted(
        e.path for e in os.scandir(extended) if e.name.endswith(".nc")
    )
    filenames = nc_files

    if not filenames:
        raise FileNotFoundError(
            f"No .nc or manifest files found in {product_path}"
        )

    logger.debug("Collected %d files from %s", len(filenames), product_path.name)
    return filenames
