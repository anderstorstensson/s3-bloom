"""Tests for export.geotiff."""
import numpy as np
import pytest
import xarray as xr

from s3bloom.export.geotiff import _ensure_2d, export_geotiff


class TestEnsure2d:
    def test_2d_passthrough(self):
        da = xr.DataArray(np.ones((4, 6)), dims=["y", "x"])
        result = _ensure_2d(da)
        assert result.dims == ("y", "x")
        assert result is da

    def test_3d_singleton_leading_dim_squeezed(self):
        da = xr.DataArray(np.ones((1, 4, 6)), dims=["time", "y", "x"])
        result = _ensure_2d(da)
        assert result.dims == ("y", "x")
        assert result.shape == (4, 6)

    def test_3d_non_singleton_raises(self):
        da = xr.DataArray(np.ones((2, 4, 6)), dims=["time", "y", "x"])
        with pytest.raises(ValueError, match="Expected 2D"):
            _ensure_2d(da)

    def test_4d_raises(self):
        da = xr.DataArray(np.ones((1, 1, 4, 6)), dims=["a", "b", "y", "x"])
        with pytest.raises(ValueError, match="Expected 2D"):
            _ensure_2d(da)


class TestExportGeotiff:
    def test_writes_file(self, tmp_path, simple_dataarray, provenance):
        out = tmp_path / "out.tif"
        result = export_geotiff(simple_dataarray, out, provenance)
        assert result == out
        assert out.exists()
        assert out.stat().st_size > 0

    def test_creates_parent_directories(self, tmp_path, simple_dataarray, provenance):
        out = tmp_path / "nested" / "deeper" / "out.tif"
        export_geotiff(simple_dataarray, out, provenance)
        assert out.exists()

    def test_returns_path(self, tmp_path, simple_dataarray, provenance):
        out = tmp_path / "out.tif"
        result = export_geotiff(simple_dataarray, out, provenance)
        assert result == out

    def test_accepts_3d_singleton(self, tmp_path, provenance):
        data_3d = xr.DataArray(
            np.ones((1, 4, 6), dtype=np.float32),
            dims=["time", "y", "x"],
            coords={
                "y": np.linspace(3_000_000.0, 3_040_000.0, 4),
                "x": np.linspace(4_000_000.0, 4_060_000.0, 6),
            },
        )
        out = tmp_path / "out3d.tif"
        result = export_geotiff(data_3d, out, provenance)
        assert result.exists()
