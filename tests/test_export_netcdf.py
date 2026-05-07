"""Tests for export.netcdf."""
import numpy as np
import pytest
import xarray as xr

from s3bloom.export.netcdf import (
    _build_dataset,
    _long_name,
    _standard_name,
    export_netcdf,
)


class TestLongName:
    def test_chl_nn(self):
        assert "chlorophyll" in _long_name("chl_nn").lower()

    def test_chl_oc4me(self):
        assert "chlorophyll" in _long_name("chl_oc4me").lower()

    def test_tsm_nn(self):
        assert "suspended" in _long_name("tsm_nn").lower()

    def test_unknown_returns_name_itself(self):
        assert _long_name("unknown_var") == "unknown_var"


class TestStandardName:
    def test_chl_nn(self):
        assert _standard_name("chl_nn") == "mass_concentration_of_chlorophyll_a_in_sea_water"

    def test_chl_oc4me(self):
        assert _standard_name("chl_oc4me") == "mass_concentration_of_chlorophyll_a_in_sea_water"

    def test_tsm_nn(self):
        assert "suspended_matter" in _standard_name("tsm_nn")

    def test_unknown_returns_empty_string(self):
        assert _standard_name("unknown") == ""


class TestBuildDataset:
    def test_returns_dataset(self, simple_dataarray, provenance):
        ds = _build_dataset(simple_dataarray, "chl_nn", provenance)
        assert isinstance(ds, xr.Dataset)

    def test_variable_present(self, simple_dataarray, provenance):
        ds = _build_dataset(simple_dataarray, "chl_nn", provenance)
        assert "chl_nn" in ds.data_vars

    def test_cf_conventions_attribute(self, simple_dataarray, provenance):
        ds = _build_dataset(simple_dataarray, "chl_nn", provenance)
        assert ds.attrs["Conventions"] == "CF-1.8"

    def test_provenance_attrs_prefixed(self, simple_dataarray, provenance):
        ds = _build_dataset(simple_dataarray, "chl_nn", provenance)
        assert "s3bloom_source_product" in ds.attrs
        assert "s3bloom_satellite" in ds.attrs

    def test_crs_wkt_stored(self, simple_dataarray, provenance):
        ds = _build_dataset(simple_dataarray, "chl_nn", provenance)
        assert "crs_wkt" in ds.attrs
        assert ds.attrs["crs_wkt"] == "EPSG:3035"

    def test_variable_has_long_name(self, simple_dataarray, provenance):
        ds = _build_dataset(simple_dataarray, "chl_nn", provenance)
        assert "long_name" in ds["chl_nn"].attrs
        assert "units" in ds["chl_nn"].attrs

    def test_x_y_coord_attrs(self, simple_dataarray, provenance):
        ds = _build_dataset(simple_dataarray, "chl_nn", provenance)
        assert ds.x.attrs.get("axis") == "X"
        assert ds.y.attrs.get("axis") == "Y"
        assert ds.x.attrs.get("standard_name") == "projection_x_coordinate"

    def test_drops_non_spatial_extra_coords(self, simple_dataarray, provenance):
        da = simple_dataarray.assign_coords({"spatial_ref": 0})
        ds = _build_dataset(da, "chl_nn", provenance)
        assert "spatial_ref" not in ds.coords

    def test_unknown_dataset_still_builds(self, simple_dataarray, provenance):
        ds = _build_dataset(simple_dataarray, "unknown_var", provenance)
        assert "unknown_var" in ds.data_vars


class TestExportNetcdf:
    def test_writes_file(self, tmp_path, simple_dataarray, provenance):
        out = tmp_path / "out.nc"
        result = export_netcdf(simple_dataarray, out, provenance, "chl_nn")
        assert result == out
        assert out.exists()
        assert out.stat().st_size > 0

    def test_creates_parent_directories(self, tmp_path, simple_dataarray, provenance):
        out = tmp_path / "nested" / "out.nc"
        export_netcdf(simple_dataarray, out, provenance, "chl_nn")
        assert out.exists()

    def test_returns_path(self, tmp_path, simple_dataarray, provenance):
        out = tmp_path / "out.nc"
        result = export_netcdf(simple_dataarray, out, provenance, "chl_nn")
        assert result == out

    def test_output_is_readable_netcdf(self, tmp_path, simple_dataarray, provenance):
        out = tmp_path / "out.nc"
        export_netcdf(simple_dataarray, out, provenance, "chl_nn")
        ds = xr.open_dataset(out)
        assert "chl_nn" in ds.data_vars
        ds.close()

    def test_netcdf_has_cf_conventions(self, tmp_path, simple_dataarray, provenance):
        out = tmp_path / "out.nc"
        export_netcdf(simple_dataarray, out, provenance, "chl_nn")
        ds = xr.open_dataset(out)
        assert ds.attrs.get("Conventions") == "CF-1.8"
        ds.close()
