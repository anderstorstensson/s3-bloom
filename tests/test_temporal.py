"""Tests for compositing/temporal.py."""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pytest
import xarray as xr

from s3bloom.compositing import temporal
from s3bloom.compositing.temporal import (
    _compute_composite_dates,
    _group_by_dataset,
    _nanmean_composite,
    create_composites,
)
from s3bloom.config import BoundingBox, PipelineConfig, TimePeriod
from s3bloom.processing.pipeline import PassResult


def _make_array(values: list[list[float]], attrs: dict | None = None) -> xr.DataArray:
    """Build a small (y, x) DataArray for tests."""
    a = np.asarray(values, dtype=np.float64)
    return xr.DataArray(
        a,
        dims=("y", "x"),
        coords={"y": np.arange(a.shape[0], dtype=float),
                "x": np.arange(a.shape[1], dtype=float)},
        attrs=attrs or {},
    )


def _make_pass(
    *,
    sensing: datetime,
    satellite: str,
    dataset: str,
    values: list[list[float]],
    product_name: str | None = None,
) -> PassResult:
    da = _make_array(values, attrs={"crs": "EPSG:3035"})
    name = product_name or f"{satellite}_OL_2_WFR____{sensing:%Y%m%dT%H%M%S}.SEN3"
    return PassResult(
        product_path=Path(name),
        satellite=satellite,
        sensing_time=sensing,
        datasets={dataset: da},
        provenance={},
        output_files=[],
    )


def _make_config(tmp_path: Path, *, formats: list[str], window: int = 3) -> PipelineConfig:
    return PipelineConfig(
        bbox=BoundingBox(lon_min=10.0, lat_min=56.0, lon_max=12.0, lat_max=58.0),
        time_period=TimePeriod(
            start_date=date(2024, 3, 15), end_date=date(2024, 3, 17)
        ),
        datasets=["chl_nn"],
        output={
            "base_dir": tmp_path,
            "formats": formats,
            "composite_window_days": window,
        },
    )


# --------------------------------------------------------------------------- #
# _nanmean_composite                                                          #
# --------------------------------------------------------------------------- #


class TestNanmeanComposite:
    def test_single_array_returned_as_copy(self):
        arr = _make_array([[1.0, 2.0], [3.0, 4.0]], attrs={"crs": "EPSG:3035"})
        out = _nanmean_composite([arr])

        np.testing.assert_array_equal(out.values, arr.values)
        # Modifying the input must not affect the output → copy semantics.
        arr.values[0, 0] = 99.0
        assert out.values[0, 0] == 1.0

    def test_mean_of_two_arrays(self):
        a = _make_array([[1.0, 2.0], [3.0, 4.0]])
        b = _make_array([[3.0, 4.0], [5.0, 6.0]])

        out = _nanmean_composite([a, b])

        np.testing.assert_array_equal(out.values, [[2.0, 3.0], [4.0, 5.0]])

    def test_nan_is_skipped_per_pixel(self):
        a = _make_array([[1.0, np.nan], [np.nan, 4.0]])
        b = _make_array([[3.0, 5.0], [7.0, np.nan]])
        c = _make_array([[np.nan, 9.0], [np.nan, 8.0]])

        out = _nanmean_composite([a, b, c])

        # (0,0): mean(1, 3) = 2; (0,1): mean(5, 9) = 7
        # (1,0): only 7;        (1,1): mean(4, 8) = 6
        np.testing.assert_array_equal(out.values, [[2.0, 7.0], [7.0, 6.0]])

    def test_all_nan_pixel_stays_nan(self):
        a = _make_array([[np.nan, 1.0], [1.0, 1.0]])
        b = _make_array([[np.nan, 2.0], [2.0, 2.0]])

        out = _nanmean_composite([a, b])

        assert np.isnan(out.values[0, 0])
        assert out.values[0, 1] == 1.5

    def test_attributes_preserved_from_first_array(self):
        a = _make_array([[1.0, 2.0], [3.0, 4.0]], attrs={"crs": "EPSG:3035", "units": "mg m-3"})
        b = _make_array([[3.0, 4.0], [5.0, 6.0]], attrs={"crs": "EPSG:9999"})

        out = _nanmean_composite([a, b])

        assert out.attrs["crs"] == "EPSG:3035"
        assert out.attrs["units"] == "mg m-3"

    def test_pass_idx_dim_is_consumed(self):
        a = _make_array([[1.0]])
        b = _make_array([[3.0]])
        out = _nanmean_composite([a, b])
        assert "pass_idx" not in out.dims


# --------------------------------------------------------------------------- #
# _compute_composite_dates                                                    #
# --------------------------------------------------------------------------- #


