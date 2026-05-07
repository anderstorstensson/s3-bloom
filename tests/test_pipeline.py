"""Tests for processing.pipeline."""
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
import xarray as xr

from s3bloom.config import BoundingBox, MaskingConfig, OutputConfig, PipelineConfig, TimePeriod
from s3bloom.processing.pipeline import PassResult, process_single_pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minimal_config(tmp_path: Path) -> PipelineConfig:
    return PipelineConfig(
        bbox=BoundingBox(lon_min=10.0, lat_min=56.5, lon_max=13.0, lat_max=59.0),
        time_period=TimePeriod(start_date=date(2024, 3, 15), end_date=date(2024, 3, 15)),
        output=OutputConfig(base_dir=tmp_path, formats=["netcdf"]),
    )


# ---------------------------------------------------------------------------
# PassResult
# ---------------------------------------------------------------------------

class TestPassResult:
    def test_construction(self, tmp_path, simple_dataarray, provenance):
        result = PassResult(
            product_path=tmp_path / "product.SEN3",
            satellite="S3A",
            sensing_time=datetime(2024, 3, 15, tzinfo=timezone.utc),
            datasets={"chl_nn": simple_dataarray},
            provenance={"chl_nn": provenance},
            output_files=[tmp_path / "out.nc"],
        )
        assert result.satellite == "S3A"
        assert "chl_nn" in result.datasets

    def test_frozen(self, tmp_path, simple_dataarray, provenance):
        result = PassResult(
            product_path=tmp_path / "product.SEN3",
            satellite="S3A",
            sensing_time=datetime(2024, 3, 15, tzinfo=timezone.utc),
            datasets={"chl_nn": simple_dataarray},
            provenance={"chl_nn": provenance},
            output_files=[],
        )
        with pytest.raises(Exception):
            result.satellite = "S3B"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# process_single_pass
# ---------------------------------------------------------------------------

class TestProcessSinglePass:
    def test_returns_pass_result(self, tmp_path, simple_dataarray, provenance):
        config = _minimal_config(tmp_path)
        product_path = tmp_path / "S3A_OL_2_WFR____20240315T091500.SEN3"
        product_path.mkdir()

        mock_scene = {"chl_nn": simple_dataarray}

        with (
            patch("s3bloom.processing.pipeline.extract_satellite", return_value="S3A"),
            patch("s3bloom.processing.pipeline.extract_sensing_time", return_value=datetime(2024, 3, 15, tzinfo=timezone.utc)),
            patch("s3bloom.processing.pipeline.load_scene", return_value=mock_scene),
            patch("s3bloom.processing.pipeline.build_quality_mask", return_value=MagicMock()),
            patch("s3bloom.processing.pipeline.apply_mask", return_value=simple_dataarray),
            patch("s3bloom.processing.pipeline.create_target_area", return_value=MagicMock()),
            patch("s3bloom.processing.pipeline.resample_scene", return_value={"chl_nn": simple_dataarray}),
            patch("s3bloom.processing.pipeline.create_pass_provenance", return_value=provenance),
            patch("s3bloom.processing.pipeline.pass_output_path", return_value=tmp_path / "out.nc"),
            patch("s3bloom.processing.pipeline.export_dataset"),
        ):
            result = process_single_pass(product_path, config)

        assert isinstance(result, PassResult)
        assert result.satellite == "S3A"
        assert "chl_nn" in result.datasets
        assert "chl_nn" in result.provenance

    def test_output_files_collected(self, tmp_path, simple_dataarray, provenance):
        config = _minimal_config(tmp_path)
        product_path = tmp_path / "S3A_OL_2_WFR____20240315T091500.SEN3"
        product_path.mkdir()

        out_path = tmp_path / "out.nc"
        mock_scene = {"chl_nn": simple_dataarray}

        with (
            patch("s3bloom.processing.pipeline.extract_satellite", return_value="S3A"),
            patch("s3bloom.processing.pipeline.extract_sensing_time", return_value=datetime(2024, 3, 15, tzinfo=timezone.utc)),
            patch("s3bloom.processing.pipeline.load_scene", return_value=mock_scene),
            patch("s3bloom.processing.pipeline.build_quality_mask", return_value=MagicMock()),
            patch("s3bloom.processing.pipeline.apply_mask", return_value=simple_dataarray),
            patch("s3bloom.processing.pipeline.create_target_area", return_value=MagicMock()),
            patch("s3bloom.processing.pipeline.resample_scene", return_value={"chl_nn": simple_dataarray}),
            patch("s3bloom.processing.pipeline.create_pass_provenance", return_value=provenance),
            patch("s3bloom.processing.pipeline.pass_output_path", return_value=out_path),
            patch("s3bloom.processing.pipeline.export_dataset"),
        ):
            result = process_single_pass(product_path, config)

        # one format (netcdf) × one dataset (chl_nn) = 1 file
        assert len(result.output_files) == 1
        assert result.output_files[0] == out_path

    def test_dataset_not_in_scene_skips_masking(self, tmp_path, simple_dataarray, provenance):
        config = _minimal_config(tmp_path)
        product_path = tmp_path / "S3A_OL_2_WFR____20240315T091500.SEN3"
        product_path.mkdir()

        # Scene does NOT contain the requested dataset
        mock_scene = {}

        with (
            patch("s3bloom.processing.pipeline.extract_satellite", return_value="S3A"),
            patch("s3bloom.processing.pipeline.extract_sensing_time", return_value=datetime(2024, 3, 15, tzinfo=timezone.utc)),
            patch("s3bloom.processing.pipeline.load_scene", return_value=mock_scene),
            patch("s3bloom.processing.pipeline.build_quality_mask") as mock_mask,
            patch("s3bloom.processing.pipeline.apply_mask"),
            patch("s3bloom.processing.pipeline.create_target_area", return_value=MagicMock()),
            patch("s3bloom.processing.pipeline.resample_scene", return_value={}),
            patch("s3bloom.processing.pipeline.create_pass_provenance", return_value=provenance),
            patch("s3bloom.processing.pipeline.pass_output_path", return_value=tmp_path / "out.nc"),
            patch("s3bloom.processing.pipeline.export_dataset"),
        ):
            result = process_single_pass(product_path, config)

        mock_mask.assert_not_called()
        assert result.output_files == []
