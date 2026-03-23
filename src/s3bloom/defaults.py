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

# --- WQSF masking flags (EUMETSAT Matchup Protocols v8B, Appendix A) -------
#
# Flags are split into three categories per the EUMETSAT guidance:
#   1. Common flags   – shared by all Ocean Colour products
#   2. Processing-chain flags – BAC (Baseline Atmospheric Correction, Open
#      Water) only; *not* applied to AAC (Alternative AC, Complex Water)
#   3. Product flags  – per-product algorithm failure flags
#
# The strictness presets (strict / moderate / relaxed) control how many
# *common* flags are applied.  Processing-chain and product flags are
# selected automatically based on the requested dataset name.

_COMMON_FLAGS: dict[str, list[str]] = {
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
    ],
    "moderate": [
        "CLOUD",
        "CLOUD_AMBIGUOUS",
        "INVALID",
        "COSMETIC",
        "SATURATED",
        "SUSPECT",
        "SNOW_ICE",
    ],
    "relaxed": [
        "CLOUD",
        "INVALID",
        "SATURATED",
        "SNOW_ICE",
    ],
}

# BAC processing-chain flags (Open Water products only)
_BAC_FLAGS: dict[str, list[str]] = {
    "strict": [
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
        "AC_FAIL",
        "WHITECAPS",
    ],
    "relaxed": [],
}

# Products processed with the Baseline Atmospheric Correction (Open Water)
BAC_PRODUCTS: frozenset[str] = frozenset({"chl_oc4me"})

# Per-product algorithm failure flags (applied regardless of strictness)
PRODUCT_FLAGS: dict[str, list[str]] = {
    "chl_oc4me": ["OC4ME_FAIL"],
    "chl_nn": ["OCNN_FAIL"],
    "tsm_nn": ["OCNN_FAIL"],
    "iop_nn": ["OCNN_FAIL"],
}


def get_masking_flags(preset: str, product: str) -> list[str]:
    """Return the correct masking flags for a product at a given strictness.

    Combines:
      common flags (strictness-dependent)
      + BAC processing-chain flags (only for Open Water products)
      + product-specific algorithm failure flag

    Based on EUMETSAT Matchup Protocols v8B, Appendix A, Table 1.
    """
    if preset not in _COMMON_FLAGS:
        raise ValueError(
            f"Unknown masking preset: {preset!r}. "
            f"Choose from: {', '.join(_COMMON_FLAGS)}"
        )

    flags = list(_COMMON_FLAGS[preset])

    if product in BAC_PRODUCTS:
        flags.extend(_BAC_FLAGS[preset])

    flags.extend(PRODUCT_FLAGS.get(product, []))

    return flags


# Preset names exposed for CLI validation and help text.
MASKING_PRESETS: dict[str, list[str]] = {
    name: list(flags) for name, flags in _COMMON_FLAGS.items()
}

DEFAULT_MASKING = "strict"

DEFAULT_DATASETS: list[str] = ["chl_nn"]

DEFAULT_PROJECTION = "EPSG:3035"

DEFAULT_RESOLUTION = 300  # metres

COMPOSITE_WINDOW_DAYS = 3

COLORMAP_SETTINGS: dict[str, dict] = {
    "chl_nn": {
        "cmap": "cmo.algae",
        "vmin": 0.0,
        "vmax": 30.0,
        "log_scale": False,
        "log10_encoded": True,
        "label": "Chl-a (mg m⁻³)",
        "units": "mg m⁻³",
    },
    "chl_oc4me": {
        "cmap": "cmo.algae",
        "vmin": 0.0,
        "vmax": 30.0,
        "log_scale": False,
        "log10_encoded": True,
        "label": "Chl-a OC4Me (mg m⁻³)",
        "units": "mg m⁻³",
    },
    "tsm_nn": {
        "cmap": "cmo.turbid",
        "vmin": 0.5,
        "vmax": 50.0,
        "log_scale": False,
        "log10_encoded": True,
        "label": "TSM (g m⁻³)",
        "units": "g m⁻³",
    },
}

MAX_PARALLEL_DOWNLOADS = 2
