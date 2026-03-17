"""PNG map export with coastlines and colorbar."""

from __future__ import annotations

import logging
from pathlib import Path

import matplotlib
import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import numpy as np
import xarray as xr

matplotlib.use("Agg")

import cmocean  # noqa: E402, F401 -- registers colormaps

from s3bloom.defaults import COLORMAP_SETTINGS
from s3bloom.metadata.provenance import Provenance

logger = logging.getLogger(__name__)


def export_png(
    data: xr.DataArray,
    path: Path,
    provenance: Provenance,
    dataset_name: str,
) -> Path:
    """Export a DataArray as a PNG map with coastlines and colorbar."""
    path.parent.mkdir(parents=True, exist_ok=True)

    color_cfg = COLORMAP_SETTINGS.get(
        dataset_name,
        {"cmap": "viridis", "vmin": 0, "vmax": 100, "log_scale": False, "label": dataset_name},
    )

    values = data.values if hasattr(data, "values") else np.array(data)
    if hasattr(values, "compute"):
        values = values.compute()
    values = np.asarray(values, dtype=np.float64)

    # Convert log10-encoded data to linear (mg/m³)
    if color_cfg.get("log10_encoded"):
        values = np.power(10.0, values)

    fig, ax = plt.subplots(1, 1, figsize=(10, 8))

    norm = _create_norm(color_cfg)
    cmap = plt.get_cmap(color_cfg["cmap"]).copy()
    cmap.set_bad(color="#d9d9d9")

    y_coords = data.coords.get("y", np.arange(values.shape[0]))
    x_coords = data.coords.get("x", np.arange(values.shape[1]))
    y_vals = np.asarray(y_coords)
    x_vals = np.asarray(x_coords)

    im = ax.pcolormesh(
        x_vals,
        y_vals,
        values,
        cmap=cmap,
        norm=norm,
        shading="auto",
    )

    cbar = fig.colorbar(im, ax=ax, shrink=0.8, pad=0.02)
    cbar.set_label(color_cfg.get("label", dataset_name), fontsize=11)

    ax.set_aspect("equal")
    _add_coastlines(ax)

    title = _build_title(provenance, dataset_name)
    ax.set_title(title, fontsize=12, pad=10)
    ax.set_xlabel("Easting (m)", fontsize=10)
    ax.set_ylabel("Northing (m)", fontsize=10)

    fig.tight_layout()
    fig.savefig(str(path), dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)

    logger.info("PNG exported: %s", path)
    return path


def _create_norm(color_cfg: dict) -> mcolors.Normalize:
    """Create a matplotlib norm (linear or log)."""
    if color_cfg.get("log_scale"):
        return mcolors.LogNorm(
            vmin=color_cfg["vmin"],
            vmax=color_cfg["vmax"],
        )
    return mcolors.Normalize(
        vmin=color_cfg["vmin"],
        vmax=color_cfg["vmax"],
    )


def _add_coastlines(ax: plt.Axes) -> None:
    """Add coastlines using pycoast if available, otherwise skip."""
    try:
        from pycoast import ContourWriterAGG  # noqa: F401

        logger.debug(
            "pycoast available but matplotlib-based rendering used. "
            "Coastlines require GSHHS shapefiles configured separately."
        )
    except ImportError:
        logger.debug("pycoast not available, coastlines not added")
    except Exception as exc:
        logger.debug("Could not add coastlines: %s", exc)


def _build_title(provenance: Provenance, dataset_name: str) -> str:
    """Build a descriptive title for the PNG."""
    time_str = provenance.sensing_time.strftime("%Y-%m-%d %H:%M UTC")

    if provenance.composite_window_days:
        return (
            f"{dataset_name} — {provenance.composite_window_days}-day composite\n"
            f"{time_str} | {provenance.satellite} | "
            f"Mask: {provenance.masking_preset}"
        )

    return (
        f"{dataset_name} — {provenance.satellite}\n"
        f"{time_str} | Mask: {provenance.masking_preset}"
    )
