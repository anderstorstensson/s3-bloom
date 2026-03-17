"""Presets for bounding boxes, masking flags, and colormaps."""

from __future__ import annotations

BBOX_PRESETS: dict[str, tuple[float, float, float, float]] = {
    "swedish_west_coast": (10.0, 56.5, 13.0, 59.0),
    "kattegat": (10.0, 55.5, 13.0, 58.0),
    "skagerrak": (7.0, 57.0, 12.0, 59.5),
    "kattegat_skagerrak": (7.0, 55.5, 13.5, 59.5),
    "baltic_proper": (13.0, 54.0, 30.0, 66.0),
    "baltic_all": (7.0, 54.0, 30.0, 66.0),
}

MASKING_PRESETS: dict[str, list[str]] = {
    "strict": [
        "CLOUD",
        "CLOUD_AMBIGUOUS",
        "CLOUD_MARGIN",
        "INVALID",
        "COSMETIC",
        "SATURATED",
        "SUSPECT",
        "HISOLZEN",
        "HIGHGLINT",
        "SNOW_ICE",
        "AC_FAIL",
        "WHITECAPS",
        "ADJAC",
        "RWNEG_O2",
        "RWNEG_O3",
        "RWNEG_O4",
        "RWNEG_O5",
        "RWNEG_O6",
        "RWNEG_O7",
        "RWNEG_O8",
    ],
    "moderate": [
        "CLOUD",
        "CLOUD_AMBIGUOUS",
        "INVALID",
        "COSMETIC",
        "SATURATED",
        "SUSPECT",
        "SNOW_ICE",
        "AC_FAIL",
        "WHITECAPS",
    ],
    "relaxed": [
        "CLOUD",
        "INVALID",
        "SATURATED",
        "SNOW_ICE",
    ],
}

DEFAULT_MASKING = "strict"

DEFAULT_DATASETS: list[str] = ["chl_nn"]

DEFAULT_PROJECTION = "EPSG:3035"

DEFAULT_RESOLUTION = 300  # metres

COMPOSITE_WINDOW_DAYS = 3

COLORMAP_SETTINGS: dict[str, dict] = {
    "chl_nn": {
        "cmap": "cmo.algae",
        "vmin": 0.5,
        "vmax": 30.0,
        "log_scale": True,
        "log10_encoded": True,
        "label": "Chl-a (mg m⁻³)",
        "units": "mg m⁻³",
    },
    "chl_oc4me": {
        "cmap": "cmo.algae",
        "vmin": 0.5,
        "vmax": 30.0,
        "log_scale": True,
        "log10_encoded": True,
        "label": "Chl-a OC4Me (mg m⁻³)",
        "units": "mg m⁻³",
    },
    "tsm_nn": {
        "cmap": "cmo.turbid",
        "vmin": 0.5,
        "vmax": 50.0,
        "log_scale": True,
        "log10_encoded": True,
        "label": "TSM (g m⁻³)",
        "units": "g m⁻³",
    },
}

MAX_PARALLEL_DOWNLOADS = 2