class TestComputeCompositeDates:
    def test_empty_input_returns_empty(self):
        assert _compute_composite_dates([], window=3) == []

    def test_single_date(self):
        d = date(2024, 3, 15)
        assert _compute_composite_dates([d], window=3) == [d]

    def test_fills_gaps_between_first_and_last(self):
        # Only two observed dates with a gap in between — every calendar
        # day in the range still gets a composite date.
        observed = [date(2024, 3, 15), date(2024, 3, 18)]
        out = _compute_composite_dates(observed, window=3)
        assert out == [
            date(2024, 3, 15),
            date(2024, 3, 16),
            date(2024, 3, 17),
            date(2024, 3, 18),
        ]

    def test_returns_sorted_dates(self):
        observed = [date(2024, 3, 18), date(2024, 3, 15), date(2024, 3, 16)]
        out = _compute_composite_dates(observed, window=3)
        assert out == sorted(out)

    def test_duplicates_collapsed(self):
        observed = [date(2024, 3, 15), date(2024, 3, 15), date(2024, 3, 16)]
        out = _compute_composite_dates(observed, window=3)
        assert out == [date(2024, 3, 15), date(2024, 3, 16)]


# --------------------------------------------------------------------------- #
# _group_by_dataset                                                           #
# --------------------------------------------------------------------------- #


class TestGroupByDataset:
    def test_groups_by_dataset_then_date(self):
        p1 = _make_pass(
            sensing=datetime(2024, 3, 15, 9, 15, tzinfo=timezone.utc),
            satellite="S3A",
            dataset="chl_nn",
            values=[[1.0]],
        )
        p2 = _make_pass(
            sensing=datetime(2024, 3, 16, 9, 30, tzinfo=timezone.utc),
            satellite="S3B",
            dataset="chl_nn",
            values=[[2.0]],
        )

        grouped = _group_by_dataset([p1, p2])

        assert set(grouped.keys()) == {"chl_nn"}
        assert set(grouped["chl_nn"].keys()) == {date(2024, 3, 15), date(2024, 3, 16)}

    def test_same_day_passes_share_a_bucket(self):
        # S3A and S3B passes on the same day should land together so they
        # contribute equally to that day's composite.
        p1 = _make_pass(
            sensing=datetime(2024, 3, 15, 9, 0, tzinfo=timezone.utc),
            satellite="S3A",
            dataset="chl_nn",
            values=[[1.0]],
        )
        p2 = _make_pass(
            sensing=datetime(2024, 3, 15, 11, 0, tzinfo=timezone.utc),
            satellite="S3B",
            dataset="chl_nn",
            values=[[2.0]],
        )

        grouped = _group_by_dataset([p1, p2])

        bucket = grouped["chl_nn"][date(2024, 3, 15)]
        assert len(bucket) == 2
        sats = {entry[1] for entry in bucket}
        assert sats == {"S3A", "S3B"}

    def test_multi_dataset_pass_split_per_dataset(self):
        da_chl = _make_array([[1.0]])
        da_tsm = _make_array([[10.0]])
        pr = PassResult(
            product_path=Path("S3A.SEN3"),
            satellite="S3A",
            sensing_time=datetime(2024, 3, 15, tzinfo=timezone.utc),
            datasets={"chl_nn": da_chl, "tsm_nn": da_tsm},
            provenance={},
            output_files=[],
        )

        grouped = _group_by_dataset([pr])

        assert set(grouped.keys()) == {"chl_nn", "tsm_nn"}

    def test_empty_input(self):
        assert _group_by_dataset([]) == {}

    def test_entries_carry_data_satellite_product_name(self):
        p = _make_pass(
            sensing=datetime(2024, 3, 15, tzinfo=timezone.utc),
            satellite="S3A",
            dataset="chl_nn",
            values=[[42.0]],
            product_name="S3A_TEST.SEN3",
        )

        grouped = _group_by_dataset([p])
        entry = grouped["chl_nn"][date(2024, 3, 15)][0]

        data, satellite, product_name = entry
        assert isinstance(data, xr.DataArray)
        assert satellite == "S3A"
        assert product_name == "S3A_TEST.SEN3"


# --------------------------------------------------------------------------- #
# create_composites                                                           #
# --------------------------------------------------------------------------- #


class _ExportRecorder:
    """Captures export_dataset calls so create_composites can be tested
    without exercising the real GeoTIFF/NetCDF/PNG writers."""

    def __init__(self):
        self.calls: list[dict] = []

    def __call__(self, data, path, fmt, provenance, dataset_name):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        self.calls.append(
            {
                "path": path,
                "fmt": fmt,
                "dataset": dataset_name,
                "shape": data.shape,
                "values": np.asarray(data.values).copy(),
                "provenance": provenance,
            }
        )


@pytest.fixture
def recorder(monkeypatch):
    rec = _ExportRecorder()
    monkeypatch.setattr(temporal, "export_dataset", rec)
    return rec


