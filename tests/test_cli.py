"""Tests for the CLI."""
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from s3bloom.cli import _build_config, app
from s3bloom.config import OutputConfig
from s3bloom.processing.pipeline import PassResult

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pass_result(tmp_path, simple_dataarray, provenance):
    return PassResult(
        product_path=tmp_path / "product.SEN3",
        satellite="S3A",
        sensing_time=datetime(2024, 3, 15, tzinfo=timezone.utc),
        datasets={"chl_nn": simple_dataarray},
        provenance={"chl_nn": provenance},
        output_files=[tmp_path / "out.nc"],
    )


# ---------------------------------------------------------------------------
# --version
# ---------------------------------------------------------------------------

class TestVersionFlag:
    def test_prints_version(self):
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert "s3bloom" in result.output


# ---------------------------------------------------------------------------
# list-presets
# ---------------------------------------------------------------------------

class TestListPresets:
    def test_exit_code_zero(self):
        result = runner.invoke(app, ["list-presets"])
        assert result.exit_code == 0

    def test_shows_bbox_preset_name(self):
        result = runner.invoke(app, ["list-presets"])
        assert "swedish_west_coast" in result.output

    def test_shows_masking_presets(self):
        result = runner.invoke(app, ["list-presets"])
        assert "strict" in result.output


# ---------------------------------------------------------------------------
# _build_config
# ---------------------------------------------------------------------------

class TestBuildConfig:
    def _call(self, **overrides):
        defaults = dict(
            start_date="2024-03-01",
            end_date="2024-03-31",
            bbox_str="swedish_west_coast",
            masking="strict",
            mask_dilation=-1,
            datasets="chl_nn",
            output_dir=Path("data"),
            projection="EPSG:3035",
            resolution=300,
            composite_window=3,
            formats="geotiff,netcdf,png",
        )
        defaults.update(overrides)
        return _build_config(**defaults)

    def test_parses_bbox_preset(self):
        config = self._call()
        assert config.bbox.lon_min == 10.0

    def test_parses_date_strings(self):
        from datetime import date
        config = self._call(start_date="2024-01-15", end_date="2024-01-20")
        assert config.time_period.start_date == date(2024, 1, 15)

    def test_parses_datasets_csv(self):
        config = self._call(datasets="chl_nn,tsm_nn")
        assert config.datasets == ["chl_nn", "tsm_nn"]

    def test_parses_formats_csv(self):
        config = self._call(formats="geotiff,netcdf")
        assert config.output.formats == ["geotiff", "netcdf"]

    def test_invalid_date_raises(self):
        with pytest.raises(ValueError):
            self._call(start_date="not-a-date")

    def test_invalid_bbox_raises(self):
        with pytest.raises(ValueError):
            self._call(bbox_str="not_a_preset_or_csv")

    def test_delete_raw_defaults_to_false(self):
        config = self._call()
        assert config.output.delete_raw is False

    def test_delete_raw_true_propagates(self):
        config = self._call(delete_raw=True)
        assert config.output.delete_raw is True


# ---------------------------------------------------------------------------
# run command — mock all external I/O
# ---------------------------------------------------------------------------

_RUN_BASE_ARGS = [
    "run",
    "--start-date", "2024-03-01",
    "--end-date", "2024-03-31",
    "--bbox", "swedish_west_coast",
]


