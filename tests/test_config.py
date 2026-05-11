"""Tests for config module."""

from datetime import date

import pytest

from s3bloom.config import (
    BoundingBox,
    MaskingConfig,
    OutputConfig,
    PipelineConfig,
    TimePeriod,
)


class TestBoundingBox:
    def test_valid_bbox(self):
        bb = BoundingBox(lon_min=10.0, lat_min=56.5, lon_max=13.0, lat_max=59.0)
        assert bb.lon_min == 10.0
        assert bb.as_tuple() == (10.0, 56.5, 13.0, 59.0)

    def test_invalid_lon_order(self):
        with pytest.raises(ValueError, match="lon_min"):
            BoundingBox(lon_min=13.0, lat_min=56.5, lon_max=10.0, lat_max=59.0)

    def test_invalid_lat_order(self):
        with pytest.raises(ValueError, match="lat_min"):
            BoundingBox(lon_min=10.0, lat_min=59.0, lon_max=13.0, lat_max=56.5)

    def test_from_preset(self):
        bb = BoundingBox.from_string("swedish_west_coast")
        assert bb.lon_min == 10.0
        assert bb.lat_max == 59.0

    def test_from_csv(self):
        bb = BoundingBox.from_string("7.0,57.0,12.0,59.5")
        assert bb.lon_min == 7.0
        assert bb.lat_min == 57.0
        assert bb.lon_max == 12.0
        assert bb.lat_max == 59.5

    def test_from_string_invalid_count(self):
        with pytest.raises(ValueError, match="4 comma-separated"):
            BoundingBox.from_string("1.0,2.0,3.0")

    def test_from_string_unknown_preset(self):
        with pytest.raises(ValueError, match="4 comma-separated"):
            BoundingBox.from_string("nowhere_land")


class TestTimePeriod:
    def test_valid_period(self):
        tp = TimePeriod(start_date=date(2024, 3, 1), end_date=date(2024, 3, 31))
        assert tp.start_date == date(2024, 3, 1)

    def test_same_day(self):
        tp = TimePeriod(start_date=date(2024, 3, 1), end_date=date(2024, 3, 1))
        assert tp.start_date == tp.end_date

    def test_invalid_order(self):
        with pytest.raises(ValueError, match="start_date"):
            TimePeriod(start_date=date(2024, 3, 31), end_date=date(2024, 3, 1))


class TestMaskingConfig:
    def test_default_preset(self):
        mc = MaskingConfig()
        assert mc.preset == "strict"
        assert "CLOUD" in mc.flags

    def test_moderate(self):
        mc = MaskingConfig(preset="moderate")
        assert "CLOUD_MARGIN" not in mc.flags

    def test_invalid_preset(self):
        with pytest.raises(ValueError, match="Unknown masking preset"):
            MaskingConfig(preset="nonexistent")

    def test_custom_flags(self):
        mc = MaskingConfig(custom_flags=["CLOUD", "INVALID"])
        assert mc.flags == ["CLOUD", "INVALID"]

    def test_flags_for_chl_nn(self):
        mc = MaskingConfig(preset="strict")
        flags = mc.flags_for_product("chl_nn")
        assert "CLOUD" in flags
        assert "OCNN_FAIL" in flags
        assert "MEGLINT" in flags
        assert "AC_FAIL" not in flags
        assert "RWNEG_O2" not in flags
        assert "WHITECAPS" not in flags

    def test_flags_for_chl_oc4me(self):
        mc = MaskingConfig(preset="strict")
        flags = mc.flags_for_product("chl_oc4me")
        assert "CLOUD" in flags
        assert "OC4ME_FAIL" in flags
        assert "AC_FAIL" in flags
        assert "RWNEG_O2" in flags

    def test_flags_for_product_with_custom_flags(self):
        mc = MaskingConfig(custom_flags=["CLOUD", "INVALID"])
        assert mc.flags_for_product("chl_nn") == ["CLOUD", "INVALID"]

    def test_moderate_chl_nn_no_bac_flags(self):
        mc = MaskingConfig(preset="moderate")
        flags = mc.flags_for_product("chl_nn")
        assert "AC_FAIL" not in flags
        assert "WHITECAPS" not in flags
        assert "OCNN_FAIL" in flags
        assert "MEGLINT" in flags

    def test_relaxed_chl_nn_no_meglint(self):
        mc = MaskingConfig(preset="relaxed")
        flags = mc.flags_for_product("chl_nn")
        assert "MEGLINT" not in flags
        assert "OCNN_FAIL" in flags


class TestOutputConfig:
    def test_defaults(self):
        oc = OutputConfig()
        assert oc.projection == "EPSG:3035"
        assert oc.resolution_m == 300

    def test_directory_properties(self):
        from pathlib import Path

        oc = OutputConfig(base_dir=Path("/tmp/test"))
        assert oc.raw_dir == Path("/tmp/test/raw")
        assert oc.processed_dir == Path("/tmp/test/processed")
        assert oc.composites_dir == Path("/tmp/test/composites")

    def test_invalid_format(self):
        with pytest.raises(ValueError, match="Unknown format"):
            OutputConfig(formats=["geotiff", "jpeg"])


class TestEnsureDirectories:
    def test_creates_full_directory_tree(self, tmp_path):
        from pathlib import Path

        config = PipelineConfig(
            bbox=BoundingBox.from_string("swedish_west_coast"),
            time_period=TimePeriod(start_date=date(2024, 3, 1), end_date=date(2024, 3, 31)),
            output=OutputConfig(base_dir=tmp_path),
        )
        config.ensure_directories()

        assert (tmp_path / "raw").is_dir()
        for sub in ["geotiff", "netcdf", "png"]:
            assert (tmp_path / "processed" / sub).is_dir()
            assert (tmp_path / "composites" / sub).is_dir()

    def test_idempotent(self, tmp_path):
        config = PipelineConfig(
            bbox=BoundingBox.from_string("swedish_west_coast"),
            time_period=TimePeriod(start_date=date(2024, 3, 1), end_date=date(2024, 3, 31)),
            output=OutputConfig(base_dir=tmp_path),
        )
        config.ensure_directories()
        config.ensure_directories()  # must not raise


class TestPipelineConfig:
    def test_valid_config(self):
        config = PipelineConfig(
            bbox=BoundingBox.from_string("swedish_west_coast"),
            time_period=TimePeriod(
                start_date=date(2024, 3, 1), end_date=date(2024, 3, 31)
            ),
        )
        assert config.datasets == ["chl_nn"]
        assert config.masking.preset == "strict"

    def test_invalid_dataset_name(self):
        with pytest.raises(ValueError, match="Invalid dataset name"):
            PipelineConfig(
                bbox=BoundingBox.from_string("swedish_west_coast"),
                time_period=TimePeriod(
                    start_date=date(2024, 3, 1), end_date=date(2024, 3, 31)
                ),
                datasets=["Invalid-Name!"],
            )
