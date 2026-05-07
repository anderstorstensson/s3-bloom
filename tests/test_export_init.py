"""Tests for export.__init__.export_dataset dispatch."""
import pytest
from unittest.mock import patch

from s3bloom.export import export_dataset


class TestExportDataset:
    def test_dispatches_geotiff(self, tmp_path, simple_dataarray, provenance):
        out = tmp_path / "out.tif"
        with patch("s3bloom.export.geotiff.export_geotiff") as mock_gt:
            export_dataset(simple_dataarray, out, "geotiff", provenance, "chl_nn")
        mock_gt.assert_called_once_with(simple_dataarray, out, provenance)

    def test_dispatches_netcdf(self, tmp_path, simple_dataarray, provenance):
        out = tmp_path / "out.nc"
        with patch("s3bloom.export.netcdf.export_netcdf") as mock_nc:
            export_dataset(simple_dataarray, out, "netcdf", provenance, "chl_nn")
        mock_nc.assert_called_once_with(simple_dataarray, out, provenance, "chl_nn")

    def test_dispatches_png(self, tmp_path, simple_dataarray, provenance):
        out = tmp_path / "out.png"
        with patch("s3bloom.export.png.export_png") as mock_png:
            export_dataset(simple_dataarray, out, "png", provenance, "chl_nn")
        mock_png.assert_called_once_with(simple_dataarray, out, provenance, "chl_nn")

    def test_unknown_format_raises_value_error(self, tmp_path, simple_dataarray, provenance):
        out = tmp_path / "out.xyz"
        with pytest.raises(ValueError, match="Unknown export format"):
            export_dataset(simple_dataarray, out, "xyz", provenance, "chl_nn")

    def test_creates_parent_directory(self, tmp_path, simple_dataarray, provenance):
        out = tmp_path / "nested" / "out.nc"
        with patch("s3bloom.export.netcdf.export_netcdf"):
            export_dataset(simple_dataarray, out, "netcdf", provenance, "chl_nn")
        assert (tmp_path / "nested").is_dir()
