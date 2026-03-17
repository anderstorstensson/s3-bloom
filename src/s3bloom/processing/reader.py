"""Load Sentinel-3 OLCI L2 products using satpy."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from satpy import Scene

logger = logging.getLogger(__name__)

READER_NAME = "olci_l2"


def load_scene(
    product_path: Path,
    datasets: list[str],
) -> Scene:
    """Load a .SEN3 product directory into a satpy Scene.

    Args:
        product_path: Path to the .SEN3 directory.
        datasets: Dataset names to load (e.g. ['chl_nn']).

    Returns:
        satpy Scene with requested datasets loaded.
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

    to_load.append("wqsf")
    scn.load(to_load)

    logger.info("Loaded datasets: %s", list(scn.keys()))
    return scn


def extract_sensing_time(product_path: Path) -> datetime:
    """Extract sensing start time from the product directory name.

    Filename format:
    S3A_OL_2_WFR____20240315T091500_..._..._..._.SEN3
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

    logger.warning("Could not parse sensing time from %s, using now", name)
    return datetime.now(tz=timezone.utc)


def extract_satellite(product_path: Path) -> str:
    """Extract satellite identifier from product directory name."""
    name = product_path.name
    if name.startswith("S3A"):
        return "S3A"
    if name.startswith("S3B"):
        return "S3B"
    return "S3X"


def _collect_filenames(product_path: Path) -> list[str]:
    """Collect all .nc files from a .SEN3 directory for satpy."""
    nc_files = sorted(product_path.glob("*.nc"))
    xml_files = sorted(product_path.glob("xfdumanifest.xml"))

    filenames = [str(f) for f in nc_files]

    if not filenames:
        raise FileNotFoundError(
            f"No .nc or manifest files found in {product_path}"
        )

    logger.debug("Collected %d files from %s", len(filenames), product_path.name)
    return filenames
