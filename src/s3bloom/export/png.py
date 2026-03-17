"""PNG map export with land mask, coastlines, and lat/lon gridlines."""

from __future__ import annotations

import logging
from pathlib import Path

import cartopy.crs as ccrs
import cartopy.feature as cfeature
import matplotlib
import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import numpy as np
import pyproj
import xarray as xr

matplotlib.use("Agg")

import cmocean  # noqa: E402, F401 -- registers colormaps

from s3bloom.defaults import COLORMAP_SETTINGS
from s3bloom.metadata.provenance import Provenance

logger = logging.getLogger(__name__)

_LAND_FEATURE = cfeature.NaturalEarthFeature(
    "physical", "land", "10m", facecolor="#f0e6d2", edgecolor="none"
)
_COASTLINE_FEATURE = cfeature.NaturalEarthFeature(
    "physical", "coastline", "10m", facecolor="none", edgecolor="#444444", linewidth=0.6
)


def _projection_from_epsg(epsg_str: str) -> ccrs.Projection:
    """Convert an EPSG string like 'EPSG:3035' to a cartopy CRS."""
    code = int(epsg_str.split(":")[-1])
    proj = pyproj.CRS.from_epsg(code)
    cf = proj.to_cf()

    if "lambert_azimuthal_equal_area" in cf.get("grid_mapping_name", ""):
        return ccrs.LambertAzimuthalEqualArea(
            central_longitude=cf["longitude_of_projection_origin"],
            central_latitude=cf["latitude_of_projection_origin"],
            false_easting=cf.get("false_easting", 0),
            false_northing=cf.get("false_northing", 0),
        )

    # Fallback for other projections
    return ccrs.epsg(code)


def export_png(
    data: xr.DataArray,
    path: Path,
    provenance: Provenance,
    dataset_name: str,
) -> Path:
    """Export a DataArray as a PNG map with land mask, coastlines, and gridlines."""
    path.parent.mkdir(parents=True, exist_ok=True)

    color_cfg = COLORMAP_SETTINGS.get(
        dataset_name,
        {"cmap": "viridis", "vmin": 0, "vmax": 100, "log_scale": False, "label": dataset_name},
    )

    values = data.values if hasattr(data, "values") else np.array(data)
    if hasattr(values, "compute"):
        values = values.compute()
    values = np.asarray(values, dtype=np.float64)

    if color_cfg.get("log10_encoded"):
        values = np.power(10.0, values)

    data_crs = _projection_from_epsg(provenance.projection)

    fig, ax = plt.subplots(
        1, 1, figsize=(10, 8),
        subplot_kw={"projection": data_crs},
    )

    norm = _create_norm(color_cfg)
    cmap = plt.get_cmap(color_cfg["cmap"]).copy()
    cmap.set_bad(color="#d9d9d9")

    y_vals = np.asarray(data.coords.get("y", np.arange(values.shape[0])))
    x_vals = np.asarray(data.coords.get("x", np.arange(values.shape[1])))

    im = ax.pcolormesh(
        x_vals,
        y_vals,
        values,
        cmap=cmap,
        norm=norm,
        shading="auto",
        transform=data_crs,
    )

    ax.add_feature(_LAND_FEATURE, zorder=2)
    ax.add_feature(_COASTLINE_FEATURE, zorder=3)

    gl = ax.gridlines(
        draw_labels=True,
        linewidth=0.4,
        color="gray",
        alpha=0.5,
        linestyle="--",
    )
    gl.top_labels = False
    gl.right_labels = False

    cbar = fig.colorbar(im, ax=ax, shrink=0.8, pad=0.02)
    cbar.set_label(color_cfg.get("label", dataset_name), fontsize=11)

    ax.set_title(_build_title(provenance, dataset_name), fontsize=12, pad=10)

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