class TestCreateComposites:
    def test_empty_pass_results_returns_empty(self, tmp_path, recorder):
        cfg = _make_config(tmp_path, formats=["geotiff"])
        out = create_composites([], cfg)
        assert out == []
        assert recorder.calls == []

    def test_writes_one_file_per_format_per_date(self, tmp_path, recorder):
        # Three consecutive days, one pass each. With a 3-day window
        # every day gets a composite, in two formats.
        passes = [
            _make_pass(
                sensing=datetime(2024, 3, 15, 9, tzinfo=timezone.utc),
                satellite="S3A",
                dataset="chl_nn",
                values=[[1.0]],
            ),
            _make_pass(
                sensing=datetime(2024, 3, 16, 9, tzinfo=timezone.utc),
                satellite="S3A",
                dataset="chl_nn",
                values=[[2.0]],
            ),
            _make_pass(
                sensing=datetime(2024, 3, 17, 9, tzinfo=timezone.utc),
                satellite="S3A",
                dataset="chl_nn",
                values=[[3.0]],
            ),
        ]
        cfg = _make_config(tmp_path, formats=["geotiff", "netcdf"], window=3)

        files = create_composites(passes, cfg)

        # 3 dates × 2 formats = 6 outputs
        assert len(files) == 6
        assert len(recorder.calls) == 6
        formats = {c["fmt"] for c in recorder.calls}
        assert formats == {"geotiff", "netcdf"}

    def test_window_average_is_computed(self, tmp_path, recorder):
        # With window=3, the composite for 2024-03-16 should average
        # the passes from 15, 16, 17 → mean(1, 2, 3) = 2.
        passes = [
            _make_pass(
                sensing=datetime(2024, 3, 15, tzinfo=timezone.utc),
                satellite="S3A", dataset="chl_nn", values=[[1.0]],
            ),
            _make_pass(
                sensing=datetime(2024, 3, 16, tzinfo=timezone.utc),
                satellite="S3A", dataset="chl_nn", values=[[2.0]],
            ),
            _make_pass(
                sensing=datetime(2024, 3, 17, tzinfo=timezone.utc),
                satellite="S3A", dataset="chl_nn", values=[[3.0]],
            ),
        ]
        cfg = _make_config(tmp_path, formats=["geotiff"], window=3)

        create_composites(passes, cfg)

        by_date = {c["path"].name: c["values"] for c in recorder.calls}
        # The middle date sees all three passes.
        middle = next(v for n, v in by_date.items() if "20240316" in n)
        np.testing.assert_allclose(middle, [[2.0]])

    def test_combines_multiple_satellites_in_provenance(self, tmp_path, recorder):
        # Two satellites on the same day → satellite tag "S3A-S3B".
        passes = [
            _make_pass(
                sensing=datetime(2024, 3, 15, 9, tzinfo=timezone.utc),
                satellite="S3A", dataset="chl_nn", values=[[1.0]],
            ),
            _make_pass(
                sensing=datetime(2024, 3, 15, 11, tzinfo=timezone.utc),
                satellite="S3B", dataset="chl_nn", values=[[3.0]],
            ),
        ]
        cfg = _make_config(tmp_path, formats=["geotiff"], window=3)

        create_composites(passes, cfg)

        prov = recorder.calls[0]["provenance"]
        assert prov.satellite == "S3A-S3B"
        assert prov.pass_count == 2
        assert prov.composite_window_days == 3

    def test_composite_filename_encodes_window_and_date(self, tmp_path, recorder):
        passes = [
            _make_pass(
                sensing=datetime(2024, 3, 15, 9, tzinfo=timezone.utc),
                satellite="S3A", dataset="chl_nn", values=[[1.0]],
            ),
        ]
        cfg = _make_config(tmp_path, formats=["geotiff"], window=3)

        files = create_composites(passes, cfg)

        assert len(files) == 1
        name = files[0].name
        assert "composite3d" in name
        assert "20240315" in name
        assert name.startswith("s3bloom_chl_nn_")
        assert name.endswith(".tif")

    def test_per_dataset_composites(self, tmp_path, recorder):
        da_chl = _make_array([[1.0]])
        da_tsm = _make_array([[10.0]])
        pr = PassResult(
            product_path=Path("S3A.SEN3"),
            satellite="S3A",
            sensing_time=datetime(2024, 3, 15, tzinfo=timezone.utc),
            datasets={"chl_nn": da_chl, "tsm_nn": da_tsm},
            provenance={},
            output_files=[],
        )
        cfg = _make_config(tmp_path, formats=["geotiff"], window=3)

        create_composites([pr], cfg)

        datasets = {c["dataset"] for c in recorder.calls}
        assert datasets == {"chl_nn", "tsm_nn"}