class TestRunCommand:
    def test_no_products_found_exits_0(self, tmp_path):
        with patch("s3bloom.discovery.search.search_products", return_value=[]):
            result = runner.invoke(app, _RUN_BASE_ARGS + ["--output-dir", str(tmp_path)])
        assert result.exit_code == 0
        assert "No products found" in result.output

    def test_invalid_bbox_exits_1(self, tmp_path):
        result = runner.invoke(app, [
            "run",
            "--start-date", "2024-03-01",
            "--end-date", "2024-03-31",
            "--bbox", "definitely_unknown_preset_xyz",
            "--output-dir", str(tmp_path),
        ])
        assert result.exit_code == 1

    def test_no_downloads_exits_1(self, tmp_path):
        with (
            patch("s3bloom.discovery.search.search_products", return_value=[MagicMock()]),
            patch("s3bloom.discovery.download.download_products", return_value=[]),
        ):
            result = runner.invoke(app, _RUN_BASE_ARGS + ["--output-dir", str(tmp_path)])
        assert result.exit_code == 1
        assert "No products were downloaded" in result.output

    def test_all_passes_fail_exits_1(self, tmp_path):
        with (
            patch("s3bloom.discovery.search.search_products", return_value=[MagicMock()]),
            patch("s3bloom.discovery.download.download_products", return_value=[tmp_path / "p.SEN3"]),
            patch("s3bloom.processing.pipeline.process_single_pass", side_effect=RuntimeError("bad")),
        ):
            result = runner.invoke(app, _RUN_BASE_ARGS + ["--output-dir", str(tmp_path)])
        assert result.exit_code == 1
        assert "No passes were processed" in result.output

    def test_successful_run_no_composites_exits_0(self, tmp_path, simple_dataarray, provenance):
        pr = _pass_result(tmp_path, simple_dataarray, provenance)
        with (
            patch("s3bloom.discovery.search.search_products", return_value=[MagicMock()]),
            patch("s3bloom.discovery.download.download_products", return_value=[tmp_path / "p.SEN3"]),
            patch("s3bloom.processing.pipeline.process_single_pass", return_value=pr),
        ):
            result = runner.invoke(app, _RUN_BASE_ARGS + [
                "--output-dir", str(tmp_path),
                "--no-composites",
            ])
        assert result.exit_code == 0
        assert "Pipeline complete" in result.output

    def test_verbose_flag_accepted(self, tmp_path):
        with patch("s3bloom.discovery.search.search_products", return_value=[]):
            result = runner.invoke(app, _RUN_BASE_ARGS + [
                "--output-dir", str(tmp_path),
                "--verbose",
            ])
        assert result.exit_code == 0

    def test_delete_raw_flag_accepted(self, tmp_path):
        with patch("s3bloom.discovery.search.search_products", return_value=[]):
            result = runner.invoke(app, _RUN_BASE_ARGS + [
                "--output-dir", str(tmp_path),
                "--delete-raw",
            ])
        assert result.exit_code == 0

    def test_delete_raw_removes_product_dir_after_successful_pass(
        self, tmp_path, simple_dataarray, provenance
    ):
        product_dir = tmp_path / "p.SEN3"
        product_dir.mkdir()
        pr = _pass_result(tmp_path, simple_dataarray, provenance)
        with (
            patch("s3bloom.discovery.search.search_products", return_value=[MagicMock()]),
            patch("s3bloom.discovery.download.download_products", return_value=[product_dir]),
            patch("s3bloom.processing.pipeline.process_single_pass", return_value=pr),
            patch("shutil.rmtree") as mock_rmtree,
        ):
            result = runner.invoke(app, _RUN_BASE_ARGS + [
                "--output-dir", str(tmp_path),
                "--no-composites",
                "--delete-raw",
            ])
        assert result.exit_code == 0
        mock_rmtree.assert_called_once_with(product_dir)

    def test_delete_raw_not_called_by_default(
        self, tmp_path, simple_dataarray, provenance
    ):
        product_dir = tmp_path / "p.SEN3"
        product_dir.mkdir()
        pr = _pass_result(tmp_path, simple_dataarray, provenance)
        with (
            patch("s3bloom.discovery.search.search_products", return_value=[MagicMock()]),
            patch("s3bloom.discovery.download.download_products", return_value=[product_dir]),
            patch("s3bloom.processing.pipeline.process_single_pass", return_value=pr),
            patch("shutil.rmtree") as mock_rmtree,
        ):
            result = runner.invoke(app, _RUN_BASE_ARGS + [
                "--output-dir", str(tmp_path),
                "--no-composites",
            ])
        assert result.exit_code == 0
        mock_rmtree.assert_not_called()

    def test_delete_raw_not_called_on_failed_pass(self, tmp_path):
        product_dir = tmp_path / "p.SEN3"
        product_dir.mkdir()
        with (
            patch("s3bloom.discovery.search.search_products", return_value=[MagicMock()]),
            patch("s3bloom.discovery.download.download_products", return_value=[product_dir]),
            patch("s3bloom.processing.pipeline.process_single_pass", side_effect=RuntimeError("bad")),
            patch("shutil.rmtree") as mock_rmtree,
        ):
            result = runner.invoke(app, _RUN_BASE_ARGS + [
                "--output-dir", str(tmp_path),
                "--delete-raw",
            ])
        assert result.exit_code == 1
        mock_rmtree.assert_not_called()
