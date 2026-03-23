"""WQSF quality/cloud masking for OLCI L2 products.

Flag definitions are read from product metadata rather than hardcoded
bit positions, following EUMETSAT guidance for cross-collection compatibility.
"""

from __future__ import annotations

import logging

import dask.array as da
import numpy as np
import xarray as xr
from satpy import Scene

from s3bloom.config import MaskingConfig

logger = logging.getLogger(__name__)


def build_quality_mask(
    scene: Scene,
    masking_config: MaskingConfig,
    *,
    product: str | None = None,
) -> xr.DataArray:
    """Build a boolean mask from WQSF flags.

    True = pixel should be masked (bad quality).
    False = pixel is good.

    Flag bit positions are read from the dataset's flag_meanings/flag_masks
    attributes, not hardcoded.

    When *product* is given, the flag list is tailored to that product
    (e.g. BAC flags for chl_oc4me, OCNN_FAIL for chl_nn).
    """
    if product is not None:
        flags_to_mask = masking_config.flags_for_product(product)
    else:
        flags_to_mask = masking_config.flags
    logger.info("Building quality mask with flags: %s", flags_to_mask)

    wqsf = scene["wqsf"]

    flag_meanings, flag_masks = _get_flag_definitions(wqsf)

    if not flag_meanings:
        logger.warning(
            "No flag definitions found in WQSF dataset. "
            "Falling back to no masking."
        )
        return xr.DataArray(
            da.zeros(wqsf.shape, dtype=bool, chunks=wqsf.data.chunks),
            dims=wqsf.dims,
            coords=wqsf.coords,
        )

    combined_bitmask = _build_combined_bitmask(
        flags_to_mask, flag_meanings, flag_masks
    )

    if combined_bitmask == 0:
        logger.warning(
            "None of the requested flags were found in WQSF metadata. "
            "No pixels will be masked."
        )
        return xr.DataArray(
            da.zeros(wqsf.shape, dtype=bool, chunks=wqsf.data.chunks),
            dims=wqsf.dims,
            coords=wqsf.coords,
        )

    wqsf_values = wqsf.values if not hasattr(wqsf.data, "dask") else wqsf.data
    mask_data = (wqsf_values.astype(np.uint64) & np.uint64(combined_bitmask)) != 0

    mask = xr.DataArray(
        mask_data,
        dims=wqsf.dims,
        coords=wqsf.coords,
        attrs={"description": f"Quality mask ({masking_config.preset})"},
    )

    if hasattr(mask_data, "compute"):
        masked_count = "deferred (dask)"
    else:
        masked_count = int(np.sum(mask_data))
        total = int(np.prod(mask_data.shape))
        pct = 100.0 * masked_count / total if total > 0 else 0
        logger.info(
            "Mask: %d / %d pixels masked (%.1f%%)",
            masked_count,
            total,
            pct,
        )

    return mask


def apply_mask(
    data: xr.DataArray,
    mask: xr.DataArray,
) -> xr.DataArray:
    """Apply quality mask: set masked pixels to NaN."""
    return data.where(~mask, other=np.nan)


def _get_flag_definitions(
    wqsf: xr.DataArray,
) -> tuple[list[str], list[int]]:
    """Extract flag_meanings and flag_masks from dataset attributes."""
    attrs = wqsf.attrs

    meanings_raw = attrs.get("flag_meanings", "")
    if isinstance(meanings_raw, str):
        meanings = meanings_raw.split()
    elif isinstance(meanings_raw, (list, tuple)):
        meanings = list(meanings_raw)
    else:
        meanings = []

    masks_raw = attrs.get("flag_masks", [])
    if isinstance(masks_raw, np.ndarray):
        masks = masks_raw.tolist()
    elif isinstance(masks_raw, (list, tuple)):
        masks = list(masks_raw)
    else:
        masks = []

    if len(meanings) != len(masks):
        logger.warning(
            "flag_meanings (%d) and flag_masks (%d) length mismatch",
            len(meanings),
            len(masks),
        )
        min_len = min(len(meanings), len(masks))
        meanings = meanings[:min_len]
        masks = masks[:min_len]

    return meanings, masks


def _build_combined_bitmask(
    flags_to_mask: list[str],
    flag_meanings: list[str],
    flag_masks: list[int],
) -> int:
    """Build a combined bitmask from requested flag names."""
    meaning_to_mask = dict(zip(flag_meanings, flag_masks))
    combined = 0
    matched = []
    unmatched = []

    for flag in flags_to_mask:
        if flag in meaning_to_mask:
            combined |= int(meaning_to_mask[flag])
            matched.append(flag)
        else:
            for meaning in flag_meanings:
                if meaning.startswith(flag):
                    combined |= int(meaning_to_mask[meaning])
                    matched.append(meaning)
                    break
            else:
                unmatched.append(flag)

    if matched:
        logger.debug("Matched flags: %s", matched)
    if unmatched:
        logger.warning(
            "Flags not found in product metadata: %s. "
            "Available flags: %s",
            unmatched,
            flag_meanings[:30],
        )

    return combined
