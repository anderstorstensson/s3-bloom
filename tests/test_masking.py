"""Tests for masking logic."""

import dask.array as da
import numpy as np
import xarray as xr

from s3bloom.config import MaskingConfig
from s3bloom.processing.masking import (
    _build_combined_bitmask,
    _get_flag_definitions,
    apply_mask,
    build_quality_mask,
)


class TestBuildCombinedBitmask:
    def test_single_flag(self):
        meanings = ["CLOUD", "INVALID", "SATURATED"]
        masks = [1, 2, 4]
        result = _build_combined_bitmask(["CLOUD"], meanings, masks)
        assert result == 1

    def test_multiple_flags(self):
        meanings = ["CLOUD", "INVALID", "SATURATED"]
        masks = [1, 2, 4]
        result = _build_combined_bitmask(["CLOUD", "SATURATED"], meanings, masks)
        assert result == 5  # 1 | 4

    def test_unknown_flag(self):
        meanings = ["CLOUD", "INVALID"]
        masks = [1, 2]
        result = _build_combined_bitmask(["NONEXISTENT"], meanings, masks)
        assert result == 0

    def test_prefix_matching(self):
        meanings = ["RWNEG_O2", "RWNEG_O3"]
        masks = [16, 32]
        result = _build_combined_bitmask(["RWNEG_O2"], meanings, masks)
        assert result == 16

    def test_empty_flags(self):
        result = _build_combined_bitmask([], ["CLOUD"], [1])
        assert result == 0

    def test_prefix_match_without_exact_match(self):
        # "RWNEG" is not in meanings verbatim; "RWNEG_O2" starts with it
        meanings = ["RWNEG_O2", "RWNEG_O3"]
        masks = [16, 32]
        result = _build_combined_bitmask(["RWNEG"], meanings, masks)
        assert result == 16  # first prefix match, then break


class TestGetFlagDefinitions:
    def test_string_meanings(self):
        da = xr.DataArray(
            np.zeros((2, 2), dtype=np.uint32),
            attrs={
                "flag_meanings": "CLOUD INVALID SATURATED",
                "flag_masks": np.array([1, 2, 4], dtype=np.uint32),
            },
        )
        meanings, masks = _get_flag_definitions(da)
        assert meanings == ["CLOUD", "INVALID", "SATURATED"]
        assert masks == [1, 2, 4]

    def test_list_meanings(self):
        da = xr.DataArray(
            np.zeros((2, 2), dtype=np.uint32),
            attrs={
                "flag_meanings": ["CLOUD", "INVALID"],
                "flag_masks": [1, 2],
            },
        )
        meanings, masks = _get_flag_definitions(da)
        assert meanings == ["CLOUD", "INVALID"]

    def test_empty_attrs(self):
        da_empty = xr.DataArray(np.zeros((2, 2), dtype=np.uint32))
        meanings, masks = _get_flag_definitions(da_empty)
        assert meanings == []
        assert masks == []

    def test_flag_meanings_wrong_type_returns_empty(self):
        da_bad = xr.DataArray(
            np.zeros((2, 2), dtype=np.uint32),
            attrs={"flag_meanings": 99999, "flag_masks": [1, 2]},
        )
        meanings, _ = _get_flag_definitions(da_bad)
        assert meanings == []

    def test_flag_masks_wrong_type_returns_empty(self):
        da_bad = xr.DataArray(
            np.zeros((2, 2), dtype=np.uint32),
            attrs={"flag_meanings": "CLOUD INVALID", "flag_masks": 99999},
        )
        _, masks = _get_flag_definitions(da_bad)
        assert masks == []

    def test_mismatched_lengths_truncated_to_shorter(self):
        da_mismatch = xr.DataArray(
            np.zeros((2, 2), dtype=np.uint32),
            attrs={
                "flag_meanings": "CLOUD INVALID SATURATED",  # 3
                "flag_masks": [1, 2],                        # 2 → truncate to 2
            },
        )
        meanings, masks = _get_flag_definitions(da_mismatch)
        assert len(meanings) == 2
        assert len(masks) == 2
        assert meanings == ["CLOUD", "INVALID"]


class TestApplyMask:
    def test_basic_masking(self):
        data = xr.DataArray(np.array([[1.0, 2.0], [3.0, 4.0]]))
        mask = xr.DataArray(np.array([[True, False], [False, True]]))
        result = apply_mask(data, mask)
        assert np.isnan(result.values[0, 0])
        assert result.values[0, 1] == 2.0
        assert result.values[1, 0] == 3.0
        assert np.isnan(result.values[1, 1])

    def test_no_mask(self):
        data = xr.DataArray(np.array([[1.0, 2.0]]))
        mask = xr.DataArray(np.array([[False, False]]))
        result = apply_mask(data, mask)
        np.testing.assert_array_equal(result.values, data.values)


class TestBuildQualityMask:
    """Tests for build_quality_mask — uses a plain dict as a Scene stand-in."""

    def _numpy_scene(self, data=None, flag_meanings="CLOUD INVALID SATURATED", flag_masks=None):
        if data is None:
            # 3 = CLOUD|INVALID, 0 = clean, 5 = CLOUD|SATURATED
            data = np.array([[3, 0], [0, 5]], dtype=np.uint32)
        if flag_masks is None:
            flag_masks = np.array([1, 2, 4], dtype=np.uint32)
        wqsf = xr.DataArray(
            data,
            dims=["y", "x"],
            attrs={"flag_meanings": flag_meanings, "flag_masks": flag_masks},
        )
        return {"wqsf": wqsf}

    def _dask_scene(self, flag_meanings="CLOUD INVALID", flag_masks=None):
        if flag_masks is None:
            flag_masks = np.array([1, 2], dtype=np.uint32)
        wqsf = xr.DataArray(
            da.zeros((2, 2), dtype=np.uint32, chunks=(2, 2)),
            dims=["y", "x"],
            attrs={"flag_meanings": flag_meanings, "flag_masks": flag_masks},
        )
        return {"wqsf": wqsf}

    def test_returns_boolean_dataarray(self):
        scene = self._numpy_scene()
        mask = build_quality_mask(scene, MaskingConfig(custom_flags=["CLOUD"]))
        assert mask.dtype == bool

    def test_masks_flagged_pixels(self):
        scene = self._numpy_scene()
        mask = build_quality_mask(scene, MaskingConfig(custom_flags=["CLOUD"]))
        # pixel [0,0] = 3 has CLOUD bit → masked; [0,1] = 0 → not masked
        assert bool(mask.values[0, 0]) is True
        assert bool(mask.values[0, 1]) is False

    def test_no_product_uses_common_flags(self):
        scene = self._numpy_scene()
        mask = build_quality_mask(scene, MaskingConfig(preset="moderate"))
        assert mask.dtype == bool

    def test_with_product_uses_product_aware_flags(self):
        scene = self._numpy_scene()
        mask = build_quality_mask(scene, MaskingConfig(preset="strict"), product="chl_nn")
        assert mask.dtype == bool

    def test_empty_flag_meanings_returns_all_false(self):
        wqsf = xr.DataArray(
            da.zeros((2, 2), dtype=np.uint32, chunks=(2, 2)),
            dims=["y", "x"],
            attrs={},
        )
        scene = {"wqsf": wqsf}
        mask = build_quality_mask(scene, MaskingConfig(custom_flags=["CLOUD"]))
        assert not mask.values.any()

    def test_no_matching_flags_returns_all_false(self):
        scene = self._dask_scene()
        mask = build_quality_mask(scene, MaskingConfig(custom_flags=["COMPLETELY_UNKNOWN"]))
        assert not mask.values.any()
