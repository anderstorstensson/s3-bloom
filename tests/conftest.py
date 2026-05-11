"""Shared pytest fixtures."""
from datetime import datetime, timezone

import numpy as np
import pytest
import xarray as xr

from s3bloom.metadata.provenance import Provenance


@pytest.fixture
def sensing_time():
    return datetime(2024, 3, 15, 9, 15, 0, tzinfo=timezone.utc)


@pytest.fixture
def provenance(sensing_time):
    return Provenance(
        source_product="S3A_OL_2_WFR____20240315T091500.SEN3",
        satellite="S3A",
        sensing_time=sensing_time,
        dataset="chl_nn",
        masking_preset="strict",
        masking_flags=("CLOUD", "INVALID"),
        masking_dilation_px=3,
        projection="EPSG:3035",
        resolution_m=300,
        pipeline_version="0.1.0",
    )


@pytest.fixture
def composite_provenance(sensing_time):
    return Provenance(
        source_product="composite_20240315",
        satellite="S3A-S3B",
        sensing_time=sensing_time,
        dataset="chl_nn",
        masking_preset="strict",
        masking_flags=("CLOUD", "INVALID"),
        masking_dilation_px=3,
        projection="EPSG:3035",
        resolution_m=300,
        pipeline_version="0.1.0",
        source_products=("prod1.SEN3", "prod2.SEN3"),
        composite_window_days=3,
        pass_count=2,
    )


@pytest.fixture
def simple_dataarray():
    """Small 4×6 2D DataArray with LAEA-projected x/y coords."""
    rng = np.random.default_rng(42)
    data = rng.random((4, 6)).astype(np.float32)
    data[0, 0] = np.nan
    x = np.linspace(4_000_000.0, 4_060_000.0, 6)
    y = np.linspace(3_000_000.0, 3_040_000.0, 4)
    return xr.DataArray(
        data,
        dims=["y", "x"],
        coords={"y": y, "x": x},
        name="chl_nn",
    )
