"""Tests for masking logic."""

import numpy as np
import xarray as xr

from s3bloom.processing.masking import (
    _build_combined_bitmask,
    _get_flag_definitions,
    apply_mask,
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
        da = xr.DataArray(np.zeros((2, 2), dtype=np.uint32))
        meanings, masks = _get_flag_definitions(da)
        assert meanings == []
        assert masks == []


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
